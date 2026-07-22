"""Structural questions: entrypoints (Q3) and the module inventory (Q2's
deterministic half — the summaries arrive with CP-3.2).
"""

from __future__ import annotations

from app.graph.schema import EdgeKind, NodeKind
from app.graph.traversal import GraphView
from app.queries.base import RankedNode, ResultGraph, register

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}


@register("entrypoints")
def entrypoints(view: GraphView) -> ResultGraph:
    """Where does execution start? — grouped by kind (server / CLI / main)."""
    found = [
        node
        for node in view.nodes_by_id.values()
        if node.is_entrypoint and node.entrypoint_kind is not None
    ]
    found.sort(key=lambda n: (n.entrypoint_kind.value, n.id))  # type: ignore[union-attr]

    by_kind: dict[str, list[str]] = {}
    for node in found:
        assert node.entrypoint_kind is not None
        by_kind.setdefault(node.entrypoint_kind.value, []).append(node.id)

    return ResultGraph(
        query="entrypoints",
        node_ids=[node.id for node in found],
        ranked=[
            RankedNode(
                node_id=node.id,
                score=1.0,
                reasons={"entrypoint_kind": node.entrypoint_kind.value},  # type: ignore[union-attr]
            )
            for node in found
        ],
        meta={"by_kind": by_kind},
    )


@register("modules")
def modules(view: GraphView) -> ResultGraph:
    """The main modules, sized and ordered by how much depends on them."""
    ranked: list[RankedNode] = []
    for node in view.nodes_by_id.values():
        if node.kind is not NodeKind.MODULE:
            continue
        file_ids = [
            child_id
            for child_id in view.children_of(node.id)
            if (child := view.node(child_id)) is not None and child.kind is NodeKind.FILE
        ]
        # A module's pull is the pull of its files from *outside* the module.
        internal = set(file_ids)
        external_fan_in = 0
        for file_id in file_ids:
            for source, _, attributes in view.g.in_edges(file_id, data=True):
                if attributes["kind"] in DEPENDENCY_KINDS and source not in internal:
                    external_fan_in += 1
        loc = sum(
            (file_node.loc or 0)
            for file_id in file_ids
            if (file_node := view.node(file_id)) is not None
        )
        ranked.append(
            RankedNode(
                node_id=node.id,
                score=float(external_fan_in),
                reasons={"files": len(file_ids), "loc": loc, "external_fan_in": external_fan_in},
            )
        )

    ranked.sort(key=lambda r: (-r.score, r.node_id))
    return ResultGraph(
        query="modules",
        node_ids=[r.node_id for r in ranked],
        ranked=ranked,
        meta={"module_count": len(ranked)},
    )
