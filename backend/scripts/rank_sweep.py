#!/usr/bin/env python3
"""Fit the ranking weights on one set of repositories, report on another.

    .venv/bin/python scripts/rank_sweep.py

## Why a split, and not just "try weights until the number goes up"

Four weights searched against eight repositories, then reported on those same
eight, would produce a better number and a worse ranking. The search would be
free to encode "on express, churn matters less" — a fact about express, not
about software — and the published figure would be the score of a model
fitted to its own test set.

So the repositories are split once, by hand, before any weight is tried:

    fit       requests, flask, click, itsdangerous
    held out  fastapi, express, jinja, markupsafe

The search never sees the held-out four. Both numbers are printed, and the
held-out one is the result. If the two disagree sharply, the weights are
fitted to the fit set and the honest conclusion is that this model does not
generalise.

Graphs and history are parsed once per repository and reused across every
weight combination — the search is over scoring arithmetic, not over parsing,
so a sweep of 100 combinations costs one parse and 100 cheap re-rankings.
"""

from __future__ import annotations

import itertools
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.traversal import GraphView  # noqa: E402
from app.ingestion import ingest  # noqa: E402
from app.ingestion.git_history import read_log  # noqa: E402
from app.ledger.backtest import Example, collect_examples, grade  # noqa: E402
from app.parser import parse_ingested  # noqa: E402
from app.queries.ranking import Weights  # noqa: E402

FIT = [
    "https://github.com/psf/requests",
    "https://github.com/pallets/flask",
    "https://github.com/pallets/click",
    "https://github.com/pallets/itsdangerous",
]

HELD_OUT = [
    "https://github.com/tiangolo/fastapi",
    "https://github.com/expressjs/express",
    "https://github.com/pallets/jinja",
    "https://github.com/pallets/markupsafe",
]

K = 10
COMMITS = 300


@dataclass
class Loaded:
    source: str
    examples: list[Example]


def load(sources: list[str]) -> list[Loaded]:
    """Parse each repository once and build its examples once.

    This is the whole reason a sweep is affordable. Collecting examples runs
    the graph walks; grading them is arithmetic. A hundred weight vectors cost
    one parse and a hundred cheap re-gradings.
    """
    loaded: list[Loaded] = []
    for source in sources:
        started = time.monotonic()
        ingested = ingest(source)
        graph = parse_ingested(ingested)
        commits = read_log(ingested.root)[:COMMITS]
        if not commits:
            print(f"  {source}: no history, skipped", flush=True)
            continue
        examples, _, _ = collect_examples(GraphView(graph), commits, k=K)
        loaded.append(Loaded(source, examples))
        print(
            f"  {source}: {len(graph.nodes):,} nodes, {len(commits):,} commits,"
            f" {len(examples):,} examples ({time.monotonic() - started:.1f}s)",
            flush=True,
        )
    return loaded


def evaluate(loaded: list[Loaded], weights: Weights) -> tuple[float, float, float]:
    """Pooled precision@K, baseline precision@K, and silence rate.

    Pooled over examples rather than averaged over repositories: a repo with
    54 examples and one with 254 are not equally informative, and averaging
    the two would let the smallest repository swing the fit hardest.
    """
    precisions: list[float] = []
    baselines: list[float] = []
    silent = 0
    total = 0
    for entry in loaded:
        report = grade(entry.examples, k=K, weights=weights)
        precisions.extend(p.precision_at_k for p in report.predictions)
        baselines.extend(p.precision_at_k for p in report.baseline)
        silent += report.silent()
        total += len(report.predictions)

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return mean(precisions), mean(baselines), (silent / total if total else 0.0)


def main() -> int:
    print("Loading fit set")
    fit = load(FIT)
    print("Loading held-out set")
    held = load(HELD_OUT)

    # Distance is pinned at 1.0 as the unit the others are measured against.
    # Searching all four independently would only rediscover that the ranking
    # is invariant to a common scale factor, at four times the cost.
    # Widened after a first pass put the optimum on the grid's own edge at
    # churn=0.5 and structure=0.5. A maximum that sits on a boundary is not a
    # maximum, it is the search running out of room, and reporting it as the
    # best weights would have been reporting where the grid stopped.
    grid = {
        "co_change": [0.0, 0.5, 1.0, 1.5, 2.5],
        "churn": [0.0, 0.5, 1.0, 1.5, 2.5, 4.0, 6.0],
        "structure": [0.0, 0.5, 1.0, 1.5, 2.5, 4.0, 6.0],
    }
    combinations = list(
        itertools.product(grid["co_change"], grid["churn"], grid["structure"])
    )
    print(f"\nSearching {len(combinations)} weight combinations on the fit set\n")

    results: list[tuple[float, Weights, float]] = []
    for co_change, churn, structure in combinations:
        weights = Weights(
            distance=1.0, co_change=co_change, churn=churn, structure=structure
        )
        precision, baseline, silence = evaluate(fit, weights)
        results.append((precision, weights, silence))
        print(
            f"  co={co_change:<5} churn={churn:<5} struct={structure:<5}"
            f"  precision@{K} {precision:.4f}  (baseline {baseline:.4f})",
            flush=True,
        )

    results.sort(key=lambda row: -row[0])
    print("\nBest five on the fit set")
    for precision, weights, silence in results[:5]:
        print(
            f"  {precision:.4f}  co={weights.co_change} churn={weights.churn}"
            f" struct={weights.structure}  silence {silence:.1%}"
        )

    best = results[0][1]
    edge = {
        name
        for name, value in (
            ("co_change", best.co_change),
            ("churn", best.churn),
            ("structure", best.structure),
        )
        if value == max(grid[name])
    }
    if edge:
        print(f"\n  WARNING: optimum sits on the grid edge for {sorted(edge)} — widen it")

    print(f"\n{'=' * 72}\nHELD OUT — these four repositories were never searched\n{'=' * 72}")
    # Ablations, so "it got better" can be attributed rather than asserted.
    # Each arm turns one signal on alone at its fitted weight; the last turns
    # them all on. A signal whose arm does nothing did not earn its place.
    arms = [
        ("distance only", Weights(1.0, 0.0, 0.0, 0.0)),
        # The shipped ranking before this change, reconstructed: distance
        # bands intact, fan-in breaking ties inside them. 0.4 is the largest
        # structural weight that cannot lift a distance-2 file past a
        # distance-1 one (the gap there is 0.5), which is exactly what
        # `1/distance + fan_in/1000` did.
        ("old ranking (d, then fan-in)", Weights(1.0, 0.0, 0.0, 0.4)),
        ("+ co-change", Weights(1.0, best.co_change, 0.0, 0.0)),
        ("+ churn", Weights(1.0, 0.0, best.churn, 0.0)),
        ("+ structure", Weights(1.0, 0.0, 0.0, best.structure)),
        ("all four (fitted)", best),
    ]
    for label, weights in arms:
        precision, baseline, silence = evaluate(held, weights)
        print(
            f"  {label:22} precision@{K} {precision:.4f}"
            f"  baseline {baseline:.4f}  lift {precision / baseline if baseline else 0:.2f}x"
            f"  silence {silence:.1%}"
        )

    print(f"\n{'=' * 72}\nSAME ABLATIONS ON THE FIT SET (for comparison only)\n{'=' * 72}")
    for label, weights in arms:
        precision, baseline, _ = evaluate(fit, weights)
        print(f"  {label:22} precision@{K} {precision:.4f}  baseline {baseline:.4f}")

    print(f"\n  chosen weights: {best}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
