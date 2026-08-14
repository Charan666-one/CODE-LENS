#!/usr/bin/env python3
"""Run the Accuracy Ledger backtest against real repositories.

    .venv/bin/python scripts/backtest.py https://github.com/pallets/flask
    .venv/bin/python scripts/backtest.py --k 5 --examples 300 <url> [<url> ...]

Prints, per repo and in total: precision@K, recall@K, hit rate, MRR, and the
same numbers for a graph-free popularity baseline. The gap between them is
the only part that means anything.

Read the result as an upper bound. Predictions use the graph at HEAD while
the examples come from earlier commits, so a dependency added after an
example can only help the prediction. See app/ledger/backtest.py.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph.traversal import GraphView  # noqa: E402
from app.ingestion import ingest  # noqa: E402
from app.ingestion.git_history import read_log  # noqa: E402
from app.ledger import BacktestReport, backtest  # noqa: E402
from app.parser import parse_ingested  # noqa: E402


def run(source: str, *, k: int, examples: int, commits: int) -> BacktestReport | None:
    print(f"\n{'=' * 72}\n{source}\n{'=' * 72}")

    started = time.monotonic()
    ingested = ingest(source)
    graph = parse_ingested(ingested)
    view = GraphView(graph)
    print(
        f"  graph      {len(graph.nodes):,} nodes  {len(graph.edges):,} edges"
        f"  ({time.monotonic() - started:.1f}s)"
    )

    history = read_log(ingested.root)[:commits]
    if not history:
        print("  no history — nothing to grade (was this a shallow clone?)")
        return None
    print(f"  history    {len(history):,} commits")

    # How many files the baseline is choosing between. This is the number
    # that decides whether "guess the busiest 10" is a hard bar or a trivial
    # one: in a repo with 30 parsed files, naming 10 of them is most of the
    # answer, and a graph beating that would be the surprise.
    tracked = sum(1 for n in graph.nodes if n.kind.value == "file")
    print(f"  parsed     {tracked:,} files (the baseline picks {k} of these)")

    started = time.monotonic()
    report = backtest(view, history, k=k, max_examples=examples)
    print(f"  graded     {report.examples:,} examples from "
          f"{report.commits_used:,} usable commits ({time.monotonic() - started:.1f}s)")

    if not report.predictions:
        print("  no multi-file commits touching parsed files — nothing to grade")
        return None

    _print_report(report)
    return report


def _print_report(report: BacktestReport) -> None:
    k = report.k
    print()
    print(f"  {'':22}{'blast radius':>14}{'popularity':>14}")
    print(f"  {'-' * 50}")
    print(
        f"  {f'precision@{k}':22}{report.precision():>14.3f}"
        f"{report.baseline_precision():>14.3f}"
    )
    print(f"  {f'hit rate@{k}':22}{report.hit_rate():>14.3f}"
          f"{report.baseline_hit_rate():>14.3f}")
    print(f"  {f'recall@{k}':22}{report.recall():>14.3f}")
    print(f"  {'MRR':22}{report.mrr():>14.3f}")
    print()
    silent = report.silent()
    share = silent / len(report.predictions) if report.predictions else 0.0
    print(
        f"  named nothing at all:  {silent}/{len(report.predictions)} examples "
        f"({share:.0%});  average answer {report.mean_predicted():.1f} of {k}"
    )
    print()
    print(f"  WHEN THE GRAPH ANSWERS AT ALL ({len(report.answered())} examples)")
    print(f"  {f'precision@{k}':22}{report.answered_precision():>14.3f}")
    print(f"  {f'hit rate@{k}':22}{report.answered_hit_rate():>14.3f}")
    print(f"  {'MRR':22}{report.answered_mrr():>14.3f}")
    print()
    lift = report.lift()
    if lift >= 1.0:
        print(f"  The graph is {lift:.1f}x more precise than guessing busy files.")
    else:
        print(
            f"  The graph is WORSE than guessing busy files ({lift:.2f}x). "
            "That is the result; publish it."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", help="repo URLs or local paths")
    parser.add_argument("--k", type=int, default=10, help="ranking cutoff (default 10)")
    parser.add_argument(
        "--examples", type=int, default=400, help="max graded examples per repo"
    )
    parser.add_argument(
        "--commits", type=int, default=300, help="most recent commits to draw from"
    )
    args = parser.parse_args()

    reports: list[tuple[str, BacktestReport]] = []
    for source in args.sources:
        try:
            report = run(source, k=args.k, examples=args.examples, commits=args.commits)
        except Exception as exc:  # noqa: BLE001 - one bad repo must not end the run
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            continue
        if report is not None:
            reports.append((source, report))

    if len(reports) > 1:
        print(f"\n{'=' * 72}\nALL REPOSITORIES\n{'=' * 72}")
        combined = BacktestReport(k=args.k, examples=0, commits_used=0, commits_skipped=0)
        for _, report in reports:
            combined.predictions.extend(report.predictions)
            combined.baseline.extend(report.baseline)
            combined.examples += report.examples
            combined.commits_used += report.commits_used
        print(f"  {combined.examples:,} examples across {len(reports)} repositories")
        _print_report(combined)

    return 0 if reports else 1


if __name__ == "__main__":
    raise SystemExit(main())
