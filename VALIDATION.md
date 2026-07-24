# CodeLens Validation Kit — Stage 5, the gate that decides everything

*The instrument for CP-5.1. This is the one checkpoint no code can pass — it needs ~20 real developers on their own repos. This file is how you run it with discipline instead of hope.*

> **The gate (from CHECKPOINTS.md CP-5.1):** a real developer uses CodeLens on *their own* repo and says **"I'd use this again" — unprompted.** Across ~20 ICP conversations, **≥5 must genuinely pull.** If fewer than 5 pull, **change the wedge before writing another line of code.** That's the process working, not failing.

**Nothing in Stages 6–9 may begin until this passes.** No billing, no auth, no GitHub App, no deploy. This is the line.

---

## Why this exists and can't be skipped

Everything built so far — the graph, the queries, the ripple, the whole hero moment — is a *bet* that developers want this. It has never been tested against a stranger's reaction to their own code. A product that works flawlessly on `requests` and `flask` proves the engineering; it proves nothing about demand. **One developer leaning in unprompted is worth more than another 10,000 lines.**

The failure mode this guards against: building the entire business (Stage 6+) for a wedge nobody actually pulls for. That's the classic solo-founder death. This gate is cheap insurance against months of misdirected work.

---

## Who to talk to (the ICP)

From STRATEGY §5 — don't dilute this:

- **Primary:** engineers & eng leads at **10–150-person orgs that adopted AI coding tools in the last year.** They generate comprehension debt daily and feel it weekly. Budget authority for $20–40/mo exists at this size.
- **Beachhead:** **OSS maintainers drowning in AI-generated PRs** (loud, public, badge-friendly) and **AI-forward teams whose velocity outran their understanding.**
- **Anti-ICP (skip for now):** big enterprise (sales cycle too long for a solo founder), hobbyists on tiny repos (no pain, no budget), and — critically — **your friends who want to be nice.** Nice is noise. You need honest.

**Where to find ~20:** your own network's second degree (ask for intros, not favors), r/ExperiencedDevs, Lobsters, relevant Discords/Slacks, the maintainers of repos you admire, replies to the comprehension-debt problem posts (STRATEGY §7 Phase 0).

---

## The session (15–20 minutes, screen-share or in person)

The golden rule (STRATEGY §10): **interview the pain, don't pitch the product.** The moment you start selling, the data turns to mush — people stop telling you the truth and start being polite.

### 1. Pain first (4 min) — *before they see anything*
Ask, and shut up:
- "Walk me through the last time you had to change code you didn't fully understand. What happened?"
- "How do you figure out what a change might break, today?"
- "Last time something broke in production somewhere you didn't expect — what was that?"

*You're listening for whether the pain is real and felt, in their words. Write down their exact phrases.*

### 2. Their repo, cold (8 min) — *the actual test*
Run `./run.sh`, paste **their** repo (or one they know intimately). Then **say as little as possible** and watch:
- Do they lean in at the assembly reveal? (the 10-second silent test)
- Do they grab the mouse? Do they click a node without being told?
- Do they say a name out loud — *"oh that's exactly where the mess is"*?
- Fire the ripple on a module they care about. Watch their face when the wave hits modules they didn't expect.

*The reactions matter more than the words. "Huh, interesting" is a fail. Reaching for the keyboard is a pass.*

### 3. The pull test (3 min) — *do NOT lead the witness*
- "What would you use this for, if anything?" *(open — let them not have an answer)*
- "Would you want to run this on your own repos?" *(then SILENCE — count to five)*
- Only if they're positive: "What would make you *stop* using it?"

### 4. Price (2 min) — B-2, only if they pulled
- "If this saved you from one bad merge a month, what's that worth?"
- "$19/mo for private repos and blast radius — too high, too low, about right?"
- *Test against the Free / Pro (~$19–29) / Team (~$29–49/seat) hypothesis (STRATEGY §8). You want the flinch or the shrug, not a number to believe.*

---

## The scorecard — track every conversation

Copy this table into a sheet. One row per person. **Be brutally honest in the PULL column** — the whole gate depends on it not being wishful.

| # | Who / role | Org size | Their repo? | Pain real? (1–5) | Leaned in? | **Unprompted "I'd use this again"?** | Would pay? | The exact words they used |
|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | ☐ | | |
| 2 | | | | | | ☐ | | |
| … | | | | | | ☐ | | |
| 20 | | | | | | ☐ | | |

**What counts as a "pull" (the ☐ checked):** they asked to use it again, asked when they could have it, asked for access to another repo, or started using it without being prompted. **Politeness does not count.** "This is cool" does not count. "Can I run this on our monorepo Monday?" counts.

---

## The decision (after ~20)

Count the genuine pulls.

- **≥5 pull →** ✅ **CONTINUE.** The wedge is validated. *Now* Stage 6 is earned — productionize, then distribution, then the Accuracy Ledger. Wire your `ANTHROPIC_API_KEY` and stand it up for real.
- **<5 pull →** 🔄 **CHANGE THE WEDGE — don't build more.** The graph, queries, and visualization are all reusable substrate; what's wrong is the *framing* or the *first feature*. Re-read the transcripts: what did people actually lean toward? Maybe it's onboarding, not blast radius. Maybe it's a different ICP. Re-aim, re-run 10 more. This is not failure; it's the cheapest pivot you will ever make.
- **0–1 pull →** stop and rethink the problem itself, not just the wedge.

**Write the number down publicly to yourself.** "7 of 21 pulled" or "3 of 19 pulled." The honesty of that number is the whole point — the same honesty the Accuracy Ledger will later make public. If you fudge it here, you'll fudge it there, and the trust position dies.

---

## Before the first session — demo readiness checklist

- [ ] `./run.sh` opens a working app (verified — it builds prod and opens the browser).
- [ ] `ANTHROPIC_API_KEY` in `backend/.env` **if** you want the AI narration/summaries in the demo (the graph, queries, and ripple work without it; the story panels need it).
- [ ] Pre-analyze 2–3 famous repos so the first thing they see is instant, then do *their* repo live.
- [ ] Know the flaky path: paste-URL-then-**Understand** is the rock-solid trigger; that's what you drive.
- [ ] A repo of theirs cloned or its URL ready, so section 2 has no fumbling.
- [ ] The scorecard open in another window.

---

## What I (the build) can and can't do here

**Built and ready:** the product, the one-command launcher, this kit, the scorecard, the interview script.

**Only you can do:** be the 20 conversations. Recruit the people, run the sessions, record the pulls honestly, make the continue/change call. I can prep, polish, and analyze transcripts you bring back — but the gate is walked by real developers, not by me. That's not a limitation to fix; it's the definition of validation.

The moment someone you don't know, looking at their own code, says *"wait — can I use this on our repo?"* — that's the first real data point this project has ever had. Everything before it was preparation.
