"""BlastRadius — "what breaks if I change X?" (FOUNDATION Q8, the paid wedge).

Reverse transitive closure over CALLS + IMPORTS from the target, ranked, with
the actual dependency paths shown — STRATEGY.md §Layer 1's promise, verbatim.
The paths are the product: a claim without its path is an opinion.

Ranking: nearer dependents first (a direct caller feels a change before a
transitive one), then higher fan-in (a hub breaking spreads further), then id
for determinism. Every path also reports the *weakest* edge confidence along
it — a blast radius that runs through a dynamic_unknown edge says so.
"""

from __future__ import annotations

import networkx as nx

from app.graph.schema import CallConfidence, Edge, EdgeKind
from app.graph.traversal import GraphView
from app.queries.base import QueryError, RankedNode, ResultGraph, register

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}

_WEAKEST_FIRST = [
    CallConfidence.DYNAMIC_UNKNOWN,
    CallConfidence.HEURISTIC,
    CallConfidence.RESOLVED,
]


@register("blast_radius")
def blast_radius(view: GraphView, *, node_id: str, max_depth: int | None = None) -> ResultGraph:
    if not view.has_node(node_id):
        raise QueryError(f"unknown node {node_id!r}")

    dependency_view = view.subgraph(DEPENDENCY_KINDS)
    # Walk *against* the arrows: who reaches the target?
    reverse = dependency_view.reverse(copy=True)

    distances: dict[str, int] = nx.single_source_shortest_path_length(
        reverse, node_id, cutoff=max_depth
    )
    distances.pop(node_id, None)
    if max_depth is not None:
        distances = {n: d for n, d in distances.items() if d <= max_depth}

    shortest_paths = nx.single_source_shortest_path(reverse, node_id, cutoff=max_depth)

    ranked: list[RankedNode] = []
    paths: dict[str, list[str]] = {}
    for dependent, distance in distances.items():
        fan_in = view.fan_in(dependent, DEPENDENCY_KINDS)
        # path was walked target->dependent in the reversed graph; flip it so
        # it reads the way the dependency actually flows.
        path = list(reversed(shortest_paths[dependent]))
        paths[dependent] = path
        node = view.node(dependent)
        ranked.append(
            RankedNode(
                node_id=dependent,
                # Score is for ordering only; the reasons are the real answer.
                score=1.0 / distance + fan_in / 1000.0,
                reasons={
                    "distance": distance,
                    "fan_in": fan_in,
                    "path_confidence": _weakest_confidence(view, path).value,
                    # Which file this lands in. A caller counting "how many
                    # files does this change touch" cannot derive it from the
                    # id: a function's id carries a dotted qualified name, not
                    # a path, so without this the UI either counted classes
                    # and functions as files or had to re-fetch every node.
                    "name": node.name if node else dependent,
                    "kind": node.kind.value if node else None,
                    "file_path": node.file_path if node else None,
                },
            )
        )

    ranked.sort(key=lambda r: (-r.score, r.node_id))

    affected = set(distances)
    affected.add(node_id)
    focus = view.node(node_id)
    return ResultGraph(
        query="blast_radius",
        params={"node_id": node_id, "max_depth": max_depth},
        focus_id=node_id,
        node_ids=sorted(affected),
        edges=_induced_edges(view, affected),
        ranked=ranked,
        paths=paths,
        meta={
            "total_affected": len(distances),
            # Where the changed thing lives. A renderer showing files cannot
            # draw a wave whose source is a function unless it can find the
            # file that holds it — asking about a symbol while looking at the
            # file level is the common case, not the exotic one.
            "focus": {
                "id": node_id,
                "name": focus.name if focus else node_id,
                "file_path": focus.file_path if focus else None,
            },
        },
    )


def _weakest_confidence(view: GraphView, path: list[str]) -> CallConfidence:
    """The chain is as trustworthy as its least trustworthy link."""
    weakest = CallConfidence.RESOLVED
    for source, target in zip(path, path[1:], strict=False):
        step_best: CallConfidence | None = None
        for edge in view.edges_between(source, target):
            if edge.kind not in DEPENDENCY_KINDS:
                continue
            # Parallel edges: the strongest one carries the step.
            if step_best is None or _WEAKEST_FIRST.index(edge.confidence) > (
                _WEAKEST_FIRST.index(step_best)
            ):
                step_best = edge.confidence
        if step_best is not None and _WEAKEST_FIRST.index(step_best) < _WEAKEST_FIRST.index(
            weakest
        ):
            weakest = step_best
    return weakest


def _induced_edges(view: GraphView, nodes: set[str]) -> list[Edge]:
    """Dependency edges whose both ends are in the affected set — the evidence."""
    found: list[Edge] = []
    for source, target, attributes in view.g.edges(data=True):
        if attributes["kind"] not in DEPENDENCY_KINDS:
            continue
        if source in nodes and target in nodes:
            found.append(
                Edge(
                    source_id=source,
                    target_id=target,
                    kind=attributes["kind"],
                    confidence=attributes["confidence"],
                    file_path=attributes.get("file_path"),
                    line=attributes.get("line"),
                )
            )
    return found
