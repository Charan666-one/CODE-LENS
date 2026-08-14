# The Accuracy Ledger — first entry

**Date:** 2026-08-14 · **Method:** offline backtest against git history
**Reproduce:** `cd backend && .venv/bin/python scripts/backtest.py <repo-url>`

CodeLens's claim is "what breaks if I change this?" This is the first
measurement of whether that claim is true. It is not a good result.

---

## The headline

Across **534 graded examples from 4 repositories**, blast radius was compared
against a baseline with no graph in it at all — "guess whichever files change
most often":

| | blast radius | popularity baseline |
|---|---|---|
| precision@10 | **0.174** | **0.264** |
| hit rate@10 | 0.451 | 0.811 |
| recall@10 | 0.251 | — |
| MRR | 0.284 | — |

**The graph is 0.66× the baseline. It loses.** A user would be better served,
on this measure, by a list of the repository's busiest files.

That sentence is the reason this document exists. It is also the reason the
Ledger is the moat: no competitor can fake having run this, and no amount of
demo polish changes the number.

---

## The finding underneath the headline

The graph **says nothing at all in 39% of examples** (209 of 534). Silence and
error score identically as zero, so the aggregate hides which one is
happening. Separating them:

| | all examples | only when the graph answers |
|---|---|---|
| examples | 534 | 325 |
| precision@10 | 0.174 | **0.286** |
| hit rate@10 | 0.451 | **0.742** |
| MRR | 0.284 | **0.466** |

**When the graph names anything, it is right 74% of the time within the top
10, and the first correct file sits around rank 2.** That is a usable tool.

So the problem is not the ranking. It is coverage — the graph has no
dependents to name for two-fifths of the files people actually change. This
is a parser and language-support problem, and it is the single highest-value
thing to work on next.

---

## Per repository

| repo | parsed files | examples | silent | precision@10 | baseline | verdict |
|---|---|---|---|---|---|---|
| psf/requests | 37 | 122 | 22% | **0.349** | 0.311 | graph wins, 1.1× |
| pallets/flask | ~30 | 165 | ~30% | 0.183 | 0.313 | loses, 0.59× |
| tiangolo/fastapi | 1,140 | 54 | 56% | 0.054 | 0.093 | loses, 0.58× |
| expressjs/express | 141 | 129 | **66%** | 0.037 | 0.244 | loses, 0.15× |

The pattern is exact: **the graph wins where it is not silent, and loses where
it is.** requests, with the lowest silence rate, is the only repository where
the graph beats guessing.

---

## Method, including what is wrong with it

**The examples.** Every commit touching 2–20 parsed files is a labelled
example nobody had to collect: the developer changed one file and *also had
to change* the others. Take each changed file as a seed, ask blast radius,
and check whether the rest of the commit appears in the top 10. Commits above
20 files are excluded as sweeps (reformats, license headers, lockfiles).

**Why precision@10.** Blast radius returns a transitive closure — hundreds of
files. Scored as a set its precision is near zero and the number is
meaningless, because nobody reads a set. They read the top of a ranked list.

**Why a baseline at all.** "Precision 0.286" is unfalsifiable on its own. The
popularity baseline is the bar any dependency graph has to clear to justify
its complexity.

### Three known problems with these numbers

1. **The results are an upper bound.** Predictions use the graph built at
   HEAD while examples come from earlier commits, so a dependency added
   *after* an example can only help the prediction. Rebuilding the graph per
   commit removes this and costs a checkout plus full parse per example.
   **Not yet done.**

2. **Silence is scored as a miss, and sometimes it is correct.** Seeding on a
   test file asks "what breaks if I change this test?", whose true answer is
   "nothing" — while the commit's other files were its *dependencies*, not its
   dependents. Blast radius is the wrong direction for those seeds. This is
   why both conditional and unconditional numbers are published; neither
   alone is honest. A forward-direction measure would grade those seeds
   fairly and does not exist yet.

3. **Four repositories is not a sample.** Three are Python, one is
   JavaScript, all are libraries. Application repositories and monorepos —
   the actual target customer — are unmeasured.

*A leak was found and fixed while producing these numbers: the popularity
baseline originally counted over all history including the commit being
graded, letting it "predict" files it had already watched change. Corrected
to count strictly older commits. It moved the baseline by ~0.01, so the
conclusion stands, but the first version of this benchmark would have
published a number that was wrong in the graph's favour.*

---

## What this changes

**Do not** put a confidence number in the product UI yet. There is nothing
here worth advertising.

**Do** work the silence rate. It is 39%, it is the whole gap, and it is a
tractable engineering problem rather than a research one. Express alone lost
90 of 141 files' import edges to a single unresolved specifier form
(`require('../')`), found by this backtest and fixed the same day.

The order of work this implies:

1. **Coverage before ranking.** Every point of silence removed is worth more
   than any reranking, because the ranking is already good when it fires.
2. **Then rerun this, on more repos, including applications.**
3. **Publish the number when it beats the baseline** — and publish it when it
   does not, because a ledger that only reports wins is marketing.
