"""The backtest grades the product, so its own arithmetic has to be right.

A benchmark that flatters itself is worse than no benchmark: it produces a
number people act on. These tests pin the two ways this one nearly did.
"""

from __future__ import annotations

from app.graph.schema import Edge, EdgeKind, KnowledgeGraph, Node, NodeKind, RepoSnapshot
from app.graph.traversal import GraphView
from app.ingestion.git_history import Commit
from app.ledger import backtest, popularity_baseline


def _file(path: str) -> Node:
    return Node(
        id=f"file:{path}",
        kind=NodeKind.FILE,
        name=path.rsplit("/", 1)[-1],
        qualified_name=path,
        file_path=path,
    )


def _view(paths: list[str], imports: list[tuple[str, str]]) -> GraphView:
    graph = KnowledgeGraph(
        snapshot=RepoSnapshot(
            repo_url="file:///t",
            commit_sha="x",
            primary_language="Python",
            languages={"py": len(paths)},
            file_count=len(paths),
            analyzed_at="2026-01-01T00:00:00+00:00",
        ),
        nodes=[_file(p) for p in paths],
        edges=[
            Edge(source_id=f"file:{s}", target_id=f"file:{t}", kind=EdgeKind.IMPORTS)
            for s, t in imports
        ],
    )
    return GraphView(graph)


def _commit(*paths: str) -> Commit:
    return Commit(author="d@e.com", date="2026-01-01T00:00:00+00:00", files=paths)


def test_a_correct_prediction_scores() -> None:
    """`b.py` imports `a.py`, and they changed together. That is a hit."""
    view = _view(["a.py", "b.py"], [("b.py", "a.py")])
    report = backtest(view, [_commit("a.py", "b.py")] * 3, k=5)

    seeded_on_a = [p for p in report.predictions if p.seed == "a.py"]
    assert all(p.hits_at_k == 1 for p in seeded_on_a)
    assert all(p.first_hit_rank == 1 for p in seeded_on_a)
    assert report.mrr() > 0


def test_the_baseline_cannot_see_the_commit_it_is_grading() -> None:
    """The bug that made the first run publish a wrong conclusion.

    Counting popularity over all of history lets the baseline "predict" a
    file by having already watched it change in the very commit under test.
    """
    commits = [_commit("new.py", "other.py"), _commit("old.py", "other.py")]
    tracked = {"new.py", "old.py", "other.py"}

    leaky = popularity_baseline(commits, tracked)
    honest = popularity_baseline(commits, tracked, before_index=0)

    assert "new.py" in leaky  # the newest commit's file, seen by the leaky version
    assert "new.py" not in honest  # nothing older than commit 0 has ever seen it


def test_silence_is_counted_as_an_example_not_dropped() -> None:
    """A leaf file has no dependents, so blast radius says nothing. That must
    still count — quietly discarding the hard cases is how a benchmark ends
    up reporting a score nobody can reproduce in the product."""
    view = _view(["leaf.py", "other.py"], [])
    report = backtest(view, [_commit("leaf.py", "other.py")] * 3, k=5)

    assert report.examples > 0
    assert report.silent() == len(report.predictions)
    assert report.precision() == 0.0
    # …and the conditional view reports nothing rather than a flattering 0/0.
    assert report.answered() == []
    assert report.answered_precision() == 0.0


def test_a_test_file_seed_is_graded_in_the_direction_it_has() -> None:
    """The artifact that made entry #1's headline finding wrong.

    `test_auth.py` imports `auth.py`; nothing imports the test. Graded on
    dependents alone the graph correctly says nothing and is scored zero —
    and 99% of the "silent" examples in the first run were exactly this. The
    benchmark's question is symmetric, so the ranking has to be too.
    """
    view = _view(["auth.py", "test_auth.py"], [("test_auth.py", "auth.py")])
    report = backtest(view, [_commit("auth.py", "test_auth.py")] * 3, k=5)

    seeded_on_test = [p for p in report.predictions if p.seed == "test_auth.py"]
    assert seeded_on_test
    assert all(p.predicted == ("auth.py",) for p in seeded_on_test)
    assert all(p.hits_at_k == 1 for p in seeded_on_test)


def test_dependents_still_outrank_dependencies() -> None:
    """Both directions are ranked, but not equally: blast radius is the
    product's claim, so a dependent is named before a dependency."""
    view = _view(
        ["seed.py", "dependent.py", "dependency.py"],
        [("dependent.py", "seed.py"), ("seed.py", "dependency.py")],
    )
    report = backtest(view, [_commit("seed.py", "dependent.py")] * 3, k=5)

    seeded = [p for p in report.predictions if p.seed == "seed.py"]
    assert seeded
    assert all(p.predicted[0] == "dependent.py" for p in seeded)
    assert all("dependency.py" in p.predicted for p in seeded)


def test_sweeping_commits_are_excluded() -> None:
    paths = [f"f{i}.py" for i in range(40)]
    view = _view(paths, [])
    report = backtest(view, [_commit(*paths)] * 3, k=5, max_files_per_commit=20)

    assert report.commits_used == 0
    assert report.commits_skipped == 3
    assert report.examples == 0


def test_single_file_commits_teach_nothing() -> None:
    view = _view(["a.py"], [])
    report = backtest(view, [_commit("a.py")] * 5, k=5)
    assert report.examples == 0
