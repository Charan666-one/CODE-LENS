# CodeLens Progress Ledger — What Changed, and Why It Matters

*The running record of the build. Three documents work together:*
- *[CHECKPOINTS.md](PLANNING%20/CHECKPOINTS.md) — the plan (what to build, with gates)*
- *[LEARNING.md](LEARNING.md) — the concepts (how the ideas work)*
- ***This file — the record (what actually changed, and how each change moves the company)***

*Every entry is verified against git history, not memory. Verification results at the bottom are from a real run, dated.*

---

## The numbers, as of July 23, 2026

| Metric | Value |
|---|---|
| Commits | 16 (every one a gated checkpoint or its docs) |
| Application code | ~5,400 lines (`backend/app/`) |
| Test code | ~2,600 lines, **199 tests passing**, ruff + mypy clean |
| Frontend | ~1,000 lines TypeScript (parked, compiles) |
| Languages parsed | Python + JavaScript, one shared schema |
| LLM tokens spent | **$0** — every AI path tested via a counting fake |
| Roadmap position | Stages 0–3 complete · Stage 4 compiler+API done, renderer parked |

---

## The change log

### 1 · `959fa22` + `28086f4` — The executable roadmap *(Jul 19–21)*
**What changed:** The six founding documents (CORE, STRATEGY, FOUNDATION, EXPERIENCE, ARCHITECTURE, ROADMAP) were committed alongside a new `CHECKPOINTS.md` — 10 stages, ~35 checkpoints, each with a Deliverable, a hard acceptance **Gate**, and its strategic Why.

**How it helps:** Before this, CodeLens had a vision and empty folders — nothing connected "decade ambition" to "what do I do Monday morning." The checkpoint ladder is that connection, and its gates are the discipline that has caught real bugs at almost every stage since. The single most protective decision in it: **Stages 6–9 are forbidden until a real developer says "I'd use this again" unprompted** — the guard against the solo-founder death of industrializing an unvalidated product.

### 2 · `e4d9ed0` — CP-0.1/0.2: Realignment + golden fixtures *(Jul 21)*
**What changed:** Deleted the premature Postgres/Redis/Neo4j config; trimmed requirements from 156 pinned packages to ~52 (verified sufficient in a clean venv); **added `networkx`, which was missing entirely**; made one `schema.py` canonical; wired ruff + mypy + pytest + CI. Built the golden-fixture harness: a tiny hand-verified repo whose exact expected graph is committed as JSON.

**How it helps:** The code had drifted from its own architecture before a feature existed — this reset the foundation to match the plan (SQLite + NetworkX until users justify more). The golden fixtures became the project's conscience: the expected answers were fixed *before* the parser existed, so the parser could never be graded on a curve. They have caught **three real bugs** since.

### 3 · `155ebe2` — CP-1.1: Ingestion *(Jul 21)*
**What changed:** `ingest(source)` — GitHub URL (shallow clone, size-capped, timeout-guarded), zip (zip-slip protected), or local directory → a normalized `RepoSnapshot` plus a per-file inventory where every file carries a SHA-256.

**How it helps:** Two ways. **Security:** CodeLens will clone URLs typed by strangers, and this treats that as the attack surface it is — `ext::` command execution, `file://` disclosure, and option-injection are all rejected by allowlist, verified by attempted (failed) injection. **Economics:** the per-file content hash is the engine of everything cheap later — skip-if-unchanged pipelines and pay-once AI summaries both key on it. Constitution rule 4 ("incremental everything") starts here.

### 4 · `5ee05cc` — CP-1.2: The Python parser *(Jul 21)*
**What changed:** tree-sitter walks every Python file and emits Layer-A facts into the frozen schema: CONTAINS/IMPORTS/CALLS/INHERITS, complexity, docstrings, per-definition hashes, entrypoints. Two-pass design: per-file observation, then whole-repo resolution.

**How it helps:** This is the product's raw material — "the parser's correctness is CodeLens's correctness." The two-pass split is what later allowed confidence labels without rewriting the walker. Verified on real repos (requests/flask/click): **zero dangling edges, ~1.8s for 1,700 functions**, 12/12 spot-checked call edges correct. The fixtures caught their first bug here: a missing INHERITS edge from conflated name-resolution contexts.

### 5 · `a3e2dad` — CP-1.3: Call resolution with honest confidence *(Jul 21)*
**What changed:** Every CALLS edge now declares how it was found: `resolved` (proof via imports, re-exports, the call site's class and its bases), `heuristic` (name matches exactly one definition — a bet), `dynamic_unknown` (could reach several — the ambiguity, admitted). Plus a distinctiveness gate: generic names (`get`, `close`, `copy`…) are never guessed from.

**How it helps:** This is **the differentiator made real**. Static analysis of Python cannot resolve every call; competitors either miss dependencies or silently guess. CodeLens over-approximates *and says so* — which is what lets the UI render uncertain edges honestly and the future Accuracy Ledger grade guesses separately. The distinctiveness gate came from reading actual output (`sock.close()` "calling" `Session.close`) and cut garbage dynamic edges **600 → 72** while leaving every proof byte-identical. Gate: 20/20 hand-verified resolved edges (bar was 90%).

### 6 · `8176bb0` — CP-1.4/1.5/1.6: Store, churn, dump — **M1 complete** *(Jul 22)*
**What changed:** `GraphStore` interface + SQLite backend (swap-ready for Neo4j); NetworkX traversal view; the checkpointed pipeline (`cloned → parsed → metrics → graph_built`) whose skip decision uses a **content digest**, not just the commit sha; git churn per file verified against `git log` itself; a crude GraphViz dump.

**How it helps:** The graph stopped being ephemeral and became **the asset** — persisted, reloadable, incrementally updated. The content-digest skip means a dirty working tree rebuilds and an unchanged one costs nothing: analysis cost scales with the diff, not the repo, which is the whole economic model of continuous monitoring later. The pipeline's progress events are the same telemetry the future UI will render — the "Understanding…" theater cannot lie because it has no separate source. **M1, the first milestone gate, passed here**: the core asset exists, provably, with zero AI.

### 7 · `0f63e4f` — CP-2.1–2.4: The query engine *(Jul 22)*
**What changed:** The QueryPlan registry (every question is a named, tested plan behind one entry point) and six deterministic queries: **blast_radius** (ranked, with real dependency paths, each path reporting its weakest-link confidence), centrality (hand-rolled PageRank), dependencies, risk (`complexity × fan_in × churn`), entrypoints, modules.

**How it helps:** Blast radius is **the paid wedge** — "know what breaks before you merge" — and it shipped deterministic and checkable, which is the precondition for the Accuracy Ledger moat. The registry is why chat later is a *router*, never a freelancer: the LLM will pick plans, not invent facts. The fixture gate caught an inverted PageRank here (main.py outranking calculator.py); the judge gate on CodeLens itself confirmed the answers match human judgment (`schema.py` ranked #1, correctly).

### 8 · `a59dc4a` — CP-3.1–3.4: The semantic layer *(Jul 22)*
**What changed:** The RAG rule as code (graph-selected context, hard token budget, never the repo); summaries cached by content hash with `derived_from` evidence; deterministic concept search returning graph nodes; narrated answers (project story, blast-radius story) with evidence ids — and the learning path deliberately *not* narrated, because an ordering needs no model.

**How it helps:** AI entered exactly where the architecture allows — at the edge, explaining facts. The cache gate was proven three ways with a counting fake: unchanged repo → **exactly zero** second LLM calls; identical source elsewhere → zero; one edited function → pays for 1–2 summaries. That is the unit economics of the free tier: viral drive-by analyses cost tokens once per unique file, ever. And every claim carries receipts — the trust posture, enforced by types.

### 9 · `2782fb3` — CP-4.1 + API: ViewSpec compiler + HTTP surface *(Jul 23)*
**What changed:** The visualization engine's core: graph → **ViewSpec** per zoom level (L1 districts with aggregated flows, L2 files, L3 functions), with server-computed deterministic layout, risk-ramp colors, and assembly choreography replaying real construction order. Plus thin FastAPI routes: analyze, viewspec, query — with the CP-1.1 trust boundary enforced at the HTTP door.

**How it helps:** "The frontend renders; it never computes truth" is now structural — whatever renderer exists (ours today, a better one later), the map is always a projection of real facts. Semantic zoom is also the performance strategy: a huge monorepo at L1 is a handful of districts. Found and fixed a bug that only a real serving path could show: SQLite's thread-bound connections under FastAPI's thread pool.

### 10 · `3255b87` — Frontend scaffold *(parked by decision, Jul 23)*
**What changed:** Next.js + sigma.js (WebGL) + a Graph State Manager; the assembly reveal, focus mode, understanding-language overlay; dark hero aesthetic. Compiles; deliberately paused before live verification.

**How it helps:** The hero moment — the distribution strategy itself — has its skeleton, and the phase machine (idle → understanding → revealing → exploring) encodes EXPERIENCE.md's choreography. Parked last so the backend it renders could be finished first; nothing in it computes truth, so it cannot rot as the backend advances.

### 11 · `9cb6001` — Semantic API: all 10 questions callable *(Jul 23)*
**What changed:** Search, learning path, narrated project/blast-radius answers, and summarization as endpoints. Deterministic answers never need a key; narrated ones 503 honestly without one; unknown nodes are rejected *before* tokens would be spent; the summary cache's zero-cost re-run now proven over HTTP.

**How it helps:** The complete product API exists — the frontend, a CLI, or the future GitHub App all consume the same surface. The fact/prose split being visible in status codes is the business model in miniature: the graph is always available; the narration is the premium layer.

### 12 · `ee5a45f` — The JavaScript emitter *(Jul 23)*
**What changed:** Full JS support — ESM and CommonJS imports, assignment-defined APIs (`exports.foo = fn`, `res.send = fn`, `X.prototype.y = fn`), `this.`/`super.` resolution, Node's main-guard, `index.js` answering for its directory — all emitting into the **untouched** schema through the same resolution ladder.

**How it helps:** Doubles the addressable market to the JS/TS ecosystem FOUNDATION always specified, and *proves the platform claim*: a second language changed one dispatch statement and zero downstream systems. The express stress test exposed why parsing idioms matters commercially — before assignment support, express's actual API (`lib/response.js`) parsed to zero functions; blast radius on the most popular Node framework would have been silently empty. After: 168 functions, 379 labeled edges, 6/6 spot checks. Python regression-verified byte-identical.

---

## What this adds up to

Strategy said the moat is: **graph → validated predictions → trust, compounding with time**. The record above is that machine being assembled in order: the graph exists and is provably correct (M1) · the wedge query is deterministic and checkable (Stage 2) · AI explains without inventing, at near-zero marginal cost (Stage 3) · everything is reachable over HTTP in two languages (Stage 4a). What remains before users: the hero-moment frontend (parked, by choice, for last) — then CP-5.1, the only gate that matters.

---

## Full-system verification run — July 23, 2026

Every layer exercised end to end against a **real repository** (psf/requests, freshly cloned), zero tokens spent. Recorded verbatim from the run:

```
LINT   ruff check .            All checks passed!
TYPES  mypy                    Success: no issues found in 42 source files
TESTS  pytest                  199 passed, 1 skipped

CodeLens full-system verification · https://github.com/psf/requests

1. PIPELINE
  ✓  clone → parse → metrics → persist — 830 nodes, 1366 edges in 1.9s
  ✓  unchanged re-run skips via content digest
  ✓  graph invariants — no dangling edges, no duplicate ids

2. DETERMINISTIC QUERIES
  ✓  centrality — top file: src/requests/compat.py
  ✓  blast radius with paths — 16 dependents, every path walkable
  ✓  risk ranking — riskiest: src/requests/models.py
  ✓  entrypoints — 1 found

3. VIEWSPEC COMPILER
  ✓  semantic zoom L1<L2<L3 — nodes per level: [4, 37, 733]
  ✓  deterministic layout

4. SEMANTIC LAYER (fake model, $0)
  ✓  summaries generated with evidence — 25 paid calls
  ✓  unchanged source: ZERO second calls — 25 served from cache
  ✓  concept search returns graph nodes — Session.close for "session cookies authentication"
  ✓  learning path is deterministic (model: none) — 8 stops
  ✓  temporal facts present on files — 37/37 files carry churn

5. API (TestClient)
  ✓  health
  ✓  trust boundary rejects hostile source
  ✓  local paths disabled by default
  ✓  analyze over HTTP
  ✓  viewspec over HTTP
  ✓  blast radius over HTTP
  ✓  narration 503s honestly without a key
  ✓  learning path over HTTP, no key needed

CHECKS: 22/22 passed — the whole system works, end to end.
```

Worth noticing in those results: the riskiest file the formula picked (`models.py` — requests' Request/Response core) and the top-central file (`compat.py` — imported by everything) are exactly what a maintainer of requests would name. The deterministic machinery produces *believable* answers on code it has never seen — which is the entire bet.

### How to re-run it yourself

```bash
cd backend
.venv/bin/ruff check .          # lint: must be clean
.venv/bin/mypy                  # types: must be clean
.venv/bin/python -m pytest -q   # 199 tests: must pass
```

End-to-end (clones a real repo, builds the graph, runs every query):

```bash
cd backend && .venv/bin/python scripts/verify_system.py
```
