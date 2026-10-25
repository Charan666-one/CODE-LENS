"""A hard ceiling on what narration may spend.

The per-client rate limit bounds how often one caller may ask. It does not
bound the total: twenty clients each within their quota still add up, and the
number they add up to is an invoice the operator did not agree to.

So there is a second counter, and it now survives the process. It did not,
and the gap was the one case that matters: a container in a crash loop
re-granted the whole cap on every restart, so the single control protecting
the bill was the one that forgot. "Resets on restart" is a fine scope for a
counter nobody is trying to defeat; it is no scope at all for one that a
crash loop — or an attacker who can cause one — resets for free.

**Durability forced a window, and that is not incidental.** A persistent
counter with no window is strictly worse than the in-memory one it replaces:
spend the cap once and narration is dead permanently, with no way back short
of an operator editing the database by hand. So the cap is now per window
(a day by default) rather than per process, which is also what an operator
means by "a budget".

**It degrades to the old behaviour, never past it.** Every database call is
best-effort, as in `JobStore`: if the write fails the counter falls back to
this process's own memory, still bounded by the same limit. A broken disk
therefore costs at most what the previous design always cost, and cannot
fail open into an unbounded bill.

The provider's own spend cap is still the only ceiling that is beyond this
service's reach to get wrong, and `DEPLOYING.md` still asks for one.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS narration_budget (
    window_start TEXT PRIMARY KEY,
    used         INTEGER NOT NULL DEFAULT 0
);
"""


class BudgetStore:
    """The counter's durable half. Best-effort, like `JobStore`."""

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

    def claim(self, window: str, limit: int) -> bool | None:
        """Claim one call in `window`. True granted, False exhausted.

        `None` means the database could not answer, and the caller falls
        back to its in-memory count rather than failing open.

        One statement does the whole thing, so two workers racing cannot
        both see room and both take the last call: the `WHERE used < ?`
        rides on the UPDATE, and SQLite reports whether a row changed.
        """
        try:
            with self._lock, self._connection:
                cursor = self._connection.execute(
                    "INSERT INTO narration_budget (window_start, used)"
                    " VALUES (?, 1)"
                    " ON CONFLICT(window_start) DO UPDATE SET used = used + 1"
                    " WHERE used < ?",
                    (window, limit),
                )
                return bool(cursor.rowcount)
        except sqlite3.Error as exc:
            logger.warning("narration budget write failed, counting in memory: %s", exc)
            return None

    def used(self, window: str) -> int:
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT used FROM narration_budget WHERE window_start = ?",
                    (window,),
                ).fetchone()
        except sqlite3.Error:
            return 0
        return int(row[0]) if row else 0

    def clear(self) -> None:
        try:
            with self._lock, self._connection:
                self._connection.execute("DELETE FROM narration_budget")
        except sqlite3.Error as exc:
            logger.warning("narration budget reset failed: %s", exc)


class CallBudget:
    """Counts model calls per window and refuses past a cap."""

    def __init__(self, limit: int, window_hours: int = 24) -> None:
        self.limit = limit
        self.window_hours = max(1, window_hours)
        self._used = 0
        self._window = self.current_window()
        self._lock = threading.Lock()
        self._store: BudgetStore | None = None

    def attach(self, store: BudgetStore) -> None:
        """Give the budget somewhere durable to count. Called at startup, so
        the module stays importable — and testable — with no database."""
        self._store = store

    def current_window(self) -> str:
        """The window a call now belongs to, as a sortable stamp.

        Fixed buckets rather than a rolling window: a rolling one needs
        every timestamp kept, and the question here is "how much this day",
        not "how much in any 24 hours".
        """
        now = datetime.now(UTC)
        bucket = (now.hour // self.window_hours) * self.window_hours
        return f"{now:%Y-%m-%d}T{bucket:02d}"

    def allow(self) -> bool:
        """Claim one call. False when this window's budget is gone."""
        window = self.current_window()
        store = self._store
        if store is not None:
            granted = store.claim(window, self.limit)
            if granted is not None:
                if not granted:
                    self._warn_exhausted(window)
                return granted
            # Fell through: the database could not answer, so this process
            # counts for itself and the cap still holds for its own lifetime.

        with self._lock:
            if window != self._window:  # a new window: the cap replenishes
                self._window = window
                self._used = 0
            if self._used >= self.limit:
                return False
            self._used += 1
            if self._used == self.limit:
                self._warn_exhausted(window)
            return True

    def _warn_exhausted(self, window: str) -> None:
        logger.warning(
            "narration_budget_exhausted limit=%d window=%s; narration now 503s "
            "until the window turns over",
            self.limit,
            window,
        )

    @property
    def used(self) -> int:
        store = self._store
        if store is not None:
            return store.used(self.current_window())
        with self._lock:
            return self._used if self._window == self.current_window() else 0

    def reset(self) -> None:
        """For tests, and for an operator who has raised the cap."""
        with self._lock:
            self._used = 0
            self._window = self.current_window()
        if self._store is not None:
            self._store.clear()


from app.core.config import settings  # noqa: E402 - avoids a config import cycle

budget = CallBudget(settings.NARRATION_MAX_CALLS, settings.NARRATION_BUDGET_WINDOW_HOURS)
