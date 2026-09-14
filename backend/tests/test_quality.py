"""TESTS edges, ownership, and the two findings built on them."""

from __future__ import annotations

from app.graph.coverage import is_test_path, link_tests
from app.graph.ownership import _author_id, ownership
from app.graph.schema import CallConfidence, Edge, EdgeKind, Node, NodeKind
from app.ingestion.git_history import Commit


def _file(path: str) -> Node:
    return Node(
        id=f"file:{path}",
        kind=NodeKind.FILE,
        name=path.rsplit("/", 1)[-1],
        qualified_name=path,
        file_path=path,
    )


def _imports(source: str, target: str) -> Edge:
    return Edge(source_id=f"file:{source}", target_id=f"file:{target}", kind=EdgeKind.IMPORTS)


# ── recognising tests ─────────────────────────────────────────────────────


def test_test_paths_are_recognised_across_conventions() -> None:
    for path in (
        "tests/test_views.py",
        "src/thing_test.py",
        "src/components/Button.test.tsx",
        "src/components/Button.spec.ts",
        "__tests__/helpers.js",
        "spec/models/user.js",
        "backend/tests/conftest.py",
    ):
        assert is_test_path(path), path


def test_source_paths_are_not_mistaken_for_tests() -> None:
    for path in (
        "src/flask/testing.py",  # ships test helpers, is not a test
        "src/latest.py",  # ends in "test" only by accident
        "app/contest.py",
        "src/protest/views.py",
    ):
        assert not is_test_path(path), path


# ── TESTS edges ───────────────────────────────────────────────────────────


def test_name_match_is_resolved_other_imports_are_heuristic() -> None:
    nodes = [_file(p) for p in ("tests/test_views.py", "src/views.py", "src/db.py")]
    edges = [
        _imports("tests/test_views.py", "src/views.py"),
        _imports("tests/test_views.py", "src/db.py"),
    ]

    found = {(e.target_id, e.confidence) for e in link_tests(nodes, edges)}
    assert ("file:src/views.py", CallConfidence.RESOLVED) in found
    assert ("file:src/db.py", CallConfidence.HEURISTIC) in found


def test_tests_importing_tests_are_not_coverage() -> None:
    nodes = [_file(p) for p in ("tests/test_a.py", "tests/conftest.py")]
    edges = [_imports("tests/test_a.py", "tests/conftest.py")]
    assert link_tests(nodes, edges) == []


def test_source_importing_source_produces_no_tests_edge() -> None:
    nodes = [_file(p) for p in ("src/a.py", "src/b.py")]
    assert link_tests(nodes, [_imports("src/a.py", "src/b.py")]) == []


# ── ownership ─────────────────────────────────────────────────────────────


def _commit(email: str, name: str, *paths: str) -> Commit:
    return Commit(author=email, date="2026-01-01T00:00:00+00:00", files=paths, display_name=name)


def test_sole_author_is_recorded_as_full_ownership() -> None:
    nodes = [_file("src/a.py")]
    commits = [_commit("ana@example.com", "Ana", "src/a.py") for _ in range(5)]

    authors, edges = ownership(commits, nodes)
    assert [a.name for a in authors] == ["Ana"]
    assert nodes[0].extra["primary_author_share"] == 1.0
    assert nodes[0].extra["primary_author"] == "Ana"
    assert edges[0].weight == 1.0


def test_email_addresses_never_reach_the_graph() -> None:
    """Ownership needs people told apart and named, not their addresses."""
    nodes = [_file("src/a.py")]
    commits = [_commit("ana@example.com", "Ana", "src/a.py") for _ in range(3)]

    authors, edges = ownership(commits, nodes)
    serialised = "".join(a.model_dump_json() for a in authors)
    serialised += "".join(e.model_dump_json() for e in edges)
    serialised += nodes[0].model_dump_json()

    assert "ana@example.com" not in serialised
    assert "@" not in serialised.split('"name"')[1][:40]  # the display name is bare
    assert authors[0].id == _author_id("ana@example.com")  # still stable and distinct


def test_the_same_person_under_two_display_names_stays_one_author() -> None:
    nodes = [_file("src/a.py")]
    commits = [
        _commit("ana@example.com", "Ana", "src/a.py"),
        _commit("ana@example.com", "ana g", "src/a.py"),
        _commit("ana@example.com", "Ana G", "src/a.py"),
    ]
    authors, _ = ownership(commits, nodes)
    assert len(authors) == 1


def test_a_drive_by_commit_does_not_make_someone_an_owner() -> None:
    nodes = [_file("src/a.py")]
    commits = [_commit("ana@example.com", "Ana", "src/a.py") for _ in range(19)]
    commits.append(_commit("bo@example.com", "Bo", "src/a.py"))  # 5%

    _, edges = ownership(commits, nodes)
    assert len(edges) == 1
    assert edges[0].weight == 0.95
