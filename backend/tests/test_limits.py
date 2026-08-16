"""The ceilings that make this API safe to point at the internet.

A limit nobody exercises is a limit that quietly stops working — and the way
it stops working is that everything looks fine right up until a stranger's
loop fills the disk. Each of these pins one failure the limits exist to
prevent, and the HTTP tests check the *contract* a client sees, not just that
a request was refused: a 429 with no `Retry-After` is a dead end.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core import jobs
from app.core.graph_cache import cache as graph_cache
from app.core.limits import (
    ConcurrencyGate,
    RateLimiter,
    directory_size_mb,
    reclaim_clone_cache,
)
from app.graph.store import SQLiteGraphStore
from app.main import app

BACKEND_DIR = Path(__file__).resolve().parent.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CODELENS_ALLOW_LOCAL_ANALYSIS", "1")
    store = SQLiteGraphStore(tmp_path / "api.db")
    monkeypatch.setattr(routes, "_STORE", store)
    graph_cache.clear()
    jobs.registry.reset()
    routes.rate_limiter.reset()
    with TestClient(app) as test_client:
        yield test_client
    store.close()


# ── rate limiting ─────────────────────────────────────────────────────────


def test_the_quota_refuses_the_next_one_and_says_when() -> None:
    limiter = RateLimiter(limit=2, window_seconds=60)
    assert limiter.check("a") is None
    assert limiter.check("a") is None

    wait = limiter.check("a")
    assert wait is not None
    assert 0 < wait <= 60  # a number the caller can act on, not just "no"


def test_a_refused_attempt_does_not_extend_the_lockout() -> None:
    """Otherwise a client that retries can never get back in.

    Recording refusals would let each retry push the window forward, so a
    polling client would be locked out permanently by its own politeness.
    The limit is a ceiling on work done, not a punishment for asking.
    """
    limiter = RateLimiter(limit=1, window_seconds=10)
    start = time.monotonic()
    assert limiter.check("a", now=start) is None

    for _ in range(5):
        assert limiter.check("a", now=start + 1) is not None

    # The original hit still expires on schedule, unmoved by the retries.
    assert limiter.check("a", now=start + 11) is None


def test_a_limit_of_zero_refuses_instead_of_crashing() -> None:
    """"Analysis is off" is a real configuration, not a misconfiguration.

    With no hits recorded there is no oldest one to expire, and asking the
    empty deque when a slot frees raised IndexError — turning a refusal into
    a 500 for anyone who set the limit to zero.
    """
    limiter = RateLimiter(limit=0, window_seconds=60)
    wait = limiter.check("a")
    assert wait == 60


def test_clients_do_not_share_a_bucket() -> None:
    limiter = RateLimiter(limit=1, window_seconds=60)
    assert limiter.check("a") is None
    assert limiter.check("b") is None  # b is not punished for a


def test_analyze_returns_429_with_retry_after(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "rate_limiter", RateLimiter(limit=1, window_seconds=60))

    first = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})
    assert first.status_code == 202

    # A *different* source: the same one would be deduped into the first job
    # rather than reaching the limiter at all.
    second = client.post("/api/analyze", json={"source": str(BACKEND_DIR / "fixtures")})
    assert second.status_code == 429
    assert "Retry-After" in second.headers
    assert "5 analyses" in second.json()["detail"] or "Rate limit" in second.json()["detail"]


def test_asking_twice_for_the_same_repo_costs_one_analysis(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dedupe runs before the quota, so a double-click is not charged twice.

    Charging it would penalise exactly the accident the dedupe was added to
    absorb, and the client gets the same job id either way.
    """
    monkeypatch.setattr(routes, "rate_limiter", RateLimiter(limit=1, window_seconds=60))

    first = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})
    second = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]


# ── concurrency ───────────────────────────────────────────────────────────


def test_the_gate_refuses_rather_than_queues() -> None:
    gate = ConcurrencyGate(limit=2)
    assert gate.try_acquire()
    assert gate.try_acquire()
    assert not gate.try_acquire()

    gate.release()
    assert gate.try_acquire()


def test_a_double_release_cannot_raise_the_cap() -> None:
    """A leak here only shows up under the load the cap exists for."""
    gate = ConcurrencyGate(limit=1)
    assert gate.try_acquire()
    gate.release()
    gate.release()
    gate.release()

    assert gate.active == 0
    assert gate.try_acquire()
    assert not gate.try_acquire()  # still a cap of one


def test_analyze_returns_503_when_saturated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    saturated = ConcurrencyGate(limit=0)
    monkeypatch.setattr(routes, "analysis_gate", saturated)

    response = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})
    assert response.status_code == 503
    assert response.headers.get("Retry-After")


def test_being_refused_for_a_busy_server_costs_no_quota(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise a saturated machine spends the quota of everyone who arrives
    while it is saturated — clients punished for the server's state.

    Found by exercising both limits against the running container: two
    requests refused with 503 had silently consumed two of five analyses.
    """
    limiter = RateLimiter(limit=2, window_seconds=600)
    monkeypatch.setattr(routes, "rate_limiter", limiter)
    monkeypatch.setattr(routes, "analysis_gate", ConcurrencyGate(limit=0))

    for _ in range(5):
        assert client.post("/api/analyze", json={"source": str(TINY_PYTHON)}).status_code == 503

    # Room again: the full quota is intact, none of it spent on the refusals.
    monkeypatch.setattr(routes, "analysis_gate", ConcurrencyGate(limit=4))
    assert client.post("/api/analyze", json={"source": str(TINY_PYTHON)}).status_code == 202


def test_a_rate_limited_request_does_not_hold_a_slot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capacity is taken before quota is checked, so the slot has to come back
    when the quota refuses — otherwise every 429 permanently shrinks the pool
    and the service ends up "busy" with nothing running."""
    gate = ConcurrencyGate(limit=2)
    monkeypatch.setattr(routes, "analysis_gate", gate)
    monkeypatch.setattr(routes, "rate_limiter", RateLimiter(limit=0, window_seconds=600))

    assert client.post("/api/analyze", json={"source": str(TINY_PYTHON)}).status_code == 429
    assert gate.active == 0


# ── the clone cache ───────────────────────────────────────────────────────


def _clone(root: Path, name: str, mb: int, *, age_seconds: float = 0) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "blob").write_bytes(b"\0" * (mb * 1024 * 1024))
    if age_seconds:
        stamp = time.time() - age_seconds
        import os

        os.utime(directory, (stamp, stamp))
    return directory


def test_reclaim_evicts_oldest_first_until_it_fits(tmp_path: Path) -> None:
    root = tmp_path / "clones"
    _clone(root, "ancient", 3, age_seconds=9000)
    _clone(root, "older", 3, age_seconds=3000)
    fresh = _clone(root, "fresh", 3)

    removed = reclaim_clone_cache(root, max_mb=4)

    assert removed == ["ancient", "older"]
    assert fresh.exists()
    assert directory_size_mb(root) <= 4


def test_reclaim_never_deletes_a_tree_being_parsed(tmp_path: Path) -> None:
    """Deleting the working tree of a running pipeline would reproduce, on
    purpose, the race that job dedupe and the source lock were added to fix:
    one process removing the directory another is mid-parse."""
    root = tmp_path / "clones"
    in_use = _clone(root, "in-use", 5, age_seconds=9000)  # oldest, so first in line
    _clone(root, "idle", 5)

    removed = reclaim_clone_cache(root, max_mb=4, keep={"in-use"})

    assert in_use.exists()
    assert removed == ["idle"]


def test_reclaim_is_a_no_op_under_the_ceiling(tmp_path: Path) -> None:
    root = tmp_path / "clones"
    _clone(root, "small", 1)
    assert reclaim_clone_cache(root, max_mb=100) == []
    assert reclaim_clone_cache(tmp_path / "missing", max_mb=1) == []


# ── the job table ─────────────────────────────────────────────────────────


def test_finished_jobs_are_evicted_but_running_ones_are_never_dropped() -> None:
    """The table only ever grew, which on a long-lived process is a slow leak
    that looks like nothing until it looks like an OOM. But a pending job is
    what a client is actively polling for: dropping it would answer a
    legitimate poll with 404 while the work carried on invisibly.
    """
    registry = jobs.JobRegistry(max_finished=3)
    live = registry.create(key="still-going")

    for index in range(10):
        job = registry.create(key=f"done-{index}")
        registry.update(job.id, status="done")

    registry.create(key="trigger-eviction")

    finished = [j for j in registry._jobs.values() if j.status == "done"]
    assert len(finished) <= 3
    assert registry.get(live.id) is not None
    assert registry.active_keys() >= {"still-going"}
