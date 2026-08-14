"""Backtesting blast radius against history that already happened.

## The labelled data nobody had to collect

A commit that changes several files is a developer answering our question for
us. They changed `auth.py`, and they *also had to change* `session.py` and
`tests/test_auth.py`. Ask the graph "what breaks if I change auth.py?", rank
the answer, and check how far down the list the other two appear. No survey,
no instrumentation, no users — every repository on GitHub is already full of
graded examples.

## Why precision@K and not precision

Blast radius returns a transitive closure, which on a real repo is hundreds
of files. Scored as a set, precision is near zero and the number is
meaningless — but so is the criticism, because nobody reads a set. They read
the top of a ranked list. So the measure is precision@K and recall@K: of the
first K files the ranking names, how many did the developer actually touch.

## Why a baseline is not optional

"Precision@10 of 0.31" means nothing on its own. Some files change in almost
every commit, so a strategy of "always guess the busiest files" scores
surprisingly well and involves no graph at all. The report therefore always
runs that baseline on exactly the same examples. The graph earns its keep
only by the gap, and if there is no gap, the honest thing is to publish that.

## The leakage this design accepts, and why it is bounded

Predictions use the graph built from the working tree at HEAD, while the
examples come from earlier commits. A file's dependencies today are not
exactly what they were then. Rebuilding the graph per commit removes this
entirely and costs a checkout plus a full parse per example — hours instead
of minutes — so `backtest()` takes the graph it is given and the caller
decides. The default script samples only recent history, where HEAD is the
closest approximation available, and says so in its output.

The direction of the bias is worth stating plainly: it flatters the graph.
A dependency added *after* the example commit can only help the prediction.
So the honest reading of a HEAD-graph result is **an upper bound**, and it
is reported as one.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from app.graph.schema import EdgeKind, NodeKind
from app.graph.traversal import GraphView
from app.ingestion.git_history import Commit

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}

#: Commits touching more than this are sweeps, not changes — the same
#: reasoning as co_change.py. A 200-file reformat would dominate the score
#: with examples nobody would ever ask a tool about.
MAX_FILES_PER_COMMIT = 20

#: A one-file commit teaches nothing: there is no "what else" to predict.
MIN_FILES_PER_COMMIT = 2

#: Where the ranked list is cut. 10 is roughly what a person reads before
#: deciding the tool is guessing.
DEFAULT_K = 10


@dataclass
class ScoredPrediction:
    """One graded example: a seed file, and what the graph said about it."""

    commit_index: int
    seed: str
    actual: frozenset[str]  # the other files in the same commit
    predicted: tuple[str, ...]  # ranked, best first
    hits_at_k: int
    first_hit_rank: int | None  # 1-based, None if the ranking never hit

    @property
    def precision_at_k(self) -> float:
        return self.hits_at_k / len(self.predicted) if self.predicted else 0.0

    @property
    def recall_at_k(self) -> float:
        return self.hits_at_k / len(self.actual) if self.actual else 0.0

    @property
    def reciprocal_rank(self) -> float:
        return 1.0 / self.first_hit_rank if self.first_hit_rank else 0.0


@dataclass
class BacktestReport:
    """What the run measured, with enough context to argue with it."""

    k: int
    examples: int
    commits_used: int
    commits_skipped: int
    predictions: list[ScoredPrediction] = field(default_factory=list)
    #: The same examples scored by "always guess the busiest files".
    baseline: list[ScoredPrediction] = field(default_factory=list)

    @staticmethod
    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    def precision(self) -> float:
        return self._mean([p.precision_at_k for p in self.predictions])

    def recall(self) -> float:
        return self._mean([p.recall_at_k for p in self.predictions])

    def mrr(self) -> float:
        """Mean reciprocal rank — how near the top the first correct file is.
        The measure that matches how the list is actually read."""
        return self._mean([p.reciprocal_rank for p in self.predictions])

    def hit_rate(self) -> float:
        """Share of examples where the top K contained *anything* right. The
        blunt version of the question: was the answer useful at all?"""
        if not self.predictions:
            return 0.0
        return sum(1 for p in self.predictions if p.hits_at_k) / len(self.predictions)

    def baseline_precision(self) -> float:
        return self._mean([p.precision_at_k for p in self.baseline])

    def baseline_hit_rate(self) -> float:
        if not self.baseline:
            return 0.0
        return sum(1 for p in self.baseline if p.hits_at_k) / len(self.baseline)

    def lift(self) -> float:
        """How many times better than guessing the busiest files. 1.0 means
        the graph added nothing, and that is a publishable result too."""
        base = self.baseline_precision()
        return self.precision() / base if base else 0.0

    def silent(self) -> int:
        """Examples where the graph named nothing at all.

        The distinction this exposes is the difference between two very
        different failures: a graph that answers wrongly is a ranking
        problem, and a graph that answers *nothing* is a parser problem. Both
        score zero, and confusing them would send the next month of work in
        the wrong direction.
        """
        return sum(1 for p in self.predictions if not p.predicted)

    def answered(self) -> list[ScoredPrediction]:
        """Examples where the graph actually named something.

        Both numbers have to be published, because each alone is a lie of a
        different kind. The unconditional score is what a user experiences —
        ask about a leaf file and you get nothing, and nothing is not an
        answer. But scoring silence as a *miss* also punishes correctness:
        for a test file, "nothing depends on this" is exactly right, and the
        commit's other files were its dependencies, not its dependents. The
        conditional score isolates the different question — when the graph
        does speak, is the ranking any good — and that is the one that says
        whether the ranking needs work or the parser does.
        """
        return [p for p in self.predictions if p.predicted]

    def answered_precision(self) -> float:
        return self._mean([p.precision_at_k for p in self.answered()])

    def answered_hit_rate(self) -> float:
        answered = self.answered()
        if not answered:
            return 0.0
        return sum(1 for p in answered if p.hits_at_k) / len(answered)

    def answered_mrr(self) -> float:
        return self._mean([p.reciprocal_rank for p in self.answered()])

    def mean_predicted(self) -> float:
        """Average length of a non-empty ranked answer, capped at K. Well
        below K means the closure is running out of dependents to name."""
        lengths = [len(p.predicted) for p in self.predictions if p.predicted]
        return self._mean([float(n) for n in lengths])


def _rank_by_blast_radius(view: GraphView, seed_file: str, limit: int) -> list[str]:
    """Files the graph says are affected by a change to `seed_file`, best first.

    Function- and class-level dependents are lifted to their files, keeping
    each file's best rank: the comparison is against a commit, and commits
    touch files.
    """
    from app.queries.base import QueryError
    from app.queries.blast_radius import blast_radius

    node_id = f"{NodeKind.FILE.value}:{seed_file}"
    if not view.has_node(node_id):
        return []
    try:
        result = blast_radius(view, node_id=node_id)
    except QueryError:
        return []

    ordered: list[str] = []
    seen: set[str] = set()
    for entry in result.ranked:
        node = view.node(entry.node_id)
        if node is None or not node.file_path:
            continue
        path = node.file_path
        if path == seed_file or path in seen:
            continue
        seen.add(path)
        ordered.append(path)
        if len(ordered) >= limit:
            break
    return ordered


def popularity_baseline(
    commits: list[Commit], tracked: set[str], *, before_index: int | None = None
) -> list[str]:
    """The strategy with no graph in it: the files that change most often.

    This is the bar. A dependency graph that cannot beat "guess whatever
    changed last month" is not earning its complexity, and the report says so
    rather than quietly omitting the comparison.

    `before_index` is not optional in practice, and leaving it out was a real
    bug in the first version of this benchmark. `commits` is newest-first, so
    counting over *all* of it lets the baseline see the very commit it is
    being graded on — it "predicts" files by having already watched them
    change. That inflated the baseline enough to beat the graph, which is a
    conclusion the code would have published as fact. Counting strictly older
    commits (`commits[before_index + 1:]`) is the only causally honest
    version: information from the future cannot reach a prediction.
    """
    window = commits if before_index is None else commits[before_index + 1 :]
    counts: Counter[str] = Counter()
    for commit in window:
        for path in commit.files:
            if path in tracked:
                counts[path] += 1
    return [path for path, _ in counts.most_common()]


def backtest(
    view: GraphView,
    commits: list[Commit],
    *,
    k: int = DEFAULT_K,
    max_examples: int | None = None,
    max_files_per_commit: int = MAX_FILES_PER_COMMIT,
) -> BacktestReport:
    """Grade blast radius against what developers actually changed together.

    `commits` should be newest-first, as `read_log` returns them.
    """
    tracked = {
        node.file_path
        for node in view.nodes_by_id.values()
        if node.kind is NodeKind.FILE and node.file_path
    }
    report = BacktestReport(k=k, examples=0, commits_used=0, commits_skipped=0)

    for index, commit in enumerate(commits):
        touched = sorted({p for p in commit.files if p in tracked})
        if not (MIN_FILES_PER_COMMIT <= len(touched) <= max_files_per_commit):
            report.commits_skipped += 1
            continue
        report.commits_used += 1

        # Recomputed per commit, over strictly older history only. See the
        # note in popularity_baseline: a shared baseline computed once over
        # everything can see the future, and scores far too well for it.
        ranked_baseline = popularity_baseline(commits, tracked, before_index=index)

        for seed in touched:
            actual = frozenset(touched) - {seed}
            predicted = tuple(_rank_by_blast_radius(view, seed, k))
            if not predicted:
                # No prediction at all is not a wrong prediction, but it is
                # not a right one either — it counts as an example with zero
                # hits, because silently dropping the hard cases is how a
                # benchmark ends up flattering itself.
                report.predictions.append(
                    ScoredPrediction(index, seed, actual, (), 0, None)
                )
            else:
                report.predictions.append(
                    _score(index, seed, actual, predicted)
                )

            base = tuple(p for p in ranked_baseline if p != seed)[:k]
            report.baseline.append(_score(index, seed, actual, base))

            report.examples += 1
            if max_examples is not None and report.examples >= max_examples:
                return report

    return report


def _score(
    index: int, seed: str, actual: frozenset[str], predicted: tuple[str, ...]
) -> ScoredPrediction:
    hits = sum(1 for path in predicted if path in actual)
    first: int | None = None
    for rank, path in enumerate(predicted, start=1):
        if path in actual:
            first = rank
            break
    return ScoredPrediction(
        commit_index=index,
        seed=seed,
        actual=actual,
        predicted=predicted,
        hits_at_k=hits,
        first_hit_rank=first,
    )
