"""BlastRadius — "what breaks if I change X?" (FOUNDATION Q8, the paid wedge).

Reverse transitive closure over CALLS + IMPORTS from the target, ranked, with
the actual dependency paths shown — STRATEGY.md §Layer 1's promise, verbatim.
The paths are the product: a claim without its path is an opinion.

Ranking lives in `queries/ranking.py` and is scored per *file* — the unit the
answer is read in — with the file's score then carried by each of its symbols,
so this list stays symbol-level (the renderer needs that to light nodes) while
the order is one a reader can defend. Symbols in the file being changed come
first and unconditionally: they are a certainty rather than a prediction.

Every path also reports the *weakest* edge confidence along it — a blast
radius that runs through a dynamic_unknown edge says so.
"""

from __future__ import annotations

import networkx as nx

from app.graph.schema import CallConfidence, Edge, EdgeKind
from app.graph.traversal import GraphView
from app.queries.base import QueryError, RankedNode, ResultGraph, register
from app.queries.ranking import (
    GraphHistory,
    Scored,
    candidates_from_ranked,
    same_file_score,
    score_candidates,
)

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

    # The ranking is decided in queries/ranking.py, over four normalised
    # signals. It is scored per *file* — that is the unit the answer is read
    # in — and the file's score is then carried by each of its symbols, so the
    # returned list stays symbol-level (the UI needs that to light nodes)
    # while the order is the one a reader would defend.
    # Fan-in once per node: it is wanted twice — as a reason on the node and
    # as the structural signal on its file — and on a wide blast radius that
    # is thousands of duplicated neighbour walks.
    fan_ins = {n: view.fan_in(n, DEPENDENCY_KINDS) for n in distances}

    focus_node = view.node(node_id)
    seed_file = focus_node.file_path if focus_node else None
    file_scores: dict[str, Scored] = {}
    if seed_file:
        provisional = [
            RankedNode(
                node_id=n, score=0.0, reasons={"distance": d, "fan_in": fan_ins[n]}
            )
            for n, d in distances.items()
        ]
        candidates = candidates_from_ranked(view, provisional, seed_file=seed_file)
        file_scores = {
            entry.file_path: entry
            for entry in score_candidates(
                candidates, seed_file=seed_file, history=GraphHistory(view)
            )
        }

    ranked: list[RankedNode] = []
    paths: dict[str, list[str]] = {}
    for dependent, distance in distances.items():
        fan_in = fan_ins[dependent]
        # path was walked target->dependent in the reversed graph; flip it so
        # it reads the way the dependency actually flows.
        path = list(reversed(shortest_paths[dependent]))
        paths[dependent] = path
        node = view.node(dependent)
        file_path = node.file_path if node else None
        scored = file_scores.get(file_path) if file_path else None
        reasons: dict[str, object] = {
            "distance": distance,
            "fan_in": fan_in,
            "path_confidence": _weakest_confidence(view, path).value,
            # Which file this lands in. A caller counting "how many files does
            # this change touch" cannot derive it from the id: a function's id
            # carries a dotted qualified name, not a path, so without this the
            # UI either counted classes and functions as files or had to
            # re-fetch every node.
            "name": node.name if node else dependent,
            "kind": node.kind.value if node else None,
            "file_path": file_path,
        }
        if scored is not None:
            # Why this outranks the next file, in the units the score is made
            # of. "distance 2, fan-in 7" cannot explain an ordering between
            # two files that are both at distance 2.
            reasons["co_change"] = round(scored.co_change, 4)
            reasons["churn"] = scored.churn
            reasons["contributions"] = {
                name: round(value, 4) for name, value in scored.contributions.items()
            }
            score = scored.score
        elif seed_file and file_path == seed_file:
            # Same file as the change: a certainty, not a prediction.
            score = same_file_score(distance)
        else:
            # No file of its own to score against — fall back to distance.
            score = 1.0 / distance
        ranked.append(
            RankedNode(
                node_id=dependent,
                # Ordering only; the reasons are the real answer.
                score=score,
                reasons=reasons,
            )
        )

    # Distance still breaks ties inside a file: a file scores once, and its
    # nearest symbol is the one a reader should look at first.
    ranked.sort(key=lambda r: (-r.score, r.reasons["distance"], r.node_id))

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
