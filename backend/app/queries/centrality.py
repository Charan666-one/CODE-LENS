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
    # PageRank over a monorepo is ~30s of pure arithmetic (73k nodes, 100
    # iterations) and depends only on the graph — so compute it once per
    # snapshot and reuse. Without this, every centrality call paid it again.
    cached = view.memo.get("pagerank")
    if isinstance(cached, dict):
        scores = cached
    else:
        scores = _pagerank(nx.DiGraph(dependency_view))
        view.memo["pagerank"] = scores

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
    graph: nx.DiGraph, *, alpha: float = 0.85, iterations: int = 100, tolerance: float = 1e-8
) -> dict[str, float]:
    """Plain power-iteration PageRank.

    Hand-rolled because networkx's implementation imports scipy, and pulling
    a 30 MB numerical stack for one function on graphs of a few thousand nodes
    is exactly the premature weight CP-0.1 removed. Deterministic: iteration
    order is the sorted node list.

    The drift tolerance is a total, not per-node, so it tightens naturally as
    graphs grow; 1e-8 converges well inside 100 iterations while leaving the
    ranking (which is all a reader sees) identical.
    """
    nodes = sorted(graph.nodes)
    if not nodes:
        return {}
    n = len(nodes)
    rank = dict.fromkeys(nodes, 1.0 / n)

    # Push mass along edges instead of pulling it per node. The pull form asks
    # every node for its in-edges on every iteration, which on a monorepo
    # (73k nodes, 209k edges) is ~30 seconds; walking the edge list once per
    # iteration is the same arithmetic in O(iterations x E) and finishes in
    # about a second. Precompute out-degrees and the dangling set once.
    out_degree = {node: graph.out_degree(node) for node in nodes}
    edges = [(u, v) for u, v in graph.edges()]
    dangling_nodes = [node for node in nodes if out_degree[node] == 0]
    base = (1 - alpha) / n

    for _ in range(iterations):
        dangling = sum(rank[u] for u in dangling_nodes)
        shared = alpha * dangling / n + base
        next_rank = dict.fromkeys(nodes, shared)
        for source, target in edges:
            next_rank[target] += alpha * rank[source] / out_degree[source]
        drift = sum(abs(next_rank[u] - rank[u]) for u in nodes)
        rank = next_rank
        if drift < tolerance:
            break
    return rank
