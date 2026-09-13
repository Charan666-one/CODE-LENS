"""In-process analyze job registry.

A large monorepo can legitimately take a minute or more to clone and parse.
Holding that as one synchronous HTTP request is fragile in a way no timeout
tweak fixes for good: Next.js's rewrite proxy aborts at 30s by default, a
browser's own connection handling can give up, and even a busy Python
process can starve health checks — each severs the SAME long-lived request
differently, so the failure looks different every time while the root cause
never changes. The fix is structural: `/api/analyze` returns in milliseconds,
always, and the browser polls a trivial status endpoint instead.

This is deliberately a small in-memory table, not a real queue. CP-6.2 is
where a durable, multi-worker job system (Celery/arq, Postgres-backed)
belongs — introducing it before there is a second worker process would be
exactly the premature weight CP-0.1 removed. A process restart drops
in-flight jobs; that is an accepted MVP trade-off, stated here rather than
hidden.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from app.core.config import settings
from app.core.job_store import JobStore

#: `interrupted` is what a restart leaves behind. It is distinct from `error`
#: on purpose: nothing went wrong with the analysis, the process that was
#: running it stopped existing, and "run it again" is the right advice rather
#: than "something failed".
JobStatus = Literal["pending", "running", "done", "error", "interrupted"]


@dataclass
class Job:
    id: str
    #: What this job is analysing. Two requests for the same thing are the
    #: same job — see `find_active`.
    key: str | None = None
    status: JobStatus = "pending"
    stages: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class JobRegistry:
    """Thread-safe in-memory store of one process's running jobs."""

    def __init__(self, max_finished: int = 200, store: JobStore | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._max_finished = max_finished
        self._store = store
        self._lock = threading.Lock()
        #: Every runner thread still alive, so something can wait for them.
        #: Without this a job is fire-and-forget by construction, and a test
        #: that closed its SQLite store while a runner was mid-query on the
        #: same connection took the whole interpreter down with a segfault.
        self._threads: set[threading.Thread] = set()

    def attach(self, store: JobStore) -> None:
        """Give the registry somewhere durable to write.

        Called at application startup rather than in `__init__` so the module
        stays importable — and fully testable — with no database at all.
        """
        self._store = store
        store.sweep_interrupted()

    def _persist(self, job: Job) -> None:
        if self._store is None:
            return
        self._store.upsert(
            {
                "id": job.id,
                "key": job.key,
                "status": job.status,
                "stages": job.stages,
                "result": job.result,
                "error": job.error,
                "created_at": job.created_at,
            }
        )

    def create(self, key: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex, key=key)
        with self._lock:
            self._jobs[job.id] = job
            self._evict_finished()
        self._persist(job)
        if self._store is not None:
            self._store.prune(self._max_finished)
        return job

    def _evict_finished(self) -> None:
        """Drop the oldest finished jobs past the cap. Caller holds the lock.

        This table only ever grew. A process serving a public URL would hold
        every job it had ever run — each with its stage list and result —
        for as long as it stayed up, which is a slow leak that looks like
        nothing until it looks like an OOM.

        Only `done` and `error` are evictable: a pending or running job is
        the one thing a client is actively polling for, and dropping it would
        answer a legitimate poll with 404 while the work continued invisibly.
        """
        finished = [
            job for job in self._jobs.values() if job.status in ("done", "error")
        ]
        if len(finished) <= self._max_finished:
            return
        finished.sort(key=lambda job: job.created_at)
        for job in finished[: len(finished) - self._max_finished]:
            self._jobs.pop(job.id, None)

    def active_keys(self) -> set[str]:
        """Sources with a job still in flight — what must not be reclaimed."""
        with self._lock:
            return {
                job.key
                for job in self._jobs.values()
                if job.key and job.status in ("pending", "running")
            }

    def find_active(self, key: str) -> Job | None:
        """A pending or running job for the same source, if one exists.

        Double-clicking "Understand" used to start a second pipeline for the
        same repository, and both would then fight over the same clone
        directory — one `rmtree`-ing the tree the other was parsing. The
        observed result was a 172KB half-clone and a job wedged on "running"
        forever. Returning the job already in flight is both the correct
        answer to the question and the fix for the race.
        """
        with self._lock:
            for job in self._jobs.values():
                if job.key == key and job.status in ("pending", "running"):
                    return job
        return None

    def reset(self) -> None:
        """Forget every job. A process has one registry for its lifetime, so
        this exists for tests: each gets a fresh store, and a job left
        running from a previous test would otherwise be handed back by
        `find_active` and then fail against a database that has been closed.
        """
        with self._lock:
            self._jobs.clear()

    def drain(self, timeout: float = 30.0) -> bool:
        """Wait for every background runner to finish. Returns whether it did.

        For tests, and for the same reason `reset` exists: a fixture that
        closes its store must not do so under a runner that is still using
        it. sqlite3 connections are not thread-safe against being closed
        mid-query, and the failure is not an exception but a segmentation
        fault in the C module — the test process simply dies, on whichever
        machine happens to schedule the teardown a few milliseconds early.
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                live = [thread for thread in self._threads if thread.is_alive()]
                self._threads = set(live)
            if not live:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            live[0].join(min(remaining, 0.5))

    def get(self, job_id: str) -> Job | None:
        """The job, from memory or from the database.

        The database fallback is what turns a restart from a 404 into an
        answer: the row survives, says `interrupted`, and the client learns
        something true instead of "no such job".
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            return job
        if self._store is None:
            return None
        record = self._store.get(job_id)
        if record is None:
            return None
        return Job(
            id=record["id"],
            key=record["key"],
            status=record["status"],
            stages=record["stages"],
            result=record["result"],
            error=record["error"],
            created_at=record["created_at"],
        )

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)
        self._persist(job)

    def append_stage(self, job_id: str, stage: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.stages.append(stage)
        if job is not None:
            self._persist(job)

    def run_in_background(
        self,
        job_id: str,
        fn: Callable[[], None],
        *,
        timeout_seconds: float | None = None,
        on_timeout: Callable[[], None] | None = None,
    ) -> None:
        """Run `fn` on a daemon thread; any exception becomes the job's error
        rather than an unhandled crash with nowhere to surface.

        ## The timeout, and what it honestly does

        A Python thread cannot be killed from outside. So when `fn` overruns,
        this does **not** stop the work — it stops the work from mattering:
        the job is marked failed so the client gets a real answer instead of
        polling forever, and `on_timeout` releases the concurrency slot so the
        service keeps accepting requests.

        That distinction is the whole point. Before this, a repository
        pathological enough to wedge the parser held its slot indefinitely,
        and `MAX_CONCURRENT_ANALYSES` of them permanently bricked the
        instance while every request politely returned 503. Now the machine
        recovers even though the thread does not.

        **The orphaned thread is a real cost, stated rather than hidden:** it
        keeps burning CPU until it finishes or the process restarts. Bounding
        that properly means running the pipeline in a killable subprocess,
        which is the correct fix and a larger change than this audit should
        make. The container's CPU limit is what caps the blast radius
        meanwhile.
        """

        def _runner() -> None:
            self.update(job_id, status="running")
            timer: threading.Timer | None = None
            if timeout_seconds is not None:
                timer = threading.Timer(timeout_seconds, _expire)
                timer.daemon = True
                timer.start()
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - reported to the client, not swallowed
                self.update(job_id, status="error", error=str(exc))
            finally:
                if timer is not None:
                    timer.cancel()
                with self._lock:
                    self._threads.discard(threading.current_thread())

        def _expire() -> None:
            job = self.get(job_id)
            if job is None or job.status != "running":
                return
            self.update(
                job_id,
                status="error",
                error=(
                    f"Analysis exceeded {timeout_seconds:.0f}s and was abandoned. "
                    "The repository may be too large or pathologically structured."
                ),
            )
            if on_timeout is not None:
                on_timeout()

        thread = threading.Thread(target=_runner, daemon=True)
        with self._lock:
            self._threads.add(thread)
        thread.start()


registry = JobRegistry(max_finished=settings.MAX_FINISHED_JOBS)
