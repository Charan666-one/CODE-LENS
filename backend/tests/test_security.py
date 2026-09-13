"""Regression tests for the security audit.

One test per finding, named for the attack rather than the mechanism, so a
future reader can tell what breaks if the test starts failing. Everything
here is synthetic — no intentionally malicious repository is downloaded, and
nothing in this file needs the network.

The URL-validator table in `test_ingestion.py` already covers scheme and host
rejection; this file covers what the audit found *past* that boundary.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.api.admission as admission
import app.api.routes as routes
import app.api.semantic_routes as semantic
from app.core import jobs
from app.core.config import settings
from app.core.graph_cache import cache as graph_cache
from app.core.limits import ConcurrencyGate, RateLimiter
from app.graph.store import SQLiteGraphStore
from app.ingestion.clone import shallow_clone
from app.ingestion.errors import RepoTooLargeError
from app.ingestion.inventory import walk_source_files
from app.main import app
from app.semantic import CountingFakeLLM

BACKEND_DIR = Path(__file__).resolve().parent.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CODELENS_ALLOW_LOCAL_ANALYSIS", "1")
    store = SQLiteGraphStore(tmp_path / "api.db")
    monkeypatch.setattr(routes, "_STORE", store)
    # The lifespan opens a JobStore at SQLITE_PATH. Without this a test run
    # writes job rows into the real data volume, and jobs persisted by one
    # test are visible to the next through the registry's database fallback.
    monkeypatch.setattr(settings, "SQLITE_PATH", tmp_path / "jobs.db")
    graph_cache.clear()
    jobs.registry.reset()
    admission.reset()
    with TestClient(app) as test_client:
        yield test_client
    jobs.registry.drain()  # never close a store under a running job
    store.close()


def _analyse(client: TestClient, source: str = str(TINY_PYTHON)) -> int:
    accepted = client.post("/api/analyze", json={"source": source})
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        body = client.get(f"/api/analyze/{job_id}").json()
        if body["status"] == "done":
            return int(body["snapshot_id"])
        if body["status"] == "error":
            raise AssertionError(body["error"])
        time.sleep(0.2)
    raise AssertionError("analysis did not finish")


# ── CRITICAL: the limit bypass ────────────────────────────────────────────


def test_summarize_cannot_clone_around_the_rate_limit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worst finding in the audit, pinned.

    `/summarize` re-acquires the working tree — for a remote snapshot that is
    a second full clone — and went through none of the ceilings `/analyze`
    enforces. Snapshot ids are small integers and `GET /repos` lists them, so
    anyone could loop this endpoint and clone without limit while `/analyze`
    politely returned 429.
    """
    snapshot_id = _analyse(client)
    monkeypatch.setattr(admission, "rate_limiter", RateLimiter(limit=0, window_seconds=600))

    with patch.object(semantic, "get_llm", lambda: CountingFakeLLM()):
        response = client.post(f"/api/repos/{snapshot_id}/summarize", json={})

    assert response.status_code == 429
    assert response.headers.get("Retry-After")


def test_summarize_is_refused_when_the_machine_is_full(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It costs a clone and a pile of LLM calls, so it takes a slot like any
    other expensive work — and is refused, not queued, when there is none."""
    snapshot_id = _analyse(client)
    monkeypatch.setattr(admission, "analysis_gate", ConcurrencyGate(limit=0))

    with patch.object(semantic, "get_llm", lambda: CountingFakeLLM()):
        response = client.post(f"/api/repos/{snapshot_id}/summarize", json={})

    assert response.status_code == 503


def test_summarize_returns_its_slot(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A slot leaked per call would brick the instance after two summaries."""
    snapshot_id = _analyse(client)
    gate = ConcurrencyGate(limit=2)
    monkeypatch.setattr(admission, "analysis_gate", gate)

    with patch.object(semantic, "get_llm", lambda: CountingFakeLLM()):
        for _ in range(4):
            client.post(f"/api/repos/{snapshot_id}/summarize", json={})

    assert gate.active == 0


def test_narrated_answers_are_metered(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narration spends the operator's API credit one call at a time. Without
    a ceiling, a loop here is someone else's invoice."""
    snapshot_id = _analyse(client)
    monkeypatch.setattr(admission, "llm_limiter", RateLimiter(limit=0, window_seconds=600))

    with patch.object(semantic, "get_llm", lambda: CountingFakeLLM()):
        response = client.post(f"/api/repos/{snapshot_id}/answers/project")

    assert response.status_code == 429


# ── HIGH: resource exhaustion ─────────────────────────────────────────────


class _FakeGrowingClone:
    """A stand-in for git that writes into the destination as it "runs".

    The size watchdog is a loop around a live subprocess, and the only honest
    way to test it is to give it a subprocess that grows. Faking `Popen` keeps
    the real loop — including the kill path — under test with no network.
    """

    def __init__(self, dest: Path, mb_per_tick: int = 8) -> None:
        self.dest = dest
        self.mb_per_tick = mb_per_tick
        self.returncode: int | None = None
        self.killed = False
        self.terminated = False
        self.stdout = None
        self.stderr = None
        self._ticks = 0

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        self._ticks += 1
        self.dest.mkdir(parents=True, exist_ok=True)
        blob = self.dest / f"chunk{self._ticks}.bin"
        blob.write_bytes(b"\0" * (self.mb_per_tick * 1024 * 1024))
        if self._ticks > 20:  # would otherwise never finish
            self.returncode = 0
            return 0
        raise subprocess.TimeoutExpired(cmd="git", timeout=timeout or 0)

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_an_enormous_repository_is_killed_mid_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size ceiling used to be enforced *after* the clone returned, so a
    50 GB repository was downloaded in full and then refused. The limit
    protected the parser while advertising that it protected the disk.
    """
    dest = tmp_path / "huge"
    fake = _FakeGrowingClone(dest, mb_per_tick=8)
    monkeypatch.setattr("app.ingestion.clone._SIZE_POLL_SECONDS", 0.01)
    monkeypatch.setattr("app.ingestion.clone.subprocess.Popen", lambda *a, **k: fake)

    with pytest.raises(RepoTooLargeError):
        shallow_clone(
            "https://github.com/o/r", dest, timeout_seconds=30, max_size_mb=16
        )

    assert fake.terminated or fake.killed  # git was actually stopped
    assert not dest.exists()  # and the partial tree cleaned up


def test_one_enormous_file_cannot_be_read_into_memory(tmp_path: Path) -> None:
    """`walk_source_files` reads every file whole to hash and sniff it, so a
    single generated 2 GB source file is 2 GB of resident memory — inside a
    repository comfortably under the total size limit.

    Skipped, never truncated: half a file parses into a confidently wrong
    graph, which is worse than an absent one.
    """
    (tmp_path / "small.py").write_text("x = 1\n")
    (tmp_path / "huge.py").write_text("y = 2\n" * 300_000)  # ~1.8 MB

    kept = walk_source_files(tmp_path, max_file_bytes=64 * 1024)

    assert [f.path for f in kept] == ["small.py"]


def test_a_repository_of_many_tiny_files_is_capped(tmp_path: Path) -> None:
    """Total size does not bound the file count: half a million tiny files fit
    under any byte limit and each still costs a hash, a parse and a node."""
    for index in range(50):
        (tmp_path / f"f{index}.py").write_text("x = 1\n")

    assert len(walk_source_files(tmp_path, max_files=10)) == 10


def test_an_oversized_request_body_is_refused(client: TestClient) -> None:
    """Starlette buffers a body into memory before any validator runs, and
    nothing capped it."""
    response = client.post("/api/analyze", json={"source": "x" * 200_000})
    assert response.status_code == 413


def test_search_parameters_have_ceilings(client: TestClient) -> None:
    """`top` was unbounded, so one request could ask for the whole graph
    back; `text` was unbounded, so a megabyte string was scored against every
    node."""
    snapshot_id = _analyse(client)
    assert (
        client.post(f"/api/repos/{snapshot_id}/search", json={"text": "x", "top": 10**9})
    ).status_code == 422
    assert (
        client.post(f"/api/repos/{snapshot_id}/search", json={"text": "x" * 50_000})
    ).status_code == 422


def test_summarize_cannot_ask_for_unlimited_llm_calls(client: TestClient) -> None:
    """One node summarised is one call against the operator's key."""
    snapshot_id = _analyse(client)
    response = client.post(f"/api/repos/{snapshot_id}/summarize", json={"max_nodes": 10**9})
    assert response.status_code == 422


# ── the invariant: no repository code is ever executed ────────────────────


def test_analysis_never_executes_repository_code(
    client: TestClient, tmp_path: Path
) -> None:
    """CodeLens parses untrusted source. It must never *run* it.

    The fixture below is what a hostile repository would actually contain:
    the install and build hooks every package manager honours. Nothing in the
    pipeline invokes pip, npm, make or a shell — the only subprocesses are
    `git clone` and `git log`, both with fixed argv — and this asserts the
    marker file none of those hooks got to write.
    """
    marker = tmp_path / "EXECUTED"
    payload = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('pwned')"
    repo = tmp_path / "hostile"
    repo.mkdir()
    (repo / "setup.py").write_text(payload)
    (repo / "conftest.py").write_text(payload)
    (repo / "sitecustomize.py").write_text(payload)
    (repo / "Makefile").write_text(f"all:\n\ttouch {marker}\n")
    (repo / "package.json").write_text(
        '{"name":"x","version":"1.0.0","scripts":'
        '{"preinstall":"touch ' + str(marker) + '","postinstall":"touch ' + str(marker) + '"}}'
    )
    (repo / "app.py").write_text("def hello():\n    return 1\n")

    snapshot_id = _analyse(client, source=str(repo))

    assert not marker.exists(), "repository code was executed during analysis"
    # …and the harmless file was still parsed, so this proves the pipeline ran.
    spec = client.get(f"/api/repos/{snapshot_id}/viewspec", params={"zoom": 2})
    assert spec.status_code == 200


# ── durability ────────────────────────────────────────────────────────────


def test_a_parser_crash_leaves_a_usable_error_and_frees_the_slot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed job must not hold the concurrency slot: two of those and the
    instance is permanently "busy" with nothing running. The client gets a
    real error, and the next analysis still works."""
    gate = ConcurrencyGate(limit=2)
    monkeypatch.setattr(admission, "analysis_gate", gate)
    monkeypatch.setattr(
        routes, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    accepted = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})
    job_id = accepted.json()["job_id"]

    # Two separate waits, because they are two separate events. The worker
    # sets the job's status and *then* releases the slot in its `finally`, so
    # there is a real window where the status is already `error` and the slot
    # has not come back yet. Asserting the slot the instant the status flips
    # made this test fail about half the time — a flake caused by the test
    # assuming an atomicity the code never claimed.
    deadline = time.monotonic() + 10
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/analyze/{job_id}").json()
        if body["status"] == "error":
            break
        time.sleep(0.05)
    assert body["status"] == "error"

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and gate.active != 0:
        time.sleep(0.05)
    assert gate.active == 0


def test_an_internal_failure_does_not_leak_its_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`str(exc)` on an internal error is whatever the raising library felt
    like saying — a container path, a database location, a stack-shaped
    string. The client gets a job id to quote; the detail goes to the log."""
    secret = "/var/secret/path/codelens.db is corrupt"
    monkeypatch.setattr(
        routes, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(secret))
    )

    accepted = client.post("/api/analyze", json={"source": str(TINY_PYTHON)})
    job_id = accepted.json()["job_id"]
    deadline = time.monotonic() + 10
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/analyze/{job_id}").json()
        if body["status"] == "error":
            break
        time.sleep(0.1)

    assert body["status"] == "error"
    assert secret not in body["error"]
    assert job_id in body["error"]  # quotable, so the operator can find the log


def test_an_overrunning_job_frees_its_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A thread cannot be killed, so the timeout does not stop the work — it
    stops the work from mattering. The job reports failure and the slot comes
    back, which is what keeps the service answering."""
    registry = jobs.JobRegistry()
    released: list[bool] = []
    started = __import__("threading").Event()

    def slow() -> None:
        started.set()
        time.sleep(2.0)

    job = registry.create(key="slow")
    registry.run_in_background(
        job.id, slow, timeout_seconds=0.2, on_timeout=lambda: released.append(True)
    )
    started.wait(timeout=5)
    time.sleep(0.6)

    assert registry.get(job.id).status == "error"
    assert "abandoned" in (registry.get(job.id).error or "")
    assert released == [True]


def test_a_missing_snapshot_is_404_not_a_stack_trace(client: TestClient) -> None:
    for path in ("/api/repos/999999/viewspec", "/api/repos/999999/explain?node_id=x"):
        assert client.get(path).status_code == 404
