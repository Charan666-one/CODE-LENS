"""Dependencies — "what does X depend on?" (FOUNDATION Q9).

Forward transitive closure over CALLS + IMPORTS, depth-limited, grouped by
distance so a reader sees the direct dependencies before the transitive tail.
"""

from __future__ import annotations

import networkx as nx

from app.graph.schema import EdgeKind
from app.graph.traversal import GraphView
from app.queries.base import QueryError, RankedNode, ResultGraph, register
from app.queries.blast_radius import _induced_edges

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}


@register("dependencies")
def dependencies(view: GraphView, *, node_id: str, max_depth: int = 5) -> ResultGraph:
    if not view.has_node(node_id):
        raise QueryError(f"unknown node {node_id!r}")

    dependency_view = view.subgraph(DEPENDENCY_KINDS)
    distances: dict[str, int] = nx.single_source_shortest_path_length(
        dependency_view, node_id, cutoff=max_depth
    )
    distances.pop(node_id, None)

    ranked = [
        RankedNode(node_id=node, score=1.0 / distance, reasons={"distance": distance})
        for node, distance in distances.items()
    ]
    ranked.sort(key=lambda r: (-r.score, r.node_id))

    reached = set(distances) | {node_id}
    return ResultGraph(
        query="dependencies",
        params={"node_id": node_id, "max_depth": max_depth},
        focus_id=node_id,
        node_ids=sorted(reached),
        edges=_induced_edges(view, reached),
        ranked=ranked,
        meta={"total_dependencies": len(distances)},
    )
