"""Findings — the questions worth asking without being asked.

Every other plan answers something the user typed. These two answer things
nobody thinks to ask about a repository they have just met, and both are
ranked by *consequence* rather than by badness in the abstract:

    untested_hubs   files many things depend on, that no test imports
    bus_factor      files whose history is essentially one person's

The ranking rule matters more than the detection. Any linter can list files
without tests; the list is enormous and nobody reads it. The finding is the
intersection with importance — a leaf utility with no tests is a shrug, and
the same gap in something forty files depend on is the reason a Friday
deploy goes wrong. So both plans multiply the gap by reach, and both refuse
to report anything the graph has no evidence for.
"""

from __future__ import annotations

from typing import Any

from app.graph.ownership import BUS_FACTOR_ONE_SHARE
from app.graph.schema import EdgeKind, NodeKind
from app.graph.traversal import GraphView
from app.queries.base import RankedNode, ResultGraph, register

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}

#: Findings are a to-do list, not a report. Twenty is already long.
DEFAULT_LIMIT = 20

#: Below this many dependents, "nothing tests it" is not yet a finding —
#: plenty of small files legitimately go untested and saying so is noise.
MIN_HUB_DEPENDENTS = 3


@register("untested_hubs")
def untested_hubs(view: GraphView, *, limit: int = DEFAULT_LIMIT) -> ResultGraph:
    """Files a lot depends on that no test file imports.

    "Untested" is deliberately the strongest available claim: **no test file
    reaches this file at all**, even transitively through imports.

    The weaker version — "no TESTS edge points directly at it" — was tried
    first and lied. A package that re-exports its internals (`from .app import
    Flask` in `__init__.py`) collects every test's import at the package root,
    so `app.py` looked untested in a repository with a thousand tests
    exercising it. Counting reachability instead means a finding survives the
    question "well, does anything test it *indirectly*?" before it is shown.

    What it still cannot see: whether the test asserts anything useful. This
    reports the absence of a path, not the presence of quality — but the
    absence of a path is decisive on its own.
    """
    from app.graph.coverage import is_test_path

    tested = _reachable_from_tests(view)

    findings: list[dict[str, Any]] = []
    for node_id, node in view.nodes_by_id.items():
        if node.kind is not NodeKind.FILE or node.file_path is None:
            continue
        if node_id in tested:
            continue
        if is_test_path(node.file_path):
            continue  # a test file having no test is not a finding
        dependents = view.fan_in(node_id, DEPENDENCY_KINDS)
        if dependents < MIN_HUB_DEPENDENTS:
            continue
        reach = len(view.dependents_of(node_id, DEPENDENCY_KINDS))
        findings.append(
            {
                "id": node_id,
                "name": node.name,
                "file_path": node.file_path,
                "direct_dependents": dependents,
                "transitive_dependents": reach,
                "complexity": node.complexity,
                "churn_count": node.churn_count,
            }
        )

    findings.sort(key=lambda f: (-f["transitive_dependents"], -f["direct_dependents"], f["id"]))
    findings = findings[:limit]

    return ResultGraph(
        query="untested_hubs",
        params={"limit": limit},
        node_ids=[f["id"] for f in findings],
        ranked=[
            RankedNode(
                node_id=f["id"],
                score=float(f["transitive_dependents"]),
                reasons={
                    "direct_dependents": f["direct_dependents"],
                    "transitive_dependents": f["transitive_dependents"],
                    "tested_by": 0,
                },
            )
            for f in findings
        ],
        meta={
            "findings": findings,
            "total": len(findings),
            "tested_files": len(tested),
            "explanation": (
                "No test file reaches these, directly or through any chain of "
                "imports, and other code depends on them. Ranked by how far a "
                "mistake in each one would reach."
            ),
        },
    )


def _reachable_from_tests(view: GraphView) -> set[str]:
    """Every file a test file can reach over TESTS then IMPORTS.

    Memoised on the view: the answer depends only on the snapshot, and both
    findings plus any future coverage question want the same set.
    """
    memo = view.memo.get("reachable_from_tests")
    if isinstance(memo, set):
        return memo

    frontier = [
        target
        for _, target, attributes in view.g.edges(data=True)
        if attributes["kind"] is EdgeKind.TESTS
    ]
    reached: set[str] = set(frontier)
    while frontier:
        current = frontier.pop()
        for _, target, attributes in view.g.out_edges(current, data=True):
            if attributes["kind"] is not EdgeKind.IMPORTS or target in reached:
                continue
            reached.add(target)
            frontier.append(target)

    view.memo["reachable_from_tests"] = reached
    return reached


@register("bus_factor")
def bus_factor(
    view: GraphView,
    *,
    limit: int = DEFAULT_LIMIT,
    threshold: float = BUS_FACTOR_ONE_SHARE,
) -> ResultGraph:
    """Important files whose history is concentrated in one person.

    Reported only where the graph has enough history to mean it: a file with
    two commits has a 100% primary author and says nothing. The share is a
    fact about the commits in the analysed window, which is bounded — a repo
    older than that window reports its recent past, and the meta says so.
    """
    findings: list[dict[str, Any]] = []
    for node_id, node in view.nodes_by_id.items():
        if node.kind is not NodeKind.FILE:
            continue
        share = node.extra.get("primary_author_share")
        if not isinstance(share, (int, float)) or share < threshold:
            continue
        if (node.churn_count or 0) < 3:
            continue  # too little history to call it ownership
        dependents = view.fan_in(node_id, DEPENDENCY_KINDS)
        if dependents < 1:
            continue
        reach = len(view.dependents_of(node_id, DEPENDENCY_KINDS))
        findings.append(
            {
                "id": node_id,
                "name": node.name,
                "file_path": node.file_path,
                "primary_author": node.extra.get("primary_author"),
                "share": round(float(share), 3),
                "commits": node.churn_count,
                "authors": node.author_count,
                "direct_dependents": dependents,
                "transitive_dependents": reach,
            }
        )

    findings.sort(key=lambda f: (-f["transitive_dependents"], -f["share"], f["id"]))
    findings = findings[:limit]

    return ResultGraph(
        query="bus_factor",
        params={"limit": limit, "threshold": threshold},
        node_ids=[f["id"] for f in findings],
        ranked=[
            RankedNode(
                node_id=f["id"],
                score=float(f["transitive_dependents"]),
                reasons={"share": f["share"], "commits": f["commits"]},
            )
            for f in findings
        ],
        meta={
            "findings": findings,
            "total": len(findings),
            "threshold": threshold,
            "explanation": (
                f"One person wrote at least {int(threshold * 100)}% of the commits "
                "to each of these, and other code depends on them. Shares are "
                "measured over the history that was cloned, not all time."
            ),
        },
    )
