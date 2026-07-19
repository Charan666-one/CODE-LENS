# CodeLens Roadmap — The Long Game

*July 19, 2026. The sixth and final founding document. CORE = the idea. STRATEGY = the business. FOUNDATION = the data. EXPERIENCE = the feel. ARCHITECTURE = the build. This = the decade: what CodeLens must come to own, in what order, and the principles that never bend.*

---

## The ambition, stated correctly

Not: "the best AI code assistant." That category competes on whoever has the newest LLM, and resets every model generation.

**The ambition: the most trusted system for understanding software.** That competes on accumulated knowledge, validated evidence, and workflow integration — things that compound with time instead of resetting.

The behavioral north star — the single moment that proves it's working:

> Someone says: *"Before we merge this…"*
> Someone else replies: *"Let's check CodeLens."*

When CodeLens is the default place engineers go before an important decision, it has become infrastructure. Every phase below exists to earn that sentence.

---

## The five things CodeLens must come to own

Great platform companies (GitHub, Stripe, Datadog, Snowflake) end up owning the same five layers. This is the scorecard we grade the company against — not revenue, not features:

| # | Own | Meaning for CodeLens | Where it's designed |
|---|---|---|---|
| 1 | **Infrastructure** | The Software Knowledge Graph — when anyone wants to understand software, they use CodeLens | CORE, ARCHITECTURE |
| 2 | **Data** | Years of accumulated graphs + millions of validated predictions — a combination nobody else has | FOUNDATION Layers A–F |
| 3 | **Workflow** | Opened 20× a day: before changes, in review, while debugging, during onboarding, incidents, releases | EXPERIENCE modes, Phase ladder below |
| 4 | **Trust** | "Right 97.4% of the time over six months" — measured, public, auditable | STRATEGY: Accuracy Ledger |
| 5 | **Ecosystem** | APIs on the graph; others build security, compliance, education, analytics plugins on top | Phase 6+ below |

**The moat, precisely:** not AI. `Graph → years of accumulated knowledge → millions of validated predictions → trusted by teams.` Every element requires *time with real users* — the one thing a well-funded competitor cannot buy. The moat is a clock, and it starts when the first real repo flows through.

---

## The maturity ladder (phases, not years)

Each phase = a new graph layer + the applications it unlocks + a workflow moment captured. Each stands on the previous; none is skippable.

### Phase 1 — Understanding *(current target)*
**Goal:** understand any repository. **Graph:** Layers A/B/D (structure, external, semantic).
**Applications:** Architecture Map, Blast Radius, AI explanation — the 10 launch questions.
**Workflow moment captured:** onboarding, "before I touch this."
**Exit test:** a developer uses it on their own repo and comes back unprompted.

### Phase 2 — Trust
**Goal:** accurate enough that engineers *rely* on it. **Graph:** outcome records begin (Layer E-outcome).
**Applications:** Accuracy Ledger (private → public), visible confidence on every answer, PR-level blast radius via GitHub App.
**Workflow moment:** code review — "let's check CodeLens" enters the vocabulary.
**Exit test:** a team cites the accuracy number when deciding to trust an answer.

### Phase 3 — Memory
**Goal:** the graph remembers *why*. **Graph:** Layer E-memory — PRs, issues, docs, reviews, commits linked to code nodes.
**Applications:** "Why does this function exist?" · "What was decided and where?" · living documentation.
**Workflow moment:** debugging and archaeology — the questions only a senior veteran could answer.
**Exit test:** CodeLens answers a "why" question whose answer isn't in any single file.

### Phase 4 — Runtime understanding
**Goal:** connect what code *could do* with what it *actually does*. **Graph:** new Layer F — traces, error rates, deploy events (APM/OTel integrations).
**Applications:** "This module is high-risk *and* hot in production" · incident-time blast radius · dead-code proof from real traffic.
**Workflow moment:** incident response and release gates.
**Exit test:** an on-call engineer opens CodeLens *during* an incident.

### Phase 5 — Team knowledge
**Goal:** understand the humans around the code. **Graph:** Layer C matured — ownership, expertise, decision records.
**Applications:** "Who should review this?" · "Who understands Redis here?" · bus-factor alerts · staffing insight.
**Workflow moment:** planning and org decisions.
**Exit test:** a lead uses CodeLens to route work.

### Phase 6 — Action
**Goal:** from answering to doing. **Applications:** fix it · generate the test · open the PR · notify the owner · update the docs.
**Workflow moment:** the loop closes — understanding executes.
**Two hard rules, set now:** (1) every automated action goes through the same blast-radius check a human would, and **the Ledger grades CodeLens's own actions publicly** — the auditor must audit itself or the trust position dies; (2) action is earned by Phase 2–5 accuracy, never shipped ahead of it. Acting on a graph nobody trusts is how this company would destroy itself in one incident.

### Phase 7 — Ecosystem *(the horizon)*
Graph API + plugin surface. Security, compliance, education, analytics vendors build on the graph instead of rebuilding it. CodeLens stops being a product with users and becomes a platform with an economy. (This is also the endgame defense: ecosystems, not features, are what incumbents can't clone.)

---

## The benchmark question set (the north-star eval)

FOUNDATION's 100 questions cover understanding, navigation, change, contribution, and architecture. The long game adds three categories — folded in as the graph layers that answer them arrive:

- **Debugging** *(Phases 3–4)*: Where is the bug? Which commit caused it? What changed between working and broken?
- **Team** *(Phase 5)*: Who owns this? Who understands X? Who should review?
- **Business mapping** *(Phases 3–4)*: Which service handles payments? Which feature generates invoices? Which modules are truly unused?

Standing benchmark discipline: maintain this as a living eval suite. Every phase ships when its question categories move from "unanswerable" to "answered with evidence." That's the honest definition of progress — not feature count.

---

## The CodeLens Constitution

Principles that never bend, regardless of phase, pressure, or trend. Every design review, hire, and investor conversation is downstream of these:

1. **Evidence before explanation.** Every answer is backed by graph facts, cited and clickable. No orphan claims, ever.
2. **Deterministic first, AI second.** Algorithms wherever possible; LLMs for interpretation, never fabrication.
3. **Fast enough for daily use.** A workflow tool that's slow gets opened weekly, then never. Speed is a feature of trust.
4. **Incremental everything.** Reanalyze only what changed. Cost and latency scale with the diff, not the repo.
5. **Confidence is visible.** The system says how sure it is — on every edge, every answer, every prediction. Uncertainty shown beautifully beats certainty faked.
6. **The graph is the product.** Every new capability is a query on the graph or a new layer in it — never a silo beside it.
7. **The Ledger never lies.** Misses are published, analyzed, and learned from — including, eventually, the misses of CodeLens's own automated actions.

---

## How to hold this without being crushed by it

This document describes a decade. The only part that exists today is a plan and a schema. The discipline that connects them:

- **The ladder is sequential on purpose.** Phase 1's exit test — one developer returning unprompted — is the only thing that matters right now. Phases 4–7 are direction, not commitments; they exist so today's schema reserves their seats (Layer F, action records) and so no near-term decision forecloses them.
- **Every phase is judged by a behavior, not a feature list.** "Opened during an incident" can't be faked with a launch post.
- **The billion-dollar outcome is a side effect.** GitHub, Stripe, and Datadog aimed at a fundamental problem and let scale follow. The fundamental problem here: *software has become too large, too fast-changing, and too AI-generated for humans to hold in their heads.* CodeLens holds it for them. Solve that for one developer, then a team, then the industry — in that order.
