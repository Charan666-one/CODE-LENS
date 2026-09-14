"""A hard ceiling on what narration may spend.

The per-client rate limit bounds how often one caller may ask. It does not
bound the total: twenty clients each within their quota still add up, and the
number they add up to is an invoice the operator did not agree to.

So there is a second counter that nothing resets except a restart. It is
crude on purpose — no time windows, no per-client accounting, no persistence.
Those all belong to the provider's own spend controls, which are the only
place a *durable* cost ceiling can honestly live. This exists so that the
worst case for a beta is a bounded surprise rather than an unbounded one, and
so the answer to "what stops the bill" is a number rather than a hope.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class CallBudget:
    """Counts model calls for the life of the process and refuses past a cap."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._used = 0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """Claim one call. False when the budget is gone."""
        with self._lock:
            if self._used >= self.limit:
                return False
            self._used += 1
            if self._used == self.limit:
                logger.warning(
                    "narration_budget_exhausted limit=%d; narration now 503s "
                    "until this process restarts",
                    self.limit,
                )
            return True

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def reset(self) -> None:
        """For tests, and for an operator who has raised the cap."""
        with self._lock:
            self._used = 0


from app.core.config import settings  # noqa: E402 - avoids a config import cycle

budget = CallBudget(settings.NARRATION_MAX_CALLS)
