"""The HTTP surface — thin routes, no logic (ARCHITECTURE.md: api/ is thin).

Every endpoint is a straight line to a system that already exists and is
already tested: pipeline, store, query registry, viewspec compiler. If a
route grows an if-tree, the logic belongs in the system it fronts.

Trust boundary (CP-1.1): the analyze endpoint accepts repository *URLs*.
Local paths reach the ingestion layer only when CODELENS_ALLOW_LOCAL_ANALYSIS
is set — a dev/dogfood switch, never a production default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.pipeline import run_pipeline
from app.graph.store import SQLiteGraphStore
from app.graph.traversal import GraphView
from app.ingestion import IngestionError, looks_like_remote
from app.ingestion.clone import normalize_repo_url
from app.queries import QueryError, registered_queries, run_query
from app.views.viewspec import compile_viewspec

router = APIRouter(prefix="/api", tags=["CodeLens"])


def get_store() -> SQLiteGraphStore:
    """One store per process; the file is the durable thing."""
    global _STORE
    if _STORE is None:
        _STORE = SQLiteGraphStore(settings.SQLITE_PATH)
    return _STORE


_STORE: SQLiteGraphStore | None = None


class AnalyzeRequest(BaseModel):
    source: str = Field(min_length=1, description="https GitHub URL (owner/repo)")


class AnalyzeResponse(BaseModel):
    snapshot_id: int
    repo_url: str
    commit_sha: str
    skipped: bool
    stages: list[dict[str, Any]]
    nodes: int
    edges: int


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
    source: str | Path = request.source.strip()
    if not looks_like_remote(str(source)):
        if os.environ.get("CODELENS_ALLOW_LOCAL_ANALYSIS") != "1":
            raise HTTPException(
                status_code=400,
                detail="Only repository URLs are accepted (local analysis is disabled).",
            )
        source = Path(str(source))
    else:
        try:
            source = normalize_repo_url(str(source))
        except IngestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = run_pipeline(source, get_store(), max_size_mb=settings.MAX_REPO_SIZE_MB)
    except IngestionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return AnalyzeResponse(
        snapshot_id=result.snapshot_id,
        repo_url=result.graph.snapshot.repo_url,
        commit_sha=result.graph.snapshot.commit_sha,
        skipped=result.skipped,
        stages=[
            {"stage": stage.value, "seconds": round(seconds, 3), "skipped": skipped}
            for stage, seconds, skipped in result.stages
        ],
        nodes=len(result.graph.nodes),
        edges=len(result.graph.edges),
    )


@router.get("/repos")
def list_repos() -> list[dict[str, Any]]:
    return get_store().list_snapshots()


@router.get("/repos/{snapshot_id}/viewspec")
def viewspec(snapshot_id: int, zoom: int = 2) -> dict[str, Any]:
    graph = get_store().load_graph_by_id(snapshot_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"no snapshot {snapshot_id}")
    try:
        return compile_viewspec(graph, zoom=zoom).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class QueryRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/repos/{snapshot_id}/query/{name}")
def query(snapshot_id: int, name: str, request: QueryRequest) -> dict[str, Any]:
    graph = get_store().load_graph_by_id(snapshot_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"no snapshot {snapshot_id}")
    try:
        result = run_query(name, GraphView(graph), **request.params)
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TypeError as exc:  # wrong/missing params for the plan
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.model_dump()


@router.get("/queries")
def queries() -> list[str]:
    return registered_queries()
