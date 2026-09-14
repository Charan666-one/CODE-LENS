"""Endpoints — the service's public surface, and what each one touches.

Two questions, one plan:

    endpoints                what does this service expose?
    endpoints(node_id=…)     which endpoints break if I change this?

The second is the one worth building for. "This helper has 12 dependents" is
abstract; "changing this helper affects POST /checkout and DELETE /account"
is a decision. It is the blast radius question asked in the vocabulary the
person deploying actually thinks in.
"""

from __future__ import annotations

from typing import Any

from app.graph.schema import EdgeKind, NodeKind
from app.graph.traversal import GraphView
from app.queries.base import QueryError, RankedNode, ResultGraph, register

#: How an endpoint reaches code: through its handler, then normal dependencies.
REACH_KINDS = {EdgeKind.ROUTES_TO, EdgeKind.CALLS, EdgeKind.IMPORTS}

DEFAULT_LIMIT = 100


@register("endpoints")
def endpoints(
    view: GraphView,
    *,
    limit: int = DEFAULT_LIMIT,
    node_id: str | None = None,
    include_tests: bool = False,
) -> ResultGraph:
    """Every HTTP route, or only those that reach `node_id`.

    Routes declared inside test files are excluded by default. Flask's own
    test suite declares 170 of them — every `@app.route("/")` in a fixture is
    a real route object, and none of them is part of Flask's surface. Left in,
    they bury the answer; the flag is there because "which fixtures mount a
    route" is occasionally a question too.
    """
    from app.graph.coverage import is_test_path

    if node_id is not None and not view.has_node(node_id):
        raise QueryError(f"unknown node {node_id!r}")

    all_endpoints = [
        node
        for node in view.nodes_by_id.values()
        if node.kind is NodeKind.ENDPOINT
        and (include_tests or not is_test_path(node.file_path or ""))
    ]

    if node_id is None:
        selected = all_endpoints
        affected_by: dict[str, int] = {}
    else:
        # Everything that can reach the changed node, then keep the endpoints.
        # One reverse closure answers for every endpoint at once — asking each
        # endpoint "do you reach this?" would walk the graph once per route.
        upstream = view.dependents_of(node_id, REACH_KINDS)
        target = view.node(node_id)
        if target is not None and target.file_path:
            # A change to a file is a change to everything defined in it.
            for member_id in _members(view, node_id):
                upstream |= view.dependents_of(member_id, REACH_KINDS)
            upstream |= _members(view, node_id)
        selected = [e for e in all_endpoints if e.id in upstream]
        affected_by = {}

    rows: list[dict[str, Any]] = []
    for endpoint in selected:
        handlers = [
            target
            for _, target, attributes in view.g.out_edges(endpoint.id, data=True)
            if attributes["kind"] is EdgeKind.ROUTES_TO
        ]
        handler = view.node(handlers[0]) if handlers else None
        rows.append(
            {
                "id": endpoint.id,
                "method": endpoint.extra.get("method", "ANY"),
                "path": endpoint.extra.get("path", ""),
                "file_path": endpoint.file_path,
                "line": endpoint.start_line,
                "handler": (
                    {"id": handler.id, "name": handler.name} if handler else None
                ),
            }
        )

    rows.sort(key=lambda r: (r["path"], r["method"]))
    rows = rows[:limit]

    by_method: dict[str, int] = {}
    for row in rows:
        by_method[row["method"]] = by_method.get(row["method"], 0) + 1

    return ResultGraph(
        query="endpoints",
        params={"limit": limit, "node_id": node_id},
        focus_id=node_id,
        node_ids=[row["id"] for row in rows],
        ranked=[
            RankedNode(
                node_id=row["id"],
                score=float(affected_by.get(row["id"], 0)),
                reasons={"method": row["method"], "path": row["path"]},
            )
            for row in rows
        ],
        meta={
            "endpoints": rows,
            "total": len(selected),
            "shown": len(rows),
            "by_method": by_method,
            "explanation": (
                "Every HTTP route this service declares."
                if node_id is None
                else "These endpoints reach the selected code, so a change here "
                "changes what they do."
            ),
        },
    )


def _members(view: GraphView, node_id: str) -> set[str]:
    """Everything CONTAINS-nested under a node."""
    found: set[str] = set()
    stack = [node_id]
    while stack:
        for child_id in view.children_of(stack.pop()):
            if child_id not in found:
                found.add(child_id)
                stack.append(child_id)
    return found
