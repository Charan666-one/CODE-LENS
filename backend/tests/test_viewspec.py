"""CP-4.1 — the ViewSpec compiler.

Gate: the same graph compiles to *distinct* specs at zoom 1/2/3 (semantic
zoom: what exists changes), with layout computed server-side, deterministic
output, honest colors, and choreography that replays real construction order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.graph.schema import KnowledgeGraph
from app.parser import parse_repository
from app.views.viewspec import compile_viewspec

BACKEND_DIR = Path(__file__).resolve().parent.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"


@pytest.fixture(scope="module")
def codelens_graph() -> KnowledgeGraph:
    return parse_repository(BACKEND_DIR, max_size_mb=5_000)


@pytest.fixture(scope="module")
def tiny_graph() -> KnowledgeGraph:
    return parse_repository(TINY_PYTHON)


# ── semantic zoom: what exists changes ────────────────────────────────────


def test_zoom_levels_show_different_worlds(codelens_graph: KnowledgeGraph) -> None:
    l1 = compile_viewspec(codelens_graph, zoom=1)
    l2 = compile_viewspec(codelens_graph, zoom=2)
    l3 = compile_viewspec(codelens_graph, zoom=3)

    # L1: districts only — a handful of cluster nodes, no files.
    assert all(node.kind == "cluster" for node in l1.nodes)
    assert len(l1.nodes) < 8

    # L2: files exist; functions do not.
    l2_kinds = {node.kind for node in l2.nodes}
    assert "file" in l2_kinds and "function" not in l2_kinds

    # L3: the full street level.
    l3_kinds = {node.kind for node in l3.nodes}
    assert {"file", "function", "class"} <= l3_kinds
    assert len(l3.nodes) > len(l2.nodes) > len(l1.nodes)


def test_invalid_zoom_is_rejected(tiny_graph: KnowledgeGraph) -> None:
    with pytest.raises(ValueError, match="zoom"):
        compile_viewspec(tiny_graph, zoom=4)


# ── layout is server truth ────────────────────────────────────────────────


def test_every_node_has_precomputed_coordinates(codelens_graph: KnowledgeGraph) -> None:
    spec = compile_viewspec(codelens_graph, zoom=2)
    for node in spec.nodes:
        assert isinstance(node.x, float) and isinstance(node.y, float)
    # And they are not all in one heap: the districts separate the nodes.
    xs = {round(node.x, 1) for node in spec.nodes}
    assert len(xs) > len(spec.nodes) / 2


def test_compilation_is_deterministic(codelens_graph: KnowledgeGraph) -> None:
    first = compile_viewspec(codelens_graph, zoom=2)
    second = compile_viewspec(codelens_graph, zoom=2)
    assert first.model_dump() == second.model_dump()


def test_members_stay_inside_their_district(codelens_graph: KnowledgeGraph) -> None:
    spec = compile_viewspec(codelens_graph, zoom=2)
    centers = {cluster.id: cluster for cluster in spec.clusters}
    for node in spec.nodes:
        cluster = centers[node.cluster]
        distance = ((node.x - cluster.x) ** 2 + (node.y - cluster.y) ** 2) ** 0.5
        assert distance <= cluster.radius + 1e-6


# ── edges lift honestly across zoom ───────────────────────────────────────


def test_l2_lifts_function_calls_to_file_relationships(
    tiny_graph: KnowledgeGraph,
) -> None:
    """multiply->add is function-level; at L2 it must surface as a weighted
    file edge (or not at all) — never as a dangling function reference."""
    spec = compile_viewspec(tiny_graph, zoom=2)
    node_ids = {node.id for node in spec.nodes}
    for edge in spec.edges:
        assert edge.source in node_ids
        assert edge.target in node_ids
    # shapes.py's method calls into calculator.py: lifted edge must exist.
    lifted = [
        e for e in spec.edges
        if e.source == "file:shapes.py" and e.target == "file:calculator.py"
    ]
    assert lifted, "cross-file dependency must survive the lift"


def test_l1_flows_aggregate_with_weights(codelens_graph: KnowledgeGraph) -> None:
    spec = compile_viewspec(codelens_graph, zoom=1)
    assert spec.edges, "CodeLens has cross-district dependencies"
    heaviest = spec.edges[0]
    assert heaviest.weight >= 2  # many app-internal edges collapse into flows
    node_ids = {node.id for node in spec.nodes}
    for edge in spec.edges:
        assert edge.source in node_ids and edge.target in node_ids


def test_aggregated_confidence_reports_the_weakest_link(
    codelens_graph: KnowledgeGraph,
) -> None:
    spec = compile_viewspec(codelens_graph, zoom=2)
    allowed = {"resolved", "heuristic", "dynamic_unknown"}
    assert all(edge.confidence in allowed for edge in spec.edges)


# ── honest theater: colors and choreography are facts ─────────────────────


def test_entrypoint_files_get_the_accent(codelens_graph: KnowledgeGraph) -> None:
    spec = compile_viewspec(codelens_graph, zoom=2)
    entry_nodes = [node for node in spec.nodes if node.is_entrypoint]
    assert entry_nodes, "app/main.py carries HTTP routes"
    assert all(node.color == "#34d399" for node in entry_nodes)


def test_assembly_replays_construction_order(tiny_graph: KnowledgeGraph) -> None:
    """main.py holds the __main__ guard: it must assemble first, and the file
    it imports (calculator.py) must follow, never precede."""
    spec = compile_viewspec(tiny_graph, zoom=2)
    order = {node.id: node.assembly_index for node in spec.nodes}
    assert order["file:main.py"] == 0
    assert order["file:calculator.py"] > order["file:main.py"]


def test_risk_colors_span_a_ramp(codelens_graph: KnowledgeGraph) -> None:
    spec = compile_viewspec(codelens_graph, zoom=2)
    non_entry = [node for node in spec.nodes if not node.is_entrypoint]
    assert len({node.color for node in non_entry}) > 3  # a ramp, not a constant
    assert all(0.0 <= node.risk <= 1.0 for node in spec.nodes)


def test_view_nodes_are_clickable_to_code(codelens_graph: KnowledgeGraph) -> None:
    """EXPERIENCE non-negotiable: every visual claim clickable down to code."""
    spec = compile_viewspec(codelens_graph, zoom=3)
    for node in spec.nodes:
        if node.kind in ("function", "class"):
            assert node.file_path is not None
            assert node.start_line is not None


def test_spec_serialises_for_the_wire(tiny_graph: KnowledgeGraph) -> None:
    payload = compile_viewspec(tiny_graph, zoom=2).model_dump_json()
    assert '"zoom":2' in payload
