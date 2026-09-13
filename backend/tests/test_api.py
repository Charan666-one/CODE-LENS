"""The API layer — thin routes over tested systems, end to end."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.api.admission as admission
import app.api.routes as routes
from app.core import jobs
from app.core.config import settings
from app.core.graph_cache import cache as graph_cache
from app.graph.store import SQLiteGraphStore
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parent.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Each test gets a fresh store file and local analysis enabled."""
    monkeypatch.setenv("CODELENS_ALLOW_LOCAL_ANALYSIS", "1")
    store = SQLiteGraphStore(tmp_path / "api.db")
    monkeypatch.setattr(routes, "_STORE", store)
    # The lifespan opens a JobStore at SQLITE_PATH. Without this a test run
    # writes job rows into the real data volume, and jobs persisted by one
    # test are visible to the next through the registry's database fallback.
    monkeypatch.setattr(settings, "SQLITE_PATH", tmp_path / "jobs.db")
    graph_cache.clear()  # snapshot ids restart per test; never serve a stale graph
    # Each test gets its own store, so a job left in flight by a previous one
    # must not be handed back by the analyze dedupe — it would run against a
    # database this fixture has since closed.
    jobs.registry.reset()
    # Limits are per process, and the whole suite is one process behind one
    # client address — without this the sixth test to analyse anything would
    # be rate-limited by the fifth. The limits themselves are exercised
    # deliberately in test_limits.py.
    admission.reset()
    with TestClient(app) as test_client:
        yield test_client
    jobs.registry.drain()  # never close a store under a running job
    store.close()


def analyze_and_wait(client: TestClient, source: str, timeout: float = 10.0) -> dict[str, Any]:
    """POST /api/analyze returns a job id in milliseconds, always — this
    polls the status endpoint to completion, exactly as the frontend does."""
    accepted = client.post("/api/analyze", json={"source": source})
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/analyze/{job_id}")
        assert status.status_code == 200
        body = status.json()
        if body["status"] == "done":
            return body
        if body["status"] == "error":
            raise AssertionError(f"analyze job failed: {body['error']}")
        time.sleep(0.02)
    raise AssertionError(f"analyze job {job_id} did not finish within {timeout}s")


def analyze_fixture(client: TestClient) -> int:
    return analyze_and_wait(client, str(TINY_PYTHON))["snapshot_id"]


def test_analyze_returns_a_job_id_immediately(client: TestClient) -> None:
    """The whole point: this must never block on the pipeline, regardless of
    repo size — a large monorepo must return exactly as fast as a tiny one."""
    started = time.monotonic()
    response = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})
    elapsed = time.monotonic() - started
    assert response.status_code == 202
    assert "job_id" in response.json()
    assert response.json()["status"] == "pending"
    assert elapsed < 1.0


def test_unknown_job_id_is_404(client: TestClient) -> None:
    assert client.get("/api/analyze/does-not-exist").status_code == 404


def test_analyze_runs_the_real_pipeline(client: TestClient) -> None:
    body = analyze_and_wait(client, str(TINY_PYTHON))
    assert body["nodes"] > 0 and body["edges"] > 0
    assert [s["stage"] for s in body["stages"]] == [
        "cloned",
        "parsed",
        "metrics",
        "graph_built",
    ]

    # Second run: the digest skip must surface through the API too.
    again = analyze_and_wait(client, str(TINY_PYTHON))
    assert again["skipped"] is True
    assert again["snapshot_id"] == body["snapshot_id"]


def test_analyze_job_error_is_reported_not_swallowed(client: TestClient) -> None:
    """A background failure (here: an oversized-looking clone target that
    doesn't exist) must surface as status=error with a real message, not a
    silently stuck job or an unhandled crash."""
    accepted = client.post(
        "/api/analyze", json={"source": "https://github.com/psf/no-such-repo-xyz-123"}
    )
    job_id = accepted.json()["job_id"]

    deadline = time.monotonic() + 15.0
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/analyze/{job_id}").json()
        if body["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert body["status"] == "error"
    assert body["error"]


def test_analyze_rejects_local_paths_unless_enabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CP-1.1 trust boundary, enforced at the HTTP door."""
    monkeypatch.delenv("CODELENS_ALLOW_LOCAL_ANALYSIS")
    response = client.post("/api/analyze", json={"source": "/etc"})
    assert response.status_code == 400
    assert "local analysis is disabled" in response.json()["detail"]


def test_analyze_rejects_hostile_urls(client: TestClient) -> None:
    response = client.post("/api/analyze", json={"source": "ext::sh -c 'id'"})
    assert response.status_code == 400


def test_viewspec_endpoint_serves_all_zooms(client: TestClient) -> None:
    snapshot_id = analyze_fixture(client)
    sizes = []
    for zoom in (1, 2, 3):
        response = client.get(f"/api/repos/{snapshot_id}/viewspec", params={"zoom": zoom})
        assert response.status_code == 200
        body = response.json()
        assert body["zoom"] == zoom
        sizes.append(len(body["nodes"]))
    assert sizes[0] < sizes[1] < sizes[2]  # semantic zoom over the wire


def test_viewspec_rejects_bad_zoom_and_missing_snapshot(client: TestClient) -> None:
    snapshot_id = analyze_fixture(client)
    assert client.get(f"/api/repos/{snapshot_id}/viewspec", params={"zoom": 9}).status_code == 400
    assert client.get("/api/repos/999/viewspec").status_code == 404


def test_query_endpoint_runs_registered_plans(client: TestClient) -> None:
    snapshot_id = analyze_fixture(client)
    response = client.post(
        f"/api/repos/{snapshot_id}/query/blast_radius",
        json={"params": {"node_id": "function:calculator.add"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["total_affected"] == 5
    assert "function:main.main" in body["paths"]


def test_query_endpoint_surfaces_query_errors(client: TestClient) -> None:
    snapshot_id = analyze_fixture(client)
    response = client.post(
        f"/api/repos/{snapshot_id}/query/blast_radius",
        json={"params": {"node_id": "function:ghost.f"}},
    )
    assert response.status_code == 400
    assert "unknown node" in response.json()["detail"]


def test_queries_listing(client: TestClient) -> None:
    listed = client.get("/api/queries").json()
    assert "blast_radius" in listed and "centrality" in listed


def test_repos_listing(client: TestClient) -> None:
    snapshot_id = analyze_fixture(client)
    listed = client.get("/api/repos").json()
    assert any(row["snapshot_id"] == snapshot_id for row in listed)
