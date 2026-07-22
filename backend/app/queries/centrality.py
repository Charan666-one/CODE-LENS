"""Centrality — "what are the most important modules?" (FOUNDATION Q4).

Pure graph math: PageRank plus fan-in over IMPORTS + CALLS. PageRank because
importance flows — being needed by important things makes you important —
and fan-in because a raw dependent count is the number a human can check.
"""

from __future__ import annotations

import networkx as nx

from app.graph.schema import EdgeKind, NodeKind
from app.graph.traversal import GraphView
from app.queries.base import RankedNode, ResultGraph, register

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}


@register("centrality")
def centrality(view: GraphView, *, kind: str = "file", top: int = 20) -> ResultGraph:
    """Rank nodes of `kind` ("file", "function", "class") by importance."""
    node_kind = NodeKind(kind)
    dependency_view = view.subgraph(DEPENDENCY_KINDS)

    # Parallel edges collapse to one. NO reversal: a dependency edge A->B
    # already points the way PageRank wants — "A depends on B" confers
    # importance on B, exactly as a web link confers it on its target.
    simple = nx.DiGraph(dependency_view)
    scores = _pagerank(simple)

    ranked = [
        RankedNode(
            node_id=node.id,
            score=scores.get(node.id, 0.0),
            reasons={
                "pagerank": round(scores.get(node.id, 0.0), 6),
                "fan_in": view.fan_in(node.id, DEPENDENCY_KINDS),
            },
        )
        for node in view.nodes_by_id.values()
        if node.kind is node_kind
    ]
    ranked.sort(key=lambda r: (-r.score, r.node_id))
    ranked = ranked[:top]

    return ResultGraph(
        query="centrality",
        params={"kind": kind, "top": top},
        node_ids=[r.node_id for r in ranked],
        ranked=ranked,
        meta={"population": sum(1 for n in view.nodes_by_id.values() if n.kind is node_kind)},
    )


def _pagerank(
    graph: nx.DiGraph, *, alpha: float = 0.85, iterations: int = 100, tolerance: float = 1e-9
) -> dict[str, float]:
    """Plain power-iteration PageRank.

    Hand-rolled because networkx's implementation imports scipy, and pulling
    a 30 MB numerical stack for one function on graphs of a few thousand nodes
    is exactly the premature weight CP-0.1 removed. Deterministic: iteration
    order is the sorted node list.
    """
    nodes = sorted(graph.nodes)
    if not nodes:
        return {}
    n = len(nodes)
    rank = dict.fromkeys(nodes, 1.0 / n)

    for _ in range(iterations):
        # Mass from dangling nodes (no out-edges) is shared evenly, as in the
        # canonical formulation.
        dangling = sum(rank[u] for u in nodes if graph.out_degree(u) == 0)
        next_rank: dict[str, float] = {}
        for node in nodes:
            incoming = sum(
                rank[source] / graph.out_degree(source)
                for source, _ in graph.in_edges(node)
            )
            next_rank[node] = (1 - alpha) / n + alpha * (incoming + dangling / n)
        drift = sum(abs(next_rank[u] - rank[u]) for u in nodes)
        rank = next_rank
        if drift < tolerance:
            break
    return rank
