# CodeLens Foundation — The Graph, The Questions, The Minimum Build

*July 19, 2026. This is the product-architecture layer. STRATEGY.md covers market, moat, and go-to-market; this document defines what CodeLens fundamentally IS and answers the three founding questions: (1) what the knowledge graph stores, (2) the first 100 questions a repository should answer, (3) the minimum graph that answers the first 10 correctly.*

---

## North Star

**CodeLens is a Software Understanding Engine.** It reconstructs, automatically and continuously, the mental model that senior engineers spend months building: where things start, how flows connect, what's dangerous, what depends on what. Software stops being text you read and becomes a knowledge graph you explore, question, and safely evolve.

The Google Maps principle governs everything: **build ONE map; everything else becomes a query.** Maps didn't ship "fastest route" as one product and "avoid tolls" as another — one substrate, infinite questions. CodeLens builds one graph per repository; understanding, navigation, blast radius, and architecture health are all *reads* on it.

**How this reconciles with STRATEGY.md** (they don't conflict — they stack):

- The **graph** is the platform (this document).
- **Understanding + navigation** is the free, viral layer (Strategy §Layer 3 — maps, scores, badges).
- **Change intelligence** (blast radius) is the monetization layer (Strategy §Layer 1) — commercially first among equals because it's the moment of maximum pain and willingness to pay, but architecturally just another traversal.
- The **Accuracy Ledger** (Strategy §Layer 2) is how the graph proves itself — it survives unchanged, and matters more now: a "Software Understanding Engine" that publicly grades its own understanding is the credibility mechanism.

One discipline carried over: *the vision answers hundreds of questions; the launch answers ten, excellently.* The 100-question list below is the map of the territory, not the launch checklist.

---

# Question 1 — What the Knowledge Graph Stores

## Design principles (these matter more than the schema)

1. **Facts and interpretations never mix.** The graph has two strata: **deterministic facts** (from AST, git, config — provable, versioned) and **derived annotations** (AI summaries, risk scores, embeddings — always carrying pointers to the facts they're derived from). Any answer CodeLens gives must be traceable to fact-stratum evidence. This is the determinism moat expressed as a data model.
2. **Query-first schema.** Nothing enters the graph unless a question in §2 needs it. Schemas grown "because we might need it" become swamps.
3. **Incremental by content hash.** Every node keys to a SHA-256 of its source. Unchanged file → untouched subgraph. This makes re-analysis cheap and continuous monitoring feasible.
4. **Language-agnostic core.** Parsers (tree-sitter per language) emit into one universal schema. Adding a language never changes the graph model.

## The five layers

### Layer A — Structural core (from AST — deterministic)

**Nodes** (each with: `id`, `name`, `qualified_name`, `file_path`, `start_line`, `end_line`, `language`, `content_hash`):

| Node | Notes |
|---|---|
| `Repository` | root; metadata: primary language, size, default branch |
| `Package/Module` | directory- or module-level grouping; the unit humans reason about |
| `File` | LOC, language |
| `Class` | methods, attributes, docstring |
| `Function/Method` | signature, params, return type (where inferrable), cyclomatic complexity, docstring, `is_entrypoint` flag |
| `Endpoint` | HTTP route / CLI command / event handler — how the outside world enters the code |

**Edges:**

| Edge | Meaning |
|---|---|
| `CONTAINS` | Repo→Package→File→Class→Function hierarchy |
| `IMPORTS` | file/module-level dependency |
| `CALLS` | function→function (static call resolution; confidence flag for dynamic dispatch) |
| `INHERITS` / `IMPLEMENTS` | class hierarchies |
| `INSTANTIATES` | who creates instances of what |
| `ROUTES_TO` | Endpoint→handler function (framework-aware: FastAPI/Flask/Express/Next) |
| `TESTS` | test function→tested code (by import + naming convention) |

### Layer B — Interface & external layer (from config — deterministic)

Nodes: `ExternalDependency` (npm/pip package, version), `ExternalService` (database, cache, queue, API — inferred from client-library imports and connection strings), `ConfigValue` (env vars, settings keys).
Edges: `DEPENDS_ON` (code→package), `TALKS_TO` (module→external service), `READS_CONFIG` (function→config key).
This layer answers "what does this system touch beyond itself" — where most production surprises live.

### Layer C — Temporal layer (from git history — deterministic)

Per-file/function metadata: `churn` (change frequency), `last_modified`, `age`, `author_count`; `AUTHORED_BY` edges to `Author` nodes (→ ownership, bus factor).
`CO_CHANGES` edges: files that repeatedly change in the same commits **without** a structural edge between them — hidden coupling that no static analysis sees. This is CodeScene's core insight, absorbed as one edge type among many.

### Layer D — Semantic layer (derived — annotations)

Per Function/Class/Module: an **AI-generated plain-English summary** (one sentence, regenerated only when content hash changes) and an **embedding** of code+docstring+summary for concept search ("where is rate limiting?" when no file is named `rate_limit`). Summaries carry `derived_from: [node ids]` — always traceable.

### Layer E — Outcome layer (from CI/PR observation — deterministic, Phase 3+)

Prediction records (blast-radius claims per PR), outcome signals (CI failures, reverts, hotfixes touching predicted nodes). This is the Accuracy Ledger's substrate. Designed now, populated later.

**Explicitly NOT stored (v1):** runtime traces/APM data, full data-flow (variable-level READS/WRITES), issue/PR text ingestion, cross-repository edges. All are future layers the schema can accept; none are needed for the first 10 questions.

---

# Question 2 — The First 100 Questions

Each question is tagged with the capability it requires — this tagging is what actually drives the architecture:
**[S]** structural graph traversal · **[G]** git/temporal layer · **[E]** embedding/concept search · **[A]** AI synthesis over graph context · **[X]** external/config layer

The pattern to notice: **almost every question is [S] at its core, with [A] as the presentation layer.** The graph answers; AI explains the answer. That division is the product.

## Beginner — "First day" (1–25)

| # | Question | Needs |
|---|---|---|
| 1 | What does this project do? | S+A |
| 2 | What are the main modules, and what does each do? | S+A |
| 3 | Where does execution start? | S |
| 4 | What frameworks and technologies does it use? | X |
| 5 | What architecture pattern is this (MVC, layered, hexagonal…)? | S+A |
| 6 | Where should I start reading? (learning path) | S+G |
| 7 | What are the most important files/modules? | S |
| 8 | Explain module X in plain English | S+A |
| 9 | What external services does this talk to? | X |
| 10 | What config/env vars does this need to run? | X |
| 11 | What's the data model? | S+X |
| 12 | Show me the authentication flow | S+E+A |
| 13 | Show me the [payment/signup/…] flow | S+E+A |
| 14 | What does this specific function do? | S+A |
| 15 | What happens when a request hits endpoint /X? | S+A |
| 16 | Which parts can I safely ignore at first? | S+G |
| 17 | Who are the main contributors, and who knows what? | G |
| 18 | How are tests organized? | S |
| 19 | How do the frontend and backend connect? | S+X |
| 20 | Which module handles errors/logging? | S+E |
| 21 | What does term X mean in this codebase? (glossary) | E+A |
| 22 | What are the coding conventions here? | S+A |
| 23 | Explain this repo to me as a frontend engineer | S+A |
| 24 | Explain this repo to me as a backend engineer | S+A |
| 25 | Give me a 10-minute tour | S+G+A |

## Contributor — "I have a task" (26–50)

| # | Question | Needs |
|---|---|---|
| 26 | Where should I fix issue #N? | E+S |
| 27 | Where is feature Y implemented? | E+S |
| 28 | Where should I add feature Z? | E+S+A |
| 29 | What calls this function? | S |
| 30 | What does this function call? | S |
| 31 | Where is this class instantiated? | S |
| 32 | Where is this config value used? | X+S |
| 33 | Is there an existing utility for X? (avoid duplication) | E |
| 34 | What tests cover this file? | S |
| 35 | What do I need to mock to test X? | S+X |
| 36 | Which files change together with this one? | G |
| 37 | Who should review my change? | G |
| 38 | How were similar features built here? | E+S+A |
| 39 | Where is endpoint /X defined? | S |
| 40 | How does data flow from A to B? | S+A |
| 41 | Is this code path dead or reachable? | S |
| 42 | What errors can this raise, and where are they handled? | S |
| 43 | Where is the schema/migration for table X? | X+E |
| 44 | Which modules should I avoid touching? | S+G |
| 45 | What changed recently in this area? | G |
| 46 | What's the minimal set of files for this change? | S+E |
| 47 | Does my change follow the existing patterns? | S+A |
| 48 | What's the naming convention for what I'm adding? | S+A |
| 49 | Which similar PRs were reverted, and why? | G |
| 50 | What should my PR description mention? | S+A |

## Maintainer — "Keep it safe" (51–75)

| # | Question | Needs |
|---|---|---|
| 51 | **What breaks if I change X?** (blast radius) | S |
| 52 | What's the blast radius of this entire PR? | S |
| 53 | Who depends on this public API? | S |
| 54 | Can I delete this function/module? | S+G |
| 55 | Is it safe to rename X? | S |
| 56 | What's the risk score of this change? | S+G |
| 57 | Which paths connect this change to critical modules? | S |
| 58 | Does this PR introduce circular dependencies? | S |
| 59 | Does this PR increase coupling? | S |
| 60 | What tests should run for this change? | S |
| 61 | Which high-risk modules lack test coverage? | S+G |
| 62 | What changed architecturally between two versions? | S+G |
| 63 | Which files are hotspots (high churn × complexity)? | S+G |
| 64 | Where is hidden coupling (co-change without imports)? | G+S |
| 65 | What's the bus factor of this area? | G |
| 66 | What's the impact of upgrading dependency X? | X+S |
| 67 | Which deprecated code is still called? | S |
| 68 | What's the safe order of steps for this refactor? | S+A |
| 69 | Which recent merges were riskiest? | S+G |
| 70 | Where does AI-generated code touch critical paths? | G+S |
| 71 | Where are secrets/config referenced in code? | X |
| 72 | Which endpoints changed behavior recently? | S+G |
| 73 | Was our last blast-radius prediction correct? (ledger) | E-layer |
| 74 | Which predictions did we miss, and why? | E-layer |
| 75 | What's our rolling prediction accuracy? | E-layer |

## Architect / Lead — "Evolve it" (76–100)

| # | Question | Needs |
|---|---|---|
| 76 | Which module has the highest coupling? | S |
| 77 | Where are the bottlenecks (high fan-in × complexity)? | S |
| 78 | Where are the circular dependencies? | S |
| 79 | What code is dead/unreachable? | S |
| 80 | Which dependencies are unused or outdated? | X |
| 81 | Where is the intended layering violated? | S+A |
| 82 | Rank our technical debt | S+G |
| 83 | What should we refactor first (impact vs effort)? | S+G+A |
| 84 | Where are the god classes/modules? | S |
| 85 | What are natural service boundaries if we split this? | S+G+A |
| 86 | Which modules are least stable over time? | G |
| 87 | Where is knowledge dangerously concentrated? | G |
| 88 | How has complexity trended over 6 months? | G |
| 89 | Which modules slow development the most? | S+G |
| 90 | What dies if we deprecate feature F? | S+E |
| 91 | What's the blast radius of replacing dependency X? | X+S |
| 92 | How healthy is this repo vs comparable repos? (Score) | S+G |
| 93 | What would splitting module X look like? | S+A |
| 94 | Where should new engineers be staffed? | G+S |
| 95 | What's our AI-generated-code footprint, and where? | G |
| 96 | Which areas have no owner? | G |
| 97 | Which tests are structurally brittle? | S |
| 98 | What's a realistic 6-month refactoring roadmap? | S+G+A |
| 99 | What scalability limits does the structure imply? | S+A |
| 100 | If we rebuilt this today, what would we keep? | S+G+A |

**What the tags reveal:** ~85 of 100 questions need only **[S] + [G]** at their core — deterministic layers. **[A]** appears almost exclusively as the explanation layer on top of a graph answer. **[E]** (embeddings) is needed for concept→location mapping, not for reasoning. The architecture conclusion writes itself: *invest in the graph; keep AI at the edges.* Exactly the Google Maps split — the map answers, the interface explains.

---

# Question 3 — The Minimum Graph for the First 10

## The 10 launch questions

Chosen to cover all four personas' entry moments, maximize wedge + wow, and share one substrate:

1. **What does this project do?** (#1)
2. **What are the main modules, and what does each do?** (#2)
3. **Where does execution start?** (#3)
4. **What are the most important modules?** (#7)
5. **Where should I start reading?** — generated learning path (#6)
6. **Explain module/function X in plain English** (#8/#14)
7. **Where is feature/concept Y implemented?** (#27)
8. **What breaks if I change X?** — blast radius with paths (#51)
9. **What does X depend on?** (#30, transitive)
10. **Which code is riskiest to touch?** — risk heatmap (#63-lite)

Q1–6 are the **free, viral layer** (paste a URL, understand a repo). Q7 is **navigation**. Q8–10 are **change intelligence** — the paid wedge. One graph serves all ten; the phase disagreement between "understanding first" and "blast radius first" dissolves, because *they're the same build*.

## The minimum schema

**Nodes (5):** `Repository`, `Module`, `File`, `Class`, `Function` — with `name`, `qualified_name`, `path`, `start/end_line`, `language`, `content_hash`, `loc`, `complexity` (functions), `docstring`, `is_entrypoint`.

**Edges (4):** `CONTAINS`, `IMPORTS`, `CALLS`, `INHERITS`.

**Git-lite (one `git log --numstat` pass):** per-file `churn_count`, `author_count`, `last_modified`. No CO_CHANGES matrix yet, no Author nodes.

**Semantic-lite:** one AI summary per module and per public function (cached by content hash); embeddings over name+docstring+summary for Q7.

**Entrypoint detection:** `main` guards, framework route decorators (FastAPI/Flask/Express), CLI registrations, `package.json` scripts. Cheap heuristics cover 90% of real repos.

**Languages:** Python + JavaScript/TypeScript. **Storage:** SQLite (nodes/edges/metadata) + networkx in-memory for traversals + a small vector index (sqlite-vss or FAISS). **Defer:** Neo4j, Celery, Redis, Chroma — until real load exists. The schema is Neo4j-shaped, so migration later is mechanical.

## How each question resolves (the proof the schema is sufficient)

| Q | Resolution |
|---|---|
| 1 | README + entrypoints + top-level module names → one [A] synthesis call with graph context |
| 2 | Aggregate IMPORTS/CALLS to module level → summary per module [A, cached] |
| 3 | `is_entrypoint` nodes, grouped by type (server/CLI/jobs) |
| 4 | Fan-in + PageRank over IMPORTS/CALLS — pure graph math |
| 5 | Order: entrypoints → high-centrality modules → leaves; annotate each stop with its summary |
| 6 | Node's subgraph (its calls, callers, imports) → [A] explanation with evidence pointers |
| 7 | Embedding search over summaries/names → return *nodes on the graph*, not text snippets |
| 8 | Reverse transitive closure over CALLS+IMPORTS from X, ranked by distance & fan-in, **paths included** |
| 9 | Forward transitive closure, depth-limited |
| 10 | `risk = normalize(complexity × fan_in × churn)` per module → heatmap coloring on the map |

Eight of ten are pure graph operations. Two use AI — for *explanation over graph-selected context*, never for facts. Every answer can cite its nodes. That property — **every claim traceable to the graph** — is the credibility foundation the Accuracy Ledger later makes public, and it's enforceable from day one.

## What this explicitly excludes (so scope holds)

Auth/billing/multi-tenancy · queues & websockets · Neo4j/Chroma/Redis · data-flow analysis · runtime signals · issue/PR ingestion · Java/Go/Rust · co-change matrix · the chat UI (the 10 questions can launch as buttons/commands before free-form chat exists — free-form is an interface upgrade, not a capability change).

---

## The revised phase ladder (vision-aligned, strategy-compatible)

- **Phase 1 — Repository Understanding** (Q1–6): paste URL → graph → architecture map → explanations → learning path. *Free. The demo. The viral loop.*
- **Phase 2 — Navigation + Change Intelligence** (Q7–10): concept search, blast radius, risk heatmap. *The paid wedge arrives — one sprint after Phase 1, because it's the same graph.*
- **Phase 3 — The Ledger**: GitHub App, predictions per PR, outcome tracking, public accuracy. *The moat starts accumulating.*
- **Phase 4 — Engineering Intelligence** (the architect tier): coupling, debt ranking, boundaries, Score, continuous monitoring. *The platform. The remaining 90 questions, unlocked by layers C/D/E maturing.*

Five-year direction unchanged and now better-grounded: developers won't say "open CodeLens" — they'll **ask the repository.** The graph is what makes the answers true; the ledger is what makes them trusted; the map is what makes them spread.

---

## Immediate next steps

1. Freeze this schema as `graph/schema.py` (Pydantic models for nodes/edges) — the contract everything builds against.
2. Build the Python parser → SQLite pipeline for Layer A on the CodeLens repo itself (dogfood from commit one).
3. Ship Q8 (blast radius) and Q4 (importance ranking) as the first two working queries — they validate graph correctness *deterministically*, before any AI is wired in.
4. Then Q1/Q2/Q6 summaries, then the map view, then the remaining ten.
