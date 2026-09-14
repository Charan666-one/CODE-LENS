# The Accuracy Ledger

Entries are appended, never edited. A number that turned out to be wrong stays
on the page with the correction under it, because the record of *how the
measurement was wrong* is worth more than the measurement.

- [Entry #1 — the graph loses to guessing](#entry-1--2026-08-14) (2026-08-14)
- [Entry #2 — the gap was in the benchmark](#entry-2--2026-08-16) (2026-08-16)
- [Entry #3 — ranking, and the first win over the baseline](#entry-3--2026-08-16) (2026-08-16)

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

---

# Entry #3 — 2026-08-16

**Method:** unchanged where it matters — offline backtest against git history,
same `--k 10`, same example rule. Two things did change and both are stated
below: the repository set grew from four to eight, and the ranking's history
signals are now computed from strictly older commits.
**Reproduce:**
`cd backend && .venv/bin/python scripts/backtest.py <the eight urls below> --k 10`
**Weight search:** `cd backend && .venv/bin/python scripts/rank_sweep.py`

Entry #2 ended by saying the remaining work was ranking, not coverage: the
graph named about eight of ten files and got 0.23 of them right, tying the
popularity baseline. This is that work.

---

## The audit, before any change

**Where the scoring was.** `queries/blast_radius.py`, one expression:
`1.0 / distance + fan_in / 1000.0`.

**What it actually did.** Distance 1 scores 1.0 and distance 2 scores 0.5, so
fan-in could only reorder across a distance band if it differed by 500. No
file in any of these repositories has 500 direct dependents. Fan-in was a
tiebreak *inside* a band and nothing else, so the ranking was **distance,
with ties broken by a signal that could never matter enough to be visible**.

**Where the unused signals were.** Both already computed and stored, neither
read by the ranking:

| signal | where it lives | shape |
|---|---|---|
| co-change | `CO_CHANGES` edge `weight` (`graph/co_change.py`) | Jaccard, 0.25–1.0 |
| churn | `node.churn_count` (`ingestion/git_history.py`) | commit count, heavy-tailed |

**Normalisation.** None. Two signals in incomparable units added together.

**Domination by scale.** Yes, in both directions — distance dominated because
its curve was steep, and fan-in was inert because it had been divided by 1000
to stop it dominating. The 1000 was doing the job normalisation should.

---

## The evaluation problems, and this is the important part

Three, one of them serious enough that fixing the ranking without fixing it
would have produced a large fake improvement.

**1. Co-change and churn read off the graph are computed over all history,
including the commit being graded.** A ranking using them would be told the
answer: "these two files change together" is trivially true of the example
under test. This is the same bug the popularity baseline had in entry #1, and
it inflated that baseline enough to change the published conclusion.

Fixed by `WindowedHistory` (`queries/ranking.py`), which recomputes both
signals from `commits[i+1:]` per example. Affordable because it is arithmetic
over commit lists — no reparse, no checkout. The product keeps reading the
graph, which is correct: when a user asks, all history is past.

**2. Granularity.** The ranking is symbol-level, the grading is file-level,
and a file used to inherit the distance of whichever of its symbols happened
to appear first in the list. A file is as close to a change as its *nearest*
symbol; it now takes the minimum distance and maximum fan-in across its
symbols.

**3. Fitting and reporting on the same repositories.** Four weights searched
against eight repos and reported on those eight would report a model scored
on its own test set. The eight were split before any weight was tried: four
fitted (requests, flask, click, itsdangerous), four never seen by the search
(fastapi, express, jinja, markupsafe).

*Unchanged and still true:* predictions use the graph at HEAD while examples
come from earlier commits, so every number here remains **an upper bound**.

---

## The model

```
score =   1.0 × 1/distance          the product's core claim
        + 1.0 × jaccard             co-change strength with the seed
        + 1.0 × norm(log1p(churn))  how often the file moves
        + 1.0 × norm(log1p(fan_in)) structural importance
```

Each term is bounded to [0, 1] before weighting, so a weight means what it
says. `churn` and `fan_in` are `log1p`-compressed first: both are heavy-tailed,
and raw min-max on a heavy tail is a switch that fires for the single busiest
file rather than a signal. Min-max is over the candidates of *this* query,
because the question is which of these files matters most.

**The weights are 1,1,1,1 and are deliberately not the argmax.** The search
surface is a plateau — everything from about (0.5, 1.0, 1.0) to (2.5, 2.5,
2.5) scores 0.260–0.2625 on the fit set — and the argmax relocated to the
grid's edge twice as the grid was widened. A maximum that moves when you
enlarge the search is where the search stopped, not a maximum. What the sweep
establishes is that having all four signals **on** is worth ~19% and their
exact ratio is worth ~1%, so the weights are the simplest point on the
plateau, chosen on the fit set, costing 0.6% against the argmax there.

---

## Results — before and after, same command, eight repositories

| repo | set | before | after | Δ | baseline | lift after |
|---|---|---|---|---|---|---|
| psf/requests | fit | 0.364 | **0.379** | +4% | 0.226 | 1.68× |
| pallets/flask | fit | 0.227 | **0.277** | +22% | 0.275 | 1.01× |
| pallets/click | fit | 0.113 | **0.143** | +27% | 0.167 | 0.86× |
| pallets/itsdangerous | fit | 0.342 | 0.342 | 0% | 0.345 | 0.99× |
| tiangolo/fastapi | held out | 0.062 | **0.077** | +24% | 0.097 | 0.79× |
| pallets/jinja | held out | 0.169 | **0.192** | +14% | 0.169 | 1.14× |
| expressjs/express | held out | 0.172 | 0.172 | 0% | 0.213 | 0.81× |
| pallets/markupsafe | held out | 0.294 | 0.294 | 0% | 0.284 | 1.04× |

### Pooled, 1,203 examples

| | before | after |
|---|---|---|
| precision@10 | 0.210 | **0.232** |
| recall@10 | 0.446 | **0.495** |
| MRR | 0.381 | **0.462** |
| silence | 2% | 2% |
| popularity baseline | 0.221 | 0.221 |
| **lift** | **0.95×** | **1.05×** |

**Held out alone: 0.171 → 0.183, +7.1%.** Nothing regressed on any repository.

Silence is unchanged, and should be: ranking reorders what the graph already
names, and cannot make it name more. A change in the silence rate here would
have meant something was wrong.

### Which signal did the work

Held-out, each signal switched on alone at its fitted weight:

| ranking | precision@10 | lift |
|---|---|---|
| distance only | 0.157 | 0.84× |
| **the old shipped ranking** (distance, then fan-in) | **0.171** | 0.91× |
| distance + co-change | 0.172 | 0.92× |
| distance + churn | 0.177 | 0.94× |
| distance + structure | 0.179 | 0.96× |
| **all four** | **0.183** | **0.98×** |

**Structure contributed most, then churn, then co-change — the reverse of the
order I expected.** The headline hypothesis from entry #2 was co-change; on
held-out repositories it is worth +0.001 on its own, which is nothing. What
fan-in needed was not to be introduced but to be *normalised*: it was already
in the formula and already useless there.

No single signal reaches the combination. The gain is in adding them, and
that is the one claim the sweep supports strongly.

---

## What this does not show

**The mechanism is partly convergence toward the baseline.** Weighting churn
and structure heavily makes the ranking more like "the busiest, best-connected
files near the change", and the popularity baseline is "the busiest files".
Some of the 1.05× is the graph agreeing with popularity rather than beating
it. The graph's own contribution is the restriction to the blast radius, and
this benchmark cannot separate the two.

**1.05× is a win, not a product claim.** Four of eight repositories still lose
to guessing. Express, unchanged and at 0.81×, is the only JavaScript
repository here and the worst performer — one language is not a sample.

**Still no accuracy badge in the UI.** 1.05× is worth building on, not
advertising.

---

## What this changes

1. **Ranking is no longer the bottleneck it was.** The plateau says the model
   has extracted what these four signals hold; a fifth signal, not a fifth
   decimal place on the weights, is what moves it next.
2. **Repositories that are not libraries, again.** Eight repos, seven Python,
   all libraries, all small. This is now the third entry to say it.
3. **Per-commit graphs.** Every entry has carried "these are an upper bound"
   as a footnote. It is the largest unmeasured error left, and it is the one
   correction that would make the numbers claims rather than ceilings.
