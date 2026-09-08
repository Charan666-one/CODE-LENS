# CodeLens

**A software knowledge graph that answers "what breaks if I change this?"**

Point it at a repository. It parses every file with tree-sitter, resolves the
calls and imports between them, reads the git history, and gives you one map
you can walk — architecture, then modules, then symbols — with the blast
radius of any change drawn on it.

```bash
docker compose up --build
open http://localhost:3000
```

---

## The part most tools skip

CodeLens keeps an [**Accuracy Ledger**](LEDGER.md): a reproducible measurement
of whether its central claim is true, published whether or not the number is
good. It has not always been good.

The benchmark needs no labelled data, because git already has it. A commit
that changes several files is a developer answering our question: they changed
`auth.py` and *also had to change* `session.py`. Ask the graph, rank the
answer, and see how far down the list the rest of the commit appears — then
compare against a baseline with no graph in it at all ("guess whichever files
change most often").

Latest, across **1,203 examples from 8 repositories**:

| | blast radius | popularity baseline |
|---|---|---|
| precision@10 | **0.232** | 0.221 |
| recall@10 | 0.495 | — |
| MRR | 0.462 | — |

**1.05× the baseline.** That is a narrow win, and it is the first one — entry
#1 measured 0.66× and said so. Four of the eight repositories still lose to
guessing. There is no accuracy badge in the product UI, because 1.05× is worth
building on and not worth advertising.

Reproduce it yourself:

```bash
cd backend && .venv/bin/python scripts/backtest.py https://github.com/psf/requests --k 10
```

The Ledger also records the mistakes: a benchmark that leaked future
information into its own baseline, a "39% coverage gap" that turned out to be
a measurement artifact, and a month of parser work aimed at a problem that was
not there. Entries are appended, never edited.

---

## What it actually does

**Three depths, one world.** L1 districts → L2 files → L3 symbols. Clicking a
district flies into it rather than switching a tab; the graph is the interface
and almost nothing else is permanent chrome.

**Flat or deep, same world.** A 2D/3D toggle sits beside the depth buttons. In
the deep view the third axis is a measured fact rather than a look: height is
position in the import stack, where the surface is everything nothing imports —
entry points included — and each layer below is one import deeper, so a file
always sits under whatever leans on it. Both views are drawn from one
choreography module ([`choreography.ts`](frontend/lib/choreography.ts)), so the
reveal, the ripple, the overlays and the relevance hierarchy cannot drift apart
between them. Node size and edge width stay measured in pixels in 3D on
purpose: a sphere shrinking with distance would turn "how much the project
leans on this" into "how far away the camera is".

**Find it by name.** A search field is on screen from the moment a graph is —
`/` focuses it — and a hit moves the camera, changing depth when the level on
screen cannot draw what was asked for. There is no results page.

**Blast radius.** The reverse transitive closure from any node, ranked over
four normalised signals — distance, co-change strength, churn, and structural
fan-in — with the dependency path shown for every claim. A claim without its
path is an opinion. See [`ranking.py`](backend/app/queries/ranking.py).

**Uncertainty is visible, never hidden.** Every edge carries a confidence:
`resolved` (the parser proved it), `heuristic` (a name matched), or
`dynamic_unknown` (the target could be several things). The canvas renders the
difference. A graph that hides its own guesses is the one thing this project
has consistently refused to ship.

**Analysis you can reach without leaving the canvas** (⌘K): circular
dependencies, architecture health with published sub-scores and weights,
riskiest files, untested hubs, bus factor, hidden coupling, HTTP endpoints,
entry points. Each renders *on* the graph — a query never opens a table.

**History, as evidence.** Files that change together without importing each
other (hidden coupling), how often each file moves, and who owns it.

---

## Running it

### Containers (recommended)

```bash
docker compose up --build
```

`http://localhost:3000`. The backend is deliberately not published to the
host: the browser talks only to the frontend, which proxies `/api` over the
internal network. One public port, no CORS to maintain.

State lives in two named volumes — `codelens-data` (analysed graphs, worth
keeping) and `codelens-clones` (a cache; dropping it is only slow, never
wrong).

### On the host

Needs Python 3.13, Node 22, and `git`.

```bash
./run.sh          # production build, both servers, opens a browser
./run.sh --dev    # hot reload, for hacking
```

See [RUNBOOK.md](RUNBOOK.md) for first-time setup and every verification
command, and [DEPLOYING.md](DEPLOYING.md) before pointing it at the internet.

**Read [SECURITY.md](SECURITY.md) and
[DEPLOYMENT_READINESS.md](DEPLOYMENT_READINESS.md) before running a public
instance.** The first is a full threat model and audit; the second closes the
five operational gaps it left open and tests each against the running
containers. Current verdict: **ready for staging** — behind a proxy, with
narration off. Public beta needs one more thing: backups copied off the
machine they back up.

### No API key required

The graph, every query, and every number in the Ledger are deterministic.
`GROQ_API_KEY` / `ANTHROPIC_API_KEY` enable *narration* only — prose layered
on top of facts that are computed without them.

---

## Configuration

Every value has a working default; see
[`backend/.env.example`](backend/.env.example).

| variable | default | what it protects |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:3000` | Browser origins allowed to call the API |
| `MAX_REPO_SIZE_MB` | `500` | Refuses to clone something enormous |
| `RATE_LIMIT_ANALYSES` / `_WINDOW_SECONDS` | `5` / `300` | One client looping on `/analyze` |
| `MAX_CONCURRENT_ANALYSES` | `2` | A machine that stops answering its own health check |
| `MAX_CLONE_CACHE_MB` | `4000` | A full disk, which takes the database down with it |
| `MAX_FINISHED_JOBS` | `200` | A job table that only ever grew |

Analysis accepts repository **URLs** only. Local paths require
`CODELENS_ALLOW_LOCAL_ANALYSIS=1` — a dev switch, never a production default.

---

## Architecture

```
ingest ──▶ parse ──▶ resolve ──▶ metrics ──▶ store
 clone     tree-     whole-      git log     SQLite
           sitter    repo pass   churn,
                                 co-change
                        │
                        ▼
              GraphView (NetworkX)
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
    queries/       views/viewspec   semantic/
    11 plans       L1 / L2 / L3     optional LLM
        │               │
        └──────┬────────┘
               ▼
       FastAPI  ──▶  Next.js  ──▶  sigma.js (flat) │ three.js (deep)
                            one choreography, two renderers
```

Parsing is two passes: each file records what it *saw*, then a whole-repo pass
resolves those observations into edges — a call site cannot be resolved from
inside the file containing it. Results are content-hash cached and invalidated
by `PARSER_VERSION`, so re-analysis of an unchanged repository costs nothing
and a parser upgrade never serves a stale graph.

[LEARNING.md](LEARNING.md) explains every checkpoint and why it exists.

---

## Limits, stated plainly

- **Python, JavaScript and TypeScript.** Other languages parse as nothing.
- **One process.** The job registry, rate limiter, concurrency gate and graph
  cache are per-process, so the backend runs one worker. A second replica
  needs a shared store; the container says so rather than shipping a flag that
  appears to work.
- **History is bounded** to the most recent 400 commits a blobless clone
  fetches. Churn is "within that window", not all time.
- **The Ledger's numbers are an upper bound.** Predictions use the graph at
  HEAD while examples come from earlier commits, so a dependency added after
  an example can only help. Removing that bias needs a graph per commit and is
  the largest correction still outstanding.

---

## Development

```bash
cd backend  && .venv/bin/ruff check app tests scripts   # lint
cd backend  && .venv/bin/mypy                           # types
cd backend  && .venv/bin/python -m pytest -q            # 292 tests
cd frontend && npx tsc --noEmit                         # types
```

## License

See [LICENSE](LICENSE).
