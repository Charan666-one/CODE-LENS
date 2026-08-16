"""Job state that survives the process that created it.

The registry is in memory, which was an accepted trade until it met a real
deployment: a restart — a deploy, a crash, an OOM, an orchestrator moving the
container — dropped every job, and the browser polling `/analyze/{id}` got a
404. A 404 means "no such job", so the client could not tell a restart from a
typo, and the honest answer ("that analysis was interrupted") did not exist.

This is the smallest thing that fixes it: one table, in the database the
service already has, in the volume that is already backed up. No queue, no
broker, no second service.

**Interrupted, not requeued.** On startup, anything left `pending` or
`running` is marked `interrupted`, because nothing else can be true — the
thread that was doing the work died with the process. Automatically restarting
it would be a guess about intent that costs a clone and a parse, and it turns
a crash loop into a clone loop. The client is told plainly and can ask again.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id     TEXT PRIMARY KEY,
    key        TEXT,
    status     TEXT NOT NULL,
    stages     TEXT NOT NULL DEFAULT '[]',
    result     TEXT,
    error      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
"""


class JobStore:
    """SQLite-backed job records. Best-effort by design.

    Every method swallows `sqlite3.Error` and logs it. Persistence here is a
    durability improvement, not a correctness dependency: if the database is
    read-only or the disk is full, analyses must still run and still answer
    from memory. Making the write fatal would turn a degraded disk into a
    total outage, which is a worse failure than the one this fixes.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent and str(self.path.parent) not in (".", ""):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def sweep_interrupted(self) -> int:
        """Mark orphaned jobs interrupted. Call once, at startup.

        A `pending` or `running` row at startup is by definition orphaned:
        this process has just begun and owns no threads yet, so whatever was
        running belonged to a process that is gone.
        """
        try:
            with self._lock, self._connection:
                cursor = self._connection.execute(
                    "UPDATE jobs SET status = 'interrupted',"
                    " error = 'Interrupted by a server restart. Please run the analysis again.',"
                    " updated_at = datetime('now')"
                    " WHERE status IN ('pending', 'running')"
                )
                count = cursor.rowcount or 0
        except sqlite3.Error as exc:
            logger.warning("job sweep failed: %s", exc)
            return 0
        if count:
            logger.warning("marked %d orphaned job(s) as interrupted at startup", count)
        return count

    def upsert(self, record: dict[str, Any]) -> None:
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    "INSERT INTO jobs (job_id, key, status, stages, result, error,"
                    " created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))"
                    " ON CONFLICT(job_id) DO UPDATE SET"
                    " status=excluded.status, stages=excluded.stages,"
                    " result=excluded.result, error=excluded.error,"
                    " updated_at=datetime('now')",
                    (
                        record["id"],
                        record.get("key"),
                        record["status"],
                        json.dumps(record.get("stages") or []),
                        json.dumps(record["result"]) if record.get("result") else None,
                        record.get("error"),
                        record["created_at"],
                    ),
                )
        except sqlite3.Error as exc:
            logger.warning("job persist failed for %s: %s", record.get("id"), exc)

    def get(self, job_id: str) -> dict[str, Any] | None:
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT job_id, key, status, stages, result, error, created_at"
                    " FROM jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("job read failed for %s: %s", job_id, exc)
            return None
        if row is None:
            return None
        return {
            "id": row[0],
            "key": row[1],
            "status": row[2],
            "stages": json.loads(row[3] or "[]"),
            "result": json.loads(row[4]) if row[4] else None,
            "error": row[5],
            "created_at": row[6],
        }

    def prune(self, keep: int) -> None:
        """Keep the newest `keep` finished rows. The table is not a log."""
        try:
            with self._lock, self._connection:
                self._connection.execute(
                    "DELETE FROM jobs WHERE status IN ('done','error','interrupted')"
                    " AND job_id NOT IN ("
                    "   SELECT job_id FROM jobs"
                    "   WHERE status IN ('done','error','interrupted')"
                    "   ORDER BY updated_at DESC LIMIT ?"
                    " )",
                    (keep,),
                )
        except sqlite3.Error as exc:
            logger.warning("job prune failed: %s", exc)
