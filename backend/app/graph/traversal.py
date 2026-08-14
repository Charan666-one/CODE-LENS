"""The in-memory traversal view — SQLite persists, NetworkX walks.

FOUNDATION.md §Q3: "SQLite (nodes/edges/metadata) + networkx in-memory for
traversals." A query never touches SQL; it asks this view. When the store
backend changes (CP-9.2), this file is the seam that keeps queries unchanged.
"""

from __future__ import annotations

import networkx as nx

from app.graph.schema import Edge, EdgeKind, KnowledgeGraph, Node


class GraphView:
    """A KnowledgeGraph loaded for walking.

    A MultiDiGraph because two nodes can be related in more than one way at
    once (a file both IMPORTS and its function CALLS into another), and each
    edge keeps its own evidence and confidence.
    """

    def __init__(self, graph: KnowledgeGraph) -> None:
        self.snapshot = graph.snapshot
        #: Memo for expensive per-graph computations (PageRank). The view is
        #: cached per snapshot and a snapshot's structure never changes, so
        #: anything derived purely from it can be computed once.
        self.memo: dict[str, object] = {}
        self.nodes_by_id: dict[str, Node] = {node.id: node for node in graph.nodes}
        self.g = nx.MultiDiGraph()
        for node in graph.nodes:
            self.g.add_node(node.id)
        for edge in graph.edges:
            self.g.add_edge(
                edge.source_id,
                edge.target_id,
                kind=edge.kind,
                confidence=edge.confidence,
                file_path=edge.file_path,
                line=edge.line,
                weight=edge.weight,
            )

    # ── basics ────────────────────────────────────────────────────────────

    def node(self, node_id: str) -> Node | None:
        return self.nodes_by_id.get(node_id)

    def has_node(self, node_id: str) -> bool:
        return node_id in self.nodes_by_id

    def edges_between(self, source_id: str, target_id: str) -> list[Edge]:
        found: list[Edge] = []
        data = self.g.get_edge_data(source_id, target_id) or {}
        for attributes in data.values():
            found.append(
                Edge(
                    source_id=source_id,
                    target_id=target_id,
                    kind=attributes["kind"],
                    confidence=attributes["confidence"],
                    file_path=attributes.get("file_path"),
                    line=attributes.get("line"),
                )
            )
        return found

    # ── filtered subgraphs ────────────────────────────────────────────────

    def subgraph(self, kinds: set[EdgeKind]) -> nx.MultiDiGraph:
        """The graph restricted to some edge kinds — dependency questions walk
        CALLS+IMPORTS, structure questions walk CONTAINS, never mixed."""
        filtered = nx.MultiDiGraph()
        filtered.add_nodes_from(self.g.nodes)
        for source, target, attributes in self.g.edges(data=True):
            if attributes["kind"] in kinds:
                filtered.add_edge(source, target, **attributes)
        return filtered

    # ── common walks ──────────────────────────────────────────────────────

    def dependents_of(self, node_id: str, kinds: set[EdgeKind]) -> set[str]:
        """Everything that transitively reaches `node_id` over `kinds` —
        the reverse closure that blast radius is built on."""
        view = self.subgraph(kinds)
        if node_id not in view:
            return set()
        return set(nx.ancestors(view, node_id))

    def dependencies_of(self, node_id: str, kinds: set[EdgeKind]) -> set[str]:
        """Everything `node_id` transitively reaches over `kinds`."""
        view = self.subgraph(kinds)
        if node_id not in view:
            return set()
        return set(nx.descendants(view, node_id))

    def fan_in(self, node_id: str, kinds: set[EdgeKind]) -> int:
        """Distinct direct dependents over `kinds`."""
        return len(
            {
                source
                for source, _, attributes in self.g.in_edges(node_id, data=True)
                if attributes["kind"] in kinds
            }
        )

    def children_of(self, node_id: str) -> list[str]:
        """Direct CONTAINS children, the structural hierarchy walk."""
        return [
            target
            for _, target, attributes in self.g.out_edges(node_id, data=True)
            if attributes["kind"] is EdgeKind.CONTAINS
        ]
