"""Parser engine — source in, graph facts out.

ARCHITECTURE.md §2 names this the hardest system, and the honest reason is
call resolution. This checkpoint (CP-1.2) resolves what can be resolved
statically and emits nothing for the rest; CP-1.3 adds the `heuristic` and
`dynamic_unknown` confidence levels that make the uncertain cases visible
rather than silent.

Zero LLM calls, zero database. The graph must be fully buildable and testable
without either (ARCHITECTURE.md corollary 1).
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any

import tree_sitter_typescript
from tree_sitter import Language

from app.graph.schema import (
    Edge,
    EdgeKind,
    EntrypointKind,
    KnowledgeGraph,
    Node,
    NodeKind,
)
from app.ingestion import IngestedRepo, snapshot_directory
from app.parser.externals import external_dependencies, read_manifests
from app.parser.facts import FileFacts
from app.parser.js_emitter import JsEmitter
from app.parser.python_emitter import PythonEmitter, module_qname_for
from app.parser.resolution import apply_entrypoints, resolve

__all__ = [
    "PARSED_EXTENSIONS",
    "PARSER_VERSION",
    "module_qname_for",
    "parse_ingested",
    "parse_repository",
]

#: Bump whenever emission or resolution semantics change — a new edge kind, a
#: different import resolution rule, a fixed false positive.
#:
#: The content-hash skip asks "are these the same bytes?", which is the right
#: question for re-analysing an unchanged repo and the wrong one after the
#: parser itself improves: the stored graph is still a faithful record of what
#: an *older* CodeLens saw. Without this, every fix shipped here would leave
#: existing users looking at pre-fix graphs with no way to tell. Observed
#: exactly that way — cycle counts in the UI stayed at the old value while the
#: same query on a fresh parse gave the corrected one.
#:
#:   2  type-only imports marked; `from . import X` no longer depends on the
#:      package root; endpoints, TESTS, CO_CHANGES, AUTHORED_BY emitted
#:   3  per-directory tsconfig aliases; barrel re-exports recorded;
#:      EXTERNAL_DEPENDENCY + DEPENDS_ON emitted
PARSER_VERSION = "3"

_PYTHON_EXTENSIONS = frozenset({"py", "pyi"})
_JS_EXTENSIONS = frozenset({"js", "jsx", "mjs", "cjs"})
_TS_EXTENSIONS = frozenset({"ts", "tsx"})

#: Everything the engine can read today: Python, JavaScript, TypeScript.
PARSED_EXTENSIONS = _PYTHON_EXTENSIONS | _JS_EXTENSIONS | _TS_EXTENSIONS

#: The two TypeScript grammars — .ts is the plain grammar, .tsx allows JSX.
_TS_LANGUAGE = Language(tree_sitter_typescript.language_typescript())
_TSX_LANGUAGE = Language(tree_sitter_typescript.language_tsx())


def parse_repository(root: Path | str, *, max_size_mb: int | None = None) -> KnowledgeGraph:
    """Ingest and parse a working tree in one call.

    The convenience entry point used by tests and the golden-fixture harness.
    """
    ingested = snapshot_directory(Path(root), max_size_mb=max_size_mb)
    return parse_ingested(ingested)


#: Below this many files, a process pool costs more (spawn + IPC) than the
#: parsing it parallelises. Above it, tree-sitter is CPU-bound and scales.
_PARALLEL_THRESHOLD = 400


def _build_emitters(aliases: dict[str, dict[str, str]]) -> dict[str, PythonEmitter | JsEmitter]:
    """Per-language emitters. Rebuilt inside each worker process, because
    tree-sitter Language handles cannot cross a process boundary."""
    return {
        **dict.fromkeys(_PYTHON_EXTENSIONS, PythonEmitter()),
        **dict.fromkeys(_JS_EXTENSIONS, JsEmitter(aliases=aliases)),
        "ts": JsEmitter(_TS_LANGUAGE, "TypeScript", aliases),
        "tsx": JsEmitter(_TSX_LANGUAGE, "TypeScript", aliases),
    }


_WORKER_EMITTERS: dict[str, PythonEmitter | JsEmitter] = {}
_WORKER_ROOT: Path | None = None


def _worker_init(root: Path, aliases: dict[str, dict[str, str]]) -> None:
    global _WORKER_ROOT
    _WORKER_ROOT = root
    _WORKER_EMITTERS.update(_build_emitters(aliases))


def _worker_parse(job: tuple[str, str, str, int]) -> FileFacts | None:
    """Parse one file inside a pool worker. Returns None for anything
    unreadable — one bad file must never fail the repository."""
    path, extension, content_hash, loc = job
    emitter = _WORKER_EMITTERS.get(extension)
    if emitter is None or _WORKER_ROOT is None:
        return None
    try:
        source = (_WORKER_ROOT / path).read_bytes()
    except OSError:
        return None
    return emitter.emit(path=path, source=source, content_hash=content_hash, loc=loc)


def parse_ingested(ingested: IngestedRepo) -> KnowledgeGraph:
    """Parse an already-ingested repository into a KnowledgeGraph.

    One emitter per language, one schema for all of them: the dispatch below
    is the *entire* per-language surface of the pipeline.

    Large repos parse across processes — tree-sitter is CPU-bound and a
    monorepo is tens of thousands of files (n8n: ~19k, ~30s single-threaded).
    Results are re-sorted by path afterwards, so the graph is byte-identical
    to the sequential build regardless of how the work was scheduled
    (Constitution 4: the same input always gives the same graph).
    """
    aliases = _read_path_aliases(ingested.root)
    by_extension = _build_emitters(aliases)
    facts: list[FileFacts] = []

    parseable = [f for f in ingested.files if f.extension in by_extension]
    if len(parseable) >= _PARALLEL_THRESHOLD:
        parsed = _parse_in_parallel(ingested, parseable, aliases)
        if parsed is not None:
            return _assemble(ingested, parsed)

    for source_file in ingested.files:
        emitter = by_extension.get(source_file.extension)
        if emitter is None:
            continue
        try:
            source = (ingested.root / source_file.path).read_bytes()
        except OSError:
            continue  # vanished or unreadable: skip the file, not the repo
        facts.append(
            emitter.emit(
                path=source_file.path,
                source=source,
                content_hash=source_file.content_hash,
                loc=source_file.loc,
            )
        )

    return _assemble(ingested, facts)


def _assemble(ingested: IngestedRepo, facts: list[FileFacts]) -> KnowledgeGraph:
    """Resolve facts into edges and build the structural spine. Shared by the
    sequential and parallel paths so both produce an identical graph."""
    edges, entrypoints = resolve(facts)
    nodes = _structural_nodes(ingested, facts, edges)
    apply_entrypoints(nodes, entrypoints)
    endpoint_nodes, endpoint_edges = _endpoints(facts)
    nodes.extend(endpoint_nodes)
    edges.extend(endpoint_edges)

    # Layer B. Every bare import that resolved to nothing used to be silence;
    # a declared package now becomes a node with real edges into it. The set
    # of module names that DID resolve is passed in so a first-party
    # directory sharing a dependency's name is never mistaken for it.
    resolved_targets = {f.module_qname for f in facts}
    external_nodes, external_edges = external_dependencies(
        facts, read_manifests(ingested.root), resolved_targets
    )
    nodes.extend(external_nodes)
    edges.extend(external_edges)
    snapshot = ingested.snapshot.model_copy(update={"parser_version": PARSER_VERSION})
    return KnowledgeGraph(snapshot=snapshot, nodes=nodes, edges=edges)


def _endpoints(facts: list[FileFacts]) -> tuple[list[Node], list[Edge]]:
    """ENDPOINT nodes and the ROUTES_TO edges that reach their handlers.

    One node per (method, path) *per file*: two files legitimately declaring
    `GET /health` are two endpoints on two routers, and merging them would
    invent a relationship. The file also holds the endpoint in the CONTAINS
    spine, so it appears on the map where its code lives.
    """
    nodes: list[Node] = []
    edges: list[Edge] = []
    seen: set[str] = set()

    for file_facts in facts:
        for route in file_facts.routes:
            label = f"{route.method} {route.path}"
            # `id == f"{kind}:{qualified_name}"` is an invariant the whole
            # graph relies on, so the qualified name carries the file too.
            qualified_name = f"{file_facts.path}:{label}"
            node_id = f"{NodeKind.ENDPOINT.value}:{qualified_name}"
            if node_id in seen:
                continue  # the same route declared twice in one file
            seen.add(node_id)

            nodes.append(
                Node(
                    id=node_id,
                    kind=NodeKind.ENDPOINT,
                    name=label,
                    qualified_name=qualified_name,
                    file_path=file_facts.path,
                    start_line=route.line,
                    language=file_facts.file_node.language,
                    is_entrypoint=True,
                    entrypoint_kind=EntrypointKind.HTTP_ROUTE,
                    extra={
                        "method": route.method,
                        "path": route.path,
                        "framework": route.framework,
                    },
                )
            )
            # The endpoint lives in its file, structurally.
            edges.append(
                Edge(
                    source_id=file_facts.file_node.id,
                    target_id=node_id,
                    kind=EdgeKind.CONTAINS,
                )
            )
            if route.handler_id and route.handler_id != file_facts.file_node.id:
                edges.append(
                    Edge(
                        source_id=node_id,
                        target_id=route.handler_id,
                        kind=EdgeKind.ROUTES_TO,
                        file_path=file_facts.path,
                        line=route.line,
                    )
                )

    return nodes, edges


def _parse_in_parallel(
    ingested: IngestedRepo, parseable: list[Any], aliases: dict[str, dict[str, str]]
) -> list[FileFacts] | None:
    """Parse across processes. Returns None if a pool can't be used, so the
    caller falls back to the sequential path rather than failing."""
    jobs = [(f.path, f.extension, f.content_hash, f.loc) for f in parseable]
    workers = min(os.cpu_count() or 2, 8)
    try:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(ingested.root, aliases),
        ) as pool:
            results = list(pool.map(_worker_parse, jobs, chunksize=64))
    except Exception:  # noqa: BLE001 - any pool failure degrades to sequential
        return None
    facts = [fact for fact in results if fact is not None]
    # Scheduling order must never reach the graph: sort back to path order.
    facts.sort(key=lambda fact: fact.path)
    return facts


#: Directories a config scan must never descend into.
_SKIP_DIRS = frozenset({"node_modules", ".git", ".next", "dist", "build", "vendor", ".venv"})

#: How deep to look for tsconfigs. Deep enough for `packages/*/tsconfig.json`
#: and `apps/web/tsconfig.json`; shallow enough not to walk a monorepo's
#: entire tree looking for a file that is conventionally near the top.
_CONFIG_SCAN_DEPTH = 4


def _read_path_aliases(root: Path) -> dict[str, dict[str, str]]:
    """Every tsconfig/jsconfig in the tree -> per-directory alias maps.

    Returns `{config_dir: {alias_prefix: dotted_module_prefix}}`, keyed by the
    repo-relative directory the config governs ("" for the root).

    **Why per-directory.** This used to read only `<root>/tsconfig.json`, which
    is wrong for every layout where the frontend is not the repository: a repo
    with `frontend/tsconfig.json` had its `@/` mapping ignored entirely and
    fell back to a guess. It resolved anyway *by luck*, through the unique
    suffix index — and in a monorepo where two packages both define `@/`, luck
    runs out and the alias resolves to the wrong package's file. A wrong edge
    is the expensive mistake.

    Targets are resolved relative to the config's own directory, so
    `frontend/tsconfig.json` mapping `@/*` -> `./*` yields `frontend.`, which
    is what `@/lib/store` actually means from inside `frontend/`.

    tsconfig is JSON-with-comments; comments and trailing commas are stripped
    before parsing. Best-effort by design: an unreadable config costs an alias,
    never a crash.
    """
    found: dict[str, dict[str, str]] = {}

    for config_path in _find_configs(root):
        directory = config_path.parent.relative_to(root).as_posix()
        directory = "" if directory == "." else directory
        aliases = _aliases_from(config_path, directory)
        if aliases:
            # A directory with both tsconfig and jsconfig: first wins, which
            # matches how the toolchain resolves them.
            found.setdefault(directory, {}).update(
                {k: v for k, v in aliases.items() if k not in found.get(directory, {})}
            )

    if found:
        return found

    # No usable config anywhere: the Next.js convention, at the root.
    return {"": {"@/": "src." if (root / "src").is_dir() else ""}}


def _find_configs(root: Path) -> list[Path]:
    """tsconfig/jsconfig files, nearest the root first."""
    configs: list[Path] = []
    for name in ("tsconfig.json", "jsconfig.json"):
        for depth in range(_CONFIG_SCAN_DEPTH):
            pattern = "/".join(["*"] * depth + [name]) if depth else name
            for path in root.glob(pattern):
                if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
                    continue
                if path.is_file():
                    configs.append(path)
    return configs


def _aliases_from(config_path: Path, directory: str) -> dict[str, str]:
    """One config's `paths`, as dotted module prefixes relative to the repo."""
    try:
        text = re.sub(r"//[^\n]*", "", config_path.read_text(errors="replace"))
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r",(\s*[}\]])", r"\1", text)  # trailing commas
        config = json.loads(text)
    except (OSError, ValueError):
        return {}

    options = config.get("compilerOptions", {})
    base = str(options.get("baseUrl", ".")).strip("./")
    aliases: dict[str, str] = {}
    for pattern, targets in (options.get("paths") or {}).items():
        if not targets:
            continue
        prefix = pattern.replace("*", "")
        target = str(targets[0]).replace("*", "").strip("./").replace("/", ".")
        if base and base != ".":
            target = f"{base}.{target}" if target else base
        # Prepend the config's own directory: an alias is relative to the
        # tsconfig that declares it, not to the repository root.
        if directory:
            dotted = directory.replace("/", ".")
            target = f"{dotted}.{target}" if target else dotted
        aliases[prefix] = f"{target}." if target and not target.endswith(".") else target
    return aliases


def _structural_nodes(
    ingested: IngestedRepo, facts: list[FileFacts], edges: list[Edge]
) -> list[Node]:
    """Assemble the CONTAINS spine: repository -> module -> file -> class -> function."""
    repo_name = ingested.root.name
    repository = Node(
        id=f"{NodeKind.REPOSITORY.value}:{repo_name}",
        kind=NodeKind.REPOSITORY,
        name=repo_name,
        qualified_name=repo_name,
        language=ingested.snapshot.primary_language,
        loc=ingested.snapshot.total_loc,
    )

    nodes: list[Node] = [repository]
    module_nodes: dict[str, Node] = {}

    for file_facts in facts:
        for directory in _ancestor_directories(file_facts.path):
            if directory in module_nodes:
                continue
            module_nodes[directory] = Node(
                id=f"{NodeKind.MODULE.value}:{directory}",
                kind=NodeKind.MODULE,
                name=PurePosixPath(directory).name,
                qualified_name=directory,
                file_path=directory,
                language="Python",
            )

    nodes.extend(module_nodes[key] for key in sorted(module_nodes))

    # module -> parent module, or repository for top-level directories
    for directory, module_node in sorted(module_nodes.items()):
        parent = str(PurePosixPath(directory).parent)
        parent_id = repository.id if parent == "." else f"{NodeKind.MODULE.value}:{parent}"
        edges.append(_contains(parent_id, module_node.id))

    for file_facts in facts:
        parent = str(PurePosixPath(file_facts.path).parent)
        parent_id = repository.id if parent == "." else f"{NodeKind.MODULE.value}:{parent}"
        edges.append(_contains(parent_id, file_facts.file_node.id))

        nodes.append(file_facts.file_node)
        nodes.extend(file_facts.nodes)
        for container_id, child_id in file_facts.contains:
            edges.append(_contains(container_id, child_id))

    return nodes


def _ancestor_directories(path: str) -> list[str]:
    directories: list[str] = []
    parent = PurePosixPath(path).parent
    while str(parent) != ".":
        directories.append(str(parent))
        parent = parent.parent
    return directories


def _contains(parent_id: str, child_id: str) -> Edge:
    return Edge(source_id=parent_id, target_id=child_id, kind=EdgeKind.CONTAINS)
