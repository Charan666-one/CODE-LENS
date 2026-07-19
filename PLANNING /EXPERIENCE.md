# CodeLens Experience — How It Feels

*July 19, 2026. The third founding document. STRATEGY.md = why and for whom. FOUNDATION.md = the graph and the questions. This = the experience layer: what a person feels in the first 60 seconds and every session after. These three documents together are the product.*

---

## The one goal

Every UI decision serves a single sentence:

> **"I've never seen code presented like this before."**

Not "clean dashboard." Not "nice graph." That exact reaction — because that reaction is what gets screenshotted, shared, and remembered. The experience *is* the distribution strategy (STRATEGY §Layer 3 made the map the megaphone; this document is the megaphone's design spec).

**The product feeling, in one line:** *watching software become alive.*

---

## The governing rule: honest theater

One constraint above all others, because it protects the trust position that defines CodeLens:

> **Never animate a lie. Every visual is a real fact from the graph, choreographed — never decoration.**

- The "Understanding…" stages shown during analysis are the *actual pipeline stages* (parse → graph → entrypoints → summaries), reporting real progress. Theater and telemetry are the same thing.
- A glowing path IS a real dependency path from the graph, clickable down to the line numbers.
- A pulsing module IS real churn data from git.
- If the graph doesn't know something, the UI says so — beautifully, but it says so.

This is what separates "cinematic" from "gimmick." Apple's unboxing works because the product inside is real. The moment an animation decorates instead of informs, delete it.

---

## Naming: kill the word "graph"

"Dependency graph" is commodity vocabulary — it describes the data structure, not the experience. CodeLens needs proprietary language.

**Decision: the** **Repository Brain** — "the Brain" in casual use.

Why this over Code Genome / Software DNA: it pairs perfectly with the five-year North Star ("ask the repository"), it implies *thinking* rather than just structure, and it makes the pipeline copy write itself:

```
Building Repository Brain…          92%
Repository understood ✓
```

Vocabulary system (use consistently, everywhere — UI, docs, marketing):

| Instead of | Say |
|---|---|
| Analyzing / Loading | **Understanding…** |
| Dependency graph | **the Brain** / the map |
| Node details | **the story** of this module |
| Impact analysis | **the ripple** |
| Search results | **the path lights up** |
| Analysis complete | **"I understand your software."** |

Small words, huge psychological difference. "Loading" is a machine working. "Understanding" is a mind forming.

---

## The Hero Moment (first-run choreography)

Every great product has one — Figma's shared cursor, ChatGPT's first reply, Maps' zoom from orbit. This is CodeLens's, specified as a scene:

**0s** — User pastes `github.com/vercel/next.js`. The input dissolves. Black screen, one tiny glowing node.

**0–10s** — Pipeline stages stream in, in understanding-language:

```
Understanding repository…
  Parsing structure…            ✓
  Building Repository Brain…    ✓
  Finding entry points…         ✓
  Learning dependencies…        ✓
  Constructing mental model…    ▓▓▓▓▓░░░
```

(Each line flips as the real pipeline step completes. On big repos this hides latency; on small repos it still paces the reveal — anticipation is part of the experience.)

**10–15s** — The assembly. Nodes stream outward from the first one. Edges connect. Then — the key beat — **clusters organize themselves**: Routing pulls together, Compiler pulls together, Server, Client, Cache. Named regions form like districts of a city. The repository literally assembles itself on screen.

**15s** — Everything zooms out to fit one screen. Beat of stillness. Then the title card:

> **Next.js in one view.**
>
> *"I understand your software. Ask me anything."*

That frame — a famous codebase, alive, on one screen — is the shareable artifact. It should look good enough to screenshot *by default*: dark canvas, glowing clusters, clean typography. Every share button ("Share this view") reinforces the loop.

**Engineering note:** the assembly animation is a *replay of real construction order* (entrypoints first, then BFS outward through the import graph). Precompute layout server-side; animate the reveal client-side. Render with WebGL (sigma.js / cosmos-gl class, not SVG) — thousands of nodes at 60fps or the magic dies. The animation must be interruptible: one click and you're exploring, no forced sit-through on repeat visits.

---

## The city model: zoom as the interface

Google Maps never shows every street at once. Neither does CodeLens. The zoom hierarchy maps *directly* onto the `CONTAINS` hierarchy already in FOUNDATION's schema — the experience was latent in the data model all along:

| Zoom | Shows | Graph level |
|---|---|---|
| 1 | Frontend / Backend / Database / Infra | Repository → top clusters |
| 2 | Auth, Dashboard, Payments, Editor | Packages/Modules |
| 3 | Login, JWT, OAuth, Middleware | Sub-modules |
| 4 | Individual files | Files |
| 5 | Functions, with signatures | Functions |

Rules that make this feel inevitable rather than clever:

- **Semantic zoom, not magnification.** Zooming doesn't make circles bigger — it changes *what exists*. Level 2 aggregates all cross-module edges into thick flows; level 4 resolves them into individual imports.
- **Nobody is ever overwhelmed.** The default view always fits on one screen. Detail is earned by zooming, never dumped.
- **Level-of-detail is also the performance strategy.** Render only the current zoom's nodes; a 50k-file monorepo at zoom 1 is just 8 clusters. The experience constraint and the rendering budget are the same design.

---

## The signature interaction: the repository responds

This is the feature that makes CodeLens feel like a living model instead of a dashboard. **Every question gets a visual answer from the map itself:**

- **"How does authentication work?"** → the rest of the city dims to 10% opacity; the auth path lights up; a pulse of light travels it: `Login Page → API → JWT → Redis → Dashboard`. You watch the request move. (The path is the real ROUTES_TO/CALLS chain — honest theater.)
- **"Where should I fix issue #248?"** → the camera *flies* to the relevant district; candidate files glow; tests and owners annotate in.
- **"Can I safely remove this module?"** → **the ripple**: a wave expands outward from the node through every transitive dependent, intensity fading with distance. Blast radius, felt physically before it's read.
- **Click any module** → focus mode: its direct world (callers, callees, imports) stays lit, everything else recedes.

Design law: **answers are camera movements + light, THEN text.** The text panel gives the story and evidence; the map gives the intuition. People remember what they *watched happen*.

---

## AI narrates stories, not descriptions

The AI's job (per FOUNDATION: facts from graph, AI explains) gets a voice direction: **a senior engineer giving you a tour**, not a documentation generator.

Bad (description): *"PaymentService handles payment logic."*

Good (story): *"Every purchase in this project eventually passes through PaymentService. It validates the order, records the transaction, generates an invoice, and finally triggers the notification emails."*

Voice rules: narrative arc (what enters, what happens, what leaves) · plain verbs · always mention downstream consequences ("…and three other modules depend on this behavior") · every claim carries evidence pointers back to graph nodes — click the sentence, the map shows the path. **Narration synchronized with the visual:** as the story mentions InvoiceGenerator, that node glows. Read + watch = understanding.

---

## Four modes = four camera angles on one Brain

FOUNDATION's four personas become four *lenses*, not four products:

**Beginner Mode — the guided tour.** Opening React shouldn't cause panic; it should feel like day one with a patient mentor: *"You're looking at React. ~20 hours to working knowledge. Today: Rendering."* The map isolates the rendering district; everything else fades. Lessons are the learning path (FOUNDATION Q6) rendered as chapters — each one a focused sub-map with narration. Progress persists: your explored regions stay lit, unexplored stay dim. **Your mental model, visualized as literal territory conquered.**

**Contributor Mode — the destination.** Paste an issue link → the camera flies to the target area → files, dependencies, tests, owners, and risk annotate in automatically. No wandering. The map answers "where do I work?" before you ask.

**Maintainer Mode — the ripple.** Open a PR → affected modules pulse → risk score + dependency paths shown → the ripple animation IS the blast radius report. (This is the paid wedge from STRATEGY wearing its experience clothes.)

**Architect Mode — the weather map.** Stripe-backend-scale repos render as 6 districts, not 1000 files. Overlays toggle like map layers: coupling heat, churn activity (🔥 hot districts / 🌙 dormant ones), bus-factor risk, debt ranking. Architecture stops being a diagram someone drew last year and becomes *current conditions*.

---

## The living model (the digital-twin horizon)

Phase-later, but designed-for-now: the Brain updates continuously.

- Git activity renders as *life*: hot modules glow warmer (real churn data), dormant ones cool.
- A merged PR visibly rewires its edges. Deleted code's connections dissolve.
- Open PRs appear as ghost-overlays — proposed futures on the current map.
- Timeline scrubber: drag backward, watch the architecture evolve over a year in ten seconds. (This becomes the "we mapped [repo]" content engine's killer format.)

The endpoint of the vision: a repository's Brain is its **digital twin** — the always-current, explorable model of the system, which is what "understanding" means at team scale. The Accuracy Ledger (STRATEGY §Layer 2) is the twin proving it deserves the name.

---

## The landing page (the trailer)

Black. One glowing node, drifting. Mouse moves — the node stirs, edges reach out. Within seconds the visitor is *causing* a repository to assemble. Then one line:

> **Understand software, not files.**

One input field: paste a GitHub URL. No paragraphs, no feature grid, no testimonial carousel. The landing page is a playable demo of the hero moment — because the product's first impression *is* the pitch, and everything else is commentary. (A "try Next.js / React / FastAPI" row of one-click famous repos removes even the friction of choosing.)

Timing honesty: this page is worth a month of polish — *after* Phase 1's graph works. Cinema before substance is how demos rot into vaporware. Build the Brain, then film it.

---

## The emotion hierarchy (the spec behind every screen)

| Stage | User emotion | UI goal |
|---|---|---|
| Landing | Curiosity | "I've never seen code like this." |
| Analysis | Anticipation | "The system is learning my repository." |
| First visualization | **Awe** | "This entire project fits on one screen." |
| Exploration | Discovery | "Everything is connected." |
| Asking questions | Confidence | "I don't need to search anymore." |
| Contributing | Trust | "I know exactly where to make changes." |

Every screen review asks one question: *which emotion is this screen responsible for, and does it deliver it?* If a screen has no row in this table, it probably shouldn't exist.

---

## Experience build order (folded into the phase ladder)

- **Phase 1 (with the MVP):** understanding-language pipeline copy · the assembly reveal · zoom levels 1–3 · click-to-story with synchronized highlight · dark, screenshot-worthy default aesthetic. *The hero moment ships with the first version — it IS the launch.*
- **Phase 2:** the ripple (blast radius) · path-lighting for flow questions · Contributor Mode fly-to · shareable view links.
- **Phase 3:** Beginner Mode tours · Architect overlays · live PR pulses.
- **Phase 4:** timeline scrubber · full digital-twin behavior.

Non-negotiables at every phase: 60fps or reduce detail · every animation interruptible · every visual claim clickable down to code · works breathtakingly on a 13" laptop, not just a demo rig.

---

## The test

Show it to a developer for ten seconds, silently.

If they say *"nice graph tool"* — iterate.
If they say *"wait… what is this?"* and lean in — ship.
