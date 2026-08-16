"""The five operational properties a public beta needs.

Each of these was previously "documented as a gap" rather than "true". They
are tested here for the same reason the limits are tested in test_limits.py:
an operational property nobody exercises is one that quietly stops holding.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.api.admission as admission
import app.api.routes as routes
import app.api.semantic_routes as semantic
from app.core import jobs
from app.core.budget import CallBudget
from app.core.clients import parse_networks, resolve_client
from app.core.config import settings
from app.core.graph_cache import cache as graph_cache
from app.core.job_store import JobStore
from app.graph.store import SQLiteGraphStore
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parent.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CODELENS_ALLOW_LOCAL_ANALYSIS", "1")
    store = SQLiteGraphStore(tmp_path / "api.db")
    monkeypatch.setattr(routes, "_STORE", store)
    # The lifespan opens a JobStore at SQLITE_PATH; point it at the temporary
    # directory so a test run never writes to the real data volume.
    monkeypatch.setattr(settings, "SQLITE_PATH", tmp_path / "jobs.db")
    graph_cache.clear()
    jobs.registry.reset()
    admission.reset()
    # A routable peer address. Starlette's default is the literal string
    # "testclient", which is not an IP and so matches no CIDR — the blocklist
    # tests would silently pass through rather than test anything.
    with TestClient(app, client=("203.0.113.5", 44444)) as test_client:
        yield test_client
    store.close()


# ── 1. abuse response: whose identity is counted ──────────────────────────


def test_a_forwarded_header_from_an_untrusted_peer_is_ignored() -> None:
    """The bug this closes: identity used to be the header, taken on faith.

    A caller who can write `X-Forwarded-For` can write a different value on
    every request, which mints an unlimited number of fresh quotas and makes
    the rate limit decorative. Only the socket peer is unforgeable.
    """
    identity, trusted = resolve_client("203.0.113.9", "1.2.3.4", parse_networks(""))

    assert identity == "203.0.113.9"  # the peer, not the claim
    assert trusted is False


def test_a_forwarded_header_from_a_trusted_proxy_is_believed() -> None:
    """…and it has to be believed for a real deployment to work at all: behind
    a proxy every client shares the proxy's address, so without this the whole
    internet is one bucket."""
    trusted_proxies = parse_networks("10.0.0.0/8, 172.16.0.0/12")
    identity, trusted = resolve_client("172.18.0.4", "198.51.100.7, 172.18.0.4", trusted_proxies)

    assert identity == "198.51.100.7"  # the left-most entry: the real client
    assert trusted is True


def test_a_garbage_forwarded_value_falls_back_to_the_peer() -> None:
    trusted_proxies = parse_networks("10.0.0.0/8")
    identity, trusted = resolve_client("10.1.2.3", "not-an-address", trusted_proxies)

    assert identity == "10.1.2.3"
    assert trusted is False


def test_unparseable_configuration_is_dropped_not_fatal() -> None:
    """A typo in an environment variable must not be an outage. It must also
    not be silent, which is why `parse_networks` logs — but the process
    starts."""
    assert parse_networks("10.0.0.0/8, nonsense, ,192.168.0.0/16") == parse_networks(
        "10.0.0.0/8,192.168.0.0/16"
    )


def test_a_blocked_client_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operational lever: one config value, no admin UI, no database."""
    monkeypatch.setattr(admission, "BLOCKED", parse_networks("0.0.0.0/0"))

    response = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})

    assert response.status_code == 403


def test_blocking_cannot_be_escaped_with_a_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Blocking matches the peer as well as the resolved identity, so a
    blocked address cannot walk around it by claiming to be someone else."""
    monkeypatch.setattr(admission, "BLOCKED", parse_networks("0.0.0.0/0"))
    monkeypatch.setattr(admission, "TRUSTED_PROXIES", parse_networks("0.0.0.0/0"))

    response = client.post(
        "/api/analyze",
        json={"source": str(TINY_PYTHON)},
        headers={"X-Forwarded-For": "8.8.8.8"},
    )

    assert response.status_code == 403


# ── 2. narration is off, and the graph does not care ──────────────────────


def test_narration_defaults_to_off() -> None:
    """The safest beta default, asserted rather than assumed."""
    assert settings.NARRATION_ENABLED is False


def test_the_budget_refuses_past_its_cap() -> None:
    """The per-client rate limit bounds requests. This bounds the bill: twenty
    clients each inside their quota still add up to an invoice."""
    budget = CallBudget(limit=2)

    assert budget.allow()
    assert budget.allow()
    assert not budget.allow()
    assert budget.used == 2


def test_an_exhausted_budget_stops_narration_not_analysis(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "NARRATION_ENABLED", True)
    monkeypatch.setattr(semantic, "budget", CallBudget(limit=0))

    accepted = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})
    assert accepted.status_code == 202  # analysis is unaffected

    deadline = time.monotonic() + 20
    snapshot_id = None
    while time.monotonic() < deadline:
        body = client.get(f"/api/analyze/{accepted.json()['job_id']}").json()
        if body["status"] == "done":
            snapshot_id = body["snapshot_id"]
            break
        time.sleep(0.2)
    assert snapshot_id is not None

    narrated = client.post(f"/api/repos/{snapshot_id}/answers/project")
    assert narrated.status_code == 503
    assert "budget" in narrated.json()["detail"].lower()


# ── 5. restart durability ─────────────────────────────────────────────────


def test_a_job_survives_the_process_that_created_it(tmp_path: Path) -> None:
    """The 404 this closes: a restart dropped every in-flight job, and the
    browser polling for one got "no such job" — indistinguishable from a
    typo."""
    database = tmp_path / "jobs.db"
    first = jobs.JobRegistry(store=JobStore(database))
    job = first.create(key="https://github.com/o/r")
    first.update(job.id, status="running")

    # A new process: new registry, same database, nothing in memory.
    second = jobs.JobRegistry()
    second.attach(JobStore(database))

    recovered = second.get(job.id)
    assert recovered is not None
    assert recovered.status == "interrupted"
    assert "restart" in (recovered.error or "").lower()


def test_a_finished_job_is_not_rewritten_by_the_sweep(tmp_path: Path) -> None:
    """Only `pending` and `running` are orphaned by a restart. A completed
    analysis is a real result and must survive one unchanged."""
    database = tmp_path / "jobs.db"
    first = jobs.JobRegistry(store=JobStore(database))
    job = first.create(key="k")
    first.update(job.id, status="done", result={"snapshot_id": 7})

    second = jobs.JobRegistry()
    second.attach(JobStore(database))

    recovered = second.get(job.id)
    assert recovered is not None
    assert recovered.status == "done"
    assert recovered.result == {"snapshot_id": 7}


def test_the_job_table_does_not_grow_without_bound(tmp_path: Path) -> None:
    """Persistence must not turn a bounded in-memory table into an unbounded
    on-disk one."""
    store = JobStore(tmp_path / "jobs.db")
    registry = jobs.JobRegistry(max_finished=5, store=store)

    for index in range(40):
        job = registry.create(key=f"k{index}")
        registry.update(job.id, status="done")

    rows = store._connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    assert rows <= 10  # the cap, plus the in-flight row created last


def test_a_persistence_failure_never_fails_an_analysis(tmp_path: Path) -> None:
    """Durability is an improvement, not a dependency. A read-only or full
    disk must degrade to today's behaviour — jobs in memory — rather than
    taking analysis down with it."""
    store = JobStore(tmp_path / "jobs.db")
    store.close()  # every later write now raises sqlite3.ProgrammingError
    registry = jobs.JobRegistry(store=store)

    job = registry.create(key="k")  # must not raise
    registry.update(job.id, status="done")

    assert registry.get(job.id).status == "done"  # served from memory


def test_an_interrupted_job_is_reported_over_http(
    client: TestClient, tmp_path: Path
) -> None:
    """What the browser actually receives: a real status with a real message,
    not a 404."""
    database = tmp_path / "jobs.db"
    store = JobStore(database)
    orphan = jobs.JobRegistry(store=store)
    job = orphan.create(key="https://github.com/o/r")
    orphan.update(job.id, status="running")

    jobs.registry.attach(JobStore(database))  # simulates this process starting
    response = client.get(f"/api/analyze/{job.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "interrupted"
    assert "restart" in response.json()["error"].lower()
