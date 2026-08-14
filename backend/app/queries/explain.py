"""Explain — "what is this file, and why does the project need it?"

The other query plans each answer one question. This one assembles the
answer a person actually asks when they click something on the map:

    identity     what it is, where it lives, how big
    role         is it a hub, a leaf, an entrypoint, a risk
    depends_on   what it needs to work        (forward closure)
    used_by      what breaks without it       (reverse closure = blast radius)
    contains     what lives inside it
    evidence     a real dependency path, clickable to file:line

Every field is a deterministic graph fact — this works with no API key and
spends no tokens. CP-3.4's narration sits *on top* of this payload, turning
the same facts into prose; it never replaces them (ARCHITECTURE.md: the
graph answers, the AI explains).

Folders answer too: a module aggregates its files, and its dependencies are
the ones that cross its own boundary — internal wiring is implementation,
what crosses the edge is architecture.
"""

from __future__ import annotations

from typing import Any

from app.graph.schema import EdgeKind, NodeKind
from app.graph.traversal import GraphView
from app.queries.base import QueryError, RankedNode, ResultGraph, register

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}

#: How many neighbours to name explicitly. Enough to be concrete, few enough
#: to read; the counts always report the full totals.
_TOP_NEIGHBOURS = 8


@register("explain")
def explain(view: GraphView, *, node_id: str) -> ResultGraph:
    node = view.node(node_id)
    if node is None:
        raise QueryError(f"unknown node {node_id!r}")

    members = _members_of(view, node)
    scope = set(members) | {node_id}

    # Dependencies that cross this node's own boundary. For a file that is
    # simply its edges; for a folder it excludes internal wiring, which is
    # implementation detail rather than architecture.
    depends_on: dict[str, int] = {}
    used_by: dict[str, int] = {}
    for source, target, attributes in view.g.edges(data=True):
        if attributes["kind"] not in DEPENDENCY_KINDS:
            continue
        if source in scope and target not in scope:
            outside = _lift(view, target, node)
            if outside:
                depends_on[outside] = depends_on.get(outside, 0) + 1
        elif target in scope and source not in scope:
            outside = _lift(view, source, node)
            if outside:
                used_by[outside] = used_by.get(outside, 0) + 1

    ranked = [
        RankedNode(
            node_id=other,
            score=float(weight),
            reasons={"direction": "used_by", "references": weight},
        )
        for other, weight in sorted(used_by.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    # Everything that transitively reaches this node OR anything inside it.
    # The node's own closure must always be included: a file's dependents
    # hang off the file, not off its classes, so skipping it reported 0
    # transitive dependents for a file with 35 direct ones.
    transitive_dependents = set(view.dependents_of(node_id, DEPENDENCY_KINDS))
    for member in members:
        transitive_dependents |= view.dependents_of(member, DEPENDENCY_KINDS)
    transitive_dependents -= scope

    identity: dict[str, Any] = {
        "id": node.id,
        "kind": node.kind.value,
        "name": node.name,
        "qualified_name": node.qualified_name,
        "file_path": node.file_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        "language": node.language,
        "loc": node.loc,
        "complexity": node.complexity,
        "docstring": node.docstring,
        "churn_count": node.churn_count,
        "author_count": node.author_count,
        "last_modified": node.last_modified,
    }

    role = {
        "is_entrypoint": node.is_entrypoint,
        "entrypoint_kind": node.entrypoint_kind.value if node.entrypoint_kind else None,
        "direct_dependents": len(used_by),
        "direct_dependencies": len(depends_on),
        "transitive_dependents": len(transitive_dependents),
        "verdict": _verdict(
            len(used_by), len(depends_on), len(transitive_dependents), node.is_entrypoint
        ),
    }

    return ResultGraph(
        query="explain",
        params={"node_id": node_id},
        focus_id=node_id,
        node_ids=sorted(scope | set(depends_on) | set(used_by)),
        ranked=ranked[:_TOP_NEIGHBOURS],
        paths=_evidence_paths(view, node_id, list(used_by)[:3]),
        meta={
            "identity": identity,
            "role": role,
            "depends_on": _summarise(view, depends_on),
            "used_by": _summarise(view, used_by),
            "contains": _contains_summary(view, node, members),
            "co_changes": _co_change_summary(view, scope),
        },
    )


def _co_change_summary(view: GraphView, scope: set[str]) -> list[dict[str, Any]]:
    """What history says travels with this, and whether the code admits it.

    The `hidden` flag is the whole point: a partner that also imports this
    file is unremarkable, while one that does not is a finding.
    """
    from app.queries.coupling import _declared_file_pairs, _pair

    declared = _declared_file_pairs(view)
    partners: list[dict[str, Any]] = []
    for source, target, attributes in view.g.edges(data=True):
        if attributes["kind"] is not EdgeKind.CO_CHANGES:
            continue
        if source in scope:
            mine_id, other_id = source, target
        elif target in scope:
            mine_id, other_id = target, source
        else:
            continue
        other = view.node(other_id)
        if other is None or other_id in scope:
            continue
        partners.append(
            {
                "id": other.id,
                "name": other.name,
                "file_path": other.file_path,
                "strength": attributes.get("weight") or 0.0,
                # Compare the *file* that co-changed, not the thing being
                # explained: for a folder those differ, and asking whether a
                # module imports a file would always answer "no".
                "hidden": _pair(mine_id, other_id) not in declared,
            }
        )
    # Hidden partners first — they are the ones worth a reader's attention.
    partners.sort(key=lambda p: (not p["hidden"], -p["strength"], p["id"]))
    return partners[:_TOP_NEIGHBOURS]


def _members_of(view: GraphView, node: Any) -> set[str]:
    """Every node inside a folder/file; empty for a leaf."""
    if node.kind not in (NodeKind.MODULE, NodeKind.REPOSITORY, NodeKind.FILE):
        return set()
    found: set[str] = set()
    stack = [node.id]
    while stack:
        for child_id in view.children_of(stack.pop()):
            if child_id not in found:
                found.add(child_id)
                stack.append(child_id)
    return found


def _lift(view: GraphView, node_id: str, relative_to: Any) -> str | None:
    """Report a neighbour at the same granularity as the thing being
    explained: a folder's neighbours are folders, a file's are files."""
    other = view.node(node_id)
    if other is None:
        return None
    if relative_to.kind is NodeKind.MODULE and other.file_path:
        parent = other.file_path.rsplit("/", 1)[0] if "/" in other.file_path else None
        candidate = f"{NodeKind.MODULE.value}:{parent}" if parent else None
        if candidate and view.has_node(candidate):
            return candidate
    if other.kind in (NodeKind.CLASS, NodeKind.FUNCTION) and other.file_path:
        candidate = f"{NodeKind.FILE.value}:{other.file_path}"
        if view.has_node(candidate):
            return candidate
    return other.id


def _summarise(view: GraphView, weights: dict[str, int]) -> list[dict[str, Any]]:
    ordered = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_NEIGHBOURS]
    out: list[dict[str, Any]] = []
    for node_id, weight in ordered:
        node = view.node(node_id)
        out.append(
            {
                "id": node_id,
                "name": node.name if node else node_id,
                "file_path": node.file_path if node else None,
                "references": weight,
            }
        )
    return out


def _contains_summary(view: GraphView, node: Any, members: set[str]) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    named: list[dict[str, Any]] = []
    for member_id in members:
        member = view.node(member_id)
        if member is None:
            continue
        kinds[member.kind.value] = kinds.get(member.kind.value, 0) + 1
    direct = [
        view.node(child_id)
        for child_id in sorted(view.children_of(node.id))
        if view.node(child_id) is not None
    ]
    direct.sort(key=lambda n: (-view.fan_in(n.id, DEPENDENCY_KINDS), n.id))  # type: ignore[union-attr]
    for child in direct[:_TOP_NEIGHBOURS]:
        assert child is not None
        named.append(
            {
                "id": child.id,
                "name": child.name,
                "kind": child.kind.value,
                "fan_in": view.fan_in(child.id, DEPENDENCY_KINDS),
                "file_path": child.file_path,
                "start_line": child.start_line,
            }
        )
    return {"counts": kinds, "top": named}


def _evidence_paths(
    view: GraphView, node_id: str, dependents: list[str]
) -> dict[str, list[str]]:
    """A real chain from a dependent back to this node — the receipt behind
    'X depends on this'."""
    import networkx as nx

    dependency_view = view.subgraph(DEPENDENCY_KINDS)
    paths: dict[str, list[str]] = {}
    for dependent in dependents:
        if dependent not in dependency_view or node_id not in dependency_view:
            continue
        try:
            paths[dependent] = nx.shortest_path(dependency_view, dependent, node_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
    return paths


def _verdict(
    dependents: int, dependencies: int, transitive: int, is_entrypoint: bool
) -> str:
    """One honest sentence about this node's place in the system.

    Judged on the *transitive* reach as well as the direct count: at folder
    granularity only a couple of siblings may import you directly while a
    hundred things sit downstream, and calling that "supporting" would be
    misleading. Every number quoted is one the reader can see for themselves.
    """
    if is_entrypoint:
        return "Entry point — the outside world reaches the system through here."
    if dependents == 0 and dependencies == 0:
        return "Isolated — nothing here connects to the rest of the project."
    if dependents == 0:
        return (
            "Leaf — it uses the project but nothing depends on it, "
            "so changes here stay contained."
        )
    reach = max(dependents, transitive)
    if reach >= 25:
        return "Hub — much of the project rests on this; changes ripple widely."
    if reach >= 5:
        return "Shared — several parts depend on this, directly or downstream."
    return "Supporting — a small, contained set of places depend on this."
