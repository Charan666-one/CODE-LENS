"""Crude visual dump — the parser's debugger, not the product (CP-1.6).

ARCHITECTURE.md M1 asks for "crude visual output (even GraphViz)". This is
deliberately ugly: its job is to let a human eyeball the graph for wrongness
before any real visualization exists. The Visualization Engine (CP-4.x)
replaces its output, never builds on it.
"""

from __future__ import annotations

import json

from app.graph.schema import EdgeKind, KnowledgeGraph, NodeKind

#: File-level view: the two edge kinds that mean "depends on" between files.
_DEPENDENCY_KINDS = {EdgeKind.IMPORTS, EdgeKind.CALLS}

_KIND_STYLE = {
    NodeKind.MODULE: ("box3d", "lightsteelblue"),
    NodeKind.FILE: ("box", "lightyellow"),
    NodeKind.CLASS: ("ellipse", "lightpink"),
    NodeKind.FUNCTION: ("ellipse", "white"),
}

_CONFIDENCE_STYLE = {
    "resolved": "solid",
    "heuristic": "dashed",
    "dynamic_unknown": "dotted",
}


def to_dot(graph: KnowledgeGraph, *, max_nodes: int = 150) -> str:
    """A file-level GraphViz digraph: files as boxes, IMPORTS as edges.

    File level because a function-level dot of a real repo is an unreadable
    hairball; eyeballing correctness happens at file granularity. Capped at
    `max_nodes` files by fan-in so `dot` stays renderable.
    """
    files = [n for n in graph.nodes if n.kind is NodeKind.FILE]
    fan_in: dict[str, int] = {n.id: 0 for n in files}
    for edge in graph.edges:
        if edge.kind in _DEPENDENCY_KINDS and edge.target_id in fan_in:
            fan_in[edge.target_id] += 1

    keep = {
        n.id for n in sorted(files, key=lambda n: -fan_in.get(n.id, 0))[:max_nodes]
    }

    lines = [
        "digraph codelens {",
        "  rankdir=LR;",
        '  node [fontname="Helvetica", fontsize=10];',
        f'  label="{graph.snapshot.repo_url}  ({graph.snapshot.commit_sha[:8]})";',
    ]
    for node in files:
        if node.id not in keep:
            continue
        shape, colour = _KIND_STYLE[NodeKind.FILE]
        label = f"{node.qualified_name}\\nfan-in {fan_in.get(node.id, 0)}"
        if node.churn_count:
            label += f" · churn {node.churn_count}"
        lines.append(
            f'  "{node.id}" [shape={shape}, style=filled, fillcolor={colour},'
            f' label="{label}"];'
        )

    seen: set[tuple[str, str]] = set()
    for edge in graph.edges:
        if edge.kind is not EdgeKind.IMPORTS:
            continue
        if edge.source_id not in keep or edge.target_id not in keep:
            continue
        if (edge.source_id, edge.target_id) in seen:
            continue
        seen.add((edge.source_id, edge.target_id))
        lines.append(f'  "{edge.source_id}" -> "{edge.target_id}";')

    lines.append("}")
    return "\n".join(lines)


def to_summary_json(graph: KnowledgeGraph) -> str:
    """A machine-checkable digest of the graph, for diffing and eyeballing."""
    node_counts: dict[str, int] = {}
    for node in graph.nodes:
        node_counts[node.kind.value] = node_counts.get(node.kind.value, 0) + 1

    edge_counts: dict[str, int] = {}
    confidence_counts: dict[str, int] = {}
    for edge in graph.edges:
        edge_counts[edge.kind.value] = edge_counts.get(edge.kind.value, 0) + 1
        if edge.kind is EdgeKind.CALLS:
            key = edge.confidence.value
            confidence_counts[key] = confidence_counts.get(key, 0) + 1

    entrypoints = [
        {"id": n.id, "kind": n.entrypoint_kind.value if n.entrypoint_kind else None}
        for n in graph.nodes
        if n.is_entrypoint
    ]

    return json.dumps(
        {
            "repo_url": graph.snapshot.repo_url,
            "commit_sha": graph.snapshot.commit_sha,
            "primary_language": graph.snapshot.primary_language,
            "nodes": node_counts,
            "edges": edge_counts,
            "calls_confidence": confidence_counts,
            "entrypoints": entrypoints,
        },
        indent=2,
        sort_keys=True,
    )
