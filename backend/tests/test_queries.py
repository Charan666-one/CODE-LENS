"""Stage 2 — the deterministic queries.

The blast-radius gate is the important one: the answer must match dependents
derived *by hand* from the fixture source, paths included. The other plans are
checked for correctness of shape and for sane answers on CodeLens itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.graph.schema import CallConfidence
from app.graph.traversal import GraphView
from app.parser import parse_repository
from app.queries import QueryError, registered_queries, run_query

BACKEND_DIR = Path(__file__).resolve().parent.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"
DYNAMIC_PYTHON = BACKEND_DIR / "fixtures" / "dynamic_python" / "repo"


@pytest.fixture(scope="module")
def tiny() -> GraphView:
    return GraphView(parse_repository(TINY_PYTHON))


@pytest.fixture(scope="module")
def codelens() -> GraphView:
    return GraphView(parse_repository(BACKEND_DIR, max_size_mb=5_000))


# ── CP-2.1: the registry ──────────────────────────────────────────────────


def test_all_launch_plans_are_registered() -> None:
    assert set(registered_queries()) >= {
        "blast_radius",
        "centrality",
        "dependencies",
        "risk",
        "entrypoints",
        "modules",
    }


def test_unknown_plan_raises_query_error(tiny: GraphView) -> None:
    with pytest.raises(QueryError, match="no query plan"):
        run_query("does_not_exist", tiny)


def test_unknown_node_raises_query_error(tiny: GraphView) -> None:
    with pytest.raises(QueryError, match="unknown node"):
        run_query("blast_radius", tiny, node_id="function:nope.nope")


# ── CP-2.2: blast radius, hand-verified ───────────────────────────────────


def test_blast_radius_matches_hand_derived_dependents(tiny: GraphView) -> None:
    """The gate. Derived by reading the fixture, not by running the code:

    calculator.add is called by calculator.multiply (calculator.py:13).
    multiply is called by shapes.Rectangle.area (shapes.py:23) and main.area
    (main.py:8). main.area is called by main.main (main.py:13), which the
    module scope of main.py calls under the __main__ guard (main.py:17).
    Files shapes.py and main.py IMPORT calculator.py, whose file node reaches
    add only via... it doesn't: file->file IMPORTS lands on the file node,
    not the function. So the dependents of `add` are exactly:
    """
    expected = {
        "function:calculator.multiply",
        "function:shapes.Rectangle.area",
        "function:main.area",
        "function:main.main",
        "file:main.py",
    }
    result = run_query("blast_radius", tiny, node_id="function:calculator.add")
    assert {r.node_id for r in result.ranked} == expected
    assert result.meta["total_affected"] == len(expected)


def test_blast_radius_paths_are_real_dependency_chains(tiny: GraphView) -> None:
    """Every claim carries its receipt: the path from dependent to target."""
    result = run_query("blast_radius", tiny, node_id="function:calculator.add")

    # The transitive chain, exactly as read from the source.
    assert result.paths["function:main.main"] == [
        "function:main.main",
        "function:main.area",
        "function:calculator.multiply",
        "function:calculator.add",
    ]
    # And every step of every path is a real edge in the evidence subgraph.
    edge_pairs = {(e.source_id, e.target_id) for e in result.edges}
    for path in result.paths.values():
        for source, target in zip(path, path[1:], strict=False):
            assert (source, target) in edge_pairs


def test_blast_radius_ranks_near_before_far(tiny: GraphView) -> None:
    result = run_query("blast_radius", tiny, node_id="function:calculator.add")
    by_id = {r.node_id: r for r in result.ranked}
    assert by_id["function:calculator.multiply"].reasons["distance"] == 1
    assert by_id["function:main.main"].reasons["distance"] == 3
    assert result.ranked[0].node_id == "function:calculator.multiply"


def test_blast_radius_respects_max_depth(tiny: GraphView) -> None:
    result = run_query(
        "blast_radius", tiny, node_id="function:calculator.add", max_depth=1
    )
    assert {r.node_id for r in result.ranked} == {"function:calculator.multiply"}


def test_blast_radius_reports_weakest_link_confidence() -> None:
    """A radius that runs through a guessed edge must say so."""
    view = GraphView(parse_repository(DYNAMIC_PYTHON))
    result = run_query(
        "blast_radius", view, node_id="function:handlers.EmailHandler.handle"
    )
    dispatch = next(
        r for r in result.ranked if r.node_id == "function:dispatch.dispatch"
    )
    assert dispatch.reasons["path_confidence"] == CallConfidence.DYNAMIC_UNKNOWN.value


def test_leaf_node_has_empty_radius(tiny: GraphView) -> None:
    """file:main.py is depended on by nothing — the answer is honestly empty."""
    result = run_query("blast_radius", tiny, node_id="file:main.py")
    assert result.ranked == []
    assert result.meta["total_affected"] == 0


# ── CP-2.3: centrality, dependencies, risk ────────────────────────────────


def test_centrality_finds_the_load_bearing_files(codelens: GraphView) -> None:
    """The 'knowledgeable judge' gate, encoded: schema.py is imported by
    store, traversal, parser, export... — it must rank in the top five."""
    result = run_query("centrality", codelens, kind="file", top=5)
    assert "file:app/graph/schema.py" in {r.node_id for r in result.ranked}


def test_centrality_on_fixture_is_exactly_right(tiny: GraphView) -> None:
    """calculator.py is imported by both other files; it must rank first."""
    result = run_query("centrality", tiny, kind="file", top=3)
    assert result.ranked[0].node_id == "file:calculator.py"


def test_dependencies_forward_closure(tiny: GraphView) -> None:
    result = run_query("dependencies", tiny, node_id="function:main.main")
    assert {r.node_id for r in result.ranked} == {
        "function:main.area",
        "function:calculator.multiply",
        "function:calculator.add",
    }
    by_id = {r.node_id: r for r in result.ranked}
    assert by_id["function:main.area"].reasons["distance"] == 1
    assert by_id["function:calculator.add"].reasons["distance"] == 3


def test_risk_scores_are_normalised_and_explained(codelens: GraphView) -> None:
    result = run_query("risk", codelens, top=10)
    assert result.ranked, "CodeLens must have scoreable files"
    assert result.ranked[0].score == 1.0  # the ceiling defines the scale
    for entry in result.ranked:
        assert 0.0 <= entry.score <= 1.0
        assert {"complexity", "fan_in", "churn", "churn_known"} <= entry.reasons.keys()


def test_risk_flags_missing_churn_instead_of_inventing_it(tiny: GraphView) -> None:
    """The fixture has no git history; churn must be None, not a number."""
    result = run_query("risk", tiny, top=5)
    for entry in result.ranked:
        assert entry.reasons["churn_known"] is False
        assert entry.reasons["churn"] is None


# ── CP-2.4: entrypoints and modules ───────────────────────────────────────


def test_entrypoints_grouped_by_kind(codelens: GraphView) -> None:
    result = run_query("entrypoints", codelens)
    by_kind = result.meta["by_kind"]
    assert "function:app.main.create_app.health" in by_kind.get("http_route", [])
    assert "function:fixtures.tiny_python.repo.main.main" in by_kind.get("main", [])


def test_modules_ranked_by_external_pull(codelens: GraphView) -> None:
    result = run_query("modules", codelens)
    by_id = {r.node_id: r for r in result.ranked}
    graph_module = by_id["module:app/graph"]
    # app/graph is imported from ingestion, parser, queries, core — far more
    # external pull than the test fixtures, which nothing imports.
    fixture_module = by_id.get("module:fixtures/tiny_python/repo")
    assert graph_module.reasons["external_fan_in"] > 0
    if fixture_module is not None:
        assert (
            graph_module.reasons["external_fan_in"]
            > fixture_module.reasons["external_fan_in"]
        )
    assert graph_module.reasons["files"] >= 4  # schema, store, traversal, export


def test_result_graphs_serialise(tiny: GraphView) -> None:
    """ResultGraph is the contract views and narration read — it must dump."""
    result = run_query("blast_radius", tiny, node_id="function:calculator.add")
    payload = result.model_dump_json()
    assert "function:calculator.multiply" in payload
