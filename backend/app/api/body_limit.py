"""A ceiling on request bodies, enforced while they arrive.

Starlette buffers a request body into memory before a route sees it, and
neither FastAPI nor uvicorn caps how much. Every endpoint here takes a small
JSON object — a URL, a node id, a couple of integers — so a body of any real
size is either a mistake or an attempt, and a 200 MB POST to `/api/analyze`
was 200 MB of resident memory before a single validator ran.

Written as raw ASGI rather than `BaseHTTPMiddleware` for one reason that
matters: it has to count bytes *as they stream in* and stop at the ceiling.
Checking `Content-Length` alone is a header check, and a header is a claim by
the sender — `Transfer-Encoding: chunked` omits it entirely, so a
header-only guard is bypassed by not sending the header. The declared length
is still checked first, because refusing before reading anything is cheaper
than refusing after reading a megabyte.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class BodyLimitMiddleware:
    """Reject requests whose body exceeds `max_bytes` with 413."""

    def __init__(self, app: Callable[..., Awaitable[None]], max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > self.max_bytes:
            await _too_large(send, self.max_bytes)
            return

        seen = 0
        exceeded = False

        async def counting_receive() -> Message:
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    exceeded = True
                    # Hand the app an empty final chunk rather than the
                    # oversized one. It will fail validation harmlessly; the
                    # response it produces is discarded by `guarded_send`
                    # below in favour of a truthful 413.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        started = False

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if exceeded and not started:
                # The app is answering a request we truncated, so its answer
                # would be misleading — a validation error about a malformed
                # body rather than the truth, which is that the body was too
                # big. Replace the whole response, once.
                started = True
                await _too_large(send, self.max_bytes)
                return
            if exceeded:
                return  # drop the rest of the superseded response
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, counting_receive, guarded_send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _too_large(send: Send, max_bytes: int) -> None:
    body = (
        b'{"detail":"Request body too large (limit '
        + str(max_bytes).encode()
        + b' bytes)."}'
    )
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
