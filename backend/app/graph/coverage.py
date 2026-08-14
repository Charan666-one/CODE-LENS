"""TESTS edges — which file is exercised by which test.

Nobody runs coverage on a stranger's repository, and a graph that has to
execute code to answer "is this tested?" has already lost. But the answer is
mostly sitting in the import graph already: a test file's non-test imports
are, with very few exceptions, the things it tests. That is a structural fact,
free to compute, and available on any repo the parser can read at all.

Two tiers, because the evidence really does differ:

    resolved    the test file's name matches the imported file's name —
                `test_views.py` importing `views.py` is about as close to
                a declaration of intent as untyped code offers
    heuristic   a test file imports a source file with no name relationship;
                probably exercised, possibly just a helper it needed

What this deliberately does not claim: that an edge means the file is *well*
tested, or tested at all in any line-coverage sense. It means a test file
depends on it. That is a weaker statement than coverage and it is the only
one the evidence supports — but its negative is the useful half. A file with
high fan-in and no incoming TESTS edge is a hub nobody is testing, and that
finding needs no coverage run to be true.
"""

from __future__ import annotations

import re

from app.graph.schema import CallConfidence, Edge, EdgeKind, Node, NodeKind

#: Directory names whose contents are tests regardless of file naming.
_TEST_DIRECTORIES = frozenset({"test", "tests", "__tests__", "spec", "specs", "e2e"})

#: File-name shapes that mean "this is a test", across the languages parsed.
_TEST_FILE_PATTERNS = (
    re.compile(r"^test_.+\.(py|pyi)$"),
    re.compile(r"^.+_test\.(py|pyi)$"),
    re.compile(r"^.+\.(test|spec)\.(js|jsx|mjs|cjs|ts|tsx)$"),
    re.compile(r"^conftest\.py$"),
)


def is_test_path(path: str) -> bool:
    """Does this repo-relative path hold tests?"""
    segments = path.split("/")
    if any(segment.lower() in _TEST_DIRECTORIES for segment in segments[:-1]):
        return True
    name = segments[-1]
    return any(pattern.match(name) for pattern in _TEST_FILE_PATTERNS)


def _stem(path: str) -> str:
    """`src/flask/views.py` -> `views`; `views.test.ts` -> `views`."""
    name = path.rsplit("/", 1)[-1]
    for suffix in (".test", ".spec"):
        base = name.split(".")[0]
        if f"{suffix}." in name:
            return base
    return name.split(".")[0]


def _tested_stem(test_path: str) -> str | None:
    """The source name a test file's own name points at, if any."""
    stem = _stem(test_path)
    if stem.startswith("test_"):
        return stem[len("test_") :]
    if stem.endswith("_test"):
        return stem[: -len("_test")]
    return stem or None


def link_tests(nodes: list[Node], edges: list[Edge]) -> list[Edge]:
    """Derive TESTS edges from the IMPORTS edges already resolved.

    Reuses resolution rather than re-deriving it: if the import graph knows
    `tests/test_views.py` reaches `src/flask/views.py`, that is the same
    evidence, read for a different question.
    """
    paths_by_id = {
        node.id: node.file_path
        for node in nodes
        if node.kind is NodeKind.FILE and node.file_path
    }

    found: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        if edge.kind is not EdgeKind.IMPORTS:
            continue
        source_path = paths_by_id.get(edge.source_id)
        target_path = paths_by_id.get(edge.target_id)
        if source_path is None or target_path is None:
            continue
        if not is_test_path(source_path) or is_test_path(target_path):
            continue  # tests importing tests are fixtures, not coverage
        key = (edge.source_id, edge.target_id)
        if key in seen:
            continue
        seen.add(key)

        expected = _tested_stem(source_path)
        matched = expected is not None and expected == _stem(target_path)
        found.append(
            Edge(
                source_id=edge.source_id,
                target_id=edge.target_id,
                kind=EdgeKind.TESTS,
                file_path=source_path,
                line=edge.line,
                confidence=(
                    CallConfidence.RESOLVED if matched else CallConfidence.HEURISTIC
                ),
            )
        )
    return found
