"""Architecture queries — cycles, health, and the evidence behind an edge.

All three exist to be rendered *on the canvas*: a cycle isolates itself, an
unhealthy module tints, an edge explains itself where it is drawn. None of
them is a table, and the shapes returned here are chosen so the frontend can
highlight a subgraph without a second request.
"""

from __future__ import annotations

from typing import Any

from app.analysis.cycles import MAX_CYCLE_LENGTH, find_cycles
from app.analysis.health import measure_health
from app.graph.traversal import GraphView
from app.queries.base import QueryError, RankedNode, ResultGraph, register


@register("cycles")
def cycles(view: GraphView, *, limit: int = 20) -> ResultGraph:
    """Circular dependencies between files, shortest first.

    The `truncated` flag is load-bearing: "no cycles found" and "we stopped
    looking" are different claims, and only one of them is good news.
    """
    found, truncated = find_cycles(view)
    found = found[:limit]

    findings: list[dict[str, Any]] = []
    involved: set[str] = set()
    for cycle in found:
        involved.update(cycle.files)
        findings.append(
            {
                "length": cycle.length,
                "files": [
                    {
                        "id": file_id,
                        "name": (node.name if (node := view.node(file_id)) else file_id),
                        "file_path": (node.file_path if (node := view.node(file_id)) else None),
                    }
                    for file_id in cycle.files
                ],
                "evidence": [
                    {"from": source, "to": target, "file_path": path, "line": line}
                    for source, target, path, line in cycle.evidence
                ],
            }
        )

    return ResultGraph(
        query="cycles",
        params={"limit": limit},
        node_ids=sorted(involved),
        ranked=[
            RankedNode(
                node_id=cycle.files[0],
                score=float(100 - cycle.length),
                reasons={"length": cycle.length, "files": cycle.files},
            )
            for cycle in found
        ],
        meta={
            "cycles": findings,
            "total": len(found),
            "truncated": truncated,
            "max_length_searched": MAX_CYCLE_LENGTH,
            "explanation": (
                "Each of these is a loop: every file in it depends on the next, "
                "and the last depends on the first. None can be understood, "
                "tested, or replaced alone."
            ),
        },
    )


@register("architecture_health")
def architecture_health(view: GraphView, *, top: int = 10) -> ResultGraph:
    """Four measured sub-scores, the weights between them, and the composite.

    The sub-scores and weights always ship together. A score whose derivation
    cannot be shown is a number this product has no right to display.
    """
    report = measure_health(view)

    # Worst first — the point is to look at the module dragging the score,
    # not to admire a ranking.
    worst = sorted(
        report.modules,
        key=lambda module: (not module.rigid, module.cohesion, -module.crossing_edges),
    )[:top]

    return ResultGraph(
        query="architecture_health",
        params={"top": top},
        node_ids=[module.module_id for module in worst],
        ranked=[
            RankedNode(
                node_id=module.module_id,
                score=round(1 - module.cohesion, 4),
                reasons={
                    "afferent": module.afferent,
                    "efferent": module.efferent,
                    "instability": round(module.instability, 3),
                    "cohesion": round(module.cohesion, 3),
                    "rigid": module.rigid,
                },
            )
            for module in worst
        ],
        meta={
            "overall": report.overall,
            "scores": report.scores,
            "weights": report.weights,
            "unavailable": report.unavailable,
            "cycle_count": report.cycle_count,
            "cycles_truncated": report.cycles_truncated,
            "modules": [
                {
                    "id": module.module_id,
                    "name": module.name,
                    "files": module.files,
                    "afferent": module.afferent,
                    "efferent": module.efferent,
                    "instability": round(module.instability, 3),
                    "cohesion": round(module.cohesion, 3),
                    "rigid": module.rigid,
                }
                for module in worst
            ],
            "explanation": (
                "Every sub-score is a standard metric computed from the edges. "
                "The composite is their weighted mean over the ones that could "
                "be measured; the weights are published so it can be argued with."
            ),
        },
    )


@register("edge_evidence")
def edge_evidence(view: GraphView, *, source: str, target: str) -> ResultGraph:
    """Why are these two connected? Every edge between them, with file:line.

    Nothing is inferred. `file_path` and `line` are recorded at emit time by
    the parser, so this is exposure rather than analysis — which is exactly
    what makes it usable as evidence against a narrated claim.
    """
    if not view.has_node(source):
        raise QueryError(f"unknown node {source!r}")
    if not view.has_node(target):
        raise QueryError(f"unknown node {target!r}")

    relationships: list[dict[str, Any]] = []
    for a, b, direction in ((source, target, "forward"), (target, source, "reverse")):
        for edge in view.edges_between(a, b):
            relationships.append(
                {
                    "from": a,
                    "to": b,
                    "direction": direction,
                    "kind": edge.kind.value,
                    "confidence": edge.confidence.value,
                    "file_path": edge.file_path,
                    "line": edge.line,
                    "weight": edge.weight,
                }
            )

    source_node = view.node(source)
    target_node = view.node(target)
    return ResultGraph(
        query="edge_evidence",
        params={"source": source, "target": target},
        focus_id=source,
        node_ids=[source, target],
        meta={
            "relationships": relationships,
            "total": len(relationships),
            "source": {
                "id": source,
                "name": source_node.name if source_node else source,
                "file_path": source_node.file_path if source_node else None,
            },
            "target": {
                "id": target,
                "name": target_node.name if target_node else target,
                "file_path": target_node.file_path if target_node else None,
            },
            "explanation": (
                "Each row is a relationship the parser recorded, with the place "
                "in the source where it is asserted."
                if relationships
                else "No direct relationship — these are connected only through "
                "other files, if at all."
            ),
        },
    )
