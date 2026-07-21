"""CP-1.2 — the Python parser.

Three groups, in order of what would hurt most if it broke:

1. **Graph invariants**, checked against CodeLens's own backend — the most
   demanding real input available. A dangling edge or a duplicate id is a
   corrupt graph, and every query above it inherits the corruption.
2. **Robustness** — one unreadable file must never fail a repository.
3. **Resolution behaviour** — the import, call and inheritance cases the
   emitter claims to handle.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from app.graph.schema import EdgeKind, EntrypointKind, KnowledgeGraph, NodeKind
from app.parser import parse_repository

BACKEND_DIR = Path(__file__).resolve().parent.parent


def build(tmp_path: Path, files: dict[str, str]) -> KnowledgeGraph:
    """Write a throwaway repository and parse it."""
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(content).lstrip())
    return parse_repository(tmp_path)


def edges_of(graph: KnowledgeGraph, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.source_id, e.target_id) for e in graph.edges if e.kind is kind}


@pytest.fixture(scope="module")
def codelens_graph() -> KnowledgeGraph:
    """CodeLens parsing itself — dogfood, and the hardest input we have."""
    return parse_repository(BACKEND_DIR, max_size_mb=5_000)


# ── 1. Graph invariants ───────────────────────────────────────────────────


def test_no_dangling_edges(codelens_graph: KnowledgeGraph) -> None:
    """Every edge endpoint must be a real node. The cardinal graph invariant."""
    ids = {node.id for node in codelens_graph.nodes}
    dangling = [
        (e.source_id, e.target_id, e.kind.value)
        for e in codelens_graph.edges
        if e.source_id not in ids or e.target_id not in ids
    ]
    assert dangling == []


def test_node_ids_are_unique(codelens_graph: KnowledgeGraph) -> None:
    ids = [node.id for node in codelens_graph.nodes]
    assert len(ids) == len(set(ids))


def test_node_ids_follow_the_convention(codelens_graph: KnowledgeGraph) -> None:
    for node in codelens_graph.nodes:
        assert node.id == f"{node.kind.value}:{node.qualified_name}"


def test_every_node_except_the_repository_has_a_container(
    codelens_graph: KnowledgeGraph,
) -> None:
    """CONTAINS must span the graph: repo -> module -> file -> class -> function."""
    contained = {target for _, target in edges_of(codelens_graph, EdgeKind.CONTAINS)}
    orphans = [
        node.id
        for node in codelens_graph.nodes
        if node.kind is not NodeKind.REPOSITORY and node.id not in contained
    ]
    assert orphans == []


def test_parsing_is_deterministic(tmp_path: Path) -> None:
    """Constitution 4: the same input must always give the same graph."""
    files = {
        "pkg/__init__.py": "",
        "pkg/a.py": "from pkg.b import helper\n\ndef run():\n    return helper()\n",
        "pkg/b.py": "def helper():\n    return 1\n",
    }
    # Same directory *name* under different parents: the repository node is
    # named after its directory, so differing names would be a false failure.
    first = build(tmp_path / "one" / "repo", files)
    second = build(tmp_path / "two" / "repo", files)

    assert [n.qualified_name for n in first.nodes] == [n.qualified_name for n in second.nodes]
    assert [(e.source_id, e.target_id, e.kind) for e in first.edges] == [
        (e.source_id, e.target_id, e.kind) for e in second.edges
    ]


def test_calls_edges_carry_resolved_confidence(codelens_graph: KnowledgeGraph) -> None:
    """CP-1.2 only emits statically resolved calls; CP-1.3 adds the rest."""
    from app.graph.schema import CallConfidence

    calls = [e for e in codelens_graph.edges if e.kind is EdgeKind.CALLS]
    assert calls
    assert all(e.confidence is CallConfidence.RESOLVED for e in calls)


def test_edges_carry_evidence(codelens_graph: KnowledgeGraph) -> None:
    """Constitution 1: a relationship must point at where it is asserted."""
    for edge in codelens_graph.edges:
        if edge.kind in (EdgeKind.CALLS, EdgeKind.IMPORTS, EdgeKind.INHERITS):
            assert edge.file_path is not None
            assert edge.line is not None and edge.line > 0


# ── 2. Robustness ─────────────────────────────────────────────────────────


def test_syntax_error_does_not_fail_the_repository(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "broken.py": "def oops( : ???\n",
            "fine.py": "def works():\n    return 1\n",
        },
    )
    assert "function:fine.works" in {n.id for n in graph.nodes}


def test_empty_and_comment_only_files_are_handled(tmp_path: Path) -> None:
    graph = build(tmp_path, {"empty.py": "", "comments.py": "# nothing here\n"})
    assert {"file:empty.py", "file:comments.py"} <= {n.id for n in graph.nodes}


def test_undecodable_bytes_do_not_crash(tmp_path: Path) -> None:
    (tmp_path / "weird.py").write_bytes(b"def caf\xe9():\n    return 1\n")
    (tmp_path / "ok.py").write_text("def fine():\n    return 1\n")
    graph = parse_repository(tmp_path)
    assert "function:ok.fine" in {n.id for n in graph.nodes}


def test_external_and_builtin_calls_produce_no_edges(tmp_path: Path) -> None:
    """An edge needs two real endpoints; stdlib is Layer B, not Layer A."""
    graph = build(
        tmp_path,
        {
            "app.py": """
            import os

            def run():
                print(os.getcwd())
                return len([])
            """
        },
    )
    assert edges_of(graph, EdgeKind.CALLS) == set()
    assert edges_of(graph, EdgeKind.IMPORTS) == set()


# ── 3. Imports ────────────────────────────────────────────────────────────


def test_absolute_package_import(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from pkg.b import helper\n\ndef run():\n    return helper()\n",
            "pkg/b.py": "def helper():\n    return 1\n",
        },
    )
    assert ("file:pkg/a.py", "file:pkg/b.py") in edges_of(graph, EdgeKind.IMPORTS)
    assert ("function:pkg.a.run", "function:pkg.b.helper") in edges_of(graph, EdgeKind.CALLS)


def test_relative_import_of_sibling_module(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from .b import helper\n\ndef run():\n    return helper()\n",
            "pkg/b.py": "def helper():\n    return 1\n",
        },
    )
    assert ("file:pkg/a.py", "file:pkg/b.py") in edges_of(graph, EdgeKind.IMPORTS)
    assert ("function:pkg.a.run", "function:pkg.b.helper") in edges_of(graph, EdgeKind.CALLS)


def test_relative_import_of_module_object(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from . import b\n\ndef run():\n    return b.helper()\n",
            "pkg/b.py": "def helper():\n    return 1\n",
        },
    )
    assert ("function:pkg.a.run", "function:pkg.b.helper") in edges_of(graph, EdgeKind.CALLS)


def test_parent_relative_import(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/x.py": "def helper():\n    return 1\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/a.py": "from ..x import helper\n\ndef run():\n    return helper()\n",
        },
    )
    assert ("function:pkg.sub.a.run", "function:pkg.x.helper") in edges_of(graph, EdgeKind.CALLS)


def test_aliased_module_and_symbol_imports(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/b.py": "def helper():\n    return 1\n",
            "pkg/a.py": """
            import pkg.b as shortcut
            from pkg.b import helper as aliased

            def run():
                return shortcut.helper() + aliased()
            """,
        },
    )
    calls = edges_of(graph, EdgeKind.CALLS)
    assert ("function:pkg.a.run", "function:pkg.b.helper") in calls


def test_dotted_module_import(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/b.py": "def helper():\n    return 1\n",
            "pkg/a.py": "import pkg.b\n\ndef run():\n    return pkg.b.helper()\n",
        },
    )
    assert ("function:pkg.a.run", "function:pkg.b.helper") in edges_of(graph, EdgeKind.CALLS)


# ── 4. Calls, classes, inheritance ────────────────────────────────────────


def test_method_and_self_calls(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "svc.py": """
            class Service:
                def helper(self):
                    return 1

                def run(self):
                    return self.helper()
            """
        },
    )
    assert ("function:svc.Service.run", "function:svc.Service.helper") in edges_of(
        graph, EdgeKind.CALLS
    )


def test_instantiation_is_not_a_call(tmp_path: Path) -> None:
    """INSTANTIATES is post-MVP; a constructor must not masquerade as CALLS."""
    graph = build(
        tmp_path,
        {
            "svc.py": """
            class Service:
                def __init__(self):
                    self.ready = True

            def make():
                return Service()
            """
        },
    )
    assert edges_of(graph, EdgeKind.CALLS) == set()


def test_inheritance_within_and_across_files(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "base.py": "class Base:\n    pass\n",
            "impl.py": """
            from base import Base

            class Middle(Base):
                pass

            class Leaf(Middle):
                pass
            """,
        },
    )
    inherits = edges_of(graph, EdgeKind.INHERITS)
    assert ("class:impl.Middle", "class:base.Base") in inherits
    assert ("class:impl.Leaf", "class:impl.Middle") in inherits


def test_nested_functions_are_qualified_by_their_parent(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "outer.py": """
            def outer():
                def inner():
                    return 1
                return inner()
            """
        },
    )
    ids = {n.id for n in graph.nodes}
    assert "function:outer.outer.inner" in ids
    assert ("function:outer.outer", "function:outer.outer.inner") in edges_of(
        graph, EdgeKind.CONTAINS
    )


# ── 5. Entrypoints ────────────────────────────────────────────────────────


def test_main_guard_marks_entrypoint(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "cli.py": """
            def main():
                return 1

            if __name__ == "__main__":
                main()
            """
        },
    )
    node = next(n for n in graph.nodes if n.id == "function:cli.main")
    assert node.is_entrypoint
    assert node.entrypoint_kind is EntrypointKind.MAIN


def test_http_route_decorators_mark_entrypoints(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "api.py": """
            app = object()
            router = object()

            @app.get("/health")
            def health():
                return {}

            @router.post("/items")
            def create_item():
                return {}
            """
        },
    )
    marked = {n.qualified_name: n.entrypoint_kind for n in graph.nodes if n.is_entrypoint}
    assert marked == {
        "api.health": EntrypointKind.HTTP_ROUTE,
        "api.create_item": EntrypointKind.HTTP_ROUTE,
    }


def test_cli_decorators_mark_entrypoints(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "cmd.py": """
            import click

            @click.command()
            def serve():
                return 1
            """
        },
    )
    node = next(n for n in graph.nodes if n.id == "function:cmd.serve")
    assert node.entrypoint_kind is EntrypointKind.CLI


def test_unrelated_decorators_do_not_mark_entrypoints(tmp_path: Path) -> None:
    """Conservative by design: a false entrypoint misleads every reader."""
    graph = build(
        tmp_path,
        {
            "util.py": """
            from functools import lru_cache

            @lru_cache
            def cached():
                return 1
            """
        },
    )
    assert not any(n.is_entrypoint for n in graph.nodes)


# ── 6. Node metadata ──────────────────────────────────────────────────────


def test_functions_carry_complexity_and_classes_do_not(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "m.py": """
            class Holder:
                pass

            def branchy(items):
                total = 0
                for item in items:
                    if item:
                        total += 1
                    else:
                        total -= 1
                return total
            """
        },
    )
    function = next(n for n in graph.nodes if n.id == "function:m.branchy")
    holder = next(n for n in graph.nodes if n.id == "class:m.Holder")
    assert function.complexity is not None and function.complexity >= 3
    assert holder.complexity is None


def test_docstrings_and_line_ranges_are_captured(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "m.py": '''
            def documented():
                """Explain the thing."""
                return 1
            ''',
        },
    )
    node = next(n for n in graph.nodes if n.id == "function:m.documented")
    assert node.docstring == "Explain the thing."
    assert node.start_line == 1
    assert node.end_line == 3
    assert node.language == "Python"


def test_definition_nodes_carry_distinct_content_hashes(tmp_path: Path) -> None:
    """Per-definition hashes are the cache key for CP-3.2 summaries."""
    graph = build(
        tmp_path,
        {"m.py": "def a():\n    return 1\n\n\ndef b():\n    return 2\n"},
    )
    hashes = {
        n.content_hash for n in graph.nodes if n.kind is NodeKind.FUNCTION
    }
    assert len(hashes) == 2
    assert all(h and len(h) == 64 for h in hashes)


def test_snapshot_is_carried_through(tmp_path: Path) -> None:
    graph = build(tmp_path, {"m.py": "def a():\n    return 1\n"})
    assert graph.snapshot.primary_language == "Python"
    assert graph.snapshot.file_count == 1
