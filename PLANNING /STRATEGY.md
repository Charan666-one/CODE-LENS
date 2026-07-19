# CodeLens — Startup Strategy v2

*Rewritten July 18, 2026. Supersedes v1. This is the founding document: the mission, the moat, and the order of operations. The technical spec (CodeLens_AI_v2.docx) remains the engineering reference — this defines what gets built, why, and in what sequence.*

---

## 1. Mission — the one sentence

**AI now writes most new software. Nobody verifies it's safe to change. CodeLens is the trust layer between AI-generated code and production — it tells you exactly what any change will break, proves it with a real dependency graph, and publicly tracks its own accuracy.**

Three claims in that sentence, each load-bearing:

1. **"Trust layer between AI code and production"** → the story. Big, inevitable, growing on its own.
2. **"Proves it with a real graph"** → the differentiator. Deterministic, verifiable, not an LLM guess.
3. **"Publicly tracks its own accuracy"** → the moat. A credibility engine no one has built and no one can copy quickly.

---

## 2. Why this doesn't exist yet (the gap, precisely)

The 2026 landscape, and what each player is structurally unable to do:

| Player | What they do | Why they can't be CodeLens |
|---|---|---|
| CodeRabbit (~140K paid, $24/mo), Greptile (free tier, 82% bug-catch) | Review the **diff** in a PR | They judge the change in isolation. They don't model the whole system, so they can't compute blast radius — and their LLM answers are unverifiable by design |
| DeepWiki (free), Unblocked, Sourcegraph | Answer "**how does this work?**" | Explanation, not prediction. Nothing to verify, nothing at stake, no accuracy to track |
| CodeScene (€18–27/author) | Score **historical** debt from git behavior | Backward-looking. Says "this file has been troublesome," never "this specific change will break these specific things" |
| CodeSee | Interactive code maps | Acquired by GitKraken in 2024; development stalled. The visualization seat is empty |

**The empty square:** *forward-looking, deterministic, self-verifying change prediction.* Every tool above emits opinions. None emits **predictions that can be checked** — because LLM output can't be scored, and none of them built the graph substrate that makes checkable predictions possible. CodeLens's determinism isn't just a trust feature; it's the *precondition* for the accuracy engine in §4. That's why this specific product hasn't been created: the incumbents' architecture forbids it.

**The timing:** "comprehension debt" was named by Addy Osmani (Google) in March 2026. AI generates code 5–7× faster than humans comprehend it; AI-assisted PRs carry 1.7× more issues; teams report 30–41% debt growth within six months of AI adoption. The problem CodeLens solves is brand-new as a *named category* and compounding monthly. Categories get owned by whoever names and measures them first.

---

## 3. The product, in three layers

### Layer 1 — The wedge: Blast Radius (what people pay for)

Select any module, function, or file → CodeLens returns **everything that transitively depends on it, ranked by severity, with the actual dependency paths shown.** Before you merge, you know what you're touching. Deterministic. Verifiable. Click any claim and see the path.

This stays the wedge because it's the highest-pain moment ("will this change break something I can't see?"), it's the most defensible feature (graph query, not guess), and it's the substrate every other layer reads from.

### Layer 2 — The credibility engine: the Accuracy Ledger (why people trust it)

**This is the invention. Nobody has built this.**

Every blast-radius prediction is a falsifiable claim. So CodeLens closes the loop:

1. **Predict** — "changing `auth.py` affects these 14 modules; 2 critical."
2. **Observe** — after merge, watch the outcome signals: CI failures, test breakage, reverts, hotfix commits touching predicted modules, linked incidents.
3. **Grade** — did reality match the prediction? Score it. Store it.
4. **Publish** — a public, running scoreboard: *"CodeLens correctly bounded the blast radius in 94.2% of 12,000 tracked changes."* Misses included, visible, analyzed.

What this creates:

- **Structural trust.** Every AI tool says "trust us." CodeLens says "audit us." In a market defined by hallucination anxiety, the only tool with a public error rate becomes the default answer to "but can I rely on it?"
- **A compounding, time-locked moat.** The prediction→outcome dataset can only be produced by real repos over real time. A competitor cloning every feature on day 1 still starts with an empty ledger — and an empty ledger is the product *without* its credibility. Every week of operation widens a gap money can't close.
- **A self-improving core.** Every miss is labeled training data for the graph builder (dynamic imports, config-coupled modules, runtime wiring — exactly the hard cases in the spec's "known limitations"). The moat and the product improve from the same events.
- **Permanent spotlight material.** "The first dev tool that publicly grades its own predictions" is a launch headline, a conference talk, and a standing indictment of every competitor's unverifiable output.

**Non-negotiable rule:** the ledger shows failures. The first hidden miss kills the entire premise. Checkable honesty *is* the product.

### Layer 3 — The distribution engine: Scores, Maps, Badges (how millions see it)

- **Paste any public GitHub URL → free interactive architecture map** with risk heatmap. Beautiful, color-coded, shareable. The "whoa" screenshot is the ad.
- **CodeLens Score** — a letter-grade for structural health/risk of any public repo, backed by the map and the methodology, refreshed on every analysis.
- **README badge** — like coverage badges: maintainers embed `CodeLens: A−` in their README. Every badge is a permanent free advertisement placed in front of developers by the users themselves.
- **Editorial engine** — "We mapped [famous OSS repo]: here's its architecture and its most dangerous module" posts. Infinite content, each one a product demo.

The endgame of Layer 3 is not "lots of users" — it's **becoming the standard**. When people cite a CodeLens Score the way they cite test coverage, the position is un-replicable in the only way that word actually means anything: a competitor can copy the feature, but not the citation graph.

### How the layers interlock (the flywheel)

Free maps & scores (L3) → repos flow in → predictions get made (L1) → outcomes get graded (L2) → public accuracy grows → trust grows → more repos & paid teams → more data → better predictions → louder scoreboard. Each layer feeds the next. This is a system, not a feature list — that's what makes it a startup rather than a tool.

---

## 4. Why it's precious (the value logic)

- **The pain is expensive, emotional, and growing.** What CodeLens removes is "I merged AI code and production broke somewhere I couldn't see." That's incidents, 2am pages, and eroded trust in one's own system — and the volume of such moments scales with AI adoption, automatically, forever.
- **The spend is proven.** AI code review alone is ~$420M ARR in 2026; adjacent tools charge $18–30/seat/month. CodeLens takes a sharper slice of an existing budget line, not a missionary sale.
- **The account expands without rebuilding.** Once the graph exists, architecture maps, risk heatmaps, scoped chat, security overlays, and version diffs (all in the v2 spec) are additional *reads on the same graph* — a natural free→pro→team ladder.
- **The asset appreciates.** Most software depreciates; the Accuracy Ledger appreciates. Valuation-wise, CodeLens is a data asset with a SaaS attached, which is the profile that commands multiples.

---

## 5. Who it's for

**Primary ICP:** engineers and eng leads at 10–150-person orgs that adopted AI coding tools in the past year — the people generating comprehension debt daily and feeling it weekly. Budget authority for $20–40/mo exists at this size.

**Beachhead:** OSS maintainers drowning in AI-generated PRs (loud, public, badge-friendly) and AI-forward teams whose velocity outran their understanding.

**Anti-ICP for now:** big enterprise (sales cycle vs. solo founder), hobbyists on tiny repos (no pain, no budget).

---

## 6. Positioning & the objection wall

**Tagline candidates:**
- *"Know what breaks before you merge."*
- *"The trust layer for AI-written code."*
- *"Everyone ships AI code. CodeLens is how you stay in control of it."*

**Objections, answered in advance:**

- *"My AI chat can explain my code."* — It guesses, one file at a time, unverifiably. CodeLens computes over the entire graph and shows the path. Ask your chat for its error rate; CodeLens publishes one.
- *"Copilot/Cursor will add this."* — Their architecture is LLM-first; checkable prediction requires the graph substrate and the outcome-tracking loop, which is a different product with a time-locked data moat. Also: assistants *creating* changes have a conflict of interest in *grading* them. The trust layer wants to be independent — same reason auditors aren't employed by the companies they audit.
- *"Static analysis misses dynamic behavior."* — True, disclosed, and measured: the ledger reports exactly how often it matters, and every miss improves the graph. No competitor even knows their miss rate.

---

## 7. Build order (validation-first, solo-realistic)

### Phase 0 — Smoke test (Week 0, near-zero code)
Landing page: comprehension-debt problem, blast-radius promise, mocked map screenshot, waitlist. Post the *problem* in 5 ICP communities (HN "Ask HN: how do you know what an AI-written change will break?", r/ExperiencedDevs, Lobsters, Discords). Measure resonance before building heavy.

### Phase 1 — Wedge MVP (Weeks 1–5)
- Ingest public GitHub URL / zip. **No auth, no billing, no queue.**
- tree-sitter parsing: **Python + JS/TS only.**
- Dependency graph: **networkx/SQLite first** — defer Neo4j, Celery, Redis, Chroma until real load exists. (Standing up five databases for zero users is how solo founders die.)
- **Blast radius:** transitive dependents, ranked, with paths. Must be flawless — it's the credibility seed.
- One clean map view (React Flow or server-rendered SVG), nodes colored by fan-in.
- One scoped LLM touch: per-module plain-English summary. Synthesis only; facts stay deterministic.
- Run it live on 5 interviewees' repos. Gate: an unprompted *"can I use this on my repo?"*

### Phase 2 — Credibility engine v0 (Weeks 6–10)
- GitHub App: read PRs, record a blast-radius prediction per merge.
- Outcome watcher: CI status, reverts, hotfixes touching predicted files within N days.
- Private accuracy dashboard first → **public ledger the moment the numbers are honest and stable.** Launch moment: *"We tracked our own predictions for a month. Here's our error rate — has your AI tool published theirs?"*

### Phase 3 — Distribution engine (Weeks 8–14, overlaps)
- Free public-repo analysis, CodeLens Score, README badge, shareable map links.
- Weekly "we mapped [famous repo]" post. Each is a demo, an SEO asset, and a badge-adoption pitch to that repo's maintainers.

### Phase 4 — Platform reads (post-traction)
Risk heatmaps, scoped chat, Semgrep overlay, version diff, exports, Jira/Linear, more languages, Neo4j/Celery/K8s hardening — the full v2 spec, now justified by paying users. The spec is the 18-month roadmap; it was only ever fatal as a 3-month plan.

**Kill/continue gate (unchanged from v1):** ~20 ICP conversations by end of Phase 1; if fewer than 5 people are genuinely pulling for it, change the wedge before writing more code. That's the process working, not failing.

---

## 8. Pricing hypothesis

- **Free:** public repos, maps, scores, badges. (The distribution engine must be free — it's marketing that compounds.)
- **Pro ~$19–29/mo:** private repos, blast radius on demand, saved projects.
- **Team ~$29–49/seat/mo:** GitHub App on every PR, team ledger, shared workspaces. The accuracy ledger justifies the premium tier — you're not buying reports, you're buying a *verified* safety gate.

Test against 10 real conversations before committing.

---

## 9. Honest risks

- **Distribution > engineering.** You're backend-strong; the failure mode is a great product nobody hears about. The badge loop and editorial engine are the counterweight — but only if publishing is treated as half the job, every week.
- **The ledger cuts both ways.** Early accuracy may be mediocre. Ship it anyway, framed as "we measure, they don't." Hiding it would destroy the one thing that can't be copied.
- **Outcome attribution is fuzzy.** Linking a revert to a prediction is heuristic. Start with conservative, defensible signals (CI fails + hotfixes touching predicted files); widen carefully. Overclaiming here poisons the well.
- **The parser is the grind.** Correct graphs on messy real-world repos (dynamic imports, monorepos) is the hard, unglamorous work — which is precisely why it's the moat. The grind and the moat are the same thing.
- **Free anchors exist.** DeepWiki is free; Greptile has a free tier. You don't win on price or breadth — you win on the one job (verified blast radius) they structurally cannot do.

---

## 10. The 30-day plan

1. **Days 1–2:** Landing page + waitlist + problem post in 3 communities.
2. **Days 3–14:** 10 ICP problem interviews (interview the pain, don't pitch).
3. **Days 10–30:** Phase 1 MVP: ingest → Py/JS graph → blast radius → map → module summaries.
4. **Day 30:** Live on 5 real users' repos. Collect the "I'd use this again" — or change course cheaply.

---

## 11. The story in four lines (pitch skeleton)

1. AI writes software faster than humans can understand it — comprehension debt is the defining engineering problem of this decade.
2. Every existing tool emits unverifiable opinions about code. CodeLens emits **checkable predictions**: what exactly breaks if you change this — proven by a real dependency graph.
3. And it's the only tool that **publicly grades its own accuracy** — a trust asset that compounds daily and cannot be replicated without years of live data.
4. Free maps and repo scores spread it; verified blast radius monetizes it; the accuracy ledger locks it in.
