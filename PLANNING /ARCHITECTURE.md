# CodeLens Architecture — The Engineering Constitution

*July 19, 2026. The fourth founding document. STRATEGY = why. FOUNDATION = the graph & questions. EXPERIENCE = how it feels. This = how it's engineered. When implementation questions arise, this document decides.*

---

## Prime directive

**The graph is the center. AI is at the edge.**

```
Repository → Parser → KNOWLEDGE GRAPH → Query Engine → Visualization / AI Explanation
```

CodeLens is not built around features (blast radius, maps, chat). It's built around a **Software Knowledge Graph Platform** — the way Google was built around the index and GitHub around the repository. Every feature is a query against the same substrate. If a proposed feature can't be expressed as a graph query + a presentation, it doesn't belong yet.

Corollaries:

1. **Layers below the semantic layer know nothing about AI.** The ingestion, parser, and graph layers produce and store pure facts. They must be fully functional and testable with zero LLM calls.
2. **AI never invents facts — it explains graph facts.** The LLM receives graph-selected context and produces narration with evidence pointers. (FOUNDATION's fact/annotation strata, enforced at the module boundary.)
3. **Determinism first in build order too.** Milestone 1 has no AI in it at all. If the graph is wrong, everything downstream is beautifully wrong.

---

## The six systems

```
        ┌──────────── CodeLens ────────────┐
        │                                  │
   1. Ingestion → 2. Parser → 3. Knowledge Graph
                                    │
              ┌─────────────────────┼──────────────────┐
        4. Semantic Layer     5. Query Engine    6. Visualization Engine
              │                     │                  │
              └───────────── Frontend (renderer) ──────┘
```

### 1. Ingestion
GitHub URL / zip → clone (GitPython, shallow, size-capped, timeout) → language detection (extension census) → file inventory with content hashes. Knows nothing about AI, nothing about graphs. Output: a normalized `RepoSnapshot`.

### 2. Parser Engine — the hardest system, named honestly
tree-sitter per language → emits **facts only** into the universal schema: functions, classes, imports, calls, inheritance, decorators. No explanations, no scores.

The genuinely hard problem is **call resolution** — mapping `authenticate()` at a call site to the right definition across modules, aliases, re-exports, and dynamic dispatch. Design decisions:

- **Every CALLS edge carries a `confidence` field** (`resolved` / `heuristic` / `dynamic-unknown`). The graph tells the truth about its own certainty; the ripple UI can render uncertain edges differently. This is the honest-theater rule applied to data.
- **Golden tests from day one:** a `fixtures/` directory of small repos with hand-verified expected graphs. Parser changes run against them in CI. The parser's correctness is CodeLens's correctness.
- Languages: Python, then JS/TS. Each new language is a new emitter into the *same* schema — the schema never changes per language.

### 3. Knowledge Graph — the most important store
The five-layer model from FOUNDATION (structural / external / temporal / semantic / outcome).

**MVP storage: SQLite (persistence) + NetworkX (traversal).** Neo4j when real load exists — the schema is property-graph-shaped, so migration is mechanical. Don't optimize early; also don't paint into a corner: all graph access goes through a `GraphStore` interface so the backend can swap without touching queries.

### 4. Semantic Layer — where AI enters, and not before
Summaries per module/function (cached by content hash — an unchanged file never pays for a second LLM call), embeddings for concept search, narration for query results. Input is always a graph-selected subgraph, never "the repository." Output always carries `derived_from: [node_ids]`.

### 5. Query Engine — where CodeLens becomes CodeLens
Every user question resolves to a **named query plan** in a registry:

```
"What breaks if I delete X?"  → BlastRadius(node)      → reverse transitive closure → ranked + paths
"Show the auth flow"          → FlowTrace(concept)     → embedding→nodes → path search → ordered chain
"Where should I fix #21?"     → IssueLocate(issue_text)→ embedding search → graph neighborhood → candidates
"Most important modules?"     → Centrality()           → PageRank/fan-in over IMPORTS+CALLS
```

Uniform pipeline: `Question → QueryPlan → Graph Traversal → ResultGraph → (Visualization spec + AI narration)`. The registry starts with the 10 launch questions from FOUNDATION §3 and grows toward the 100. Free-form chat later is a *router* onto this registry — the LLM picks and parameterizes plans; it never freelances answers.

### 6. Visualization Engine — a system, not a component
React Flow / WebGL is only the *renderer*. The visualization engine is server-side logic that compiles a ResultGraph into a **ViewSpec**: which nodes exist at this zoom level, cluster membership, layout positions (precomputed), colors (risk/activity), and animation choreography (assembly order, ripple propagation, path pulses). EXPERIENCE.md's semantic zoom and level-of-detail live here. The frontend renders ViewSpecs; it never computes truth.

---

## Two corrections to the plan (accepted with amendments)

These keep the architecture's shape while saving months:

**"Each folder is its own service" → modular monolith now, services later.** The DDD folder boundaries are correct and non-negotiable — modules communicate through interfaces, never reach into each other's internals. But they deploy as ONE FastAPI process until scale demands otherwise. Distributed systems for zero users is the classic solo-founder death. The boundaries make the future split mechanical; drawing them now costs nothing.

**"Everything event-driven" → pipeline with checkpoints now, events later.** The MVP pipeline is a sequential, resumable set of stages, each writing a checkpoint (`cloned → parsed → graph_built → metrics → embeddings → summaries → ready`). Same stages the EXPERIENCE progress UI reports — telemetry and theater from one source. Each stage is idempotent and hash-keyed, so re-runs skip unchanged work. When GitHub webhooks arrive (Milestone 5), stages become event handlers with almost no rewrite — the checkpoint model *is* the event model, minus the broker.

Same logic already applied in FOUNDATION: **SQLite+NetworkX now; Postgres/Neo4j/Qdrant/Redis when there are users to justify each one.** The multi-store end-state (each database one job) is right — as the destination, not the starting point.

---

## Repository layout (maps onto the existing backend skeleton)

```
backend/app/
├── ingestion/      # clone, language detect, snapshot          (new)
├── parser/         # tree-sitter emitters, golden fixtures     (exists, empty)
├── graph/          # schema.py, GraphStore, traversals         (exists, empty)
├── semantic/       # summaries, embeddings, narration          (rename ai/)
├── queries/        # QueryPlan registry — the 10, then the 100 (new)
├── views/          # visualization engine: ViewSpec compiler   (new)
├── risk/           # metrics: complexity, churn, risk formula  (exists, empty)
├── api/            # FastAPI routes: thin, no logic            (exists, empty)
└── core/           # config, pipeline runner, checkpoints      (exists)
```

`chat/` folds into `queries/` (chat is a router onto the registry). Frontend later: Next.js + a **Graph State Manager** (the game-engine model — every panel reads one client-side graph state; components never fetch independently).

## AI context assembly (the RAG rule)

Never send the repository. Always: `Question → Graph query → relevant nodes → their code snippets + summaries + (later) commits/README → LLM`. The graph IS the retrieval engine — better than vector-only RAG because structure encodes relevance that similarity can't see. Token budget enforced per query; context lists are ranked by graph distance and fan-in, truncated, never stuffed.

---

## Milestones with acceptance gates

**M1 — The core asset (no AI, no polish):** URL → clone → parse Python → build graph → SQLite → crude visual output (even GraphViz).
*Gate: run on 3 real repos (incl. CodeLens itself); golden-fixture tests pass; spot-check 20 CALLS edges by hand — ≥90% of `resolved` edges correct.*

**M2 — Node intelligence:** click/select node → dependencies, dependents, metadata, first AI story with evidence pointers.
*Gate: every narrated claim clickable to a real node.*

**M3 — Questions:** the 10 launch questions working end-to-end through the QueryPlan registry (buttons, not chat).
*Gate: 8 of 10 answers correct on an unfamiliar repo, judged by someone who knows it.*

**M4 — Change intelligence:** blast radius with ripple, risk heatmap, issue-locate.
*Gate: a real developer uses it on their repo and says "I'd use this again" unprompted. (The kill/continue gate from STRATEGY.)*

**M5 — Alive:** GitHub App, PR analysis, continuous updates, prediction records — the Accuracy Ledger's substrate begins.

Each milestone is demoable. None depends on a database that isn't running yet.

---

## The reminder that survives contact with excitement

Infrastructure over features — but *validated* infrastructure. Google's index mattered because searches hit it. The platform bet only pays if Milestones 1–4 prove people want the queries. Build the platform-shaped MVP, not the platform: right abstractions (GraphStore, QueryPlan registry, ViewSpec, checkpointed pipeline), minimal deployments (one process, one SQLite file). The abstractions are free; the distributed system is not.
