"""What stops one caller from spending the whole machine.

`POST /api/analyze` clones an arbitrary repository and parses it. On a laptop
only its author can reach, that is a feature. On a public URL it is disk, CPU
and outbound network offered to strangers on their terms, and every one of
those needs a ceiling that is *stated* rather than implied by how slow the
machine happens to be.

Three separate ceilings, because they fail in three different ways:

* **Rate** — how often one client may start work. Guards against a loop.
* **Concurrency** — how much work runs at once, across everyone. Guards
  against a machine that stops answering its own health check.
* **Disk** — how much of the clone cache survives. Guards against a full
  volume, which is the failure that takes down everything else with it.

All three are in-process, which is the honest scope: one server, one set of
counters. A second replica gets its own, and the real fix at that point is a
shared store — noted here rather than pretended away. It is still a much
better position than no limit at all, which is what this replaced.

Polling job status is deliberately unmetered. The async design exists so a
browser can poll a trivial endpoint instead of holding one long request open;
rate-limiting that would punish clients for using the API correctly.
"""

from __future__ import annotations

import shutil
import threading
import time
from collections import defaultdict, deque
from pathlib import Path


class RateLimiter:
    """Fixed quota per client over a sliding window.

    A deque of timestamps per client rather than a token bucket: the window
    is minutes and the quota is single digits, so the memory is trivial and
    the behaviour is exactly what the error message can promise — "5 per 5
    minutes" means the 6th is refused until the oldest falls out.
    """

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client: str, *, now: float | None = None) -> float | None:
        """Record an attempt. Returns `None` if allowed, else seconds to wait.

        The attempt is only recorded when it is allowed. Counting refused
        attempts would make a client that keeps retrying extend its own
        lockout indefinitely, which turns a limit into a trap.
        """
        moment = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[client]
            cutoff = moment - self.window
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                # A limit of zero is a real configuration — "analysis is off"
                # — and there is no oldest hit to expire, so asking the deque
                # when a slot frees raised IndexError and turned a refusal
                # into a 500. Nothing will ever free, so the window is the
                # only honest "come back later" available.
                return self.window if not hits else max(0.0, hits[0] + self.window - moment)
            hits.append(moment)
            return None

    def reset(self) -> None:
        """For tests, and for a process that wants a clean slate."""
        with self._lock:
            self._hits.clear()


class ConcurrencyGate:
    """A hard cap on simultaneous pipelines, refused rather than queued.

    Queueing would be the friendlier design and is the wrong one here. A
    queue behind a cap of 2 turns "too busy" into "your analysis will start
    in nine minutes", which the UI would show as an ordinary running job that
    simply never progresses — the worst of both, because the client cannot
    tell waiting from broken. Refusing immediately with a number the caller
    can act on is honest, and the client already knows how to show an error.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._active = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active >= self.limit:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            # Never below zero: a double release would otherwise silently
            # raise the effective cap, which is the kind of leak that only
            # shows up under the load the cap exists for.
            self._active = max(0, self._active - 1)

    @property
    def active(self) -> int:
        with self._lock:
            return self._active


def directory_size_mb(root: Path) -> float:
    """Bytes under `root`, in MB. Missing directory reads as empty."""
    if not root.exists():
        return 0.0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue  # vanished mid-walk; it is not in the cache any more
    return total / (1024 * 1024)


def reclaim_clone_cache(root: Path, max_mb: int, *, keep: set[str] | None = None) -> list[str]:
    """Delete least-recently-used clones until the cache fits. Returns names.

    Nothing else ever removed these. A clone directory is written once and
    read forever, so a long-lived instance accumulated every repository it
    had ever been asked about until the volume filled — and a full volume
    takes SQLite down with it, which turns "someone analysed a big repo" into
    "the whole service is broken".

    `keep` protects trees in use. Reclaiming the directory a running pipeline
    is parsing would reproduce, deliberately, the exact race that job dedupe
    and the source lock were added to prevent.
    """
    if not root.exists() or directory_size_mb(root) <= max_mb:
        return []

    protected = keep or set()
    entries: list[tuple[float, Path]] = []
    for child in root.iterdir():
        if not child.is_dir() or child.name in protected:
            continue
        try:
            entries.append((child.stat().st_mtime, child))
        except OSError:
            continue
    entries.sort()  # oldest first

    removed: list[str] = []
    for _, child in entries:
        if directory_size_mb(root) <= max_mb:
            break
        shutil.rmtree(child, ignore_errors=True)
        removed.append(child.name)
    return removed
