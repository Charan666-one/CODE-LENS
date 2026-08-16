"""How a blast radius is ordered — the one place the ranking is decided.

## What this replaces, and why

The first ranking was `1/distance + fan_in/1000`. Read it as a formula and it
looks like two signals; read it as numbers and it is one. Distance-1 scores
1.0 and distance-2 scores 0.5, so fan-in could only change the order between
two bands if it differed by 500 — which never happens, because a file with
500 direct dependents does not exist in any of the repositories measured. So
fan-in was a tiebreak inside a distance band and nothing more, and the
ranking was **distance, alphabetically broken**.

That is not a bad first ranking. Distance is genuinely the strongest single
signal. But it is a very coarse one: on a real repo a blast radius has
dozens of files at distance 2, and the graph was ordering them by node id.

## The four signals, and why these four

Every one of them was already computed and stored before this module existed.
Nothing here is a new measurement, and nothing here is a guess dressed as a
measurement:

* **distance** — hops from the change. The product's core claim.
* **co-change** — the Jaccard strength on the `CO_CHANGES` edge
  (`graph/co_change.py`). Files that historically ship together.
* **churn** — `node.churn_count`, commits touching the file
  (`ingestion/git_history.py`). A file that never changes is unlikely to be
  in the next commit whatever the graph says.
* **structure** — fan-in over CALLS + IMPORTS. A hub breaks more widely.

## Normalisation, which is the actual fix

Each signal is mapped into `[0, 1]` *before* it is weighted, so a weight
means what it says. Two of them need shaping first:

`churn` and `fan_in` are heavy-tailed — one file with 400 commits among
thirty with fewer than ten. Min-maxed raw, that file scores 1.0 and every
other file scores approximately 0, which is a switch rather than a signal.
Both are therefore `log1p`-compressed before min-max, so the difference
between 2 and 20 commits survives alongside the difference between 20 and
400.

Min-max is computed **per query, over the candidates of that query**. The
alternative — normalising against the whole repository — makes a small
module's ranking depend on whether some unrelated file elsewhere is busy.
The question is "which of *these* files matters most", so these files are
the population.

## Where the numbers come from

`History` is a protocol with two implementations, and the split is not
incidental — it is what keeps the benchmark honest. The product reads
co-change and churn off the graph, which is correct: when a user asks, all
history is in the past. The benchmark cannot do that, because the graph's
history *includes the commit being graded* — a co-change edge would be
"predicting" a pair it had already watched change together. `WindowedHistory`
recomputes both signals from strictly older commits, which is the same
correction the popularity baseline needed in `ledger/backtest.py`.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

from app.graph.schema import EdgeKind, NodeKind
from app.graph.traversal import GraphView
from app.ingestion.git_history import Commit

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}

@dataclass(frozen=True)
class Weights:
    """What each normalised signal is worth. See `WEIGHT_PROVENANCE`.

    A dataclass rather than four module constants so `scripts/rank_sweep.py`
    can search over them without mutating globals — a sweep that reassigns
    module state is one import away from silently changing production.
    """

    distance: float = 1.0
    co_change: float = 1.0
    churn: float = 1.0
    structure: float = 1.0


DEFAULT_WEIGHTS = Weights()

#: How these numbers were arrived at, so nobody has to guess later.
#:
#: Grid-searched by `scripts/rank_sweep.py` on four repositories (requests,
#: flask, click, itsdangerous) and reported on four the search never saw
#: (fastapi, express, jinja, markupsafe). Fitting and reporting on the same
#: set would have produced a better number and a worse ranking.
#:
#: **These are not the argmax, deliberately.** The search surface is a
#: plateau: everything from roughly (0.5, 1.0, 1.0) to (2.5, 2.5, 2.5) scores
#: between 0.260 and 0.2625 on the fit set — a 1% spread — and the argmax
#: moved to whatever the grid's edge happened to be, twice, as the grid was
#: widened. A maximum that relocates when you enlarge the search is not a
#: maximum; it is where the search stopped. What the sweep does establish is
#: that having all four signals *on* is worth about 19% over distance alone,
#: while their exact ratio is worth about 1%.
#:
#: So these are the simplest point on the plateau — each normalised signal
#: counts once — chosen on the fit set, where it costs 0.6% against the
#: argmax. See LEDGER.md entry #3.
WEIGHT_PROVENANCE = "plateau midpoint, fit on 4 repos, held out on 4 — LEDGER.md #3"


@dataclass(frozen=True)
class Candidate:
    """One file the graph is proposing, with the evidence behind it.

    File-level on purpose. The graph ranks symbols, but the question the
    ranking answers — "what else will I have to change?" — is asked and
    answered in files, and a list naming four functions from one file has
    spent four of its ten slots on one answer.
    """

    file_path: str
    distance: int  # nearest hop, over every symbol in this file
    fan_in: int  # largest fan-in, likewise


class History(Protocol):
    """The two facts that come from git rather than from the AST."""

    def co_change(self, seed_file: str, other_file: str) -> float:
        """Jaccard strength between two files, 0.0 when there is no edge."""

    def churn(self, file_path: str) -> int:
        """Commits touching this file."""


class GraphHistory:
    """History as the stored graph records it — the product's view.

    Correct for a user asking now: everything in the graph is the past.
    Wrong for a benchmark grading a commit inside that past, which is what
    `WindowedHistory` exists for.
    """

    #: Key under which the built tables live on `GraphView.memo`.
    MEMO_KEY = "ranking.graph_history"

    def __init__(self, view: GraphView) -> None:
        self._view = view
        self._strength: dict[tuple[str, str], float] | None = None
        self._churn: dict[str, int] | None = None

    def _load(self) -> None:
        """Build the two lookup tables once per graph, not once per query.

        Both are full scans — every node for churn, every edge for co-change —
        and blast radius is asked repeatedly against one loaded graph. Caching
        on the view rather than on `self` is what makes that a single scan for
        the life of the graph; a per-instance cache would rebuild on every
        query, which on fastapi is 8,254 nodes and 13,890 edges each time.
        """
        if self._strength is not None:
            return
        cached = self._view.memo.get(self.MEMO_KEY)
        if cached is not None:
            self._strength, self._churn = cached  # type: ignore[assignment]
            return
        strength: dict[tuple[str, str], float] = {}
        churn: dict[str, int] = {}
        for node in self._view.nodes_by_id.values():
            if node.kind is NodeKind.FILE and node.file_path:
                churn[node.file_path] = node.churn_count or 0
        for source, target, attributes in self._view.g.edges(data=True):
            if attributes["kind"] is not EdgeKind.CO_CHANGES:
                continue
            left = self._view.node(source)
            right = self._view.node(target)
            if left is None or right is None or not left.file_path or not right.file_path:
                continue
            weight = float(attributes.get("weight") or 0.0)
            # CO_CHANGES is emitted once per pair but means an undirected
            # relation (co_change.py says so); store both directions.
            strength[(left.file_path, right.file_path)] = weight
            strength[(right.file_path, left.file_path)] = weight
        self._strength = strength
        self._churn = churn
        self._view.memo[self.MEMO_KEY] = (strength, churn)

    def co_change(self, seed_file: str, other_file: str) -> float:
        self._load()
        assert self._strength is not None
        return self._strength.get((seed_file, other_file), 0.0)

    def churn(self, file_path: str) -> int:
        self._load()
        assert self._churn is not None
        return self._churn.get(file_path, 0)


class WindowedHistory:
    """History as of a point in the past — strictly older commits only.

    Built for `ledger/backtest.py`. Grading a commit with signals derived
    from that same commit is how a benchmark reports a ranking that cannot
    exist in production; the popularity baseline had exactly this bug in its
    first version and it inflated the baseline enough to change the
    conclusion. Recomputing per example is affordable because it is arithmetic
    over commit lists — no reparse, no checkout.

    The Jaccard ratio and the sweep cutoff match `graph/co_change.py`, so the
    benchmark grades the signal the product actually ships rather than a
    convenient approximation of it. The minimum-shared-commits floor is *not*
    applied: it exists to keep a rendered graph readable, and dropping weak
    evidence from a score is a different decision from dropping it from a
    picture.
    """

    #: Same as co_change.MAX_FILES_PER_COMMIT: a sweep is not coupling.
    MAX_FILES_PER_COMMIT = 30

    def __init__(self, commits: list[Commit], *, before_index: int) -> None:
        window = commits[before_index + 1 :]
        churn: dict[str, int] = defaultdict(int)
        changed_in: dict[str, int] = defaultdict(int)
        together: dict[tuple[str, str], int] = defaultdict(int)

        for commit in window:
            touched = sorted(set(commit.files))
            for path in touched:
                churn[path] += 1
            if len(touched) > self.MAX_FILES_PER_COMMIT or len(touched) < 2:
                continue
            for path in touched:
                changed_in[path] += 1
            for index, left in enumerate(touched):
                for right in touched[index + 1 :]:
                    together[(left, right)] += 1

        self._churn = dict(churn)
        self._changed_in = dict(changed_in)
        self._together = dict(together)

    def co_change(self, seed_file: str, other_file: str) -> float:
        if seed_file == other_file:
            return 0.0
        key = (seed_file, other_file) if seed_file < other_file else (other_file, seed_file)
        shared = self._together.get(key, 0)
        if not shared:
            return 0.0
        union = self._changed_in.get(seed_file, 0) + self._changed_in.get(other_file, 0) - shared
        return shared / union if union > 0 else 0.0

    def churn(self, file_path: str) -> int:
        return self._churn.get(file_path, 0)


def same_file_score(distance: int, weights: Weights = DEFAULT_WEIGHTS) -> float:
    """The score for a dependent that lives in the file being changed.

    Not a prediction. Every other candidate answers "will I *also* have to
    open this file?", which is a guess; a symbol in the file already being
    edited is already open, and its breakage is a certainty. So it outranks
    every cross-file candidate by construction — the floor is the sum of the
    weights, which is the most any cross-file score can reach — and distance
    orders within that band.

    Without this the same-file symbols fell out of file-level scoring (a file
    is not its own candidate) and landed on a bare `1/distance`, on a
    different scale from everything else. On the three-file fixture that put
    two unrelated modules above the function that directly calls the one being
    changed, which is exactly backwards.
    """
    floor = weights.distance + weights.co_change + weights.churn + weights.structure
    return floor + 1.0 / distance


def _normalised(values: list[float]) -> list[float]:
    """Min-max to [0, 1]. A flat population maps to 0, not to 1.

    When every candidate has the same churn, churn distinguishes nothing, and
    a signal that distinguishes nothing must contribute nothing. Mapping the
    flat case to 1.0 would instead add a constant to every score — harmless
    for the ordering, but it would make the weight unreadable and the
    contribution report a lie.
    """
    if not values:
        return []
    low = min(values)
    high = max(values)
    if high <= low:
        return [0.0] * len(values)
    span = high - low
    return [(value - low) / span for value in values]


@dataclass(frozen=True)
class Scored:
    """A candidate with its final score and each signal's contribution.

    The per-signal breakdown is not diagnostics-only. `RankedNode.reasons`
    already promises the *why* of a rank, and "distance 2, fan-in 7" does not
    explain why this file outranks another at distance 2 with fan-in 9. The
    contributions do, in the same units the score is made of.
    """

    file_path: str
    score: float
    distance: int
    fan_in: int
    co_change: float
    churn: int
    contributions: dict[str, float]


def score_candidates(
    candidates: list[Candidate],
    *,
    seed_file: str,
    history: History,
    weights: Weights = DEFAULT_WEIGHTS,
) -> list[Scored]:
    """Order candidates by weighted, normalised evidence. Best first.

    Deterministic: ties break on file path, so two runs over one graph give
    one answer.
    """
    if not candidates:
        return []

    # Distance needs no min-max. 1/d is already bounded, already ordered the
    # right way, and its own curve — 1.0, 0.5, 0.33 — is the statement that a
    # direct dependent matters much more than a transitive one. Min-maxing it
    # against the candidates present would make the gap between distance 1 and
    # 2 depend on whether anything happened to sit at distance 6.
    distance_relevance = [1.0 / candidate.distance for candidate in candidates]

    co_change_raw = [history.co_change(seed_file, c.file_path) for c in candidates]
    churn_raw = [float(history.churn(c.file_path)) for c in candidates]

    # log1p before min-max: both are heavy-tailed, and raw min-max on a
    # heavy tail is a switch that fires for the single busiest file.
    churn_relevance = _normalised([math.log1p(value) for value in churn_raw])
    structure_relevance = _normalised([math.log1p(c.fan_in) for c in candidates])
    # Jaccard is already a ratio in [0, 1] and comparable across repositories;
    # min-maxing it would rescale "0.3, everything is weakly coupled" into
    # "1.0, this is the strongest link here" and lose the absolute claim.
    co_change_relevance = [min(1.0, value) for value in co_change_raw]

    scored: list[Scored] = []
    for index, candidate in enumerate(candidates):
        contributions = {
            "distance": weights.distance * distance_relevance[index],
            "co_change": weights.co_change * co_change_relevance[index],
            "churn": weights.churn * churn_relevance[index],
            "structure": weights.structure * structure_relevance[index],
        }
        scored.append(
            Scored(
                file_path=candidate.file_path,
                score=sum(contributions.values()),
                distance=candidate.distance,
                fan_in=candidate.fan_in,
                co_change=co_change_raw[index],
                churn=int(churn_raw[index]),
                contributions=contributions,
            )
        )

    scored.sort(key=lambda entry: (-entry.score, entry.file_path))
    return scored


def candidates_from_ranked(
    view: GraphView,
    ranked: list,
    *,
    seed_file: str,
) -> list[Candidate]:
    """Collapse a symbol-level ranked list into file-level candidates.

    Nearest hop and largest fan-in win, rather than first-seen. First-seen let
    the arbitrary order of a symbol list decide a file's distance; a file is as
    close to the change as its closest symbol, and that is a fact about the
    file rather than about iteration order.
    """
    nearest: dict[str, tuple[int, int]] = {}
    for entry in ranked:
        node = view.node(entry.node_id)
        if node is None or not node.file_path:
            continue
        path = node.file_path
        if path == seed_file:
            continue
        distance = int(entry.reasons.get("distance", 1))
        fan_in = entry.reasons.get("fan_in")
        if fan_in is None:
            fan_in = view.fan_in(entry.node_id, DEPENDENCY_KINDS)
        existing = nearest.get(path)
        if existing is None:
            nearest[path] = (distance, int(fan_in))
        else:
            nearest[path] = (min(existing[0], distance), max(existing[1], int(fan_in)))
    return [
        Candidate(file_path=path, distance=distance, fan_in=fan_in)
        for path, (distance, fan_in) in nearest.items()
    ]
