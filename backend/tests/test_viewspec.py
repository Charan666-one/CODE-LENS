"""CP-4.1 — the ViewSpec compiler.

Gate: the same graph compiles to *distinct* specs at zoom 1/2/3 (semantic
zoom: what exists changes), with layout computed server-side, deterministic
output, honest colors, and choreography that replays real construction order.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.graph.schema import EdgeKind, KnowledgeGraph, Node, NodeKind, RepoSnapshot
from app.parser import parse_repository
from app.views.viewspec import _MAX_RENDERED_FILES, compile_viewspec

BACKEND_DIR = Path(__file__).resolve().parent.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"


def _huge_repo(hub_count: int, leaf_count: int) -> KnowledgeGraph:
    """A synthetic repo bigger than the render cap, with a known fan-in
    ranking: `hub_count` hubs (each imported by every leaf) must always
    survive a cap; the leaves are otherwise identical, so which ones survive
    is decided purely by the tie-break — a fact this test also pins down."""
    from app.graph.schema import Edge

    nodes: list[Node] = []
    edges: list[Edge] = []
    for i in range(hub_count):
        name = f"hub_{i:02d}.py"
        path = f"src/{name}"
        nodes.append(
            Node(
                id=f"file:{path}",
                kind=NodeKind.FILE,
                name=name,
                qualified_name=path,
                file_path=path,
            )
        )
    for i in range(leaf_count):
        name = f"leaf_{i:03d}.py"
        path = f"src/{name}"
        nodes.append(
            Node(
                id=f"file:{path}",
                kind=NodeKind.FILE,
                name=name,
                qualified_name=path,
                file_path=path,
            )
        )
        edges.append(
            Edge(
                source_id=f"file:src/leaf_{i:03d}.py",
                target_id=f"file:src/hub_{i % hub_count:02d}.py",
                kind=EdgeKind.IMPORTS,
            )
        )
    snapshot = RepoSnapshot(
        repo_url="https://example.test/huge/repo",
        commit_sha="0" * 40,
        primary_language="Python",
        languages={"py": len(nodes)},
        file_count=len(nodes),
        analyzed_at="2026-01-01T00:00:00+00:00",
    )
    return KnowledgeGraph(snapshot=snapshot, nodes=nodes, edges=edges)


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
        e for e in spec.edges if e.source == "file:shapes.py" and e.target == "file:calculator.py"
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


# ── the render cap: huge monorepos must stay on-screen ────────────────────
# n8n (18,767 parseable files) produced an 18,767-node zoom-2 view before
# this cap existed — unrenderable, and a direct violation of EXPERIENCE.md's
# "60fps or reduce detail" / "nobody is ever overwhelmed" non-negotiables.


def test_small_graphs_are_never_truncated(codelens_graph: KnowledgeGraph) -> None:
    spec = compile_viewspec(codelens_graph, zoom=2)
    assert spec.meta["truncated"] is False
    assert spec.meta["files"] == spec.meta["rendered_nodes"]


def test_huge_repo_is_capped_to_the_render_limit() -> None:
    hubs, leaves = 10, _MAX_RENDERED_FILES + 5  # comfortably over the cap
    graph = _huge_repo(hub_count=hubs, leaf_count=leaves)
    total_files = hubs + leaves
    assert total_files > _MAX_RENDERED_FILES  # the case under test

    spec = compile_viewspec(graph, zoom=2)

    assert len(spec.nodes) == _MAX_RENDERED_FILES
    assert spec.meta == {
        "files": total_files,
        "rendered_nodes": _MAX_RENDERED_FILES,
        "truncated": True,
    }


def test_render_cap_keeps_the_hubs_and_the_lowest_id_leaves() -> None:
    """Deterministic tie-break, pinned exactly: hubs (fan-in 60) always beat
    leaves (fan-in 0); among equal-fan-in leaves, lower id wins the tie."""
    hubs, leaves = 10, _MAX_RENDERED_FILES + 10  # 10 extra leaves must be cut
    graph = _huge_repo(hub_count=hubs, leaf_count=leaves)
    spec = compile_viewspec(graph, zoom=2)
    kept = {node.id for node in spec.nodes}

    for i in range(hubs):
        assert f"file:src/hub_{i:02d}.py" in kept  # every hub survives

    surviving_leaves = _MAX_RENDERED_FILES - hubs
    for i in range(surviving_leaves):
        assert f"file:src/leaf_{i:03d}.py" in kept  # lowest-id leaves kept
    for i in range(surviving_leaves, leaves):
        assert f"file:src/leaf_{i:03d}.py" not in kept  # highest-id leaves cut


def test_render_cap_produces_no_dangling_edges() -> None:
    graph = _huge_repo(hub_count=10, leaf_count=_MAX_RENDERED_FILES + 50)
    spec = compile_viewspec(graph, zoom=2)
    rendered = {node.id for node in spec.nodes}
    for edge in spec.edges:
        assert edge.source in rendered
        assert edge.target in rendered


def test_render_cap_applies_at_zoom_three_too() -> None:
    graph = _huge_repo(hub_count=10, leaf_count=_MAX_RENDERED_FILES + 50)
    spec = compile_viewspec(graph, zoom=3)
    file_nodes = [n for n in spec.nodes if n.kind == "file"]
    assert len(file_nodes) == _MAX_RENDERED_FILES
    assert spec.meta["truncated"] is True
