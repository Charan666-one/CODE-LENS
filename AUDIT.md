# CodeLens — Full Technical Audit

*Production-readiness review, July 2026. Written as a Staff/Principal engineer would write it for a founder who has to make decisions, not feel good. Every number is measured from the repository, not recalled.*

**Measured baseline:** `backend/app` 6,111 lines · `backend/tests` 3,077 lines / 190 test functions / 15 files · frontend 1,822 lines (645 of it CSS) · 223 tests passing · ruff + mypy clean · 33 commits · **0 users, 0 revenue, $0 of LLM spend.**

---

# Part 1 — High-Level Overview

## What CodeLens is *today*

A **single-user, single-process desktop-grade tool** that clones a public Git repository, parses Python/JavaScript/TypeScript into a dependency graph, persists it to SQLite, answers six deterministic graph questions, and renders the result as an interactive WebGL map with a blast-radius animation and an explanation panel.

That is a genuine, working product. It is **not** the "Software Knowledge Graph platform" of CORE.md — it is the substrate that platform would be built on.

## What it actually solves today

1. *"What does this unfamiliar repo look like?"* — the map, the districts, the entrypoints. **Solved well.**
2. *"What breaks if I change this?"* — blast radius with real paths and confidence labels. **Solved, and it is the differentiator.**
3. *"Why does the project need this file?"* — the explanation page. **Solved deterministically.**
4. *"Where is concept X?"* — search works, but it is lexical, not semantic (see Part 7). **Partially solved, and oversold by its own naming.**

## What it does NOT solve

- Anything about **private repos** (no auth).
- Anything **team-scale** (no accounts, no sharing, no persistence beyond one local SQLite file).
- Anything about **trust over time** — the Accuracy Ledger, the claimed moat, does not exist in any form.

## Completion table

| Subsystem | Status | % | Honest note |
|---|---|---|---|
| Ingestion (clone/zip/local) | Implemented | **95%** | Security boundary is genuinely good. Missing: private-repo auth, sparse checkout. |
| Parser — Python | Implemented | **85%** | Solid. Recall is the gap, not correctness. |
| Parser — JS/TS | Implemented | **70%** | ESM+CJS+TSX work. JSX composition unmodelled → React graphs are sparse. |
| Graph schema (Layer A) | Implemented | **90%** | Frozen contract, honoured. |
| Graph Layer B (external deps) | **Not built** | **0%** | No package.json/requirements parsing. `DEPENDS_ON`/`TALKS_TO` enum values exist, nothing emits them. |
| Graph Layer C (temporal) | Partial | **30%** | churn/authors/last-modified only. No `CO_CHANGES`, no `Author` nodes. |
| Graph Layer D (semantic) | Implemented, unproven | **60%** | Code complete; **never executed against the real Anthropic API.** |
| Graph Layer E (memory/outcome) | **Not built** | **0%** | Schema reserves seats. Nothing more. |
| Graph Store (SQLite) | Implemented | **75%** | Works. Single-file, whole-graph reads, no migrations, no concurrency story. |
| Traversal (NetworkX) | Implemented | **80%** | Correct. Loads the entire graph into RAM every time. |
| Query Engine / registry | Implemented | **85%** | Clean contract. 7 plans. |
| Semantic layer / AI | Implemented, unproven | **55%** | Cache logic proven with a fake; prompts never validated on a real model. |
| Reasoning Engine (chat router) | **Not built** | **0%** | ARCHITECTURE calls for chat-as-router. Does not exist. |
| Accuracy Ledger | **Not built** | **0%** | **The stated moat. Zero code.** |
| API layer | Implemented | **70%** | Thin and correct. No auth, no rate limits, no pagination. |
| Visualization / ViewSpec | Implemented | **85%** | Genuinely good; semantic zoom + honest colour. |
| Frontend | Implemented | **65%** | Works, looks right. **Zero tests.** No routing, no shareable URLs. |
| Pipeline / jobs | Partial | **50%** | Async now, but in-memory; a restart loses every running job. |
| Auth / billing / multi-tenancy | **Not built** | **0%** | Correctly deferred per plan, but it *is* zero. |
| Deployment / observability | **Not built** | **0%** | No Dockerfile, no deploy target, no error tracking, no metrics. |
| CI | Partial | **60%** | Lint/type/test on push. No deploy, no coverage gate, no frontend job. |

**Weighted overall: ~45% of the MVP-through-Stage-4 scope; ~15% of the CORE.md platform vision.**

---

# Part 2 — Architecture

## Actual shape (as built, not as planned)

```
        HTTP (FastAPI, one process, no auth)
                    │
   ┌────────────────┼─────────────────────────────┐
   │                │                             │
POST /analyze   GET /viewspec            POST /query/{name}
   │                │                     GET /explain
   ▼                ▼                             ▼
core/jobs.py   views/viewspec.py           queries/*.py
(in-memory,     (ViewSpec compiler)        (7 QueryPlans)
 threads)            │                             │
   │                 └──────────┬──────────────────┘
   ▼                            ▼
core/pipeline.py          graph/traversal.py  ← loads WHOLE graph to RAM
   │                            ▲
   ├─ ingestion/ ──► RepoSnapshot + SourceFile[]
   ├─ parser/ ─────► FileFacts[] ──► resolve() ──► Node[]/Edge[]
   ├─ ingestion/git_history ──► churn/authors
   └─ graph/store.py (SQLite) ──────┘
                    │
              semantic/ (optional, key-gated)
              context → llm → SemanticAnnotation
```

## Component-by-component

| Component | Purpose | In | Out | Deterministic? | Files |
|---|---|---|---|---|---|
| **Repository Engine** | Acquire + inventory source | URL/zip/path | `RepoSnapshot`, `SourceFile[]` (sha256 each) | Yes | `ingestion/{__init__,clone,archive,inventory,languages,errors}.py` |
| **Parser Engine** | Source → facts | bytes + path | `FileFacts` (nodes, raw imports/calls/bases) | Yes | `parser/{python_emitter,js_emitter,facts,complexity}.py` |
| **Graph Builder** | Facts → resolved graph | `FileFacts[]` | `Node[]`, `Edge[]` with confidence | Yes | `parser/resolution.py`, `parser/__init__.py:_assemble` |
| **Graph Store** | Persist/reload | `KnowledgeGraph` | same | Yes | `graph/store.py` |
| **Knowledge Graph** | The asset | — | — | Yes | `graph/schema.py` (frozen) |
| **Traversal** | In-memory walks | `KnowledgeGraph` | closures, fan-in | Yes | `graph/traversal.py` |
| **Query Engine** | Named plans | `GraphView` + params | `ResultGraph` | Yes | `queries/base.py` + 6 plan modules |
| **Semantic Layer** | Summaries, search, narration | subgraph | `SemanticAnnotation`, prose | **No (LLM)** except search | `semantic/{context,summaries,embeddings,narration,llm}.py` |
| **Reasoning Engine** | Route questions → plans | — | — | — | **DOES NOT EXIST** |
| **Accuracy Ledger** | Grade predictions | — | — | — | **DOES NOT EXIST** |
| **API** | HTTP surface | JSON | JSON | Yes | `api/{routes,semantic_routes}.py` |
| **Visualization** | Graph → pixels spec | `KnowledgeGraph` | `ViewSpec` | Yes | `views/viewspec.py` |
| **Frontend** | Render + interact | `ViewSpec` | DOM/WebGL | Yes | `frontend/{app,components,lib}` |

**Architectural verdict:** the deterministic core is well-separated and the AI genuinely sits at the edge — the stated principle is *actually honoured in code*, which is rarer than it sounds. The gaps are not structural mistakes; they are unbuilt boxes.

---

# Part 3 — Repository Walkthrough

| Path | Why it exists | Verdict |
|---|---|---|
| `backend/app/ingestion/` (731 L) | Acquire and inventory repos | **Production-ready.** Best-hardened module in the repo. |
| `backend/app/parser/` (1,890 L) | The hard system | **Production-quality code, incomplete coverage.** Largest module, deservedly. |
| `backend/app/graph/` (678 L) | Schema, store, traversal, export | **Ready but naive.** `export.py` is a debug tool, arguably dead now that the frontend exists. |
| `backend/app/queries/` (777 L) | 7 QueryPlans | **Ready.** Cleanest module. |
| `backend/app/semantic/` (702 L) | AI at the edge | **Unproven.** Never run against a real model. |
| `backend/app/views/` (642 L) | ViewSpec compiler | **Ready.** One 640-line file — approaching split-worthy. |
| `backend/app/api/` (377 L) | HTTP | **Ready for a demo, not for the internet.** |
| `backend/app/core/` (269 L) | config, pipeline, jobs | **Weakest link.** `jobs.py` is a toy. |
| **`backend/app/risk/`** | — | **DEAD FOLDER.** Empty `__init__.py` only; risk lives in `queries/risk.py`. Delete it. |
| `backend/fixtures/` | Golden graphs | **Excellent.** The project's conscience. |
| `backend/scripts/verify_system.py` | 22-check E2E | **Genuinely valuable.** Rare in a codebase this young. |
| `frontend/` | The hero moment | **Works, untested.** `globals.css` (645 L) is 35% of the frontend by line count. |
| `PLANNING/` | Six founding docs | **Outstanding.** Better than most Series-A companies have. |
| `softwork/`, `code lens savepoints.pdf` | Reference material | **Clutter in the repo root.** Should be gitignored or moved. |

---

# Part 4 — Data Flow

Pasting `https://github.com/pallets/click`:

| # | Stage | Code | Object produced |
|---|---|---|---|
| 1 | HTTP POST | `routes.analyze` | `AnalyzeAccepted{job_id}` — returns in **~47ms**, always |
| 2 | Validate | `clone.normalize_repo_url` | canonical URL, or 400 |
| 3 | Thread spawn | `jobs.run_in_background` | `Job(status=running)` |
| 4 | Clone | `clone.shallow_clone` | working tree (depth 1, timeout-guarded) |
| 5 | Inventory | `inventory.walk_source_files` | `SourceFile[]` — path, ext, loc, **sha256** |
| 6 | Snapshot | `snapshot_directory` | `RepoSnapshot` + `IngestedRepo` |
| 7 | **Skip check** | `pipeline._inventory_digest` | sha256 of all parseable file hashes → if match, stop here |
| 8 | Parse | `parse_ingested` (parallel ≥400 files) | `FileFacts[]` |
| 9 | Resolve | `resolution.resolve` | `Edge[]` with `resolved`/`heuristic`/`dynamic_unknown` |
| 10 | Structure | `_structural_nodes` | repo→module→file→class→function CONTAINS spine |
| 11 | Metrics | `git_history.collect_history` | churn/authors/last-modified on File nodes |
| 12 | Persist | `store.save_graph` | `snapshot_id`, rows in SQLite |
| 13 | Poll → done | `routes.analyze_status` | counts returned to browser |
| 14 | ViewSpec | `compile_viewspec` | `ViewSpec` (capped, laid out, coloured) |
| 15 | Render | `GraphCanvas.tsx` | sigma/WebGL scene |
| 16 | Query on demand | `run_query` | `ResultGraph` |
| 17 | AI (optional) | `semantic/*` | `SemanticAnnotation`, `NarratedAnswer` |

**Measured on n8n (25,508 files):** clone 26.7s · parse 13.0s · metrics 1.5s · store 2.4s · **43.6s total**; viewspec ~5s per zoom.

---

# Part 5 — Knowledge Graph

**Node kinds implemented:** `repository`, `module`, `file`, `class`, `function`.
**Declared but never emitted:** `endpoint`, `external_dependency`, `external_service`, `config_value`, `author`.

**Edge kinds implemented:** `contains`, `imports`, `calls`, `inherits`.
**Declared but never emitted:** `implements`, `instantiates`, `routes_to`, `tests`, `depends_on`, `talks_to`, `reads_config`, `co_changes`, `authored_by`.

That is **5 of 10 node kinds and 4 of 13 edge kinds.** The schema is a promise the parser has only half-kept.

**Storage:** one SQLite file. Rows carry indexed columns + full pydantic JSON. Indices on `(snapshot_id, source_id, kind)` and target. A separate `summary_cache` table sits *outside* the snapshot cascade — a genuinely good decision (paid LLM output survives re-analysis).

**NetworkX:** `MultiDiGraph`, rebuilt from scratch on every `GraphView(graph)` construction.

**What's missing that matters commercially:**
- `TESTS` edges → cannot answer "what tests cover this?" (FOUNDATION Q34).
- `ROUTES_TO` → endpoints are flagged but not linked to handlers.
- Layer B entirely → cannot answer "what external services does this talk to?" (Q9), a top-10 launch question.
- `CO_CHANGES` → the hidden-coupling insight that differentiates CodeScene.

---

# Part 6 — Query Engine

| Plan | Algorithm | Complexity | Files |
|---|---|---|---|
| `blast_radius` | Reverse BFS + shortest paths, ranked by 1/distance + fan-in/1000; weakest-link confidence per path | O(V+E) | `queries/blast_radius.py` |
| `dependencies` | Forward BFS, depth-capped | O(V+E) | `queries/dependencies.py` |
| `centrality` | Hand-rolled power-iteration PageRank (α=0.85, 100 iters) + fan-in | O(k(V+E)) | `queries/centrality.py` |
| `risk` | `normalize(complexity × fan_in × churn)`, complexity summed over contained functions | O(V+E) | `queries/risk.py` |
| `entrypoints` | Filter on `is_entrypoint` | O(V) | `queries/structure.py` |
| `modules` | Group files by module, count *external* fan-in | O(V+E) | `queries/structure.py` |
| `explain` | Boundary-crossing edge scan + closures + shortest paths | O(V+E) | `queries/explain.py` |
| `concept_search` | Cosine over hashed bag-of-words | O(V·d) | `semantic/embeddings.py` |

**Honest problems:**
1. **Every query rebuilds `GraphView` from scratch** — for n8n that is 73k nodes/209k edges re-inserted into NetworkX *per request*. This is why a viewspec takes 5s. There is no caching layer.
2. **PageRank is recomputed per call**, never stored.
3. `blast_radius`'s score mixes units (`1/distance + fan_in/1000`) — a magic constant with no justification.
4. No pagination anywhere; a query on a monorepo returns everything.

---

# Part 7 — AI Layer

**Every LLM call site — there are exactly three:**

| Call | Prompt | Context builder | Budget | Cache | Model |
|---|---|---|---|---|---|
| `summarize_graph` | "Summarise `<qname>`" + graph-selected snippets, system = senior-engineer voice, facts-only | `context.assemble` → node + children + ≤5 in/out neighbours | 1,200 tok | **content-hash, cross-snapshot** | haiku |
| `narrate_project` | Q1 story from entrypoints + central files + summaries | direct fact list | 400 tok out | none | injected |
| `narrate_blast_radius` | Q8 story from ranked dependents + paths + confidences | direct fact list | 400 tok out | none | injected |

**Token budgeting:** hard ceiling in `context.assemble`; overflow is *dropped and named* in `dropped_ids`, never truncated silently. Good.

**Hashing/caching:** sha256 per definition; `summary_cache` keyed by content hash, outside the snapshot cascade. **Proven: unchanged repo → exactly 0 second calls; identical source elsewhere → 0 calls; one edited function → 1–2 calls.**

**Works with NO API key (the majority of the product):**
ingestion · parsing · resolution · graph store · all 7 deterministic queries · concept search · learning path · viewspec/all zooms · ripple · explanation page · export.

**Requires a key:** module/function summaries · project narration · blast-radius narration.

## The two brutal truths here

1. **The prompts have never been executed against the real Anthropic API.** "$0 spent" is presented as an efficiency win; it is *also* an admission that the LLM path is unvalidated. `AnthropicClient` is ~30 lines and could fail on response parsing, rate limits, or token limits and no one would know.
2. **"Concept search" is not semantic.** It is feature-hashed bag-of-words — lexical matching with a vector-shaped interface. The code says so honestly, but the *product* calls it concept search. It will fail the exact demo it is meant to win ("where is rate limiting?" only works because the docstring literally says "rate limiting").

---

# Part 8 — Incremental Pipeline

**Mechanism:** every file gets `sha256(bytes)` at inventory. `_inventory_digest` = sha256 over the sorted `path:hash` list of *parseable* files. On re-analysis: look up `(repo_url, commit_sha)`, compare stored digest to fresh digest; identical → skip parse/metrics/build entirely.

**Walkthrough — one file changes in a 500-file repo:**
1. Clone (full cost — no caching of the clone itself).
2. Inventory: 500 hashes computed; 499 identical, 1 differs.
3. Digest differs → **no skip**.
4. **All 500 files are re-parsed.** ← *the flaw*
5. Full re-resolve, full re-store as a new snapshot.
6. Summaries: only the changed definition + its file pay for an LLM call; 498 files ride the cache.

**Verdict:** the *cost* model is incremental only for the LLM layer. **Parsing is all-or-nothing.** The FileFacts for unchanged files are not cached, so "incremental everything" (Constitution 4) is honoured for tokens and violated for CPU. On a monorepo, one edited file costs a 13-second re-parse.

---

# Part 9 — Testing

**190 test functions, 3,077 lines, ratio 0.50 test:app lines.** That is healthy.

| Suite | Tests | What it really validates |
|---|---|---|
| `test_parser.py` | 40 | Graph invariants (no dangling edges, unique ids, determinism), robustness, resolution cases |
| `test_viewspec.py` | 21 | Zoom distinctness, geometry, caps, cluster/explain_id contract |
| `test_ingestion.py` | 19 | **Security**: `ext::`, `file://`, zip-slip, option injection |
| `test_queries.py` | 17 | Hand-derived blast radius, ranking, risk honesty |
| `test_semantic.py` | 13 | **The zero-call cache gate**, evidence pointers, budget |
| `test_js_parser.py` | 13 | TS/TSX, CommonJS forms, path aliases |
| `test_api.py` | 12 | Async job contract, trust boundary |
| others | 55 | fixtures, store/pipeline, git history, explain, schema contract |

**Golden fixtures:** `tiny_python`, `dynamic_python`, `tiny_js` — hand-verified expected graphs, plus *manifest-integrity* tests that verify the hand-verification itself. This is genuinely above-average practice.

**Mock LLM:** `CountingFakeLLM` — deterministic, call-counting. It is what makes the caching gate provable.

**Weak spots — ranked:**
1. **Frontend: 0 tests.** 1,822 lines, no unit tests, no component tests, no E2E. Every frontend bug this session was found by a human looking at a screenshot.
2. **No test runs the real Anthropic client.** Not even one recorded-cassette test.
3. **No performance regression test.** The 2.2× parse speedup could silently regress.
4. **No coverage measurement at all.** The 0.50 ratio is a proxy, not a fact.
5. **No concurrency test** — two simultaneous analyses of different repos against one SQLite connection is untested.
6. **CI lacks a frontend job** (no `tsc`/`build` in `.github/workflows/ci.yml`).

---

# Part 10 — Frontend

**Exists:** hero/landing · understanding sequence (real pipeline stages) · assembly reveal · sigma WebGL renderer · semantic zoom L1/L2/L3 · risk colouring · focus mode · **ripple** (blast radius animation) · node card · **explanation panel** · responsive breakpoints (verified 375/1280).

**Missing:** any routing (single page, no URLs → **nothing is shareable**, which directly contradicts the Layer-3 distribution strategy) · error boundaries · loading skeletons · empty states · accessibility (no keyboard nav of the graph, no ARIA on the canvas) · tests · analytics · the AI story panel wired to narration endpoints · Beginner/Contributor/Architect modes from EXPERIENCE.md.

**Work remaining to "shippable free tier": ~2–3 focused weeks.** The single highest-value item is **shareable URLs** — without them the entire viral loop in STRATEGY §Layer 3 cannot function.

---

# Part 11 — Technical Debt

## CRITICAL

1. **Accuracy Ledger does not exist.** The moat, the differentiator, the thing STRATEGY says competitors cannot copy — 0 lines. Everything else is commodity without it.
2. **Zero user validation.** 33 commits, 0 people have used it on their own repo. CP-5.1 is unpassed; by the project's own rules Stages 6–9 are forbidden.
3. **Jobs are in-memory.** `core/jobs.py` holds state in a process dict. A crash or restart loses every running analysis with no recovery and no user-visible explanation.
4. **Whole-graph-in-RAM.** n8n = 614MB peak. Two concurrent large analyses will OOM a small VM. There is no streaming, paging, or partial load.

## HIGH

5. **Per-request graph rebuild** — 5s viewspec on n8n, entirely avoidable with a cache.
6. **LLM path never executed for real.** Unknown unknowns in the one paid dependency.
7. **SQLite `check_same_thread=False` + threaded jobs + no locking discipline.** Works today by luck of access patterns; a concurrent write during a read is a corruption risk waiting for load.
8. **No auth on any endpoint**, and `CODELENS_ALLOW_LOCAL_ANALYSIS` is one env var away from arbitrary local-path disclosure.
9. **Parse is not incremental** (Part 8).
10. **No deployment artifact.** No Dockerfile, no start-up hardening, no reverse proxy config, no health/readiness split.

## MEDIUM

11. `app/risk/` dead folder. 12. `graph/export.py` likely dead. 13. `views/viewspec.py` at 642 lines is doing layout + colour + choreography + capping + clustering — split it. 14. Magic constants (`_MAX_RENDERED_FILES=600`, `0.35`, `1/distance + fan_in/1000`) undocumented as tunables. 15. No DB migration story — schema change = wipe. 16. No structured logging. 17. `softwork/` + a PDF in the repo root. 18. No pagination on any list endpoint.

## LOW

19. `starlette.testclient` deprecation warning in every run. 20. Frontend CSS is one 645-line file. 21. `README.md` is a stub (2 lines) while five excellent docs sit beside it. 22. No `CONTRIBUTING`/`LICENSE` header discipline in source files.

---

# Part 12 — Scalability

| Scale | Verdict |
|---|---|
| **100 repos** | Fine, *sequentially*. One SQLite file, ~50MB–1GB of graphs. Concurrency is the risk, not volume. |
| **10,000 repos** | **Breaks.** Single SQLite file becomes a write bottleneck and a single point of failure; no sharding; no object storage; whole-graph reads make every query O(repo size). Needs Postgres + object storage + a real queue. |
| **Enterprise monorepo (n8n-scale, 25k files)** | **Works today** — 43.6s cold, ~0s cached, capped rendering. This is a genuine achievement. |
| **Million-line repo** | **Fails.** Extrapolating n8n: ~500MB+ graph, multi-GB RAM, minutes of parse, SQLite row counts in the millions per snapshot. |

**Bottlenecks, in order:** (1) whole-graph-in-RAM, (2) per-request graph rebuild, (3) single SQLite file, (4) non-incremental parse, (5) clone time (26.7s of n8n's 43.6s — now the largest single cost).

---

# Part 13 — Startup Evaluation

**As a Principal Engineer:** Unusually disciplined for a solo project. The fact/annotation separation is real, the confidence ladder is intellectually honest, the golden fixtures are proper engineering. I would hire whoever built the parser. I would also refuse to deploy this as-is.

**As a CTO:** I can demo this tomorrow. I cannot sell it. No auth, no tenancy, no deploy, no observability, no ledger. The gap between "impressive demo" and "product" is ~3 months, and the gap between "product" and "the pitch in STRATEGY.md" is ~12.

**As a YC partner:** *"You've spent 33 commits building beautifully and zero minutes talking to users. Your own document says the only gate that matters is one developer saying 'I'd use this again,' and you haven't run it. Why are we discussing PageRank?"* Talent: obvious. Judgement about sequencing: unproven. The build quality would make me *more* worried, not less — it suggests a founder who retreats into engineering.

**As a technical investor:**
- **Novelty:** Moderate. Dependency graphs and code maps are a crowded, repeatedly-failed category (CodeSee was acquired and stalled). The confidence-labelled blast radius is a real, defensible *technical* idea.
- **Defensibility today:** **Near zero.** Everything built so far is reproducible by a competent team in 6–10 weeks.
- **Defensibility as designed:** Real, *but entirely unbuilt* — the Accuracy Ledger is the whole thesis and it does not exist. You are 0% of the way into your own moat.
- **Risks:** (a) it is a feature, not a company, until the ledger exists; (b) LLM vendors could subsume "explain my repo" cheaply; (c) the founder's demonstrated preference is building over selling.

---

# Part 14 — Roadmap Validation

| Milestone | Claimed | Real |
|---|---|---|
| Stage 0 realignment | ✅ | ✅ Genuinely done |
| M1 — the graph, no AI | ✅ | ✅ Verified on 3 repos |
| Stage 2 — deterministic queries | ✅ | ✅ |
| Stage 3 — semantic layer | ✅ | ⚠️ **Code complete, gate unpassed.** M2/M3 required "8/10 correct on an unfamiliar repo judged by someone who knows it" — never run. |
| Stage 4 — hero moment | ✅ | ✅ Compiler, renderer, ripple, framing all verified live |
| Stage 5 — CP-5.1 validation | ⬜ | ⬜ **Not started. The gate.** |
| Stages 6–9 | ⬜ | ⬜ Correctly untouched |

**Drift from architecture:**
- CORE.md promises chat-as-router over the QueryPlan registry → **not built**.
- FOUNDATION's 10 launch questions: Q9 ("external services") and Q11 ("data model") are **unanswerable** — Layer B doesn't exist. So the real count is **8 of 10 with machinery, not 10**.
- ARCHITECTURE says `chat/` folds into `queries/` — done. Good.
- `risk/` was supposed to hold metrics; risk logic lives in `queries/`. Minor, but the folder should die.

**Wrong decisions (my judgement):**
1. **Building Stage 4's polish before running CP-5.1.** The ripple, the framing fixes, the responsive CSS — all excellent, all premature against a 0-user baseline.
2. **Calling lexical search "concept search."** It sets up a demo failure.
3. **Presenting "$0 spent" as pure virtue** when it also means the paid path is untested.

**Better alternative to what's next:** stop building; run the M3 gate (1 hour, cents) and CP-5.1 (a week of conversations).

---

# Part 15 — Code Quality

| Subsystem | Modularity | Naming | SOLID | Readability | Score |
|---|---|---|---|---|---|
| ingestion | 9 | 9 | 9 | 9 | **9.0** |
| parser | 8 | 9 | 8 | 8 | **8.3** |
| queries | 9 | 9 | 9 | 9 | **9.0** |
| graph | 8 | 8 | 8 | 8 | **8.0** |
| views | 6 | 8 | 6 | 8 | **7.0** (one 642-line file doing five jobs) |
| semantic | 8 | 8 | 9 | 8 | **8.3** (protocol seam is exemplary) |
| api | 8 | 8 | 8 | 8 | **8.0** |
| core | 6 | 8 | 6 | 8 | **7.0** (`jobs.py` is a placeholder pretending to be infrastructure) |
| frontend | 6 | 7 | 5 | 7 | **6.3** (untested; `GraphCanvas` mixes render, animation, interaction) |

**Overall: 7.9/10.** Comment quality is unusually high — comments explain *why*, including rejected alternatives. Dependency management is genuinely disciplined (52 packages, each justified).

---

# Part 16 — Missing Pieces

**Not built:** Accuracy Ledger · GitHub App · chat router · Layer B (external deps/services/config) · Layer E (PRs/issues/outcomes) · auth · billing · multi-tenancy · deployment · observability · shareable URLs · CodeLens Score · README badge · `TESTS`/`ROUTES_TO`/`CO_CHANGES` edges · frontend tests · migrations.

**Incorrect assumptions in the current code:**
1. *"One process is enough"* — true for one user, false the moment two people click at once.
2. *"The graph fits in memory"* — true to ~25k files, false at 100k.
3. *"Content hash makes everything incremental"* — true for LLM cost, false for CPU.
4. *"Lexical search will read as semantic"* — it won't, in the demo that matters.
5. *"Deterministic answers need no validation"* — correctness ≠ usefulness; only a human judge closes that gap.

**Hidden work nobody has scheduled:** rate limiting and abuse handling for public analysis · clone disk-space management (`/tmp/codelens` grows unbounded) · handling repos that fail mid-parse · private-repo token handling · GDPR/data-retention for stored graphs · cost controls on LLM spend · monorepo sub-path analysis.

**Problems that will appear later:** SQLite lock contention under the first real concurrency · unbounded `/tmp` growth on a server · summary cache growing without eviction · sigma performance on 600 nodes with labels on mid-range laptops · TypeScript grammar drift.

---

# Part 17 — Future Vision

**Can this evolve into CORE.md's Software Knowledge Graph platform? Yes — the foundations are right.** Specifically: the frozen schema, the `GraphStore` interface, the `QueryPlan` registry, the `LLMClient`/`EmbeddingBackend` protocols, and the fact/annotation split are all exactly the seams such a platform needs. That is genuine architectural foresight and it is *already paid for*.

**Required changes, in dependency order:**
1. **Storage:** SQLite → Postgres (metadata/jobs) + object storage (graph blobs) + optional Neo4j behind `GraphStore`.
2. **Memory:** whole-graph load → paged/lazy subgraph loading + a query-level graph cache.
3. **Jobs:** in-memory threads → durable queue (arq/Celery) with retries and visibility.
4. **Ingest:** all-or-nothing parse → per-file fact caching keyed by content hash.
5. **Layers:** implement B (deps/services/config), finish C (co-change/authors), build E (PRs/issues/outcomes).
6. **Ledger:** GitHub App → prediction records → outcome watcher → public accuracy. *This is the company.*
7. **Multi-tenancy:** accounts, per-tenant isolation, quotas.
8. **API:** versioning, pagination, rate limits, keys — before any "ecosystem" talk.

None of these invalidate what exists. That is the strongest thing I can say about this codebase.

---

# Part 18 — Final Report

**Overall completion:** **~45%** of the MVP-through-Stage-4 plan · **~15%** of the CORE.md platform vision · **0%** of the moat.

**Engineering maturity: strong MVP.** Not a prototype (too well tested, too disciplined). Not production (no auth, no deploy, no durability, no observability). Concretely: **MVP+, one hard month from production for a single-tenant paid beta.**

**Biggest strengths**
1. The deterministic core is correct, tested, and honest — the confidence ladder and evidence pointers are the real intellectual property.
2. Architectural seams (`GraphStore`, `QueryPlan`, `LLMClient`) were drawn before they were needed, and they will pay off.
3. It genuinely works on a 25,000-file monorepo in 44 seconds.
4. Test discipline (golden fixtures verifying their own hand-verification) is above industry norm.

**Biggest weaknesses**
1. **Zero users.** Everything else is subordinate to this.
2. **The moat is 0% built** while polish is 85% built.
3. **The paid dependency (LLM) has never actually been called.**
4. **In-memory jobs + whole-graph-in-RAM + single SQLite file** = three separate reasons this cannot serve two people at once.

**Immediate priorities (do these, in this order, and nothing else)**
1. Run the **M3 gate** — real API key, unfamiliar repo, 8/10 judged by you. *1 hour, cents.*
2. Run **CP-5.1** — 20 developers, their repos, count unprompted pulls. *1–2 weeks.*
3. **Only if ≥5 pull:** durable jobs → deploy → auth → **start the Ledger clock**.

## Top 20 improvements, ranked by impact

| # | Improvement | Why |
|---|---|---|
| 1 | Run CP-5.1 validation | Decides whether anything else matters |
| 2 | Run the M3 accuracy gate | The only unvalidated *correctness* claim |
| 3 | Build the Accuracy Ledger v0 | It is the entire moat |
| 4 | Durable job queue | Removes the "restart loses work" class of failure |
| 5 | Graph cache + lazy loading | Kills the 5s query latency and the OOM risk |
| 6 | Shareable URLs / routing | Unblocks the whole distribution strategy |
| 7 | Per-file incremental parse | Makes "incremental everything" true |
| 8 | Real LLM integration test | Removes unknown unknowns in the paid path |
| 9 | Deployment (Docker + one host) | Precondition for any external user |
| 10 | Auth + private repos | The Pro tier's entire value |
| 11 | Layer B (deps/services/config) | Unlocks launch questions Q9/Q11 |
| 12 | Frontend tests | 1,822 untested lines rendering the product |
| 13 | Postgres migration behind `GraphStore` | Multi-user precondition |
| 14 | Rename/replace "concept search" | Stop overselling lexical matching |
| 15 | `TESTS` + `ROUTES_TO` edges | Two more launch questions |
| 16 | Observability (Sentry + metrics) | You cannot fix what you cannot see |
| 17 | Split `viewspec.py` | 642 lines, five responsibilities |
| 18 | Delete `app/risk/`, `export.py`, root clutter | Dead code lies about the system |
| 19 | Clone caching / disk management | `/tmp` grows unbounded on a server |
| 20 | Coverage measurement + CI frontend job | Make quality measurable, not asserted |

---

## The one-paragraph verdict

CodeLens is a **well-engineered, honestly-built MVP of the substrate** — and a **0% built version of the business it claims to be.** The parser, the graph, the queries, and the visualisation are real, tested, and work on repositories most tools choke on. But the Accuracy Ledger that STRATEGY.md calls the moat does not exist, no human has ever used this on their own code, and the paid AI path has never been executed once. The engineering is not the risk here; the engineering is the *comfort zone*. Stop building. Go run the gate you wrote for yourself.
