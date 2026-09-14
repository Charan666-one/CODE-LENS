"""The explain query — "what is this, and why does the project need it?"

Every field must be a checkable graph fact: no invented importance, no
number a reader can't verify against the map.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.graph.schema import KnowledgeGraph
from app.graph.traversal import GraphView
from app.parser import parse_repository
from app.queries import QueryError, run_query

BACKEND_DIR = Path(__file__).resolve().parent.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"


@pytest.fixture(scope="module")
def tiny() -> GraphView:
    return GraphView(parse_repository(TINY_PYTHON))


@pytest.fixture(scope="module")
def codelens() -> KnowledgeGraph:
    return parse_repository(BACKEND_DIR, max_size_mb=5_000)


def test_unknown_node_is_rejected(tiny: GraphView) -> None:
    with pytest.raises(QueryError, match="unknown node"):
        run_query("explain", tiny, node_id="file:nope.py")


def test_explains_a_file_with_identity_and_role(tiny: GraphView) -> None:
    meta = run_query("explain", tiny, node_id="file:calculator.py").meta
    identity, role = meta["identity"], meta["role"]

    assert identity["kind"] == "file"
    assert identity["name"] == "calculator.py"
    assert identity["language"] == "Python"
    # shapes.py and main.py both import calculator.py — a checkable fact.
    assert role["direct_dependents"] == 2
    assert role["direct_dependencies"] == 0
    assert {d["name"] for d in meta["used_by"]} == {"shapes.py", "main.py"}


def test_contains_reports_what_lives_inside(tiny: GraphView) -> None:
    meta = run_query("explain", tiny, node_id="file:shapes.py").meta
    assert meta["contains"]["counts"]["class"] == 2  # Shape, Rectangle
    assert "Rectangle" in {child["name"] for child in meta["contains"]["top"]}


def test_transitive_reach_includes_the_node_itself(codelens: KnowledgeGraph) -> None:
    """Regression: a file's dependents hang off the file, not off its
    classes, so skipping the node's own closure reported 0 transitive
    dependents for a file with 35 direct ones."""
    view = GraphView(codelens)
    role = run_query("explain", view, node_id="file:app/graph/schema.py").meta["role"]
    assert role["direct_dependents"] > 10
    assert role["transitive_dependents"] >= role["direct_dependents"]


def test_verdict_reflects_transitive_reach(codelens: KnowledgeGraph) -> None:
    """A folder can have few direct importers but a large downstream reach;
    calling that "supporting" would mislead."""
    view = GraphView(codelens)
    meta = run_query("explain", view, node_id="module:app/parser").meta
    assert meta["role"]["transitive_dependents"] > 25
    assert meta["role"]["verdict"].startswith("Hub")


def test_entrypoint_verdict_wins(codelens: KnowledgeGraph) -> None:
    view = GraphView(codelens)
    entry = run_query("entrypoints", view)
    assert entry.node_ids, "CodeLens exposes HTTP routes"
    meta = run_query("explain", view, node_id=entry.node_ids[0]).meta
    assert meta["role"]["is_entrypoint"] is True
    assert "Entry point" in meta["role"]["verdict"]


def test_folder_dependencies_exclude_internal_wiring(codelens: KnowledgeGraph) -> None:
    """A folder's architecture is what crosses its boundary; edges between
    its own files are implementation detail."""
    view = GraphView(codelens)
    meta = run_query("explain", view, node_id="module:app/parser").meta
    for neighbour in meta["depends_on"] + meta["used_by"]:
        assert not neighbour["id"].startswith("module:app/parser")


def test_evidence_paths_are_real_walkable_chains(tiny: GraphView) -> None:
    """Constitution 1: every claim carries its receipt."""
    result = run_query("explain", tiny, node_id="file:calculator.py")
    assert result.paths, "dependents must come with a path back to the node"
    for dependent, path in result.paths.items():
        assert path[0] == dependent
        assert path[-1] == "file:calculator.py"


def test_result_serialises_for_the_wire(tiny: GraphView) -> None:
    payload = run_query("explain", tiny, node_id="file:calculator.py").model_dump_json()
    assert '"verdict"' in payload
