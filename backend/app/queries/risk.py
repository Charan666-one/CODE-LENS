"""Risk — "which code is riskiest to touch?" (FOUNDATION Q10-lite).

The formula is FOUNDATION's, verbatim: risk = normalize(complexity × fan_in ×
churn) per file. Complexity says a change is easy to get wrong, fan-in says a
mistake spreads, churn says changes actually happen. Multiplied, not summed:
a file scores high only when all three point the same way.

Facts only — every factor is deterministic and shown. When churn is absent
(no git history) the factor is neutral (1), and `churn_known: false` says so
rather than pretending a measurement (Constitution 5).
"""

from __future__ import annotations

from app.graph.schema import EdgeKind, NodeKind
from app.graph.traversal import GraphView
from app.queries.base import RankedNode, ResultGraph, register

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}


@register("risk")
def risk(view: GraphView, *, top: int = 20) -> ResultGraph:
    raw: list[tuple[str, float, dict]] = []

    for node in view.nodes_by_id.values():
        if node.kind is not NodeKind.FILE:
            continue

        complexity = _contained_complexity(view, node.id)
        fan_in = view.fan_in(node.id, DEPENDENCY_KINDS)
        churn_known = node.churn_count is not None
        churn = node.churn_count if churn_known else 1

        score = float(max(complexity, 1) * max(fan_in, 1) * max(churn or 1, 1))
        raw.append(
            (
                node.id,
                score,
                {
                    "complexity": complexity,
                    "fan_in": fan_in,
                    "churn": churn if churn_known else None,
                    "churn_known": churn_known,
                },
            )
        )

    ceiling = max((score for _, score, _ in raw), default=1.0)
    ranked = [
        RankedNode(node_id=node_id, score=score / ceiling, reasons=reasons)
        for node_id, score, reasons in raw
    ]
    ranked.sort(key=lambda r: (-r.score, r.node_id))
    ranked = ranked[:top]

    return ResultGraph(
        query="risk",
        params={"top": top},
        node_ids=[r.node_id for r in ranked],
        ranked=ranked,
        meta={"files_scored": len(raw)},
    )


def _contained_complexity(view: GraphView, file_id: str) -> int:
    """Sum of cyclomatic complexity over the functions a file CONTAINS."""
    total = 0
    stack = [file_id]
    while stack:
        for child_id in view.children_of(stack.pop()):
            child = view.node(child_id)
            if child is None:
                continue
            if child.kind is NodeKind.FUNCTION and child.complexity:
                total += child.complexity
            stack.append(child_id)
    return total
