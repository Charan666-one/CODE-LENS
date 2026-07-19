# CodeLens Checkpoints — The Execution Ladder

*July 19, 2026. The executable layer beneath ROADMAP.md. CORE = the idea. STRATEGY = the business. FOUNDATION = the data. EXPERIENCE = the feel. ARCHITECTURE = the build. ROADMAP = the decade. **This = the next 20 weeks, checkpoint by checkpoint** — what to build, how to prove each piece is real, and why it earns its place. If ROADMAP is the map, this is the turn-by-turn.*

---

## How to read this document

Every checkpoint has exactly three parts. If any is missing, the checkpoint isn't defined yet.

- **Deliverable** — what concretely exists when it's done. A file, an endpoint, a passing test, a shipped page.
- **Gate** — the observable test that proves it. **"Done" means the gate passes — nothing softer.** This is honest theater (EXPERIENCE §"honest theater") applied to your own progress: you don't get to *feel* finished, you get to *demonstrate* finished. A gate you can't run isn't a gate.
- **Why** — the one-line strategic tie-back to a founding doc, so no checkpoint drifts into busywork.

**Numbering:** `CP-N.M` = engineering checkpoints. `B-N` = business/founder checkpoints (they run *in parallel*, not after). Bold **MILESTONE GATES** are the ARCHITECTURE M1–M5 acceptance moments and the STRATEGY kill/continue gate — the handful of tests the whole company is graded on.

**Week ranges are guidance, not contracts.** A solo founder's velocity is unpredictable; the *order* and the *gates* are what matter, not the calendar.

**The five Constitution guardrails** (ROADMAP §Constitution) are checked on every checkpoint they touch — flagged inline as `⚖`:
1. Evidence before explanation · 2. Deterministic first, AI second · 3. Fast enough for daily use · 4. Incremental by hash · 5. Confidence is visible · (+ the Ledger never lies, from Stage 8 on).

---

## ⭐ The one gate that matters right now

> Everything before **CP-5.1** exists to reach a single sentence, said unprompted by a real developer about their own repository: **"I'd use this again."**

Stages 6–9 (productionization, distribution, the Ledger, scale) are **forbidden to start** until that gate passes. This is the discipline that keeps a solo founder alive: building the distributed system, the billing, and the GitHub App for a product nobody has yet pulled for is the classic death (STRATEGY §7, ARCHITECTURE §"The reminder"). Reach the gate first. Then earn the right to scale.

---

## The critical path (what blocks what)

```
ENGINEERING SPINE (sequential — each stands on the last):

  CP-0 ──▶ CP-1 ──▶ CP-2 ──▶ CP-3 ──▶ CP-4 ──▶ ★ CP-5.1 GATE ★ ──▶ CP-6 ┐
 realign   graph   queries   AI-edge  hero      "I'd use it again"    prod ├─▶ CP-9
                                       moment                          CP-7 ┤   scale
                                                                       CP-8 ┘
                                                            (6/7/8 parallelize
                                                             once the gate passes)

BUSINESS TRACK (parallel with CP-0 … CP-5, never after):

  B-0 ────────────▶ B-1 ─────────────────────────▶ B-2
  smoke test        10–20 ICP interviews            10 pricing conversations
  (before you       (while you build the graph)     (at the validation gate)
   build heavy)
```

Read that diagram as the whole plan in one glance: **build the graph, prove it deterministically, wrap it in AI and cinema, put it in front of real users — and only if they pull, industrialize it.** The business track de-risks the build in parallel, so you never spend five weeks on a wedge nobody wanted.

---
---

# STAGE 0 — Foundation realignment
*Week 0 · The scaffolding must match the architecture before the architecture can be built on it.*

The repo already drifted from its own rules. Two founding-document violations are sitting in the code right now, and every downstream checkpoint inherits them if they aren't fixed first. Stage 0 is cheap, unglamorous, and non-negotiable — it's the difference between building on rock and building on the "five databases for zero users" swamp STRATEGY §7 warns about.

### CP-0.1 — Realign scaffolding to SQLite-first
**Deliverable:**
- `backend/app/core/config.py` rewritten: a single `SQLITE_PATH` (+ `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` as the only external keys). **Delete** the hardcoded `DATABASE_URL` (Postgres), `REDIS_URL`, and `NEO4J_*` — they encode a destination, not a starting point (ARCHITECTURE §"Two corrections": *SQLite + NetworkX now; Postgres/Neo4j/Qdrant/Redis when there are users to justify each one*).
- `backend/requirements.txt` trimmed to the MVP set: `fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `tree-sitter` + `tree-sitter-python` + `tree-sitter-javascript`, `GitPython`, `radon`, `anthropic` (and/or `openai`), `pytest`, `pytest-asyncio`, `ruff`, `mypy` — **plus the two that are missing and load-bearing: `networkx` (the MVP traversal engine — currently absent entirely) and a small vector index (`sqlite-vss` or `faiss-cpu`)**. **Remove** `celery`, `chromadb`, `kubernetes`, `langgraph`, `langchain*`, `redis`, `asyncpg`, `prometheus-*`, `weasyprint`, and the rest of the premature heavy stack — they return in Stage 6+ when load justifies them (STRATEGY §7).
- **One source of truth for the schema.** `backend/app/graph/schema.py` is canonical; `PLANNING/schema.py` becomes a documentation copy (header comment pointing to the real one) or is deleted. Two divergent copies of the "frozen contract" is the one thing a frozen contract cannot survive.
- Tooling: `ruff` + `mypy` config in `pyproject.toml`, `pytest` wired, and a minimal CI file (`.github/workflows/ci.yml`) running `ruff check`, `mypy`, `pytest`.

**Gate:** `pytest` green · `uvicorn app.main:app` boots · `GET /health` returns 200 · `ruff check` clean · `python -c "import networkx"` succeeds · exactly one `schema.py` is importable as the contract.

**Why:** ⚖(2,3) The architecture explicitly chose determinism-first and minimal-deployments. Code that boots five databases contradicts the founding build order; fixing it now costs an hour and saves the months a premature distributed system steals.

### CP-0.2 — Golden-fixture harness
**Deliverable:** `backend/fixtures/` containing 2–3 *tiny* hand-authored repos (a dozen files each) with their **expected graph committed alongside** as JSON — exact node counts, edge counts, and a handful of specific CALLS/IMPORTS/INHERITS edges verified by hand. A `pytest` harness loads a fixture, runs the (soon-to-exist) parser, and asserts the emitted graph equals the expected graph.

**Gate:** the harness runs in CI and *fails loudly* on any graph drift. (It will fail today because the parser doesn't exist yet — that's correct; it's the test the parser must earn its way to passing in CP-1.2.)

**Why:** ⚖(1) "The parser's correctness is CodeLens's correctness" (ARCHITECTURE §2). Golden tests from day one are how the moat's hardest, unglamorous work (STRATEGY §9 "the parser is the grind") stays honest as it grows.

### B-0 — Smoke test *(parallel, before heavy build)*
**Deliverable:** a landing page (the EXPERIENCE §"landing page" trailer, even as a static mock: black canvas, one glowing node, "Understand software, not files," a single GitHub-URL field, waitlist capture) + the *problem* posted in 5 ICP communities — HN "Ask HN: how do you know what an AI-written change will break?", r/ExperiencedDevs, Lobsters, 2 relevant Discords (STRATEGY §7 Phase 0, §10 Days 1–2).

**Gate:** a measured resonance signal before you commit five weeks of engineering — waitlist signups + comment threads that confirm the pain is felt, not just imagined. Weak signal here is *cheap* to learn and changes what you build.

**Why:** Distribution is the stated failure mode (STRATEGY §9: "a great product nobody hears about"). Starting the audience on Day 0 treats publishing as half the job, from the first day.

---
---

# STAGE 1 — The core asset (M1: no AI, no polish)
*Weeks 1–3 · Build the graph. If the graph is wrong, everything downstream is beautifully wrong (ARCHITECTURE §"Prime directive").*

This is the heart of the company — the "one map" everything else becomes a query on (FOUNDATION §North Star). Zero LLM calls in this entire stage. The graph must be fully functional and testable without a single token spent (ARCHITECTURE corollary 1).

### CP-1.1 — Ingestion → `RepoSnapshot`
**Deliverable:** `ingestion/` module: GitHub URL or zip → shallow clone (GitPython, `--depth 1`), size-capped (`MAX_REPO_SIZE_MB`), timeout-guarded → extension census → a `RepoSnapshot` (the schema already defines it). Knows nothing about graphs or AI.

**Gate:** clones and snapshots 3 real repos including **CodeLens itself** (dogfood from commit one, FOUNDATION §"Immediate next steps" #2); the language census matches a manual `find`-count; oversized/timed-out repos fail gracefully with a clear error.

**Why:** ⚖(4) One normalized entry point, content-hash keyed, so re-analysis is incremental from the very first stage.

### CP-1.2 — Python parser → Layer A facts
**Deliverable:** `parser/` module using `tree-sitter-python`: emits **facts only** into the frozen schema — `CONTAINS` (repo→module→file→class→function), `IMPORTS`, `CALLS`, `INHERITS`. Cyclomatic complexity per function via `radon`. Entrypoint detection (`__main__` guards, FastAPI/Flask route decorators, CLI registrations, `package.json` scripts) sets `is_entrypoint` + `entrypoint_kind`. No summaries, no scores (ARCHITECTURE §2).

**Gate:** the CP-0.2 golden fixtures now **pass**; every emitted node/edge validates against `schema.py`; running on CodeLens's own backend produces a plausible module/file/function tree on inspection.

**Why:** ⚖(2) Layer A is 85% of the 100 questions' substrate (FOUNDATION §"What the tags reveal"). Deterministic facts, one universal schema, ready for a JS/TS emitter later without schema change.

### CP-1.3 — Call resolution + confidence flags
**Deliverable:** call-site → definition resolution across modules, aliases, and re-exports, with **every `CALLS` edge carrying a `confidence`**: `resolved` (definition found), `heuristic` (name/arity match), `dynamic_unknown` (dynamic dispatch). The genuinely hard problem, named honestly (ARCHITECTURE §2).

**Gate:** **hand-verify 20 `CALLS` edges** on a real repo → **≥90% of `resolved`-flagged edges are correct.** (Heuristic/dynamic edges are allowed to be uncertain — that's the point; they're *labeled* uncertain.)

**Why:** ⚖(1,5) "The graph tells the truth about its own certainty" (ARCHITECTURE §2). This confidence field is what lets the ripple UI render uncertain edges honestly and what feeds the Accuracy Ledger's self-improvement later (STRATEGY §Layer 2).

### CP-1.4 — GraphStore + SQLite persistence + NetworkX + checkpointed pipeline
**Deliverable:** a `GraphStore` **interface** (so the backend can swap to Neo4j later without touching queries — ARCHITECTURE §3), backed by SQLite for persistence + NetworkX in-memory for traversal. The pipeline runs as sequential, resumable, **idempotent, hash-keyed stages** writing checkpoints: `cloned → parsed → graph_built` (ARCHITECTURE §"pipeline with checkpoints"). Unchanged input skips work.

**Gate:** build → persist → reload → traverse CodeLens's own graph end-to-end; **re-running on an unchanged repo skips all work** (proven by timing + a "0 nodes rebuilt" log). All graph access goes through `GraphStore` — no query reaches into SQLite directly.

**Why:** ⚖(4) Incremental everything. The checkpoint model *is* the future event model minus the broker (ARCHITECTURE §"Two corrections"), so Stage 8's GitHub webhooks are a near-rewrite-free upgrade.

### CP-1.5 — Git-lite temporal pass
**Deliverable:** one `git log --numstat` pass populating per-file `churn_count`, `author_count`, `last_modified` on nodes (FOUNDATION §"Git-lite"). No CO_CHANGES matrix, no Author nodes yet — those are Layer C, post-MVP.

**Gate:** churn counts for 5 spot-checked files match a manual `git log` count.

**Why:** ⚖(2) Feeds the risk formula (CP-2.3) and the "hot module" glow (CP-4) — real churn data, so the pulsing modules are honest theater, not decoration.

### CP-1.6 — Crude visual dump
**Deliverable:** a throwaway GraphViz/JSON renderer to eyeball the graph. Deliberately ugly — this is not the product's visualization, it's the parser's debugger.

**Gate — ★ MILESTONE GATE M1 (ARCHITECTURE §Milestones):** runs on 3 real repos including CodeLens; golden-fixture tests pass; the 20-edge spot check holds at ≥90% `resolved` accuracy. **The core asset exists and is proven correct — with zero AI involved.**

**Why:** M1 is the foundation the entire platform bet rests on. "Google's index mattered because searches hit it" (ARCHITECTURE §"The reminder") — but first the index has to be *right*.

### B-1 — ICP problem interviews *(parallel, Weeks 1–4)*
**Deliverable:** 10–20 problem interviews with the ICP (engineers/leads at 10–150-person AI-forward orgs, OSS maintainers drowning in AI PRs — STRATEGY §5). **Interview the pain, don't pitch the product** (STRATEGY §10).

**Gate:** you can name, in the interviewees' own words, the exact moment the comprehension-debt pain bites — and you're tracking how many are *pulling* toward a solution (feeding the kill/continue count at CP-5.1).

**Why:** The kill/continue gate needs ~20 conversations to be meaningful; starting them during the build means the validation data is ready the moment the product is.

---
---

# STAGE 2 — Deterministic queries that prove the graph (part of M3)
*Weeks 3–4 · Ship the graph's answers before any AI touches them (FOUNDATION §"Immediate next steps": Q8 + Q4 first, "before any AI is wired in").*

Eight of the ten launch questions are **pure graph operations** (FOUNDATION §"How each question resolves"). Proving them deterministically — no LLM — validates graph correctness in a way an AI answer never could, and delivers the paid wedge (Blast Radius) as literally the second thing that works.

### CP-2.1 — QueryPlan registry + ResultGraph contract
**Deliverable:** the registry pattern from ARCHITECTURE §5 — every question is a *named, tested* query plan following the uniform pipeline `Question → QueryPlan → Graph Traversal → ResultGraph`. A `ResultGraph` type (the subgraph + ranking + paths a query returns, the input to both visualization and narration).

**Gate:** two plans register and run through the same pipeline; adding a third requires no change to the runner (the registry is genuinely a registry, not a switch statement).

**Why:** ⚖(6) "The graph is the product; every capability is a query on it." The registry is what makes free-form chat later a *router*, not a rewrite (ARCHITECTURE §5).

### CP-2.2 — BlastRadius (Q8) — *the wedge*
**Deliverable:** reverse transitive closure over `CALLS`+`IMPORTS` from a node, **ranked by distance & fan-in, with the actual dependency paths included** (FOUNDATION Q8, STRATEGY §Layer 1).

**Gate:** on a repo you know cold, the blast radius of a core module **matches a hand-derived list of dependents** — including the paths, clickable down to the edge.

**Why:** ⚖(1) This is what people pay for (STRATEGY §Layer 1) and the seed of the Accuracy Ledger. "Must be flawless — it's the credibility seed" (STRATEGY §7 Phase 1).

### CP-2.3 — Centrality (Q4), Dependencies (Q9), Risk-lite (Q10)
**Deliverable:** importance ranking via fan-in + PageRank over `IMPORTS`+`CALLS` (Q4); forward transitive closure, depth-limited (Q9); `risk = normalize(complexity × fan_in × churn)` per module (Q10, FOUNDATION §"How each question resolves").

**Gate:** the top-5 "most important modules" match the judgment of someone who knows the repo; the risk ranking surfaces modules that *feel* dangerous to that person.

**Why:** ⚖(2) Pure graph math — the deterministic core the AI later only *explains*, never computes.

### CP-2.4 — Entrypoints (Q3), Modules (Q2 structural half)
**Deliverable:** entrypoints grouped by kind (server/CLI/jobs) from the `is_entrypoint` flags; module inventory with aggregated IMPORTS/CALLS.

**Gate:** entrypoints correctly identified on a FastAPI repo, a Flask repo, and a CLI repo.

**Why:** Q2/Q3 are the "first day" understanding questions (FOUNDATION §Beginner) — the free, viral layer's substrate.

---
---

# STAGE 3 — Semantic layer (M2: AI at the edge, and not before)
*Weeks 4–5 · Now, and only now, AI enters — to explain graph facts, never to invent them (ARCHITECTURE §4).*

### CP-3.1 — RAG context assembler
**Deliverable:** the retrieval rule made real — `Question → Graph query → relevant nodes → their snippets + summaries → LLM`. **Never send the repository.** Token budget enforced per query; context ranked by graph distance and fan-in, truncated, never stuffed (ARCHITECTURE §"AI context assembly").

**Gate:** every LLM call's context is a graph-selected subgraph with a hard token ceiling — verifiable in a log; no code path sends whole-repo text.

**Why:** ⚖(1,2) "The graph IS the retrieval engine — better than vector-only RAG because structure encodes relevance similarity can't see." This *is* the differentiator against the LLM-first incumbents (STRATEGY §6 objection wall).

### CP-3.2 — Module & function summaries, hash-cached
**Deliverable:** one-sentence plain-English summaries per module and public function, stored as `SemanticAnnotation` with `derived_from: [node_ids]` populated, **cached by `content_hash`** (schema already supports this).

**Gate:** ⚖(4) re-running on an unchanged file makes **zero** second LLM calls — proven by a cache-hit log. "An unchanged file never pays for a second LLM call" (ARCHITECTURE §4).

**Why:** ⚖(1) Annotations that always point back at their facts is the determinism moat expressed as a data model (FOUNDATION §Design principle 1).

### CP-3.3 — Embeddings + concept search (Q7)
**Deliverable:** embeddings over name+docstring+summary in the small vector index (sqlite-vss/FAISS); concept search returns **nodes on the graph**, not text snippets (FOUNDATION Q7).

**Gate:** "where is rate limiting?" (or similar) returns the correct node(s) on a repo where no file is literally named that.

**Why:** ⚖ Embeddings are for concept→location mapping only — "needed for navigation, not for reasoning" (FOUNDATION §"What the tags reveal"). AI stays at the edge.

### CP-3.4 — Narrated answers (Q1, Q6, Q8 story)
**Deliverable:** the "senior engineer giving a tour" voice (EXPERIENCE §"AI narrates stories") for Q1 (what does this do), Q6 (learning path), and the Q8 blast-radius story — **every claim carrying an evidence pointer clickable to a real node.**

**Gate — ★ MILESTONE GATE M2 + M3 (ARCHITECTURE §Milestones, FOUNDATION §10 questions):** the **10 launch questions answer end-to-end** through the registry (as buttons/commands, not chat yet); **8 of 10 correct on an *unfamiliar* repo, judged by someone who knows it**; every narrated claim traces to a graph node (M2's gate: "every narrated claim clickable to a real node").

**Why:** ⚖(1) This gate is the credibility foundation the Accuracy Ledger later makes public — "every claim traceable to the graph, enforceable from day one" (FOUNDATION §"the proof the schema is sufficient").

---
---

# STAGE 4 — The Hero Moment (frontend · EXPERIENCE Phase 1)
*Weeks 5–7 · "The hero moment ships with the first version — it IS the launch" (EXPERIENCE §build order).*

The graph works and answers questions. Now make a developer say *"wait… what is this?"* The visualization engine is server-side logic (ARCHITECTURE §6); the frontend only renders — it never computes truth.

### CP-4.1 — ViewSpec compiler
**Deliverable:** server-side compiler: `ResultGraph → ViewSpec` (which nodes exist at each zoom level, cluster membership, **precomputed** layout positions, risk/activity colors, animation choreography — assembly order, ripple propagation, path pulses). Semantic zoom and level-of-detail live here (ARCHITECTURE §6, EXPERIENCE §"city model").

**Gate:** the same ResultGraph compiles to distinct ViewSpecs at zoom L1/L2/L3, with layout positions computed server-side (the frontend receives coordinates, never calculates them).

**Why:** ⚖(3) Level-of-detail is *also* the performance strategy — a 50k-file monorepo at zoom 1 is 8 clusters (EXPERIENCE §"city model"). The experience constraint and the rendering budget are one design.

### CP-4.2 — Next.js + Graph State Manager + WebGL renderer
**Deliverable:** Next.js frontend; a **Graph State Manager** (game-engine model — every panel reads one client-side graph state, components never fetch independently, ARCHITECTURE §"Repository layout"); WebGL renderer (sigma.js / cosmos-gl class, **not SVG**); semantic zoom L1–3 wired to ViewSpecs.

**Gate:** ⚖(3) thousands of nodes render at **60fps**; zooming changes *what exists*, not just magnification (EXPERIENCE §"Semantic zoom, not magnification").

**Why:** "Thousands of nodes at 60fps or the magic dies" (EXPERIENCE §engineering note). SVG cannot do this; the renderer choice is load-bearing.

### CP-4.3 — Assembly reveal + understanding-language
**Deliverable:** the first-run choreography (EXPERIENCE §"Hero Moment") — pipeline stages stream in understanding-language ("Understanding…", "Building Repository Brain…"), then nodes stream outward, clusters self-organize into named districts, the repo assembles itself, zooms to fit, and the title card lands. The pipeline copy is driven by the **real** CP-1.4 checkpoint stages (theater and telemetry from one source). Dark, screenshot-worthy default aesthetic.

**Gate:** the assembly is a *replay of real construction order* (entrypoints first, then BFS outward — not a random animation); it's **interruptible** (one click → exploring); and the final frame is good enough to screenshot *by default*.

**Why:** That frame is the shareable artifact — the distribution engine's raw material (EXPERIENCE §"the megaphone's design spec", STRATEGY §Layer 3).

### CP-4.4 — Click-to-story + the ripple
**Deliverable:** click a node → focus mode (its callers/callees/imports stay lit, rest recedes) with the AI story synchronized to node highlights (EXPERIENCE §"answers are camera movements + light, THEN text"); the **ripple** — blast radius as a wave expanding through transitive dependents, intensity fading with distance.

**Gate — ★ EXPERIENCE GATE (EXPERIENCE §"The test"):** show it to a developer for **10 seconds, silently.** If they say *"nice graph tool"* → iterate. If they lean in with *"wait… what is this?"* → passed. Also: ⚖(1) every visual claim is clickable down to the code line; ⚖(3) 60fps holds on a 13" laptop, not just a demo rig.

**Why:** This reaction is the entire distribution strategy (EXPERIENCE §"The one goal"). The experience *is* the megaphone.

---
---

# STAGE 5 — The validation gate (M4)
*Weeks 7–8 · The single decision that governs whether Stages 6–9 happen at all.*

### CP-5.1 — Live on real users' repos
**Deliverable:** the product run live on the repos of the B-1 interviewees and the B-0 waitlist — their *own* code, not a demo repo.

**Gate — ★★ THE KILL/CONTINUE GATE (STRATEGY §7, ARCHITECTURE M4):** a real developer uses CodeLens on their repo and says **"I'd use this again"** — *unprompted*. Across ~20 ICP conversations, **≥5 are genuinely pulling** for it. **If fewer than 5 pull, change the wedge before writing another line of code.** That's the process working, not failing.

**Why:** This is Phase 1's exit test and "the only thing that matters right now" (ROADMAP §"How to hold this"). One developer returning unprompted is worth more than any launch post.

### B-2 — Pricing conversations *(parallel)*
**Deliverable:** 10 real conversations testing the Free / Pro (~$19–29/mo) / Team (~$29–49/seat) hypothesis (STRATEGY §8) against actual willingness to pay.

**Gate:** the pricing tiers are validated (or corrected) against 10 real reactions before any billing is built.

**Why:** "Test against 10 real conversations before committing" (STRATEGY §8). Building Stripe tiers nobody validated is Stage 6 waste.

---
---

> ## 🚦 CHECKPOINT: The gate is passed
> If CP-5.1 passed — real developers pull for it — you've earned the right to industrialize. Everything below is now justified by demand. If it *didn't* pass, **stop here and change the wedge**; do not build any of Stages 6–9 on an unvalidated product. This is the line between a startup and an expensive hobby.

---
---

# STAGE 6 — Productionization
*Post-validation, Weeks 8–12 · Now — and only now — the boring infrastructure that turns a validated demo into a business someone can pay for.*

### CP-6.1 — Deploy infrastructure
**Deliverable:** containerized deploy, one region. **Managed Postgres for metadata/jobs only** (not the graph — graphs stay in the SQLite/object-store model until scale demands otherwise); object storage for persisted graphs. A background worker **only if needed** — prefer FastAPI `BackgroundTasks` or `arq` before reaching for Celery (STRATEGY §7: defer the heavy queue until load exists).

**Gate:** a graph analysis runs to completion on the deployed environment, asynchronously, with the result persisted and retrievable.

**Why:** ⚖ "Minimal deployments — the abstractions are free; the distributed system is not" (ARCHITECTURE §"The reminder"). Postgres arrives now because there are finally users to justify it.

### CP-6.2 — Auth, accounts, private repos, live progress
**Deliverable:** GitHub OAuth, user accounts, private-repo analysis, rate limits, and a job queue whose progress feeds the EXPERIENCE progress UI — **the CP-1.4 pipeline checkpoints are the progress events** (one source for theater + telemetry, ARCHITECTURE §"Two corrections").

**Gate:** a logged-in user analyzes a private repo and watches real pipeline stages stream as progress.

**Why:** Private repos are the Pro tier's core value (STRATEGY §8).

### CP-6.3 — Billing
**Deliverable:** Stripe integration — Free (public repos, maps, scores), Pro (private repos, blast radius on demand, saved projects), Team (per-seat) — per the B-2-validated pricing.

**Gate:** a real payment upgrades a real account to Pro and unlocks private-repo analysis.

**Why:** The free→pro→team ladder is "additional reads on the same graph" (STRATEGY §4) — the account expands without rebuilding.

### CP-6.4 — Observability, safety, security
**Deliverable:** error tracking, structured logging/metrics, database backups, secrets management, and a security review (the `/security-review` pass over auth, repo cloning, and the payment flow).

**Gate — ★ PRODUCTION GATE:** a **stranger** signs up, pays, analyzes a private repo, and gets a correct result with **zero manual intervention** from you.

**Why:** ⚖(3) "A workflow tool that's slow — or breaks — gets opened weekly, then never" (ROADMAP §Constitution 3). Production reliability is a feature of trust.

---
---

# STAGE 7 — Distribution engine (Layer 3)
*Weeks 10–16, overlaps Stage 6 · "The map is the megaphone." The free, viral layer that makes millions see it (STRATEGY §Layer 3).*

### CP-7.1 — Free public-repo analysis + shareable links
**Deliverable:** paste any public GitHub URL → free interactive map; "Share this view" produces a public, screenshot-worthy link to that exact ViewSpec.

**Gate:** a shared link renders the same beautiful frame for a logged-out visitor.

**Why:** "The whoa screenshot is the ad" (STRATEGY §Layer 3). Every share reinforces the loop.

### CP-7.2 — CodeLens Score + methodology
**Deliverable:** a letter-grade for structural health/risk of any public repo, backed by the map and a public methodology page, refreshed on every analysis.

**Gate:** the Score is defensible — the methodology page explains exactly how the grade is computed from graph facts.

**Why:** The endgame is *becoming the standard* — "when people cite a CodeLens Score the way they cite test coverage" (STRATEGY §Layer 3).

### CP-7.3 — README badge
**Deliverable:** an embeddable `CodeLens: A−` badge maintainers add to their README.

**Gate:** a maintainer embeds a badge; it renders live and links back to the map.

**Why:** "Every badge is a permanent free advertisement placed by the users themselves" (STRATEGY §Layer 3).

### CP-7.4 — Editorial engine
**Deliverable:** a repeatable "we mapped [famous OSS repo]: here's its architecture and its most dangerous module" post format (SEO asset + demo + badge-adoption pitch to that repo's maintainers).

**Gate — ★ DISTRIBUTION GATE:** the **first inbound signup traced to a shared map or badge** — organic distribution proven, not bought.

**Why:** ⚖ Distribution is the stated failure mode; the badge loop + editorial engine are the counterweight, "but only if publishing is treated as half the job, every week" (STRATEGY §9).

---
---

# STAGE 8 — The credibility engine (Layer 2 · Phase-Trust)
*Weeks 14–20 · The invention nobody has built. The moat starts accumulating the day the first real repo's predictions get graded (STRATEGY §Layer 2, ROADMAP Phase 2).*

### CP-8.1 — GitHub App: predictions per PR
**Deliverable:** a GitHub App that reads PRs and **records a blast-radius prediction per merge** — "changing `auth.py` affects these 14 modules; 2 critical." The CP-1.4 checkpoint pipeline becomes event handlers here with near-zero rewrite (ARCHITECTURE §"Two corrections").

**Gate:** a merged PR produces a stored, timestamped, falsifiable prediction record.

**Why:** "The moat is a clock, and it starts when the first real repo flows through" (ROADMAP §"The moat, precisely").

### CP-8.2 — Outcome watcher
**Deliverable:** watches outcome signals within N days of merge — CI failures, reverts, hotfix commits touching predicted files — using **conservative, defensible signals only** (STRATEGY §9: "start with CI fails + hotfixes touching predicted files; widen carefully").

**Gate:** an outcome is correctly attributed (or correctly declined as too fuzzy) to a prior prediction.

**Why:** ⚖ "Outcome attribution is fuzzy… overclaiming here poisons the well" (STRATEGY §9). Conservatism *is* the credibility.

### CP-8.3 — The Accuracy Ledger
**Deliverable:** a private accuracy dashboard first → the **public** ledger the moment the numbers are honest and stable. Misses included, visible, analyzed (STRATEGY §Layer 2).

**Gate — ★★ MOAT-START GATE:** publish the first honest accuracy number: *"We tracked our own predictions for a month. Here's our error rate — has your AI tool published theirs?"* ⚖ **The Ledger never lies — the first hidden miss kills the entire premise** (STRATEGY §Layer 2 non-negotiable rule, ROADMAP Constitution 7).

**Why:** This is the time-locked moat money can't buy — "a competitor cloning every feature on day 1 still starts with an empty ledger" (STRATEGY §Layer 2). It's also the behavioral north star's trigger: *"Before we merge this…" / "Let's check CodeLens"* (ROADMAP §"The ambition").

---
---

# STAGE 9 — Scale & platform
*Post-traction, 6–18 months · The full v2 spec, now justified by paying users (STRATEGY §7 Phase 4). Direction, not commitment — these seats were reserved in the schema from day one.*

- **CP-9.1 — JS/TS parser to parity.** A second emitter into the *same* schema (never a schema change). *Gate:* golden fixtures for a JS/TS repo pass at the same bar as Python.
- **CP-9.2 — Storage graduation.** Introduce Neo4j / Qdrant / Redis **only when load justifies each one, individually** — migrated mechanically behind the `GraphStore` interface (ARCHITECTURE §3, §"Two corrections"). *Gate:* a backend swap passes the same query tests unchanged.
- **CP-9.3 — Architect tier.** Coupling, debt ranking, service boundaries, CodeLens Score at team scale, continuous monitoring — the remaining ~90 questions as Layers C/D/E mature (FOUNDATION §Phase 4). *Gate:* each new question category moves from "unanswerable" to "answered with evidence" (ROADMAP §benchmark discipline).
- **CP-9.4 — Team & integrations.** Shared workspaces, team ledger, Jira/Linear, more languages. *Gate:* a team adopts it as shared infrastructure.
- **CP-9.5 — Fundraise-or-bootstrap.** The Ledger + traction *is* the deck — "a data asset with a SaaS attached" (STRATEGY §4). *Gate:* the decision is made from a position of proven demand and a public accuracy number, not a pitch.

---
---

## The behavioral scorecard (how you know it's actually working)

Grade the company on **behaviors, not features** (ROADMAP §"How to hold this"). Each maps to a gate above:

| Behavior observed | Proven at | Meaning |
|---|---|---|
| A developer leans in: *"wait, what is this?"* | CP-4.4 | The experience is the megaphone |
| A developer returns to their own repo *unprompted* | **CP-5.1** | **Phase 1 exit — the only thing that matters now** |
| A stranger pays with zero hand-holding | CP-6.4 | It's a business, not a demo |
| An inbound signup from a shared map/badge | CP-7.4 | Distribution compounds on its own |
| A team cites the accuracy number to trust an answer | CP-8.3 | The moat is live |
| *"Before we merge this…" → "Let's check CodeLens."* | Stage 8+ | It has become infrastructure (ROADMAP §north star) |

---

## The one-paragraph version

Fix the scaffolding to match your own architecture (Stage 0). Build the deterministic knowledge graph and prove it's correct with zero AI (Stage 1). Ship the graph's answers — blast radius first — as pure graph math (Stage 2). Wrap them in AI that explains facts and never invents them (Stage 3). Make it cinematic enough that a developer leans in (Stage 4). Put it in front of real users and **stop unless they pull** (Stage 5). Only then: productionize, distribute through free maps and badges, and start the Accuracy Ledger clock that no competitor can buy (Stages 6–8). Scale the platform once paying users justify each database you add (Stage 9). Build the platform-shaped MVP, not the platform — the abstractions are free; the distributed system is not.
