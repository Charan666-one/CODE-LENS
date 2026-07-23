"""Semantic endpoints — Stage 3 over HTTP. Thin, like everything in api/.

Two classes of answer, and the split is visible in the status codes:

* Deterministic (no key needed, never 503): concept search, learning path.
* LLM-narrated (503 without ANTHROPIC_API_KEY): project story, blast-radius
  story, summarisation. The graph's facts are never behind the key — only
  the prose about them is.

The summarize endpoint needs the working tree (context snippets are real
source lines), so it re-acquires the source from the snapshot's URL. For
an unchanged repo the content-hash cache makes the LLM cost of a re-run
zero — re-cloning is the only price.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.graph.schema import KnowledgeGraph
from app.graph.traversal import GraphView
from app.ingestion import IngestionError, ingest
from app.queries import QueryError, run_query
from app.semantic import (
    AnthropicClient,
    ConceptIndex,
    LLMClient,
    LLMError,
    learning_path,
    narrate_blast_radius,
    narrate_project,
    summarize_graph,
)

router = APIRouter(prefix="/api/repos/{snapshot_id}", tags=["Semantic"])

#: Per-snapshot concept indexes, built on first search. Process-local cache —
#: rebuilt cheaply after a restart, invalidated by snapshot id.
_INDEXES: dict[int, ConceptIndex] = {}


def get_llm() -> LLMClient:
    """The narration model. Overridden in tests; 503s cleanly without a key."""
    try:
        return AnthropicClient()
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _load(snapshot_id: int) -> KnowledgeGraph:
    from app.api.routes import get_store

    graph = get_store().load_graph_by_id(snapshot_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"no snapshot {snapshot_id}")
    return graph


# ── deterministic answers ─────────────────────────────────────────────────


class SearchRequest(BaseModel):
    text: str = Field(min_length=1)
    top: int = 10


@router.post("/search")
def concept_search(snapshot_id: int, request: SearchRequest) -> dict[str, Any]:
    graph = _load(snapshot_id)
    index = _INDEXES.get(snapshot_id)
    if index is None:
        index = ConceptIndex()
        index.build(GraphView(graph), graph.annotations)
        _INDEXES[snapshot_id] = index
    result = run_query(
        "concept_search", GraphView(graph), index=index, text=request.text, top=request.top
    )
    return result.model_dump()


@router.get("/answers/learning_path")
def answer_learning_path(snapshot_id: int) -> dict[str, Any]:
    graph = _load(snapshot_id)
    return learning_path(GraphView(graph), graph.annotations).model_dump()


# ── LLM-narrated answers ──────────────────────────────────────────────────


@router.post("/answers/project")
def answer_project(snapshot_id: int) -> dict[str, Any]:
    graph = _load(snapshot_id)
    llm = get_llm()
    try:
        return narrate_project(GraphView(graph), graph.annotations, llm).model_dump()
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class BlastStoryRequest(BaseModel):
    node_id: str = Field(min_length=1)
    max_depth: int | None = None


@router.post("/answers/blast_radius")
def answer_blast_radius(snapshot_id: int, request: BlastStoryRequest) -> dict[str, Any]:
    graph = _load(snapshot_id)
    view = GraphView(graph)
    try:
        result = run_query(
            "blast_radius", view, node_id=request.node_id, max_depth=request.max_depth
        )
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    llm = get_llm()
    try:
        answer = narrate_blast_radius(view, result, graph.annotations, llm)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    payload = answer.model_dump()
    payload["result"] = result.model_dump()  # the facts ride along with the story
    return payload


class SummarizeRequest(BaseModel):
    max_nodes: int | None = None


@router.post("/summarize")
def summarize(snapshot_id: int, request: SummarizeRequest) -> dict[str, Any]:
    from app.api.routes import get_store

    graph = _load(snapshot_id)
    llm = get_llm()
    try:
        root = _root_for(graph)
    except IngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    annotations, report = summarize_graph(
        GraphView(graph),
        root,
        get_store(),
        snapshot_id,
        llm,
        max_nodes=request.max_nodes,
    )
    _INDEXES.pop(snapshot_id, None)  # new annotations: the index must rebuild
    return {
        "annotations": len(annotations),
        "llm_calls": report.llm_calls,
        "from_cache": len(report.from_cache),
        "failed": len(report.failed),
        "model": llm.model_name,
    }


def _root_for(graph: KnowledgeGraph) -> Path:
    """Re-acquire the snapshot's working tree for source-level context."""
    repo_url = graph.snapshot.repo_url
    parsed = urlparse(repo_url)
    if parsed.scheme == "file":
        root = Path(unquote(parsed.path))
        if not root.is_dir():
            raise IngestionError(f"analysed tree {root} no longer exists")
        return root
    return ingest(repo_url).root