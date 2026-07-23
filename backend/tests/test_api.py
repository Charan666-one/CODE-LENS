"""The API layer — thin routes over tested systems, end to end."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.routes as routes
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
    with TestClient(app) as test_client:
        yield test_client
    store.close()


def analyze_fixture(client: TestClient) -> int:
    response = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})
    assert response.status_code == 200, response.text
    return response.json()["snapshot_id"]


def test_analyze_runs_the_real_pipeline(client: TestClient) -> None:
    response = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})
    body = response.json()
    assert response.status_code == 200
    assert body["nodes"] > 0 and body["edges"] > 0
    assert [s["stage"] for s in body["stages"]] == [
        "cloned",
        "parsed",
        "metrics",
        "graph_built",
    ]

    # Second run: the digest skip must surface through the API too.
    again = client.post("/api/analyze", json={"source": str(TINY_PYTHON)}).json()
    assert again["skipped"] is True
    assert again["snapshot_id"] == body["snapshot_id"]


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
