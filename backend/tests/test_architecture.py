"""Cycles, health, and edge evidence.

The first test in the cycles section is a regression for a finding this code
invented on its own first run — see `CYCLE_CONFIDENCE`.
"""

from __future__ import annotations

from app.graph.schema import (
    CallConfidence,
    Edge,
    EdgeKind,
    KnowledgeGraph,
    Node,
    NodeKind,
    RepoSnapshot,
)
from app.graph.traversal import GraphView
from app.queries import run_query


def _file(path: str) -> Node:
    return Node(
        id=f"file:{path}",
        kind=NodeKind.FILE,
        name=path.rsplit("/", 1)[-1],
        qualified_name=path,
        file_path=path,
        loc=50,
    )


def _module(path: str) -> Node:
    return Node(
        id=f"module:{path}",
        kind=NodeKind.MODULE,
        name=path.rsplit("/", 1)[-1],
        qualified_name=path,
        file_path=path,
    )


def _view(nodes: list[Node], edges: list[Edge]) -> GraphView:
    return GraphView(
        KnowledgeGraph(
            snapshot=RepoSnapshot(
                repo_url="file:///t",
                commit_sha="x",
                primary_language="Python",
                languages={"py": len(nodes)},
                file_count=len(nodes),
                analyzed_at="2026-01-01T00:00:00+00:00",
            ),
            nodes=nodes,
            edges=edges,
        )
    )


def _imports(source: str, target: str, line: int = 1) -> Edge:
    return Edge(
        source_id=f"file:{source}",
        target_id=f"file:{target}",
        kind=EdgeKind.IMPORTS,
        file_path=source,
        line=line,
    )


# ── cycles ────────────────────────────────────────────────────────────────


def test_a_real_two_file_cycle_is_found_with_its_evidence() -> None:
    view = _view(
        [_file("a.py"), _file("b.py")],
        [_imports("a.py", "b.py", 3), _imports("b.py", "a.py", 7)],
    )
    result = run_query("cycles", view)

    assert result.meta["total"] == 1
    cycle = result.meta["cycles"][0]
    assert cycle["length"] == 2
    assert {f["name"] for f in cycle["files"]} == {"a.py", "b.py"}
    # Every hop carries the place the dependency is asserted.
    assert all(hop["file_path"] and hop["line"] for hop in cycle["evidence"])


def test_a_guessed_edge_cannot_invent_a_cycle() -> None:
    """The regression. On its first run this reported
    `python_emitter.py ⇄ js_emitter.py` for this repository, and neither file
    references the other: both define a `_FileWalker.visit`, so `walker.visit()`
    matched a definition in each and the dynamic tier emitted an edge to both.

    A cycle is a single discrete claim presented as an unambiguous defect. One
    guessed edge is enough to fabricate one whole, so cycles use resolved
    evidence only.
    """
    guessed = Edge(
        source_id="file:b.py",
        target_id="file:a.py",
        kind=EdgeKind.CALLS,
        confidence=CallConfidence.DYNAMIC_UNKNOWN,
        file_path="b.py",
        line=2,
    )
    view = _view([_file("a.py"), _file("b.py")], [_imports("a.py", "b.py"), guessed])

    assert run_query("cycles", view).meta["total"] == 0


def test_a_heuristic_edge_also_cannot_invent_a_cycle() -> None:
    guessed = Edge(
        source_id="file:b.py",
        target_id="file:a.py",
        kind=EdgeKind.CALLS,
        confidence=CallConfidence.HEURISTIC,
        file_path="b.py",
        line=2,
    )
    view = _view([_file("a.py"), _file("b.py")], [_imports("a.py", "b.py"), guessed])

    assert run_query("cycles", view).meta["total"] == 0


def test_a_straight_chain_is_not_a_cycle() -> None:
    view = _view(
        [_file("a.py"), _file("b.py"), _file("c.py")],
        [_imports("a.py", "b.py"), _imports("b.py", "c.py")],
    )
    assert run_query("cycles", view).meta["total"] == 0


def test_no_cycles_and_gave_up_looking_are_different_answers() -> None:
    """`truncated` exists so a caller can tell them apart. Reporting a clean
    bill of health because the search was capped would be the worst kind of
    wrong."""
    result = run_query("cycles", _view([_file("a.py")], []))
    assert result.meta["total"] == 0
    assert result.meta["truncated"] is False
    assert result.meta["max_length_searched"] > 0


# ── health ────────────────────────────────────────────────────────────────


def test_health_publishes_its_own_arithmetic() -> None:
    """The composite must always travel with the parts and the weights, or it
    is a number nobody can argue with."""
    view = _view(
        [_module("src"), _file("src/a.py"), _file("src/b.py")],
        [
            Edge(source_id="module:src", target_id="file:src/a.py", kind=EdgeKind.CONTAINS),
            Edge(source_id="module:src", target_id="file:src/b.py", kind=EdgeKind.CONTAINS),
            _imports("src/a.py", "src/b.py"),
        ],
    )
    meta = run_query("architecture_health", view).meta

    assert set(meta["scores"]) == {"circularity", "coupling", "cohesion", "complexity"}
    assert set(meta["weights"]) == set(meta["scores"])
    assert abs(sum(meta["weights"].values()) - 1.0) < 1e-9
    assert 0 <= meta["overall"] <= 100
    # One module, all of whose edges stay inside it, is perfectly cohesive.
    assert meta["scores"]["cohesion"] == 100


def test_an_unmeasurable_subscore_is_reported_not_defaulted() -> None:
    """A metric with no data is not a score of 100."""
    meta = run_query("architecture_health", _view([_file("a.py")], [])).meta
    assert "coupling" in meta["unavailable"]
    assert "cohesion" in meta["unavailable"]


# ── edge evidence ─────────────────────────────────────────────────────────


def test_edge_evidence_returns_the_place_the_relationship_is_asserted() -> None:
    view = _view([_file("a.py"), _file("b.py")], [_imports("a.py", "b.py", 42)])
    meta = run_query(
        "edge_evidence", view, source="file:a.py", target="file:b.py"
    ).meta

    assert meta["total"] == 1
    row = meta["relationships"][0]
    assert row["kind"] == "imports"
    assert row["file_path"] == "a.py"
    assert row["line"] == 42
    assert row["direction"] == "forward"


def test_unrelated_nodes_say_so_rather_than_inventing_a_reason() -> None:
    view = _view([_file("a.py"), _file("b.py")], [])
    meta = run_query(
        "edge_evidence", view, source="file:a.py", target="file:b.py"
    ).meta
    assert meta["total"] == 0
    assert "only through" in meta["explanation"]


# ── the two false-positive classes, pinned at the parser ──────────────────


def _build(tmp_path, files: dict[str, str]):
    from textwrap import dedent

    from app.parser import parse_repository

    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dedent(body).lstrip())
    return parse_repository(tmp_path)


def test_a_type_checking_import_is_marked_and_breaks_no_cycle(tmp_path) -> None:
    """Flask's config.py imports App under `if TYPE_CHECKING:` precisely so
    sansio/app.py can import Config at runtime. That guard IS the fix for a
    circular import; reporting it as one reports the remedy as the disease."""
    graph = _build(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/config.py": """
                from typing import TYPE_CHECKING

                if TYPE_CHECKING:
                    from pkg.app import App
            """,
            "pkg/app.py": "from pkg.config import Config\n",
        },
    )

    imports = [e for e in graph.edges if e.kind is EdgeKind.IMPORTS]
    type_only = [e for e in imports if e.type_only]
    assert len(type_only) == 1
    assert type_only[0].file_path == "pkg/config.py"
    # The edge still exists — it is a real source dependency…
    assert any(e.target_id == "file:pkg/app.py" for e in imports)
    # …but it must not manufacture a cycle.
    assert run_query("cycles", GraphView(graph)).meta["total"] == 0


def test_from_package_import_submodule_does_not_depend_on_the_package(
    tmp_path,
) -> None:
    """`from . import cli` expresses a dependency on `pkg.cli`. It also
    executes `pkg/__init__.py`, but that is module loading, not architecture —
    and recording it made every Python package with a re-exporting __init__
    look circular. Flask reported 20 cycles, almost all of this shape."""
    graph = _build(
        tmp_path,
        {
            "pkg/__init__.py": "from pkg.app import Flask\n",
            "pkg/app.py": "from pkg import cli\n",
            "pkg/cli.py": "def main():\n    return 1\n",
        },
    )

    edges = {
        (e.source_id, e.target_id)
        for e in graph.edges
        if e.kind is EdgeKind.IMPORTS
    }
    assert ("file:pkg/app.py", "file:pkg/cli.py") in edges
    assert ("file:pkg/app.py", "file:pkg/__init__.py") not in edges
    assert run_query("cycles", GraphView(graph)).meta["total"] == 0
