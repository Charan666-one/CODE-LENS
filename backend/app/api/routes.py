"""The HTTP surface — thin routes, no logic (ARCHITECTURE.md: api/ is thin).

Every endpoint is a straight line to a system that already exists and is
already tested: pipeline, store, query registry, viewspec compiler. If a
route grows an if-tree, the logic belongs in the system it fronts.

Trust boundary (CP-1.1): the analyze endpoint accepts repository *URLs*.
Local paths reach the ingestion layer only when CODELENS_ALLOW_LOCAL_ANALYSIS
is set — a dev/dogfood switch, never a production default.

Analyze is asynchronous (app.core.jobs): POST kicks off the pipeline on a
background thread and returns a job id in milliseconds regardless of repo
size; the client polls GET .../analyze/{job_id}. A large monorepo can take
over a minute to clone and parse, and holding that open as one HTTP request
is fragile in a way no single timeout fixes — a reverse proxy, a browser, or
plain thread contention can each sever a long-lived request differently, so
the same root cause looks like a different bug every time. Returning fast,
always, removes the failure mode instead of chasing its symptoms.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core import jobs
from app.core.config import settings
from app.core.graph_cache import cache
from app.core.pipeline import Stage, run_pipeline
from app.graph.store import SQLiteGraphStore
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


class AnalyzeAccepted(BaseModel):
    job_id: str
    status: jobs.JobStatus = "pending"


class JobStatusResponse(BaseModel):
    job_id: str
    status: jobs.JobStatus
    stages: list[dict[str, Any]] = Field(default_factory=list)
    snapshot_id: int | None = None
    repo_url: str | None = None
    commit_sha: str | None = None
    skipped: bool | None = None
    nodes: int | None = None
    edges: int | None = None
    error: str | None = None


@router.post("/analyze", response_model=AnalyzeAccepted, status_code=202)
def analyze(request: AnalyzeRequest) -> AnalyzeAccepted:
    """Validate and kick off analysis; return a job id immediately.

    Validation (the trust boundary, and a malformed URL) still happens
    synchronously here — those fail fast with a real 400, before any thread
    is spawned. Only the actual clone+parse+store work is backgrounded.
    """
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

    # Asking twice for the same repository is one question, not two. Without
    # this, a double-click starts a second pipeline that races the first over
    # the same clone directory.
    already_running = jobs.registry.find_active(str(source))
    if already_running is not None:
        return AnalyzeAccepted(job_id=already_running.id, status=already_running.status)

    job = jobs.registry.create(key=str(source))

    def work() -> None:
        def on_progress(stage: Stage, seconds: float, skipped: bool) -> None:
            jobs.registry.append_stage(
                job.id, {"stage": stage.value, "seconds": round(seconds, 3), "skipped": skipped}
            )

        result = run_pipeline(
            source,
            get_store(),
            max_size_mb=settings.MAX_REPO_SIZE_MB,
            on_progress=on_progress,
        )
        jobs.registry.update(
            job.id,
            status="done",
            result={
                "snapshot_id": result.snapshot_id,
                "repo_url": result.graph.snapshot.repo_url,
                "commit_sha": result.graph.snapshot.commit_sha,
                "skipped": result.skipped,
                "nodes": len(result.graph.nodes),
                "edges": len(result.graph.edges),
            },
        )

    jobs.registry.run_in_background(job.id, work)
    return AnalyzeAccepted(job_id=job.id)


@router.get("/analyze/{job_id}", response_model=JobStatusResponse)
def analyze_status(job_id: str) -> JobStatusResponse:
    job = jobs.registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no job {job_id}")
    payload: dict[str, Any] = {"job_id": job.id, "status": job.status, "stages": job.stages}
    if job.status == "done" and job.result is not None:
        payload.update(job.result)
    if job.status == "error":
        payload["error"] = job.error
    return JobStatusResponse(**payload)


@router.get("/repos")
def list_repos() -> list[dict[str, Any]]:
    return get_store().list_snapshots()


@router.get("/repos/{snapshot_id}/viewspec")
def viewspec(snapshot_id: int, zoom: int = 2) -> dict[str, Any]:
    if zoom not in (1, 2, 3):
        raise HTTPException(status_code=400, detail=f"zoom must be 1, 2 or 3, got {zoom}")
    graph = cache.graph(get_store(), snapshot_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"no snapshot {snapshot_id}")
    return cache.viewspec(
        snapshot_id, zoom, lambda: compile_viewspec(graph, zoom=zoom).model_dump()
    )


class QueryRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


@router.post("/repos/{snapshot_id}/query/{name}")
def query(snapshot_id: int, name: str, request: QueryRequest) -> dict[str, Any]:
    view = cache.view(get_store(), snapshot_id)
    if view is None:
        raise HTTPException(status_code=404, detail=f"no snapshot {snapshot_id}")
    try:
        result = run_query(name, view, **request.params)
    except QueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TypeError as exc:  # wrong/missing params for the plan
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.model_dump()


@router.get("/repos/{snapshot_id}/explain")
def explain(snapshot_id: int, node_id: str) -> dict[str, Any]:
    """Everything needed to explain one file or folder: what it is, what it
    depends on, what depends on it, and the evidence for each claim.

    Deterministic — no API key, no tokens. Narration (CP-3.4) layers prose on
    top of this payload; it never replaces the facts.
    """
    graph = cache.graph(get_store(), snapshot_id)
    view = cache.view(get_store(), snapshot_id)
    if graph is None or view is None:
        raise HTTPException(status_code=404, detail=f"no snapshot {snapshot_id}")
    try:
        result = run_query("explain", view, node_id=node_id)
    except QueryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = result.model_dump()
    # The summary, when one exists, is the human sentence for this node.
    for annotation in graph.annotations:
        if annotation.node_id == node_id:
            payload["summary"] = {
                "text": annotation.summary,
                "derived_from": annotation.derived_from,
                "model": annotation.model,
            }
            break
    return payload


@router.get("/queries")
def queries() -> list[str]:
    return registered_queries()
