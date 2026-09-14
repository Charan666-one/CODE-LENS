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

from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.api import admission
from app.core.budget import budget
from app.core.config import settings
from app.core.graph_cache import cache
from app.graph.schema import KnowledgeGraph
from app.ingestion import IngestionError, ingest
from app.queries import QueryError, run_query
from app.semantic import (
    ConceptIndex,
    LLMClient,
    LLMError,
    build_llm,
    learning_path,
    narrate_blast_radius,
    narrate_project,
    summarize_graph,
)

router = APIRouter(prefix="/api/repos/{snapshot_id}", tags=["Semantic"])

#: Per-snapshot concept indexes, built on first search. Process-local cache —
#: rebuilt cheaply after a restart, invalidated by snapshot id.
#:
#: Bounded, because it was not. Every snapshot ever searched left an index in
#: memory for the life of the process, and on a public instance the number of
#: snapshots is chosen by strangers. Two matches graph_cache._MAX_CACHED: an
#: index whose graph has already been evicted is about to be rebuilt anyway.
_MAX_INDEXES = 2
_INDEXES: OrderedDict[int, ConceptIndex] = OrderedDict()


def _remember_index(snapshot_id: int, index: ConceptIndex) -> None:
    _INDEXES[snapshot_id] = index
    _INDEXES.move_to_end(snapshot_id)
    while len(_INDEXES) > _MAX_INDEXES:
        _INDEXES.popitem(last=False)


def get_llm() -> LLMClient:
    """The narration model. Overridden in tests; 503s cleanly when unavailable.

    Three ways this legitimately says no, and all three are a 503 with a
    readable reason rather than an error:

    * narration is switched off (the default),
    * no provider is configured,
    * the process has spent its call budget.

    A 503 here never degrades CodeLens itself. The graph, every query and
    every number in the Ledger are deterministic; only the prose is gated.
    """
    if not settings.NARRATION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "Narration is disabled on this instance. Every graph, query "
                "and metric is available without it."
            ),
        )
    if not budget.allow():
        raise HTTPException(
            status_code=503,
            detail="Narration budget for this instance has been exhausted.",
            headers={"Retry-After": "3600"},
        )
    try:
        return build_llm()
    except LLMError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _view(snapshot_id: int):
    """The cached traversal view; the graph was already loaded by _load."""
    from app.api.routes import get_store

    view = cache.view(get_store(), snapshot_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"no snapshot {snapshot_id}")
    return view


def _load(snapshot_id: int) -> KnowledgeGraph:
    from app.api.routes import get_store

    graph = cache.graph(get_store(), snapshot_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"no snapshot {snapshot_id}")
    return graph


# ── deterministic answers ─────────────────────────────────────────────────


class SearchRequest(BaseModel):
    # Ceilings on both. `text` was unbounded, so a multi-megabyte query string
    # was embedded and scored against every node; `top` was unbounded, so one
    # request could ask for a response containing the entire graph. Neither
    # bound constrains a real search — nobody reads 200 results.
    text: str = Field(min_length=1, max_length=1_000)
    top: int = Field(default=10, ge=1, le=200)


@router.post("/search")
def concept_search(snapshot_id: int, request: SearchRequest) -> dict[str, Any]:
    graph = _load(snapshot_id)
    index = _INDEXES.get(snapshot_id)
    if index is None:
        index = ConceptIndex()
        index.build(_view(snapshot_id), graph.annotations)
    _remember_index(snapshot_id, index)
    result = run_query(
        "concept_search", _view(snapshot_id), index=index, text=request.text, top=request.top
    )
    return result.model_dump()


@router.get("/answers/learning_path")
def answer_learning_path(snapshot_id: int) -> dict[str, Any]:
    graph = _load(snapshot_id)
    return learning_path(_view(snapshot_id), graph.annotations).model_dump()


# ── LLM-narrated answers ──────────────────────────────────────────────────


@router.post("/answers/project")
def answer_project(snapshot_id: int, http_request: Request) -> dict[str, Any]:
    admission.charge_narration(http_request)
    graph = _load(snapshot_id)
    llm = get_llm()
    try:
        return narrate_project(_view(snapshot_id), graph.annotations, llm).model_dump()
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class BlastStoryRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=512)
    max_depth: int | None = Field(default=None, ge=1, le=32)


@router.post("/answers/blast_radius")
def answer_blast_radius(
    snapshot_id: int, request: BlastStoryRequest, http_request: Request
) -> dict[str, Any]:
    admission.charge_narration(http_request)
    graph = _load(snapshot_id)
    view = _view(snapshot_id)
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
    # Each node summarised is an LLM call against the operator's key. Left
    # unbounded, one request could spend an unlimited amount of someone
    # else's money.
    max_nodes: int | None = Field(default=None, ge=1, le=500)


@router.post("/summarize")
def summarize(
    snapshot_id: int, request: SummarizeRequest, http_request: Request
) -> dict[str, Any]:
    """Summarise a snapshot's modules with the configured LLM.

    **Admission-controlled, and this is the fix for the worst finding in the
    audit.** `_root_for` re-acquires the working tree, which for a remote
    snapshot means a second full clone of the repository — and this route went
    through none of the ceilings `/analyze` enforces. Snapshot ids are small
    integers and `GET /repos` lists them, so anyone could loop this endpoint
    and clone without limit while `/analyze` returned 429.

    It is charged the same quota as an analysis because that is what it costs:
    a clone, plus one LLM call per summarised node against the operator's key.
    """
    from app.api.routes import get_store

    # The slot is held for the whole call, not just the acquisition: the
    # clone and every LLM call happen inside it, which is the work the
    # concurrency cap exists to bound.
    with admission.slot(http_request):
        graph = _load(snapshot_id)
        llm = get_llm()
        try:
            root = _root_for(graph)
        except IngestionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        annotations, report = summarize_graph(
            _view(snapshot_id),
            root,
            get_store(),
            snapshot_id,
            llm,
            max_nodes=request.max_nodes,
        )
        # New annotations change the graph a cached view was built from.
        cache.invalidate(snapshot_id)
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