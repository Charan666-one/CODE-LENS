"""The ranking decides what a user reads first, so its arithmetic is pinned.

Three things are easy to get wrong here and expensive to notice later: a
signal that silently dominates because of its scale, a normaliser that turns
a flat population into a constant, and a history window that can see the
commit it is being graded on.
"""

from __future__ import annotations

from app.graph.schema import Edge, EdgeKind, KnowledgeGraph, Node, NodeKind, RepoSnapshot
from app.graph.traversal import GraphView
from app.ingestion.git_history import Commit
from app.queries.blast_radius import blast_radius
from app.queries.ranking import (
    Candidate,
    Weights,
    WindowedHistory,
    _normalised,
    score_candidates,
)


class FakeHistory:
    def __init__(
        self, co: dict[str, float] | None = None, churn: dict[str, int] | None = None
    ) -> None:
        self._co = co or {}
        self._churn = churn or {}

    def co_change(self, seed_file: str, other_file: str) -> float:
        return self._co.get(other_file, 0.0)

    def churn(self, file_path: str) -> int:
        return self._churn.get(file_path, 0)


def _commit(*paths: str) -> Commit:
    return Commit(author="d@e.com", date="2026-01-01T00:00:00+00:00", files=paths)


def _file(path: str) -> Node:
    return Node(
        id=f"file:{path}",
        kind=NodeKind.FILE,
        name=path.rsplit("/", 1)[-1],
        qualified_name=path,
        file_path=path,
    )


# ── normalisation ─────────────────────────────────────────────────────────


def test_a_flat_signal_contributes_nothing() -> None:
    """When every candidate has the same churn, churn distinguishes nothing.

    Mapping the flat case to 1.0 would leave the ordering intact but add a
    constant to every score, making the weight unreadable and the reported
    contribution a lie about what did the work.
    """
    assert _normalised([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]
    assert _normalised([]) == []
    assert _normalised([1.0, 3.0, 5.0]) == [0.0, 0.5, 1.0]


def test_a_heavy_tail_does_not_become_a_switch() -> None:
    """One file with 400 commits among small ones must not flatten the rest.

    Raw min-max would score the outlier 1.0 and put every other candidate
    within 5% of zero, which is an on/off switch rather than a signal. log1p
    first keeps the difference between 2 and 20 commits legible.
    """
    candidates = [
        Candidate(file_path=f"f{i}.py", distance=2, fan_in=0) for i in range(4)
    ]
    history = FakeHistory(churn={"f0.py": 1, "f1.py": 5, "f2.py": 20, "f3.py": 400})
    weights = Weights(distance=0.0, co_change=0.0, churn=1.0, structure=0.0)

    scored = {s.file_path: s.score for s in score_candidates(
        candidates, seed_file="seed.py", history=history, weights=weights
    )}

    # The mid-range file keeps real separation from the smallest one rather
    # than being crushed against it by the outlier.
    assert scored["f2.py"] - scored["f1.py"] > 0.15
    assert scored["f3.py"] == 1.0


# ── domination by scale ───────────────────────────────────────────────────


def test_no_signal_can_dominate_by_its_own_units() -> None:
    """Every signal is capped at its weight, whatever its raw magnitude.

    This is the bug the old `1/distance + fan_in/1000` had in reverse: fan-in
    was scaled so far down it could never change an order. Bounding each
    signal to [0, 1] before weighting is what makes a weight mean what it says.
    """
    candidates = [
        Candidate(file_path="huge.py", distance=1, fan_in=100_000),
        Candidate(file_path="small.py", distance=1, fan_in=0),
    ]
    history = FakeHistory(churn={"huge.py": 999_999})
    weights = Weights(distance=1.0, co_change=0.6, churn=0.25, structure=0.15)

    scored = score_candidates(
        candidates, seed_file="seed.py", history=history, weights=weights
    )
    by_path = {s.file_path: s for s in scored}

    assert by_path["huge.py"].contributions["structure"] == weights.structure
    assert by_path["huge.py"].contributions["churn"] == weights.churn
    # Absurd raw values cannot buy more than the two weights are worth.
    gap = by_path["huge.py"].score - by_path["small.py"].score
    assert abs(gap - (weights.structure + weights.churn)) < 1e-9


def test_distance_still_leads_but_no_longer_decides_alone() -> None:
    """The point of the change, stated as a test.

    A direct dependent outranks a distant one when nothing else separates
    them — distance is still the strongest single signal. But a file three
    hops away that ships with the seed in most commits *can* overtake it,
    which the old ranking made arithmetically impossible.
    """
    near = Candidate(file_path="near.py", distance=1, fan_in=0)
    far = Candidate(file_path="far.py", distance=3, fan_in=0)

    quiet = score_candidates(
        [near, far], seed_file="seed.py", history=FakeHistory()
    )
    assert [s.file_path for s in quiet] == ["near.py", "far.py"]

    coupled = score_candidates(
        [near, far],
        seed_file="seed.py",
        history=FakeHistory(co={"far.py": 1.0}, churn={"far.py": 40, "near.py": 1}),
    )
    assert [s.file_path for s in coupled] == ["far.py", "near.py"]


def test_contributions_add_up_to_the_score() -> None:
    """The breakdown shown to a reader has to be the score, not a story
    about it."""
    scored = score_candidates(
        [
            Candidate(file_path="a.py", distance=1, fan_in=4),
            Candidate(file_path="b.py", distance=2, fan_in=9),
        ],
        seed_file="seed.py",
        history=FakeHistory(co={"a.py": 0.4}, churn={"a.py": 3, "b.py": 30}),
    )
    for entry in scored:
        assert abs(sum(entry.contributions.values()) - entry.score) < 1e-9


# ── the leakage guard ─────────────────────────────────────────────────────


def test_windowed_history_cannot_see_the_commit_being_graded() -> None:
    """The whole reason this class exists.

    Co-change read off the finished graph includes the commit under test, so
    "these two files change together" is trivially true of the example being
    scored. A ranking using it would look excellent and reproduce in
    production not at all — the same bug the popularity baseline had.
    """
    commits = [
        _commit("a.py", "b.py"),  # index 0: the example being graded
        _commit("a.py", "c.py"),
        _commit("a.py", "c.py"),
    ]

    grading_commit_zero = WindowedHistory(commits, before_index=0)
    assert grading_commit_zero.co_change("a.py", "b.py") == 0.0  # never seen
    assert grading_commit_zero.co_change("a.py", "c.py") == 1.0  # twice, before
    assert grading_commit_zero.churn("b.py") == 0
    assert grading_commit_zero.churn("a.py") == 2


def test_a_graph_with_no_history_degrades_to_structure() -> None:
    """`parse_ingested` alone produces no churn and no CO_CHANGES — those are
    added by the pipeline's metrics stage. Blast radius must still rank, on
    the signals it does have, rather than dividing by an absent population.

    This is the shape `scripts/backtest.py` builds, and it is also what any
    caller who parses without running the pipeline gets.
    """
    graph = KnowledgeGraph(
        snapshot=RepoSnapshot(
            repo_url="file:///t",
            commit_sha="x",
            primary_language="Python",
            languages={"py": 3},
            file_count=3,
            analyzed_at="2026-01-01T00:00:00+00:00",
        ),
        nodes=[_file("core.py"), _file("hub.py"), _file("leaf.py")],
        edges=[
            Edge(source_id="file:hub.py", target_id="file:core.py", kind=EdgeKind.IMPORTS),
            Edge(source_id="file:leaf.py", target_id="file:core.py", kind=EdgeKind.IMPORTS),
            # hub.py is imported by leaf.py too, giving it the larger fan-in.
            Edge(source_id="file:leaf.py", target_id="file:hub.py", kind=EdgeKind.IMPORTS),
        ],
    )
    view = GraphView(graph)
    result = blast_radius(view, node_id="file:core.py")

    order = [entry.reasons["file_path"] for entry in result.ranked]
    assert order == ["hub.py", "leaf.py"]  # same distance, hub has the fan-in

    top = result.ranked[0]
    assert top.reasons["contributions"]["churn"] == 0.0
    assert top.reasons["contributions"]["co_change"] == 0.0
    assert top.reasons["contributions"]["structure"] > 0.0
    # The breakdown a reader is shown must reconstruct the score they see.
    assert abs(sum(top.reasons["contributions"].values()) - top.score) < 1e-9


def test_the_changed_file_outranks_every_other_file() -> None:
    """Caught by an existing query test, and worth its own.

    A file is never its own candidate, so its symbols fell out of file-level
    scoring onto a bare `1/distance` — a different scale from the up-to-4.0 a
    cross-file candidate can reach. Two unrelated modules then outranked the
    function that directly calls the one being changed. A symbol in the file
    already open is a certainty, not a prediction, and ranks accordingly.
    """
    graph = KnowledgeGraph(
        snapshot=RepoSnapshot(
            repo_url="file:///t",
            commit_sha="x",
            primary_language="Python",
            languages={"py": 2},
            file_count=2,
            analyzed_at="2026-01-01T00:00:00+00:00",
        ),
        nodes=[
            _file("calc.py"),
            _file("app.py"),
            Node(
                id="function:calc.add",
                kind=NodeKind.FUNCTION,
                name="add",
                qualified_name="calc.add",
                file_path="calc.py",
            ),
            Node(
                id="function:calc.times",
                kind=NodeKind.FUNCTION,
                name="times",
                qualified_name="calc.times",
                file_path="calc.py",
            ),
        ],
        edges=[
            # times() calls add(); app.py is a busy hub that imports calc.py.
            Edge(
                source_id="function:calc.times",
                target_id="function:calc.add",
                kind=EdgeKind.CALLS,
            ),
            Edge(source_id="file:app.py", target_id="file:calc.py", kind=EdgeKind.IMPORTS),
            Edge(source_id="file:app.py", target_id="function:calc.add", kind=EdgeKind.CALLS),
        ],
    )
    result = blast_radius(GraphView(graph), node_id="function:calc.add")

    assert result.ranked[0].node_id == "function:calc.times"


def test_windowed_history_ignores_sweeps() -> None:
    """A 300-file reformat is not evidence of coupling, here as in
    graph/co_change.py — the two must agree or the benchmark is grading a
    signal the product does not ship."""
    sweep = _commit(*[f"f{i}.py" for i in range(40)])
    commits = [_commit("seed.py"), sweep, sweep, sweep]

    history = WindowedHistory(commits, before_index=0)
    assert history.co_change("f0.py", "f1.py") == 0.0
    # Churn still counts it: the file did change, and churn is not a claim
    # about coupling.
    assert history.churn("f0.py") == 3
