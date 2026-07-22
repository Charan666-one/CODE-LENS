"""Summaries — the first annotation stratum (CP-3.2).

One plain-English sentence per module and public function, generated from
graph-selected context, stored as `SemanticAnnotation` with `derived_from`
pointing at the exact nodes the model saw.

The cache is the point (Constitution 4): a summary is keyed by the *content
hash* of what it summarises. Unchanged source -> cache hit -> zero LLM calls,
across runs, snapshots, and even repositories. The gate test proves it by
counting calls on a fake client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.graph.schema import Node, NodeKind, SemanticAnnotation
from app.graph.store import GraphStore
from app.graph.traversal import GraphView
from app.semantic.context import assemble
from app.semantic.llm import LLMClient, LLMError

_SYSTEM_VOICE = (
    "You are a senior engineer giving a colleague a tour of a codebase. "
    "Summarise the given code in ONE plain-English sentence: what enters, "
    "what happens, what leaves, and who depends on it if that is visible. "
    "No hedging, no 'this code appears to'. Facts only — never invent "
    "behaviour the context does not show."
)


class SummaryReport:
    """What a summarisation run did — the observable the gate measures."""

    def __init__(self) -> None:
        self.generated: list[str] = []  # node ids that cost an LLM call
        self.from_cache: list[str] = []  # node ids served by the hash cache
        self.failed: list[str] = []  # node ids whose call failed (and were skipped)

    @property
    def llm_calls(self) -> int:
        return len(self.generated)


def summarize_graph(
    view: GraphView,
    root: Path,
    store: GraphStore,
    snapshot_id: int,
    llm: LLMClient,
    *,
    max_nodes: int | None = None,
) -> tuple[list[SemanticAnnotation], SummaryReport]:
    """Summarise every summarisable node, cheapest-possible: cache first.

    Returns the annotations (persisted to the snapshot) and the report.
    """
    report = SummaryReport()
    annotations: list[SemanticAnnotation] = []

    targets = _summary_targets(view)
    if max_nodes is not None:
        targets = targets[:max_nodes]

    for node in targets:
        content_hash = node.content_hash
        if not content_hash:
            continue

        cached = store.cached_summary(content_hash)
        if cached is not None:
            # Same source text, possibly a different node id or repo: the
            # summary transfers, the evidence pointer is re-anchored.
            annotations.append(
                SemanticAnnotation(
                    node_id=node.id,
                    summary=cached.summary,
                    derived_from=cached.derived_from,
                    content_hash=content_hash,
                    model=cached.model,
                    generated_at=cached.generated_at,
                )
            )
            report.from_cache.append(node.id)
            continue

        bundle = assemble(view, root, _context_ids_for(view, node), token_budget=1200)
        if not bundle.items:
            continue

        try:
            summary = llm.complete(
                system=_SYSTEM_VOICE,
                prompt=f"Summarise `{node.qualified_name}`:\n\n{bundle.as_prompt_block()}",
                max_tokens=120,
            )
        except LLMError:
            report.failed.append(node.id)
            continue  # facts never wait for annotations

        annotation = SemanticAnnotation(
            node_id=node.id,
            summary=summary,
            derived_from=bundle.included_ids,
            content_hash=content_hash,
            model=llm.model_name,
            generated_at=datetime.now(UTC).isoformat(),
        )
        annotations.append(annotation)
        store.cache_summary(annotation)
        report.generated.append(node.id)

    store.save_annotations(snapshot_id, annotations)
    return annotations, report


def _summary_targets(view: GraphView) -> list[Node]:
    """Files and public functions — FOUNDATION's 'per module and per public
    function', with files standing in for modules until directories get
    content hashes. Sorted by fan-in: the most-depended-on earn words first."""
    targets = [
        node
        for node in view.nodes_by_id.values()
        if (
            node.kind is NodeKind.FILE
            or (node.kind is NodeKind.FUNCTION and not node.name.startswith("_"))
        )
        and node.content_hash
    ]
    from app.semantic.context import DEPENDENCY_KINDS

    targets.sort(key=lambda n: (-view.fan_in(n.id, DEPENDENCY_KINDS), n.id))
    return targets


def _context_ids_for(view: GraphView, node: Node) -> list[str]:
    """The node itself, then its immediate graph neighbourhood."""
    ids = [node.id]
    ids.extend(sorted(view.children_of(node.id)))
    ids.extend(source for source, _ in list(view.g.in_edges(node.id))[:5])
    ids.extend(target for _, target in list(view.g.out_edges(node.id))[:5])
    seen: set[str] = set()
    unique: list[str] = []
    for node_id in ids:
        if node_id not in seen:
            seen.add(node_id)
            unique.append(node_id)
    return unique
