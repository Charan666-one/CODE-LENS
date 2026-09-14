"""One admission control point for every expensive endpoint.

## Why this exists as its own module

The limits used to live in `routes.py` and were applied by `POST /analyze`,
which looked complete because analyze is the endpoint that obviously clones.
It was not complete. `POST /repos/{id}/summarize` re-acquires the working tree
through `ingest()` — a second full clone of the same repository — and it went
through none of them. Anyone holding a snapshot id (they are small integers,
and `GET /repos` lists them) could loop that endpoint and clone without limit
while `/analyze` politely returned 429.

The lesson is about placement, not about the limits themselves: a ceiling
attached to *one route* protects that route, while the thing being protected
is the machine. So admission lives here, next to nothing, and every route that
can start expensive work asks the same question of the same counters.

**Expensive means: clones a repository, or spends money.** Reading a stored
graph is cheap and stays unmetered — as does polling job status, which the
async design exists to make free.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from fastapi import HTTPException, Request

from app.core.clients import in_networks, parse_networks, resolve_client
from app.core.config import settings
from app.core.limits import ConcurrencyGate, RateLimiter

logger = logging.getLogger(__name__)

#: Parsed once. See core/clients.py for why a forwarded header is only
#: believed when it arrives from a vouched-for peer.
TRUSTED_PROXIES = parse_networks(settings.TRUSTED_PROXY_IPS)
BLOCKED = parse_networks(settings.BLOCKED_CLIENTS)

#: Process-wide counters. See core/limits.py for what each defends against.
rate_limiter = RateLimiter(settings.RATE_LIMIT_ANALYSES, settings.RATE_LIMIT_WINDOW_SECONDS)
analysis_gate = ConcurrencyGate(settings.MAX_CONCURRENT_ANALYSES)

#: Narration endpoints spend the *operator's* API credit, one call per
#: request, and are otherwise cheap — no clone, no parse. They need a ceiling
#: for a different reason than analysis does (a bill, not a machine), so they
#: get their own and a more generous one. Sharing the analysis quota would
#: have made five narrations lock out an analysis, which is the wrong trade.
llm_limiter = RateLimiter(settings.RATE_LIMIT_NARRATIONS, settings.RATE_LIMIT_WINDOW_SECONDS)


def reset() -> None:
    """Clear the counters. For tests, and only for tests."""
    rate_limiter.reset()
    llm_limiter.reset()


def charge_narration(request: Request) -> None:
    """Meter an endpoint that spends money but starts no expensive work."""
    enforce_blocklist(request)
    identity = client_key(request)
    retry_after = llm_limiter.check(identity)
    if retry_after is not None:
        _log_refusal(request, "narration_rate_limit", identity)
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit: {settings.RATE_LIMIT_NARRATIONS} narrated answers per "
                f"{settings.RATE_LIMIT_WINDOW_SECONDS // 60} minutes. "
                f"Try again in {int(retry_after) + 1}s."
            ),
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


def client_key(request: Request) -> str:
    """Who to charge the quota to.

    Behind a proxy the socket address is the proxy, so every client would
    share one bucket and the first few analyses would lock out everyone.
    `X-Forwarded-For` carries the original client — but only a proxy the
    operator has vouched for is believed, because a header the caller writes
    is a quota the caller mints. See core/clients.py.
    """
    identity, _ = resolve_client(
        request.client.host if request.client else None,
        request.headers.get("x-forwarded-for"),
        TRUSTED_PROXIES,
    )
    return identity


def peer_address(request: Request) -> str:
    """The socket address, whatever the headers say. Used for blocking, which
    must never be defeatable by the thing being blocked."""
    return (request.client.host if request.client else "unknown") or "unknown"


def enforce_blocklist(request: Request) -> None:
    """Refuse a client the operator has blocked.

    Matched on the *peer* and on the resolved identity: the peer so a blocked
    address cannot escape by forging a header, the identity so a client
    behind a trusted proxy can be blocked at all.
    """
    if not BLOCKED:
        return
    peer = peer_address(request)
    identity = client_key(request)
    if in_networks(peer, BLOCKED) or in_networks(identity, BLOCKED):
        logger.warning(
            "request_blocked peer=%s identity=%s path=%s", peer, identity, request.url.path
        )
        raise HTTPException(status_code=403, detail="Blocked.")


def _log_refusal(request: Request, reason: str, identity: str) -> None:
    """One structured line per refusal, so abuse is visible without a
    dashboard. Deliberately carries no request body and no repository
    contents — only who, what and why."""
    logger.warning(
        "request_refused reason=%s identity=%s peer=%s path=%s method=%s",
        reason,
        identity,
        peer_address(request),
        request.url.path,
        request.method,
    )


def acquire(request: Request) -> Callable[[], None]:
    """Claim a slot for expensive work. Returns the release callable.

    Raises 503 when the machine is full and 429 when this client has spent
    its quota.

    **Capacity before quota, deliberately.** The other order is the obvious
    one and it is unfair: a caller refused with 503 because the server is
    saturated would have had their quota decremented for work that never ran,
    so a busy machine silently spends the quota of everyone who arrives while
    it is busy. Whether the server has room is the server's business and is
    settled first; how often this client may ask is settled second, and only
    for a request that would otherwise have proceeded.
    """
    enforce_blocklist(request)
    identity = client_key(request)

    if not analysis_gate.try_acquire():
        _log_refusal(request, "capacity", identity)
        raise HTTPException(
            status_code=503,
            detail=(
                f"Busy: {analysis_gate.limit} analyses already running. "
                "Each one is a clone and a full parse. Try again shortly."
            ),
            headers={"Retry-After": "30"},
        )

    retry_after = rate_limiter.check(identity)
    if retry_after is not None:
        analysis_gate.release()  # never hold a slot for work that will not run
        _log_refusal(request, "rate_limit", identity)
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit: {settings.RATE_LIMIT_ANALYSES} analyses per "
                f"{settings.RATE_LIMIT_WINDOW_SECONDS // 60} minutes. "
                f"Try again in {int(retry_after) + 1}s."
            ),
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    released = False

    def release() -> None:
        # Idempotent: `slot()` releases on the way out and a caller that
        # already released explicitly must not decrement the gate twice —
        # a double release silently raises the effective cap.
        nonlocal released
        if not released:
            released = True
            analysis_gate.release()

    return release


@contextmanager
def slot(request: Request) -> Iterator[None]:
    """Admission for work that finishes inside the request.

    `/analyze` cannot use this — its work continues on a background thread
    after the response is sent, so it acquires manually and releases in the
    thread's `finally`.
    """
    release = acquire(request)
    try:
        yield
    finally:
        release()
