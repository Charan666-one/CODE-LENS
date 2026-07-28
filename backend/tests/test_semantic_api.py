"""Semantic endpoints — the split between free facts and keyed prose.

Deterministic endpoints must work with no key; narrated ones must 503
cleanly without one and work end-to-end with the injected fake.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.routes as routes
import app.api.semantic_routes as semantic
from app.core.graph_cache import cache as graph_cache
from app.graph.store import SQLiteGraphStore
from app.main import app
from app.semantic import CountingFakeLLM

BACKEND_DIR = Path(__file__).resolve().parent.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CODELENS_ALLOW_LOCAL_ANALYSIS", "1")
    store = SQLiteGraphStore(tmp_path / "api.db")
    monkeypatch.setattr(routes, "_STORE", store)
    graph_cache.clear()  # snapshot ids restart per test; never serve a stale graph
    semantic._INDEXES.clear()
    with TestClient(app) as test_client:
        yield test_client
    store.close()


@pytest.fixture()
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> CountingFakeLLM:
    fake = CountingFakeLLM()
    monkeypatch.setattr(semantic, "get_llm", lambda: fake)
    return fake


def analyze_fixture(client: TestClient) -> int:
    """Analyze is asynchronous (returns a job id in ms); poll to completion."""
    accepted = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        status = client.get(f"/api/analyze/{job_id}")
        body = status.json()
        if body["status"] == "done":
            return body["snapshot_id"]
        if body["status"] == "error":
            raise AssertionError(f"analyze job failed: {body['error']}")
        time.sleep(0.02)
    raise AssertionError(f"analyze job {job_id} did not finish within 10s")


# ── deterministic: no key required, ever ──────────────────────────────────


def test_learning_path_needs_no_llm(client: TestClient) -> None:
    snapshot_id = analyze_fixture(client)
    response = client.get(f"/api/repos/{snapshot_id}/answers/learning_path")
    assert response.status_code == 200
    body = response.json()
    assert body["model"] is None  # deterministic by design
    assert body["evidence_ids"][0] == "function:main.main"  # the entrypoint leads


def test_concept_search_needs_no_llm(client: TestClient) -> None:
    snapshot_id = analyze_fixture(client)
    response = client.post(
        f"/api/repos/{snapshot_id}/search", json={"text": "area rectangle", "top": 5}
    )
    assert response.status_code == 200
    top_ids = [entry["node_id"] for entry in response.json()["ranked"]]
    assert any("area" in node_id or "Rectangle" in node_id for node_id in top_ids)


# ── narrated: honest 503 without a key ────────────────────────────────────


def test_narrated_answers_503_cleanly_without_key(client: TestClient) -> None:
    snapshot_id = analyze_fixture(client)
    response = client.post(f"/api/repos/{snapshot_id}/answers/project")
    assert response.status_code == 503
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_project_story_with_injected_model(
    client: TestClient, fake_llm: CountingFakeLLM
) -> None:
    snapshot_id = analyze_fixture(client)
    response = client.post(f"/api/repos/{snapshot_id}/answers/project")
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "fake-llm"
    assert body["evidence_ids"]
    assert fake_llm.calls == 1


def test_blast_story_carries_facts_alongside_prose(
    client: TestClient, fake_llm: CountingFakeLLM
) -> None:
    snapshot_id = analyze_fixture(client)
    response = client.post(
        f"/api/repos/{snapshot_id}/answers/blast_radius",
        json={"node_id": "function:calculator.add"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["meta"]["total_affected"] == 5  # the facts ride along
    assert "function:main.main" in body["result"]["paths"]
    assert body["model"] == "fake-llm"


def test_blast_story_rejects_unknown_nodes_before_spending_tokens(
    client: TestClient, fake_llm: CountingFakeLLM
) -> None:
    snapshot_id = analyze_fixture(client)
    response = client.post(
        f"/api/repos/{snapshot_id}/answers/blast_radius",
        json={"node_id": "function:ghost.f"},
    )
    assert response.status_code == 400
    assert fake_llm.calls == 0  # the graph said no before the model was asked


# ── summarize: the cache surfaces over HTTP ───────────────────────────────


def test_summarize_pays_once_then_rides_the_cache(
    client: TestClient, fake_llm: CountingFakeLLM
) -> None:
    snapshot_id = analyze_fixture(client)

    first = client.post(f"/api/repos/{snapshot_id}/summarize", json={}).json()
    assert first["llm_calls"] > 0
    assert first["from_cache"] == 0

    second = client.post(f"/api/repos/{snapshot_id}/summarize", json={}).json()
    assert second["llm_calls"] == 0  # zero — the CP-3.2 gate, over HTTP
    assert second["from_cache"] == first["llm_calls"]


def test_summaries_become_searchable(client: TestClient, fake_llm: CountingFakeLLM) -> None:
    """After summarisation the index rebuilds and includes annotation text."""
    snapshot_id = analyze_fixture(client)
    client.post(f"/api/repos/{snapshot_id}/search", json={"text": "warm the index"})
    client.post(f"/api/repos/{snapshot_id}/summarize", json={})

    response = client.post(
        f"/api/repos/{snapshot_id}/search", json={"text": "fake summary", "top": 5}
    )
    assert response.status_code == 200
    assert response.json()["ranked"], "annotation vocabulary must be indexed"
