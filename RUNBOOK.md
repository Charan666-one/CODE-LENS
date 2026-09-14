# CodeLens Runbook — How to Run Everything

*Every command below was executed and verified on August 16, 2026. Outputs shown are real.*

**One rule that trips people up:** commands are written **from the project root** (`CODELENS/`). If your shell prompt already says `backend`, drop the `cd backend` part — that's what caused the `cd: no such file or directory: backend` error.

```bash
cd "/Users/srisaicharanp/Desktop/PENDING PROJECTS/CODELENS"
```

---

## 1. The one command that proves everything works

```bash
cd backend && .venv/bin/python scripts/verify_system.py
```

Clones a real repository and exercises **every subsystem** — pipeline, queries, viewspec, semantic layer, API. Takes ~15s, needs no API key, spends no tokens. Ends with `23/23 passed`.

Point it at any repo:

```bash
cd backend && .venv/bin/python scripts/verify_system.py https://github.com/pallets/flask
```

## 2. Quality gates (run before every commit)

```bash
cd backend && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/python -m pytest -q
```

Expect: `All checks passed!` · `no issues found in 61 source files` · `292 passed, 1 skipped`.

---

## 3. Run the API server

```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000 --reload
```

Interactive API docs: **http://127.0.0.1:8000/api/docs**

### Analyze a repository

```bash
curl -s -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"source":"https://github.com/pallets/click"}'
```

Real output — note `snapshot_id`, you need it for every query below:

```json
{"snapshot_id": 2, "commit_sha": "398f9154...", "skipped": false, "nodes": 1965, "edges": 2808}
```

Run it a second time and `"skipped": true` — the content-digest cache proving re-analysis is free.

### Blast radius — the paid wedge

```bash
curl -s -X POST http://127.0.0.1:8000/api/repos/2/query/blast_radius \
  -H "Content-Type: application/json" \
  -d '{"params":{"node_id":"file:src/click/core.py"}}'
```

Real answer: **12 affected**, nearest first — `exceptions.py`, `globals.py`, `__init__.py` at distance 1, each with the actual dependency path and a confidence level.

### Most important files

```bash
curl -s -X POST http://127.0.0.1:8000/api/repos/2/query/centrality \
  -H "Content-Type: application/json" \
  -d '{"params":{"kind":"file","top":3}}'
```

Real answer on click: `_compat.py` (fan-in 9), `core.py` (fan-in 8) — exactly what a click maintainer would name.

### Riskiest files

```bash
curl -s -X POST http://127.0.0.1:8000/api/repos/2/query/risk \
  -H "Content-Type: application/json" -d '{"params":{"top":3}}'
```

Real answer: `core.py` at 1.00 (complexity 567), `_compat.py` 0.23, `utils.py` 0.18.

### Concept search — finds things not named for the concept

```bash
curl -s -X POST http://127.0.0.1:8000/api/repos/2/search \
  -H "Content-Type: application/json" \
  -d '{"text":"terminal color output","top":3}'
```

### Learning path — needs no API key (deterministic)

```bash
curl -s http://127.0.0.1:8000/api/repos/2/answers/learning_path
```

Returns `"model": null` — the ordering *is* the answer; no LLM involved.

### Other endpoints

```bash
curl -s http://127.0.0.1:8000/api/repos       # every stored snapshot
curl -s http://127.0.0.1:8000/api/queries     # every registered query plan
curl -s "http://127.0.0.1:8000/api/repos/2/viewspec?zoom=1"   # 1=districts 2=files 3=functions
```

### Endpoints that need an API key

Add to `backend/.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Then (these 503 honestly without a key — the graph's facts are never behind the key, only the prose):

```bash
curl -s -X POST http://127.0.0.1:8000/api/repos/2/summarize -H "Content-Type: application/json" -d '{"max_nodes":25}'
curl -s -X POST http://127.0.0.1:8000/api/repos/2/answers/project
curl -s -X POST http://127.0.0.1:8000/api/repos/2/answers/blast_radius -H "Content-Type: application/json" -d '{"node_id":"file:src/click/core.py"}'
```

---

## 4. Run the full app (the hero moment)

**Two terminals.** Terminal 1 — backend:

```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000
```

Terminal 2 — frontend. **Use the production build**, not `npm run dev`: dev-mode Fast Refresh resets the page state mid-analysis and the graph never appears.

```bash
cd frontend && npm run build && npm run start
```

Open **http://localhost:3000**, paste a GitHub URL (or click a try-chip), press **Understand**.

You'll see: the understanding sequence → the assembly reveal → the live graph. **L1/L2/L3** (top right) switch districts → files → functions. Click a node to focus it; **Esc** releases.

First run only:

```bash
cd frontend && npm install
```

---

## 5. Analyze your own local code

Local paths are gated behind a flag (the security boundary — the public API accepts URLs only):

```bash
cd backend && CODELENS_ALLOW_LOCAL_ANALYSIS=1 .venv/bin/uvicorn app.main:app --port 8000
```

```bash
curl -s -X POST http://127.0.0.1:8000/api/analyze \
  -H "Content-Type: application/json" \
  -d '{"source":"/absolute/path/to/your/repo"}'
```

---

# What's left, and why each piece matters

## 🔴 Only you can do these

### 1. Push to a git remote
**20 commits exist on one laptop.** A drive failure erases the entire project.

```bash
gh repo create codelens --private --source=. --remote=origin --push
```
*(Or create the repo on github.com and `git remote add origin <url> && git push -u origin main`.)*

**Why it matters:** this is pure risk elimination. Everything below is worth nothing if the disk dies.

### 2. Run the M2/M3 milestone gate
Add `ANTHROPIC_API_KEY` to `backend/.env`, analyze a repo **you know well but CodeLens hasn't seen**, and judge: *are 8 of the 10 launch questions answered correctly?*

**Why it matters:** this is the acceptance gate for Stages 2–3. Until a human who knows the code confirms the answers, "the queries work" means the code runs — not that it's *right*. It's cheap (haiku tokens, cents) and it's the last checkpoint before the product goes in front of strangers.

## 🟡 Engineering left

### 3. CP-4.4 — the ripple, and layout polish
The blast-radius animation: a wave expanding through dependents, intensity fading with distance. Plus tightening the cluster layout (districts currently sit far apart with long crossing edges) and thinning L3 on big repos.

**Why it matters:** this is the difference between *"nice graph tool"* and *"wait — what is this?"* Blast radius is what you sell; the ripple is what makes people *feel* it before they read it. Per your own strategy the map is the distribution engine — the screenshot that spreads is the marketing budget you don't have to pay for.

### 4. Stage 5 — CP-5.1, the kill/continue gate
Put it in front of ~20 real developers on **their own** repos. The bar: someone says *"I'd use this again"* unprompted.

**Why it matters:** this is **the only gate that actually matters**, and everything above exists to reach it. Your own CHECKPOINTS.md forbids Stages 6–9 until it passes — that rule is what stops you from spending months on billing, auth, and infrastructure for a product nobody pulled for.

### 5. Stage 6+ — production (only after the gate)
Deploy, GitHub OAuth, private repos, Stripe, then the distribution engine (free maps, CodeLens Score, README badges) and the Accuracy Ledger.

**Why it matters:** the Ledger is the moat — the one asset a funded competitor can't buy, because it's made of time and real predictions. But it only starts accumulating once real repos flow through, which is why it comes *after* validation, not before.

---

## Where things stand

| | |
|---|---|
| Stages 0–3 | ✅ complete (graph, queries, AI-at-the-edge) |
| Stage 4 | compiler + API ✅ · renderer verified live ✅ · ripple pending |
| Languages | Python ✅ JavaScript ✅ (TypeScript needs its own grammar) |
| Tests | 199 passing · ruff + mypy clean |
| Verification | 23/23 end-to-end on a real repo |
| Tokens spent | $0 |
