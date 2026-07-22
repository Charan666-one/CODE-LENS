# CodeLens — The Learning Companion

*What we built at every checkpoint, why we built it that way, and the ideas underneath — written to teach, not just to record. Read it next to the code: every section names the files it explains.*

The plan we executed is [PLANNING/CHECKPOINTS.md](PLANNING%20/CHECKPOINTS.md). This document is its mirror: **checkpoints → understanding**.

---

## The one mental model to hold

Everything in CodeLens flows through a single idea:

```
Repository → Parser → KNOWLEDGE GRAPH → Queries → AI explanation
             (facts)   (the one asset)   (answers)  (stories about answers)
```

**The graph answers; the AI explains the answer.** Every checkpoint below either builds the graph, proves the graph, queries the graph, or narrates what a query returned. Nothing else exists. When you're lost in the code, ask "which of those four is this file doing?" — the answer places it instantly.

---

# Stage 0 — Foundation realignment

## CP-0.1 · The scaffolding matches the architecture
**Files:** `backend/app/core/config.py`, `backend/requirements.txt`, `backend/pyproject.toml`, `.github/workflows/ci.yml`

**What we did.** Deleted the Postgres/Redis/Neo4j config that had crept in, trimmed 156 pinned packages to ~52, added the missing `networkx`, made `backend/app/graph/schema.py` the single copy of the frozen schema, and wired ruff + mypy + pytest into CI.

**Why it matters.** The repo had drifted from its own founding documents before a line of product code existed. The lesson generalizes: **infrastructure you stand up before you need it doesn't just cost setup time — it shapes every decision after it.** Config that mentions Neo4j makes people write Neo4j-shaped code. The MVP decision (SQLite + NetworkX, one process) wasn't laziness; it was the discipline of matching deployment weight to user count (zero).

**Concepts to know.**
- *Modular monolith*: strict module boundaries (`ingestion/`, `parser/`, `graph/`…) deployed as one process. Boundaries are free; distributed systems are not.
- *Lint/type/test as a gate, not a suggestion*: CI runs all three on every push; "done" means green.

## CP-0.2 · Golden fixtures — tests that outrank the code
**Files:** `backend/fixtures/README.md`, `backend/fixtures/tiny_python/`, `backend/tests/test_golden_fixtures.py`

**What we did.** Hand-wrote a tiny repository (`calculator.py`, `shapes.py`, `main.py`) and — by reading it line by line — wrote down the exact graph a correct parser must produce: every node, every edge, every line number, as JSON. Then a harness that (a) checks the JSON's own internal consistency and (b) compares the parser's output against it.

**Why it matters.** The parser didn't exist yet, and that's the point: **the expected answer was fixed before the code that must produce it.** This is test-first at its most honest — the fixture can't be biased by implementation convenience because there was no implementation. Layer (a) is subtle and worth studying: it verifies the *hand-verification* (does line 14 really say `class Rectangle(Shape):`?), because a golden file with a typo would silently bless a wrong parser forever.

**Concept.** *Golden testing*: comparing output against a stored, human-verified artifact. Used wherever correctness can't be expressed as a formula — compilers, renderers, parsers.

---

# Stage 1 — The core asset (Milestone M1: the graph, zero AI)

## CP-1.1 · Ingestion — repository in, snapshot out
**Files:** `backend/app/ingestion/` (`clone.py`, `archive.py`, `inventory.py`, `languages.py`, `errors.py`, `__init__.py`)

**What we did.** One entry point, `ingest(source)`, that accepts a GitHub URL (shallow clone), a zip, or a local directory, and returns an `IngestedRepo`: the frozen `RepoSnapshot` plus a per-file inventory where **every file carries a SHA-256 of its bytes**.

**The two ideas worth internalizing:**

1. **URL validation is a security boundary.** CodeLens clones URLs typed by strangers. Git URLs are a known attack surface: `ext::sh -c '...'` executes commands, `file:///etc/passwd` reads local files, a path starting with `-` becomes a git option. The fix is an allowlist (https + github.com + `owner/repo` shape) rather than a blocklist — *reject everything you didn't explicitly decide to accept*. Same thinking in `archive.py`: zip entries can contain `../` ("zip slip"), so every path is resolved and checked before extraction.
2. **The content hash is the economic engine.** That SHA-256 per file looks like bookkeeping. It is actually the foundation of everything cheap in CodeLens: skip-if-unchanged pipelines (CP-1.4), pay-once summaries (CP-3.2). *Hash the input once; never redo work for the same hash.*

**Also note:** the errors. Oversized → `RepoTooLargeError`, hung clone → killed at timeout, private repo → fails in <1s because credential prompts are disabled. A library that fails with typed, fast, truthful errors is usable; one that hangs or lies is not.

## CP-1.2 · The parser — source code to facts
**Files:** `backend/app/parser/` (`python_emitter.py`, `facts.py`, `complexity.py`, `__init__.py`)

**What we did.** Used **tree-sitter** (an incremental parsing library; it turns source text into a *concrete syntax tree*) to walk every Python file and emit facts into the schema: files, classes, functions (with line ranges, docstrings, cyclomatic complexity, per-definition content hashes), plus `CONTAINS`, `IMPORTS`, `CALLS`, `INHERITS` edges and entrypoint flags.

**The design decision to study: two passes.** Pass one walks each file *alone* and records raw observations ("this file imports `calculator`", "this call site says `multiply(...)`"). Pass two, once all files are known, resolves observations into edges. Why? Because **a call cannot be resolved from inside the file containing it** — `multiply(...)` in `shapes.py` only means `calculator.multiply` after `calculator.py` has been read. Keeping unresolved observations as first-class values (`RawCall`, `RawImport` in `facts.py`) is what let CP-1.3 add confidence levels without touching the walker. *When a problem has a "local" part and a "global" part, separate them structurally.*

**Concepts to know.**
- *AST/CST walking*: recursive descent over a tree of typed nodes; our `_FileWalker.visit()` dispatches on `node.type`.
- *Qualified names*: `shapes.Rectangle.area` — the path from module to definition. They're the graph's addressing scheme; get them wrong and no edge can find its target.
- *Cyclomatic complexity*: roughly, the number of independent paths through a function (each `if`/`for`/`and` adds one). We take it from `radon`, keyed by definition line — the one identifier both radon and tree-sitter agree on.

**A real bug the fixtures caught:** my resolver returned `None` for bare class names — correct for *calls* (`Rectangle(...)` is instantiation, a different edge type we deliberately don't emit yet), wrong for *inheritance* (`class Child(Base)` — that bare name is exactly the base class). One context flag (`allow_bare_class`) fixed it. The golden fixture failed on precisely the missing INHERITS edge. That's the harness earning its keep on day one.

## CP-1.3 · Call resolution — the hard problem, answered honestly
**Files:** `backend/app/parser/resolution.py`, `backend/fixtures/dynamic_python/`

**What we did.** Rebuilt resolution as a **confidence ladder**. Every CALLS edge now declares how it was found:

| Confidence | Meaning | Example |
|---|---|---|
| `resolved` | Followed real symbols: imports, aliases, re-export chains, the call site's own class and its bases | `self.setup()` found on the enclosing class |
| `heuristic` | Method name matches exactly **one** definition repo-wide — a good bet (duck typing), not a proof | `cache.invalidate_everything()` |
| `dynamic_unknown` | Name matches several; the receiver's type is unknowable statically — the call could reach any of them | `handler.handle()` with two `handle` methods |

**Why this is the product, not a compromise.** Static analysis of a dynamic language *cannot* resolve every call — Python's whole point is that `handler` can be anything. The naive options are both bad: emit nothing (blast radius misses real dependencies) or guess silently (blast radius lies). The ladder is the third option: **over-approximate and say so.** The UI can render dotted edges; the Accuracy Ledger can grade guesses separately. This is Constitution rule 5 ("confidence is visible") turned into a data model.

**The quality lesson — look at your outputs.** After building the ladder, the aggregate numbers looked great (992 call edges on `requests`, up from 144). Sampling actual edges told a different story: `sock.close()` "calling" `Session.close`, `proxies.copy()` "calling" `PreparedRequest.copy`. Generic names (`get`, `set`, `close`, `copy`…) belong to stdlib types far more often than to your classes, so guessing from them manufactures confident nonsense. The fix: guessed tiers require a *distinctive* name (dunders and a stoplist of ~50 generic names are never bet on); proofs are exempt — a statically resolved `self.close()` still counts. Result: `dynamic_unknown` on requests fell 600 → 72 while every `resolved` count stayed byte-identical. **Aggregate metrics hide garbage; read samples.**

**Concepts to know.**
- *MRO (method resolution order)*: where Python looks for `self.method()` — the class, then its bases. Our `_linearise` is a breadth-first approximation of Python's C3 order, deterministic and right for the shapes real code has.
- *Re-exports*: `pkg/__init__.py` doing `from .impl import helper` means `from pkg import helper` names `pkg.helper` but the function lives at `pkg.impl.helper`. `_follow_reexports` walks that chain (bounded, cycle-safe).
- *Precision vs recall*: CP-1.2 was all-precision (5% of call sites became edges, nearly all correct). CP-1.3 traded carefully: more recall in labeled tiers, proofs untouched.

## CP-1.4 · The store and the pipeline — the graph becomes durable
**Files:** `backend/app/graph/store.py`, `backend/app/graph/traversal.py`, `backend/app/core/pipeline.py`

**What we did.** Three seams, each hiding a future migration:

1. **`GraphStore`** (abstract) with **`SQLiteGraphStore`**: persistence behind an interface. Rows hold indexed columns *plus* the full model as JSON — queries filter on columns, the schema grows without migrations. When Neo4j is justified (CP-9.2), it implements the same interface and nothing above changes.
2. **`GraphView`**: the graph loaded into NetworkX for walking — reverse/forward transitive closure, fan-in, CONTAINS walks. *SQLite persists; NetworkX traverses; queries never touch SQL.*
3. **`run_pipeline`**: `cloned → parsed → metrics → graph_built`, each stage timed and reported to a callback. **The skip logic** is the part to study: the commit sha *finds* a stored candidate, but a **content digest** (hash over all Python files' hashes) *decides* — because a working tree can be dirty (HEAD unchanged, files edited) or outside git entirely. Content is the truth; the sha is just the lookup key.

**One deliberate detail:** the `summary_cache` table sits *outside* the snapshot's delete-cascade. A summary belongs to a content hash, not to an analysis run — deleting a snapshot must not throw away paid-for LLM output that's still valid for unchanged code. Schema design encoding an economic rule.

**Concept.** *Honest theater* (EXPERIENCE.md): the progress callback the future UI will render is the same event stream the pipeline itself times. The "Understanding…" animation can't lie about progress because it has no separate source to lie from.

## CP-1.5 · Git-lite — the first temporal facts
**File:** `backend/app/ingestion/git_history.py`

**What we did.** One `git log --numstat` pass → per file: `churn_count` (commits touching it), `author_count`, `last_modified`. Applied to **file nodes only** — history is recorded per path, and deriving per-function churn from per-file data would be manufactured precision.

**Why churn matters.** Complexity says a change is *easy to get wrong*; fan-in says a mistake *spreads*; churn says changes *actually happen*. A complex, central file nobody has touched in years is dormant; the same file changed weekly is where the next incident lives. This is CodeScene's core insight absorbed as one metric.

**Details worth reading:** rename normalization (`src/{old => new}/f.py`), subtree remapping (analyzing `backend/` inside a bigger repo: git reports paths from the repo root, ours are relative to `backend/`), and the honest empty-case — no history yields `{}`, never invented numbers. The gate test compares our counts against `git log` itself, which is the right oracle: *verify against the source of truth, not against your own code.*

## CP-1.6 · The crude dump — a debugger, not a product
**File:** `backend/app/graph/export.py`

**What we did.** A deliberately ugly GraphViz DOT export (file-level, fan-in capped) and a JSON digest of counts. Its only job: let a human eyeball a graph for wrongness before the real visualization exists (CP-4.x replaces it, never builds on it).

**⭐ M1 gate passed here:** pipeline end-to-end on CodeLens + requests + flask; re-runs skipped via digest; fixtures green; CP-1.3's 20/20 hand-verified spot check. **The core asset exists, provably, with zero AI.**

---

# Stage 2 — Deterministic queries (the graph's answers, before AI)

## CP-2.1 · The QueryPlan registry — questions become named plans
**File:** `backend/app/queries/base.py`

**What we did.** A registry: every supported question is a named, tested function `(GraphView, **params) → ResultGraph`. `run_query("blast_radius", view, node_id=…)` is the single entry point.

**Why a registry instead of functions called directly?** Three reasons that pay off later: (1) free-form chat becomes a *router* — the LLM picks and parameterizes a plan, it never freelances an answer; (2) every question is testable by name; (3) adding a question is registering a plan, not growing an if-tree.

**`ResultGraph` is the real contract** — study its fields: `node_ids`+`edges` (the evidence subgraph), `ranked` with per-node `reasons` (the deterministic facts behind each score — so a narrator explains a ranking without recomputing it), `paths` (the receipts). One shape feeds both the future map and the narration.

## CP-2.2 · Blast radius — the wedge
**File:** `backend/app/queries/blast_radius.py`

**What we did.** "What breaks if I change X?" = **reverse transitive closure**: flip every CALLS/IMPORTS edge and walk outward from X; everything reached depends on X. Ranked near-first (BFS distance), then by fan-in; every dependent carries its actual shortest path back to X; every path reports its **weakest-link confidence** — a radius that runs through a `dynamic_unknown` edge says so.

**Concepts.** *Transitive closure* (everything reachable, not just neighbors); *BFS distance as severity* (a direct caller feels a change before a 3-hop dependent); *the weakest link principle* (a chain of proofs with one guess in it is a guess — `min()` over the path's confidences).

**Why this one query is the business:** it's deterministic (checkable → the Accuracy Ledger can grade it), it answers the moment of maximum fear ("can I merge this?"), and every other tool structurally can't do it because they don't build the graph. The gate test derives the expected answer *by reading the fixture source by hand* and demands an exact match, paths verified edge-by-edge.

## CP-2.3 · Centrality, dependencies, risk
**Files:** `backend/app/queries/centrality.py`, `dependencies.py`, `risk.py`

**Centrality (Q4)** — PageRank + fan-in over IMPORTS+CALLS. PageRank's idea: importance flows along edges — being depended on by important things makes you important, recursively. Two implementation stories worth learning from:
- **We hand-rolled the power iteration (~25 lines)** because networkx's version imports scipy — a 30 MB numerical stack for one function on graphs of a few thousand nodes is exactly the premature weight CP-0.1 removed. Know when a dependency earns its weight.
- **I got the direction wrong first.** I reversed the graph, reasoning "importance should flow to dependees" — but a dependency edge A→B *already* points the way PageRank wants (a web link u→v confers importance on v). The fixture gate caught it instantly: `main.py` outranked `calculator.py`, obviously wrong in a repo where everything imports `calculator`. *Small hand-verified fixtures catch reasoning errors that big test suites miss.*

**Risk (Q10)** — FOUNDATION's formula verbatim: `normalize(complexity × fan_in × churn)` per file. **Multiplied, not summed**: a file is only top-risk when all three point the same way. When churn is unknown the factor is neutral and `churn_known: false` is reported — absence of data is data.

**Dependencies (Q9)** — the forward closure, depth-limited, grouped by distance. The mirror of blast radius.

## CP-2.4 · Entrypoints and modules
**File:** `backend/app/queries/structure.py`

Entrypoints grouped by kind (server/CLI/main) straight off the parser's flags. Modules ranked by **external fan-in** — dependencies arriving from *outside* the module's own files. Internal wiring is implementation; external pull is architecture. That distinction (drawing a boundary and counting only what crosses it) is the seed of every coupling metric in Stage 9.

**The judge gate:** on CodeLens itself, `schema.py` ranked #1 (fan-in 23 — it *is* the frozen contract), `traversal.py` #2 (every query reads it), and schema's blast radius found all 25 consumers. When a metric's answer matches what a person who knows the code would say, the metric has earned trust.

---

# Stage 3 — The semantic layer (AI at the edge)

## CP-3.1 · The context assembler — the RAG rule as code
**File:** `backend/app/semantic/context.py`

**What we did.** The one function every LLM call must get its context from: graph-selected node ids in → their *real source lines* out, ranked (query order, then fan-in), **hard token budget** — what doesn't fit is dropped and recorded in `dropped_ids`, never squeezed or summarized-to-fit.

**Why "never send the repository" is an architecture, not a cost tip.** Vector-only RAG retrieves what *looks similar* to the question. Graph RAG retrieves what *is structurally relevant* — callers, callees, the class hierarchy — relationships similarity cannot see. The graph is the retrieval engine; the assembler just packages its selection. And `included_ids` becomes `derived_from` on every annotation: the evidence chain starts here.

## CP-3.2 · Summaries with the content-hash cache
**Files:** `backend/app/semantic/llm.py`, `backend/app/semantic/summaries.py`

**What we did.** One-sentence summaries per file and public function, stored as `SemanticAnnotation` (summary + `derived_from` + `content_hash` + model + timestamp), **cached by content hash**.

**The seam to study — `LLMClient` as a protocol:** `AnthropicClient` (real, lazy, needs a key only when *called*) and `CountingFakeLLM` (deterministic, free, counts invocations) implement the same interface. This isn't just testing hygiene; it's how the whole layer stays optional — the deterministic 80% of CodeLens runs with no key at all.

**The gate, proven three ways with the counting fake:**
1. Unchanged repo, second run → **exactly zero** LLM calls.
2. Identical source copied to a different path → zero calls (the cache keys on *content*, not location or run).
3. Edit one function's body → pay for 1–2 summaries (the function + its file, whose hash changed with it); everything else rides the cache. **Cost scales with the diff, not the repo** — Constitution rule 4, measured.

Also note the failure path: a dead model skips gracefully and *visibly* (`report.failed`) — facts never wait for annotations, and failures are never silent.

## CP-3.3 · Concept search — finding the unnamed
**File:** `backend/app/semantic/embeddings.py`

**What we did.** "Where is rate limiting?" when nothing is *named* rate-limiting. Nodes are indexed by name + docstring + summary; queries return **graph nodes, never text snippets** — a result you can click, traverse, blast-radius.

**An honest engineering choice to learn from:** the backend is a protocol, and today's implementation is *deterministic feature hashing* — split identifiers (`throttle_requests` → `throttle`, `requests`), hash each token into one of 512 slots with a sign, weight sublinearly, cosine-compare. This is lexical matching wearing a vector interface, and the code says so out loud. It's free, offline, reproducible in CI — and when quality demands a neural model, it swaps in behind the same protocol with zero changes to the index or query plan. *Build the seam now; upgrade the implementation when justified.*

The compounding detail: summaries feed the index, so once CP-3.2 has run, an annotation's vocabulary becomes findable — the semantic layer improves its own search.

## CP-3.4 · Narration — stories with receipts
**File:** `backend/app/semantic/narration.py`

**What we did.** Three launch answers:
- **Q1 "What does this project do?"** — LLM prose over graph facts (entrypoints, central files, their summaries). The *prompt* is a list of named facts; the tests verify facts went in by name.
- **Q8 "What breaks if I change X?"** — the blast-radius ResultGraph told the truth; the LLM turns ranked dependents, paths, and confidence flags into a senior-engineer story, instructed to *say* where paths are uncertain.
- **Q6 "Where should I start reading?"** — **deliberately not narrated.** The answer *is* an ordering (entrypoints → central files → onward), and an ordering needs no model: `model=None`, fully deterministic, summaries decorate the stops. Knowing when *not* to use the LLM is the discipline of this whole layer.

Every `NarratedAnswer` carries `evidence_ids`. A claim without receipts does not ship — the rule that later becomes clickable claims in the UI and gradeable claims in the Ledger.

---

# The through-lines (if you remember five things)

1. **Facts and interpretations never mix.** Parser emits facts; AI emits annotations that point at facts (`derived_from`). Every layer enforces this in its types, and tests make unsourced claims unconstructible.
2. **Hash everything; never pay twice.** File hashes → pipeline skips. Definition hashes → summary cache. Content is the key; cost scales with the diff.
3. **Uncertainty is data.** `resolved`/`heuristic`/`dynamic_unknown` on edges; weakest-link confidence on paths; `churn_known: false` on risk. The system says how sure it is, everywhere.
4. **Interfaces are cheap; implementations are swappable.** `GraphStore` (SQLite→Neo4j), `LLMClient` (fake→Anthropic), `EmbeddingBackend` (hashing→neural). Every seam was drawn when it cost nothing.
5. **Gates over feelings.** Every checkpoint ended with an observable test — hand-derived blast radius, zero second LLM calls, churn vs `git log`, 20/20 spot-checked edges. "Done" means the gate passed.

## Where we are on the map

```
Stage 0 ✅ → Stage 1 ✅ (M1: the graph) → Stage 2 ✅ (queries) → Stage 3 ✅ (AI at the edge)
                                                                        │
                                             next → Stage 4: the Hero Moment (frontend)
                                                    then CP-5.1 — the gate that decides everything
```

What Stage 3's completion means: **8 of the 10 launch questions have working machinery** (Q1, Q3, Q4, Q6, Q7, Q8, Q9, Q10 — Q2/Q5 need module summaries wired to the modules query, a small step). The M2/M3 milestone gate ("8/10 correct on an unfamiliar repo, judged by someone who knows it") needs a real LLM key and a human judge — run it before calling Stage 3 formally closed.

*Generated at the close of the Stage-1-through-3 build session, July 22, 2026.*
