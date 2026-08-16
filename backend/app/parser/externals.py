"""External dependencies — turning silence into a named fact.

Every import that does not resolve to a file in the repository is currently
nothing: no node, no edge, no trace. That is the single largest source of the
graph's silence, and it is silence about the most ordinary thing in a
codebase — `import requests`, `from django.db import models`, `import React`.

This reads the manifests a project already maintains (`package.json`,
`requirements.txt`, `pyproject.toml`) and emits an `EXTERNAL_DEPENDENCY` node
per declared package, then a `DEPENDS_ON` edge from every file that imports
one. Three things follow that the graph could not answer before:

    which of our files use this package
    what would a version bump touch
    which dependencies is nothing actually importing

**Declared packages only.** A bare specifier that appears in no manifest gets
no node. It is far more likely to be a stdlib module, a typo, or a
first-party import the resolver failed on than a real undeclared dependency,
and inventing a dependency the project never declared would be exactly the
confident-wrong-answer failure this codebase keeps refusing.

The import-name-to-package-name gap is real and handled where it is knowable:
`import yaml` comes from `PyYAML`, `import sklearn` from `scikit-learn`. Those
mappings are a small, well-known table rather than a guess — and where no
mapping exists, a normalised name match is required, not approximated.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from app.graph.schema import Edge, EdgeKind, Node, NodeKind
from app.parser.facts import FileFacts

#: Import names that differ from the package that ships them. Deliberately
#: short: only entries that are unambiguous and common enough to matter. A
#: longer table would be guesswork dressed as knowledge.
_IMPORT_TO_PACKAGE: dict[str, str] = {
    "yaml": "pyyaml",
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "jwt": "pyjwt",
    "attr": "attrs",
    "OpenSSL": "pyopenssl",
    "serial": "pyserial",
    "usb": "pyusb",
    "git": "gitpython",
    "google_auth_oauthlib": "google-auth-oauthlib",
}

#: `package==1.2`, `package>=1.0,<2`, `package[extra]~=3`, `package # comment`
_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9._-]+)")


def _normalise(name: str) -> str:
    """PyPI treats `-`, `_` and case as equivalent; npm scopes stay intact."""
    return name.strip().lower().replace("_", "-")


#: Directories a manifest scan must never descend into.
_SKIP_DIRS = frozenset({"node_modules", ".git", ".next", "dist", "build", "vendor", ".venv"})

#: How deep to look. A repository's manifests are near the top by convention —
#: `backend/requirements.txt`, `frontend/package.json`, `packages/*/package.json`.
_SCAN_DEPTH = 4


def read_manifests(root: Path) -> dict[str, str]:
    """Declared package name -> the manifest that declared it.

    **Searched, not assumed at the root.** The first version read only
    `<root>/package.json` and `<root>/requirements.txt` and found nothing at
    all in this very repository, whose manifests live in `backend/` and
    `frontend/` — the same mistake the tsconfig reader made, in a different
    file. Almost no real repository keeps every manifest at its top level.

    Declarations are merged across manifests: a package declared anywhere in
    the tree is a dependency of the tree. Which file declared it is kept on
    the node, so the claim stays traceable.

    Best-effort: an unreadable or malformed manifest costs its packages, never
    the analysis.
    """
    declared: dict[str, str] = {}
    for manifest in _find_manifests(root):
        label = manifest.relative_to(root).as_posix()
        if manifest.name == "package.json":
            _read_package_json(manifest, label, declared)
        elif manifest.name == "pyproject.toml":
            _read_pyproject(manifest, label, declared)
        else:
            _read_requirements(manifest, label, declared)
    return declared


def _find_manifests(root: Path) -> list[Path]:
    names = ("package.json", "pyproject.toml", "requirements.txt", "requirements-dev.txt")
    found: list[Path] = []
    for name in names:
        for depth in range(_SCAN_DEPTH):
            pattern = "/".join(["*"] * depth + [name]) if depth else name
            for path in root.glob(pattern):
                if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
                    continue
                if path.is_file():
                    found.append(path)
    return found


def _read_package_json(path: Path, label: str, declared: dict[str, str]) -> None:
    try:
        data = json.loads(path.read_text(errors="replace"))
    except (OSError, ValueError):
        return
    for field in ("dependencies", "devDependencies", "peerDependencies"):
        for name in (data.get(field) or {}):
            declared.setdefault(_normalise(name), label)


def _read_requirements(path: Path, label: str, declared: dict[str, str]) -> None:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        if not line.strip() or line.lstrip().startswith(("#", "-")):
            continue
        found = _REQUIREMENT.match(line)
        if found:
            declared.setdefault(_normalise(found.group(1)), label)


def _read_pyproject(path: Path, label: str, declared: dict[str, str]) -> None:
    try:
        data = tomllib.loads(path.read_text(errors="replace"))
    except (OSError, ValueError, TypeError):
        return
    for entry in (data.get("project") or {}).get("dependencies") or []:
        found = _REQUIREMENT.match(str(entry))
        if found:
            declared.setdefault(_normalise(found.group(1)), label)
    # Poetry keeps its own table.
    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    for name in poetry:
        if _normalise(name) != "python":
            declared.setdefault(_normalise(name), label)


def _package_for(specifier: str) -> str | None:
    """The distribution a bare import specifier belongs to.

    `from django.db import models` -> `django`; `@scope/pkg/sub` -> `@scope/pkg`.
    Relative specifiers are first-party and never reach here.
    """
    if not specifier or specifier.startswith("."):
        return None
    if specifier.startswith("@"):  # npm scoped: keep two segments
        parts = specifier.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else None
    head = specifier.split("/")[0].split(".")[0]
    return head or None


def external_dependencies(
    facts: list[FileFacts], declared: dict[str, str], resolved_targets: set[str]
) -> tuple[list[Node], list[Edge]]:
    """EXTERNAL_DEPENDENCY nodes and the DEPENDS_ON edges into them.

    `resolved_targets` is the set of module names that already resolved to a
    file in this repository; those are first-party and must never be
    mistaken for a package, even when a directory shares a dependency's name.
    """
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()

    for file_facts in facts:
        for raw in file_facts.imports:
            if raw.level != 0 or not raw.module:
                continue  # relative import: first-party by construction
            # JS marks package specifiers explicitly; Python records module
            # paths, so a name that matched a real file here is first-party.
            if not raw.bare and raw.module in resolved_targets:
                continue
            package = _package_for(raw.module)
            if package is None:
                continue

            key = _IMPORT_TO_PACKAGE.get(package, _normalise(package))
            if key not in declared:
                # Undeclared: stdlib, a typo, or a first-party import the
                # resolver missed. Any of those is likelier than a real
                # dependency nobody wrote down.
                continue

            node_id = f"{NodeKind.EXTERNAL_DEPENDENCY.value}:{key}"
            if key not in nodes:
                nodes[key] = Node(
                    id=node_id,
                    kind=NodeKind.EXTERNAL_DEPENDENCY,
                    name=key,
                    qualified_name=key,
                    extra={"declared_in": declared[key]},
                )
            pair = (file_facts.file_node.id, node_id)
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(
                Edge(
                    source_id=file_facts.file_node.id,
                    target_id=node_id,
                    kind=EdgeKind.DEPENDS_ON,
                    file_path=file_facts.path,
                    line=raw.line,
                    type_only=raw.type_only,
                )
            )

    return [nodes[key] for key in sorted(nodes)], edges
