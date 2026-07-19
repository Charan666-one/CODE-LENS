# CodeLens Core Architecture — The One Page

*July 19, 2026. The top of the document stack. If a new engineer, investor, or future-you reads only one page, it's this one. Details live in STRATEGY, FOUNDATION, EXPERIENCE, ARCHITECTURE — this page is the idea they all serve.*

---

## The central idea

**CodeLens is a Software Knowledge Graph platform. Every feature is an application running on the graph.**

The graph is the operating system. Blast Radius is not the product — it's the first application. Microsoft didn't build Paint; it built Windows, and Paint ran on it.

```
                    Repository
                        │
              Repository Engine        (read: clone, snapshot, hash)
                        │
                Parser Engine          (understand: code → facts)
                        │
        ╔═══════════════════════════════╗
        ║   SOFTWARE KNOWLEDGE GRAPH    ║   (store: the one asset)
        ╚═══════════════════════════════╝
                        │
               Reasoning Engine        (answer: plan → query → evidence → explain)
                        │
      ┌─────────┬───────┼────────┬──────────┐
   Blast     Architecture   AI      Onboarding   Issue        ← Applications
   Radius       Maps       Chat     & Learning   Navigator      (all just queries)
      │
        Accuracy Ledger                (learn: prediction vs reality)
```

---

## 1. What does the Knowledge Graph store?

Five layers — two strata. **Facts** (deterministic, provable) never mix with **annotations** (AI-derived, always carrying evidence pointers to facts).

| Layer | Contents | Source | When |
|---|---|---|---|
| A — Structural | modules, files, classes, functions, endpoints; CONTAINS / IMPORTS / CALLS / INHERITS / ROUTES_TO / TESTS | AST (tree-sitter) | MVP |
| B — External | dependencies, external services, config values | package files, config | MVP-lite |
| C — Temporal | churn, ownership, co-change coupling, authors | git history | Phase 2 |
| D — Semantic | plain-English summaries, embeddings | LLM + embedding model (annotations) | MVP-lite |
| E — Memory & Outcome | PRs, issues, docs, commits linked to code nodes ("why does this exist?"); prediction→outcome records | GitHub App, CI signals | Phase 3+ |

Layer E is what upgrades a *code* graph into a true *software* knowledge graph — the repository's memory. It's roadmap, not MVP, but the schema reserves its place from day one.

## 2. How does information get in?

One pipeline, every source, always into the same graph:

```
source → extractor → facts (schema.py) → graph → [annotator → annotations]
```

- **Code** → parser engine (per-language emitters, one universal schema, confidence-tagged edges)
- **Git** → history extractor (churn, owners, co-change)
- **PRs / Issues / Docs** → GitHub App extractors, linked to the code nodes they touch (Phase 3+)
- **Reality** → CI/merge outcomes attached to prior predictions (the Ledger's food)

Everything is incremental by content hash: unchanged source → untouched subgraph → zero cost.

## 3. How do questions get answered?

Never `question → LLM`. Always:

```
Question → Planner → Graph queries → Evidence subgraph → LLM narration → Answer
              │                                              │
     (decides what information                    (explains facts; cites nodes;
      is needed — no AI yet)                       invents nothing)
```

The Planner is the QueryPlan registry: every supported question is a named, tested graph query (BlastRadius, FlowTrace, Centrality, IssueLocate…). Free-form chat is a router that picks and parameterizes plans — the LLM chooses *which query to run*, never *what the facts are*. Every answer is traceable to graph nodes; every visual is a real subgraph, choreographed.

## 4. Which features are just queries on the same graph?

All of them. That's the test for whether a feature belongs.

| Application | The query underneath |
|---|---|
| Blast Radius | reverse transitive closure from a node, ranked, with paths |
| Architecture Map | CONTAINS hierarchy + aggregated edges per zoom level |
| Risk Heatmap | complexity × fan-in × churn per module |
| AI Chat | planner-routed queries + narration |
| Onboarding / Learning paths | entrypoints + centrality ordering + summaries |
| Issue Navigator | embedding search → graph neighborhood → candidates + owners |
| Living Documentation | summaries + flows, regenerated when hashes change |
| Security overlay | pattern queries + external-dependency checks |
| Version diff | graph snapshot comparison across commits |
| CodeLens Score | health metrics aggregated over the whole graph |
| Accuracy Ledger | predictions (stored queries) joined with observed outcomes |

**New feature rule:** if it can't be expressed as *a query on the graph plus a presentation*, it either needs a new graph layer (a roadmap decision) or it doesn't belong.

---

## The sentence

> "We built a Software Knowledge Graph. Blast Radius is the first application powered by it — and the Accuracy Ledger publicly proves the graph deserves your trust."
