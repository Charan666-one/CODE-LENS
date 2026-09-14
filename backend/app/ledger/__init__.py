"""The Accuracy Ledger — the number that has to be earned, not claimed.

STRATEGY.md's moat is not the graph. Anyone can build a graph. The moat is a
published, honest record of how often the graph was *right*, because that is
the one thing a competitor cannot copy in a weekend and the one thing a buyer
cannot verify any other way.

The plan put this after the GitHub App: record a prediction per merged PR,
watch for reverts and hotfixes, publish the rate. That design needs users,
and needing users to start the moat is exactly backwards — the moat is what
makes the first users trust it.

This package starts it without them, by backtesting against history that
already happened. Every multi-file commit in a repository is a labelled
example nobody had to collect: a developer changed one file, and the other
files in that commit are what they *also had to change*. That is precisely
the blast radius claim, already graded, sitting in every repo on GitHub.
"""

from __future__ import annotations

from app.ledger.backtest import (
    BacktestReport,
    ScoredPrediction,
    backtest,
    popularity_baseline,
)

__all__ = [
    "BacktestReport",
    "ScoredPrediction",
    "backtest",
    "popularity_baseline",
]
