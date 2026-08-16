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
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

JobStatus = Literal["pending", "running", "done", "error"]


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

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, key: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex, key=key)
        with self._lock:
            self._jobs[job.id] = job
        return job

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

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)

    def append_stage(self, job_id: str, stage: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.stages.append(stage)

    def run_in_background(self, job_id: str, fn: Callable[[], None]) -> None:
        """Run `fn` on a daemon thread; any exception becomes the job's error
        rather than an unhandled crash with nowhere to surface."""

        def _runner() -> None:
            self.update(job_id, status="running")
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - reported to the client, not swallowed
                self.update(job_id, status="error", error=str(exc))

        threading.Thread(target=_runner, daemon=True).start()


registry = JobRegistry()
