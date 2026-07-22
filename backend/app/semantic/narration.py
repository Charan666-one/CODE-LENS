"""Narration — the AI explains; the graph answered (CP-3.4).

Three launch answers live here:
  Q1  "What does this project do?"      — LLM over graph-selected context
  Q6  "Where should I start reading?"   — DETERMINISTIC (entrypoints ->
       central -> leaves); summaries decorate the stops, no model needed
  Q8  "What breaks if I change X?"      — the blast_radius ResultGraph told
       the truth; the LLM turns it into a story

Every answer carries `evidence_ids` — the graph nodes it is derived from.
The voice rule (EXPERIENCE.md): a senior engineer giving a tour, narrative
arc, downstream consequences mentioned — and nothing the graph didn't say.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.graph.schema import NodeKind, SemanticAnnotation
from app.graph.traversal import GraphView
from app.queries import run_query
from app.queries.base import ResultGraph
from app.semantic.llm import LLMClient

_TOUR_VOICE = (
    "You are a senior engineer giving a new teammate a tour. Narrate — what "
    "enters, what happens, what leaves, what depends on it. Plain verbs, no "
    "filler. CRITICAL: only state what the provided facts show; if the facts "
    "don't show something, don't say it. Refer to modules and functions by "
    "their exact names so every claim can be traced."
)


class NarratedAnswer(BaseModel):
    """Text plus receipts. A claim without evidence ids does not ship."""

    question: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    model: str | None = None  # None => fully deterministic answer
    meta: dict = Field(default_factory=dict)


# ── Q1: what does this project do? ────────────────────────────────────────


def narrate_project(
    view: GraphView, annotations: list[SemanticAnnotation], llm: LLMClient
) -> NarratedAnswer:
    summaries = {a.node_id: a.summary for a in annotations}

    entry = run_query("entrypoints", view)
    important = run_query("centrality", view, kind="file", top=8)

    facts: list[str] = [f"Repository: {view.snapshot.repo_url}"]
    facts.append(f"Primary language: {view.snapshot.primary_language}")
    for kind, ids in entry.meta.get("by_kind", {}).items():
        names = ", ".join(node_id.split(":", 1)[1] for node_id in ids[:5])
        facts.append(f"Entrypoints ({kind}): {names}")
    for entry_node in important.ranked:
        line = f"Central file: {entry_node.node_id.split(':', 1)[1]}"
        line += f" (fan-in {entry_node.reasons['fan_in']})"
        if entry_node.node_id in summaries:
            line += f" — {summaries[entry_node.node_id]}"
        facts.append(line)

    evidence = entry.node_ids + [r.node_id for r in important.ranked]
    text = llm.complete(
        system=_TOUR_VOICE,
        prompt=(
            "From these graph facts only, explain in 3-5 sentences what this "
            "project does and how it is shaped:\n\n" + "\n".join(f"- {f}" for f in facts)
        ),
        max_tokens=400,
    )
    return NarratedAnswer(
        question="What does this project do?",
        text=text,
        evidence_ids=evidence,
        model=llm.model_name,
    )


# ── Q6: where should I start reading? (no LLM — the order IS the answer) ──


def learning_path(
    view: GraphView, annotations: list[SemanticAnnotation], *, stops: int = 8
) -> NarratedAnswer:
    """Entrypoints first, then the most central files, then a leaf to close.

    Deterministic by design: FOUNDATION resolves Q6 as *ordering*, and an
    ordering needs no model. Summaries, where they exist, annotate each stop.
    """
    summaries = {a.node_id: a.summary for a in annotations}
    path: list[str] = []

    entry = run_query("entrypoints", view)
    path.extend(entry.node_ids[:2])

    for ranked in run_query("centrality", view, kind="file", top=stops).ranked:
        if ranked.node_id not in path:
            path.append(ranked.node_id)
        if len(path) >= stops:
            break

    lines = []
    for position, node_id in enumerate(path, 1):
        node = view.node(node_id)
        if node is None:
            continue
        line = f"{position}. {node.qualified_name}"
        if node.file_path and node.kind is not NodeKind.FILE:
            line += f"  [{node.file_path}:{node.start_line}]"
        if node_id in summaries:
            line += f" — {summaries[node_id]}"
        lines.append(line)

    return NarratedAnswer(
        question="Where should I start reading?",
        text="\n".join(lines),
        evidence_ids=path,
        model=None,  # deterministic: the graph ordered it, nobody narrated it
        meta={"stops": len(lines)},
    )


# ── Q8: the blast-radius story ────────────────────────────────────────────


def narrate_blast_radius(
    view: GraphView,
    result: ResultGraph,
    annotations: list[SemanticAnnotation],
    llm: LLMClient,
) -> NarratedAnswer:
    assert result.query == "blast_radius", "narrates blast_radius results only"
    summaries = {a.node_id: a.summary for a in annotations}
    target = result.focus_id or ""
    target_name = target.split(":", 1)[1] if ":" in target else target

    facts: list[str] = [
        f"Changing: {target_name}",
        f"Total affected: {result.meta.get('total_affected', 0)}",
    ]
    for ranked in result.ranked[:10]:
        name = ranked.node_id.split(":", 1)[1]
        line = (
            f"Affected: {name} — distance {ranked.reasons['distance']}, "
            f"fan-in {ranked.reasons['fan_in']}, "
            f"path confidence {ranked.reasons['path_confidence']}"
        )
        path = result.paths.get(ranked.node_id)
        if path:
            line += " — path: " + " -> ".join(p.split(":", 1)[1] for p in path)
        if ranked.node_id in summaries:
            line += f" — {summaries[ranked.node_id]}"
        facts.append(line)

    text = llm.complete(
        system=_TOUR_VOICE,
        prompt=(
            "A developer asks: what breaks if I change this? Tell the story "
            "of this blast radius in 3-6 sentences — who feels it first, how "
            "far it spreads, and where the paths are uncertain (say so when "
            "path confidence is heuristic or dynamic_unknown):\n\n"
            + "\n".join(f"- {f}" for f in facts)
        ),
        max_tokens=400,
    )
    return NarratedAnswer(
        question=f"What breaks if I change {target_name}?",
        text=text,
        evidence_ids=[target, *[r.node_id for r in result.ranked]],
        model=llm.model_name,
        meta={"total_affected": result.meta.get("total_affected", 0)},
    )
