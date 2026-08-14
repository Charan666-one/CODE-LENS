"""Route declarations — the surface a service actually exposes.

"What are this app's endpoints, and what code does each one reach?" is the
first question anyone asks about a service they did not write, and until now
the graph could not answer it: routes were marked only as a boolean
`is_entrypoint` on the handler, which loses the method, the path, and the
fact that one function can serve several routes.

An ENDPOINT node fixes that. It is addressable, it carries `GET /users/{id}`
as its name, and a ROUTES_TO edge joins it to the handler — so blast radius
run from a route answers "what does this endpoint touch", and run *at* a
shared helper answers "which endpoints break if I change this", which is the
question that decides whether a deploy is safe.

Recognition is deliberately shape-based rather than framework-specific. The
decorator form covers FastAPI, Flask, Starlette, Sanic, Quart, APIRouter, and
anything else that copied the idea; the call form covers Express, Koa, Fastify,
Hono, and every router with a `.get(path, handler)` method. Neither knows a
framework by name, so a framework nobody here has heard of still works, and
none of this needs updating when the next one ships.

What it does not do: resolve dynamically built paths. `app.get(PREFIX + "/x")`
records nothing rather than a wrong string, on the same principle as the rest
of the parser — a missing edge is cheaper than a confident wrong one.
"""

from __future__ import annotations

import re

#: Method names shared by the decorator and call forms.
HTTP_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "websocket", "all"}
)

#: `@app.route("/x")` / `router.route(...)` names a path without a method.
_ANY_METHOD_NAMES = frozenset({"route", "use", "all"})

#: A route path literal. The leading slash is required: `app.use(handler)` and
#: `emitter.on("ready", …)` both put a non-path first argument where a path
#: would go, and a route with a blank path is not something anyone can call.
_LOOKS_LIKE_PATH = re.compile(r'^["\'](/[^"\']*)["\']$')

#: `methods=["POST", "PUT"]` inside a Flask-style decorator.
_METHODS_KWARG = re.compile(r"""methods\s*=\s*[\[\(]([^\]\)]*)[\]\)]""")

_QUOTED = re.compile(r"""^\s*["']([^"']*)["']\s*$""")

#: `router = APIRouter(prefix="/api/repos/{id}")` — the router's own mount.
#: Without this the recorded path is the one written at the decorator, which
#: is not the URL anybody can call: `/search` on a prefixed router is really
#: `/api/repos/{snapshot_id}/search`. A list of endpoints whose paths do not
#: exist is worse than no list, because it looks authoritative.
_ROUTER_PREFIX = re.compile(
    r"""\b(?:APIRouter|Blueprint|Router)\s*\(.*?\b(?:url_)?prefix\s*=\s*["']([^"']*)["']""",
    re.DOTALL,
)


def router_prefix(assignment_source: str) -> str | None:
    """The mount prefix declared by a router constructor, if it declares one."""
    found = _ROUTER_PREFIX.search(assignment_source)
    if found is None:
        return None
    prefix = found.group(1).rstrip("/")
    return prefix or None


def join_path(prefix: str | None, path: str) -> str:
    """Mount a route under its router. `/api` + `/x` -> `/api/x`."""
    if not prefix:
        return path
    if not path or path == "/":
        return prefix
    return f"{prefix}/{path.lstrip('/')}"


def parse_decorator_route(decorator: str) -> tuple[str, str] | None:
    """`@app.get("/users/{id}")` -> ("GET", "/users/{id}").

    Returns None for decorators that are not routes, which is most of them.
    """
    stripped = decorator.lstrip("@").strip()
    head, sep, rest = stripped.partition("(")
    if not sep:
        return None  # a bare decorator takes no path
    segments = head.split(".")
    attribute = segments[-1].lower()
    # `.get` must be an attribute of something (`app.get`), or a plain
    # `route(...)`. A bare `get(...)` decorator is a getter, not a route.
    if attribute in HTTP_METHODS and len(segments) < 2:
        return None
    if attribute not in HTTP_METHODS and attribute not in _ANY_METHOD_NAMES:
        return None

    path = _first_string_argument(rest)
    if path is None:
        return None

    if attribute in _ANY_METHOD_NAMES:
        found = _METHODS_KWARG.search(rest)
        if found:
            methods = [
                m.strip().strip("\"'").upper() for m in found.group(1).split(",") if m.strip()
            ]
            if methods:
                return (methods[0] if len(methods) == 1 else "|".join(sorted(methods)), path)
        return ("ANY", path)
    return (attribute.upper(), path)


def parse_call_route(callee: str, first_argument: str) -> tuple[str, str] | None:
    """`app.get` + `"/users/:id"` -> ("GET", "/users/:id").

    The Express family. `use` is included because a mounted sub-router is
    part of the exposed surface even though it names no method.
    """
    segments = callee.split(".")
    attribute = segments[-1].lower()
    if len(segments) < 2:
        return None  # a bare `get("/x")` is far more likely to be a fetch
    if attribute not in HTTP_METHODS and attribute not in _ANY_METHOD_NAMES:
        return None
    if not _LOOKS_LIKE_PATH.match(first_argument.strip()):
        return None
    path = first_argument.strip()[1:-1]
    if attribute in _ANY_METHOD_NAMES:
        return ("ANY", path)
    return (attribute.upper(), path)


def _first_string_argument(argument_text: str) -> str | None:
    """The first quoted literal in a decorator's argument list, if the first
    argument *is* one. A computed path resolves to nothing on purpose."""
    depth = 0
    current: list[str] = []
    for char in argument_text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif char == "," and depth == 0:
            break
        current.append(char)
    found = _QUOTED.match("".join(current))
    if found is None:
        return None
    path = found.group(1)
    return path if path.startswith("/") else None
