"""Layer B — declared packages become nodes; undeclared guesses do not."""

from __future__ import annotations

from textwrap import dedent

from app.graph.schema import EdgeKind, NodeKind
from app.parser import parse_repository


def _build(tmp_path, files: dict[str, str]):
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dedent(body).lstrip())
    return parse_repository(tmp_path)


def _externals(graph) -> dict[str, int]:
    """package name -> how many files depend on it."""
    counts: dict[str, int] = {}
    names = {
        n.id: n.name for n in graph.nodes if n.kind is NodeKind.EXTERNAL_DEPENDENCY
    }
    for edge in graph.edges:
        if edge.kind is EdgeKind.DEPENDS_ON and edge.target_id in names:
            counts[names[edge.target_id]] = counts.get(names[edge.target_id], 0) + 1
    return counts


def test_a_declared_package_becomes_a_node_with_edges(tmp_path) -> None:
    graph = _build(
        tmp_path,
        {
            "requirements.txt": "requests==2.31.0\nfastapi>=0.100\n",
            "a.py": "import requests\n",
            "b.py": "from requests import Session\nimport fastapi\n",
        },
    )
    assert _externals(graph) == {"requests": 2, "fastapi": 1}


def test_an_undeclared_import_is_not_invented(tmp_path) -> None:
    """`import json` must not conjure a dependency. An undeclared bare import
    is stdlib, a typo, or a first-party import the resolver missed — every one
    of those is likelier than a real dependency nobody wrote down."""
    graph = _build(
        tmp_path,
        {
            "requirements.txt": "requests\n",
            "a.py": "import json\nimport os\nimport requests\n",
        },
    )
    assert _externals(graph) == {"requests": 1}


def test_a_first_party_module_is_never_mistaken_for_a_package(tmp_path) -> None:
    """A repo can contain a directory named after one of its dependencies.
    The one that resolves to a real file wins."""
    graph = _build(
        tmp_path,
        {
            "requirements.txt": "click\n",
            "click/__init__.py": "def go():\n    return 1\n",
            "main.py": "import click\n",
        },
    )
    assert _externals(graph) == {}
    assert any(
        e.kind is EdgeKind.IMPORTS and e.target_id == "file:click/__init__.py"
        for e in graph.edges
    )


def test_manifests_are_found_below_the_root(tmp_path) -> None:
    """The first version read only `<root>/requirements.txt` and found nothing
    in this very repository, whose manifests live in backend/ and frontend/."""
    graph = _build(
        tmp_path,
        {
            "backend/requirements.txt": "requests\n",
            "backend/app/main.py": "import requests\n",
        },
    )
    assert _externals(graph) == {"requests": 1}


def test_import_name_and_package_name_may_differ(tmp_path) -> None:
    graph = _build(
        tmp_path,
        {"requirements.txt": "PyYAML\nGitPython\n", "a.py": "import yaml\nimport git\n"},
    )
    assert _externals(graph) == {"pyyaml": 1, "gitpython": 1}


def test_submodule_imports_attach_to_the_distribution(tmp_path) -> None:
    graph = _build(
        tmp_path,
        {
            "requirements.txt": "django\n",
            "a.py": "from django.db import models\nfrom django.conf import settings\n",
        },
    )
    assert _externals(graph) == {"django": 1}  # one file, one edge, not two


def test_npm_scoped_packages_keep_their_scope(tmp_path) -> None:
    graph = _build(
        tmp_path,
        {
            "package.json": '{"dependencies": {"@scope/ui": "1.0.0", "react": "18"}}',
            "src/a.ts": 'import { Button } from "@scope/ui/button";\nimport React from "react";\n',
        },
    )
    assert _externals(graph) == {"@scope/ui": 1, "react": 1}
