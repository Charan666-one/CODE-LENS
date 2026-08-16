# The Accuracy Ledger

Entries are appended, never edited. A number that turned out to be wrong stays
on the page with the correction under it, because the record of *how the
measurement was wrong* is worth more than the measurement.

- [Entry #1 — the graph loses to guessing](#entry-1--2026-08-14) (2026-08-14)
- [Entry #2 — the gap was in the benchmark](#entry-2--2026-08-16) (2026-08-16)

---

# Entry #1 — 2026-08-14

**Method:** offline backtest against git history
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

---

# Entry #2 — 2026-08-16

**Method:** unchanged — offline backtest against git history, same four
repositories, same `--k 10`.
**Reproduce:** `cd backend && .venv/bin/python scripts/backtest.py https://github.com/psf/requests https://github.com/pallets/flask https://github.com/tiangolo/fastapi https://github.com/expressjs/express --k 10`

Entry #1 concluded that **coverage was the whole gap** and that closing it was
the highest-value work available. A slice of that work shipped. Then it was
measured, and the conclusion did not survive.

---

## What was actually wrong

**Entry #1's own footnote had it, and I did not read my own footnote.**
"Known problem #2" says: seeding on a test file asks "what breaks if I change
this test?", whose true answer is *nothing* — the commit's other files were
its **dependencies**, not its dependents.

That is not a footnote. On these repositories it is the result.

A diagnostic over the silent examples:

| repo | silent examples | of which are test files |
|---|---|---|
| expressjs/express | 85 | **84 (99%)** |
| pallets/flask | 67 | **66 (99%)** |

The benchmark asks a **symmetric** question — "the developer changed X; what
else did they touch?" — and it was answered with a **one-directional** walk.
For a test file the graph said nothing because nothing depends on a test, and
was scored zero for being right. The reported 39% silence was very close to
entirely this artifact.

**The fix is to the measurement, not the parser.** `_rank_related` now ranks
dependents first (still the product's claim) and then falls back to
dependencies to fill the top 10.

---

## The two runs, kept separate on purpose

### Run A — the correctness slice, graded the old way

Per-directory `tsconfig`/`jsconfig` path aliases, barrel re-exports
(`export * from './x'`), bare-specifier isolation, and
`EXTERNAL_DEPENDENCY` + `DEPENDS_ON` nodes from `package.json` /
`requirements.txt` / `pyproject.toml`.

| | entry #1 | after the slice |
|---|---|---|
| precision@10 | 0.174 | **0.161** |
| silence | 39% | **39%** |

**It did not help. It scored slightly worse.** The parse genuinely improved —
on this repository resolved `IMPORTS` went 191 → 260, with 13 external
packages and 46 `DEPENDS_ON` edges that did not exist — but *none of that
touched the thing the score was actually measuring*, because the thing being
measured was the direction of the question.

### Run B — same parser, benchmark asking its question in both directions

| | blast radius | popularity baseline |
|---|---|---|
| precision@10 | **0.230** | **0.230** |
| hit rate@10 | 0.707 | 0.805 |
| recall@10 | 0.372 | — |
| MRR | 0.370 | — |

570 examples. **Silence fell from 39% to 2%** (9 of 570). Lift is **1.00×** —
a dead heat with guessing the busiest files.

### Per repository

| repo | parsed | examples | silent | precision@10 | baseline | verdict |
|---|---|---|---|---|---|---|
| psf/requests | 37 | 137 | 2% | **0.364** | 0.226 | wins, 1.6× |
| pallets/flask | 83 | 237 | 0% | 0.227 | 0.275 | loses, 0.82× |
| tiangolo/fastapi | 1,140 | 56 | 9% | 0.062 | 0.097 | loses, 0.65× |
| expressjs/express | 141 | 140 | **0%** | 0.172 | 0.213 | loses, 0.81× |

Express was 66% silent in entry #1 and is now 0%. Flask parsed ~30 files then
and 83 now.

---

## How much of Run B is real

Some and not all, and the honest split is not available from these two runs.

Grading both directions is a **strictly easier task** than grading blast
radius, so a large part of the jump from 0.161 to 0.230 is the task getting
easier rather than the graph getting better. This entry therefore does **not**
claim blast radius improved. What it claims is narrower and firmer:

- The 39% silence figure in entry #1 **measured the benchmark, not the graph**.
- Entry #1's headline — the graph loses at 0.66× — was **directionally right
  and numerically overstated**. At 1.00× it is a tie, not a loss.
- A tie is still not a product claim.

The example sets also differ slightly between the runs (534 → 570), because a
better parse changes which files are tracked and therefore which commits
qualify. The baseline moved too (0.264 → 0.230) for the same reason, which is
exactly why the baseline is recomputed on the same examples every time and
never carried over from a previous entry.

---

## What this changes

**Still no accuracy badge in the UI.** 1.00× advertises nothing.

**Retire "coverage is the whole gap."** It was inferred from a number that was
measuring something else. The remaining silence is 2%; there is no coverage
gap left to close on these repositories.

The order of work this implies:

1. **Ranking, not coverage.** The graph names ~8 of 10 files and gets ~0.23 of
   them right. Distance is currently most of the ranking signal, and the
   obvious unexploited signals — co-change weight, churn, symbol-level rather
   than file-level reachability — are already computed and unused here.
2. **Repositories that are not libraries.** Four repos, three Python, all
   libraries, remains the same unaddressed criticism as entry #1. An
   application or monorepo is where the popularity baseline should get weak,
   and it has never been tested there.
3. **Grade the two directions separately.** Reporting one blended number
   reintroduces exactly the confusion this entry had to untangle.

*The lesson to carry forward: entry #1 named this failure mode explicitly, in
writing, and the next month of work still went the other way. Listing a
known problem is not the same as pricing it.*
