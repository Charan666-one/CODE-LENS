"""Co-change coupling — the guards matter more than the happy path.

Every threshold in `co_change.py` exists because the naive version produces
garbage. These tests pin the garbage out.
"""

from __future__ import annotations

from app.graph.co_change import co_change_edges
from app.graph.schema import EdgeKind, Node, NodeKind
from app.ingestion.git_history import Commit, histories_from_commits, read_log


def _file(path: str) -> Node:
    return Node(
        id=f"file:{path}",
        kind=NodeKind.FILE,
        name=path.rsplit("/", 1)[-1],
        qualified_name=path,
        file_path=path,
    )


NODES = [_file(p) for p in ("a.py", "b.py", "c.py", "noise.py")]


def _commit(*paths: str) -> Commit:
    return Commit(author="dev@example.com", date="2026-01-01T00:00:00+00:00", files=paths)


def test_pair_that_always_travels_together_is_emitted() -> None:
    commits = [_commit("a.py", "b.py") for _ in range(5)]
    edges, report = co_change_edges(commits, NODES)

    assert len(edges) == 1
    edge = edges[0]
    assert edge.kind is EdgeKind.CO_CHANGES
    assert {edge.source_id, edge.target_id} == {"file:a.py", "file:b.py"}
    assert edge.weight == 1.0  # they never change apart
    assert report.edges_emitted == 1


def test_two_shared_commits_is_coincidence_not_coupling() -> None:
    edges, _ = co_change_edges([_commit("a.py", "b.py")] * 2, NODES)
    assert edges == []


def test_a_busy_file_does_not_couple_to_everything() -> None:
    """`noise.py` appears in every commit. Jaccard must refuse to call that
    coupling — otherwise the busiest file in a repo pairs with all of it."""
    commits = [_commit("noise.py", "a.py") for _ in range(4)]
    commits += [_commit("noise.py", "b.py") for _ in range(4)]
    commits += [_commit("noise.py") for _ in range(30)]

    edges, _ = co_change_edges(commits, NODES)
    assert edges == []


def test_mass_commits_are_ignored() -> None:
    """A 100-file sweep is a reformat, not evidence. With the cap in place
    the pair has no qualifying commits left."""
    wide = _commit(*[f"f{i}.py" for i in range(100)], "a.py", "b.py")
    nodes = NODES + [_file(f"f{i}.py") for i in range(100)]

    edges, report = co_change_edges([wide] * 10, nodes, max_files_per_commit=30)
    assert edges == []
    assert report.commits_skipped_large == 10


def test_only_files_in_the_graph_participate() -> None:
    """Coupling to a lockfile is real but unrepresentable — there is no node
    to hang the edge on, and it says nothing about code structure."""
    commits = [_commit("a.py", "package-lock.json") for _ in range(9)]
    edges, _ = co_change_edges(commits, NODES)
    assert edges == []


def test_edges_are_emitted_once_per_pair_in_canonical_order() -> None:
    commits = [_commit("b.py", "a.py") for _ in range(4)]
    edges, _ = co_change_edges(commits, NODES)
    assert len(edges) == 1
    assert edges[0].source_id == "file:a.py"  # sorted, not commit order
    assert edges[0].target_id == "file:b.py"


def test_recent_history_only() -> None:
    old = [_commit("a.py", "b.py") for _ in range(10)]
    recent = [_commit("b.py", "c.py") for _ in range(10)]
    edges, _ = co_change_edges(recent + old, NODES, max_commits=10)

    pairs = {(e.source_id, e.target_id) for e in edges}
    assert pairs == {("file:b.py", "file:c.py")}


def test_read_log_groups_files_by_commit(tmp_path) -> None:
    """The whole feature rests on commit boundaries being real."""
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    git("config", "user.email", "dev@example.com")
    git("config", "user.name", "Dev")
    (tmp_path / "a.py").write_text("a = 1\n")
    (tmp_path / "b.py").write_text("b = 1\n")
    git("add", "-A")
    git("commit", "-qm", "both")
    (tmp_path / "a.py").write_text("a = 2\n")
    git("add", "-A")
    git("commit", "-qm", "just a")

    commits = read_log(tmp_path)
    assert [set(c.files) for c in commits] == [{"a.py"}, {"a.py", "b.py"}]  # newest first

    histories = histories_from_commits(commits)
    assert histories["a.py"].churn_count == 2
    assert histories["b.py"].churn_count == 1
