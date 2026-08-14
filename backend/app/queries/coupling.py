"""Hidden coupling — the pairs the import graph swears are unrelated.

A CO_CHANGES edge on its own is mildly interesting: files that ship together.
Most of them are boring, because most of them also *import* each other, and
"the caller changed when the callee did" is not news.

The finding is the residue. Take every co-change pair, subtract every pair
with a structural dependency in either direction, and what remains is coupling
with no declared cause: two files that keep changing together while the code
insists they have nothing to do with each other. Each survivor is one of

    a shared format, constant, or protocol neither file owns
    a duplicated implementation nobody knows is duplicated
    an invariant held only by whoever remembers to hold it

and all three are the kind of thing that breaks when a newcomer changes one
side. This is the answer no import-graph tool can give, because the evidence
isn't in the code — it's in what happened to the code.

Structural dependency is checked at *file* granularity: CALLS edges join
functions, so a call from `a.py:foo` to `b.py:bar` must count as `a.py`
depending on `b.py`, or every call-only pair would be misreported as hidden.
"""

from __future__ import annotations

from typing import Any

from app.graph.schema import EdgeKind, NodeKind
from app.graph.traversal import GraphView
from app.queries.base import QueryError, RankedNode, ResultGraph, register

#: What counts as a declared relationship between two files.
STRUCTURAL_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS, EdgeKind.INHERITS}

#: Findings returned by default. This is a "read them all and act" list, not
#: a dataset — past ~25 nobody triages, they skim.
DEFAULT_LIMIT = 25


@register("hidden_coupling")
def hidden_coupling(
    view: GraphView, *, limit: int = DEFAULT_LIMIT, node_id: str | None = None
) -> ResultGraph:
    """Co-change pairs with no structural dependency, strongest first.

    With `node_id`, narrows to pairs involving that file — "what secretly
    travels with this?", the version worth showing on the explanation page.
    """
    if node_id is not None and not view.has_node(node_id):
        raise QueryError(f"unknown node {node_id!r}")

    declared = _declared_file_pairs(view)

    findings: list[dict[str, Any]] = []
    for source, target, attributes in view.g.edges(data=True):
        if attributes["kind"] is not EdgeKind.CO_CHANGES:
            continue
        if node_id is not None and node_id not in (source, target):
            continue
        if _pair(source, target) in declared:
            continue
        left, right = view.node(source), view.node(target)
        if left is None or right is None:
            continue
        findings.append(
            {
                "a": {"id": left.id, "name": left.name, "file_path": left.file_path},
                "b": {"id": right.id, "name": right.name, "file_path": right.file_path},
                "strength": attributes.get("weight") or 0.0,
            }
        )

    findings.sort(key=lambda f: (-f["strength"], f["a"]["id"], f["b"]["id"]))
    findings = findings[:limit]

    # When the question is about one file, the ranked answer is the partners;
    # otherwise it is the files that appear in the most findings — the ones
    # sitting at the centre of the undeclared web.
    if node_id is not None:
        ranked = [
            RankedNode(
                node_id=(f["b"]["id"] if f["a"]["id"] == node_id else f["a"]["id"]),
                score=float(f["strength"]),
                reasons={"strength": f["strength"], "relation": "co_changes"},
            )
            for f in findings
        ]
    else:
        appearances: dict[str, float] = {}
        for finding in findings:
            for side in ("a", "b"):
                key = finding[side]["id"]
                appearances[key] = appearances.get(key, 0.0) + float(finding["strength"])
        ranked = [
            RankedNode(
                node_id=key,
                score=round(score, 4),
                reasons={"undeclared_partners": _partner_count(findings, key)},
            )
            for key, score in sorted(appearances.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    involved = sorted({f["a"]["id"] for f in findings} | {f["b"]["id"] for f in findings})
    return ResultGraph(
        query="hidden_coupling",
        params={"limit": limit, "node_id": node_id},
        focus_id=node_id,
        node_ids=involved,
        ranked=ranked[:limit],
        meta={
            "findings": findings,
            "total": len(findings),
            "explanation": (
                "These files change together in history but neither imports or "
                "calls the other. Something connects them that the code does not "
                "declare."
            ),
        },
    )


def _partner_count(findings: list[dict[str, Any]], node_id: str) -> int:
    return sum(1 for f in findings if node_id in (f["a"]["id"], f["b"]["id"]))


def _declared_file_pairs(view: GraphView) -> set[tuple[str, str]]:
    """Every unordered file pair joined by a structural edge, with function
    and class edges lifted to the files that hold them."""
    memo = view.memo.get("declared_file_pairs")
    if isinstance(memo, set):
        return memo

    pairs: set[tuple[str, str]] = set()
    for source, target, attributes in view.g.edges(data=True):
        if attributes["kind"] not in STRUCTURAL_KINDS:
            continue
        left = _owning_file(view, source)
        right = _owning_file(view, target)
        if left and right and left != right:
            pairs.add(_pair(left, right))

    view.memo["declared_file_pairs"] = pairs
    return pairs


def _owning_file(view: GraphView, node_id: str) -> str | None:
    """The File node id for anything that lives in a file."""
    node = view.node(node_id)
    if node is None:
        return None
    if node.kind is NodeKind.FILE:
        return node.id
    if node.file_path:
        candidate = f"{NodeKind.FILE.value}:{node.file_path}"
        return candidate if view.has_node(candidate) else None
    return None


def _pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left <= right else (right, left)
