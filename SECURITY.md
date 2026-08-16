# CodeLens Security Report

**Date:** 2026-08-16 · **Scope:** security, abuse and durability audit ahead of a public beta.
**Verdict:** **NOT READY FOR PUBLIC BETA.** Blockers in §10.

> **Superseded in part.** All five §10 blockers were subsequently closed and
> tested — see [DEPLOYMENT_READINESS.md](DEPLOYMENT_READINESS.md), which
> raises the verdict to **READY FOR STAGING**. The threat model and findings
> below stand as written.

CodeLens accepts a repository URL from anyone, clones it, and parses untrusted
source. This is an audit of what a hostile user can do with that, what was
fixed, and what is still true.

---

## 1. Threat model

Five assets, and the attacker is an anonymous internet user with a URL box.

| # | Asset | What an attacker wants | Primary defence |
|---|---|---|---|
| A1 | The host machine | Code execution via the repository | Never execute repository code; container with no capabilities |
| A2 | The internal network | SSRF to metadata endpoints, internal services | URL allowlist: https + github.com + `owner/repo` |
| A3 | Availability | Exhaust disk, RAM, CPU, slots | Ceilings at every layer, enforced *in flight* |
| A4 | The operator's money | Loop an LLM endpoint | Separate narration quota; bounded `max_nodes` |
| A5 | Other users' data | Read graphs, keys, or repository contents | Only public repos stored; no secrets in responses |

**Trust boundaries.** Everything from the browser is hostile. Everything in a
cloned repository is hostile — filenames, file contents, git history, and
package manifests. The LLM's output is untrusted text rendered as text.

**Explicitly out of scope.** There is no authentication and no multi-tenancy;
any snapshot any user creates is visible to every user. That is a design
position for a public demo of public repositories, not an oversight — but it
is a *blocker* for anything else (§10).

---

## 2. Findings

Twelve findings. Severity is about the public-beta deployment, not about the
code in isolation.

### CRITICAL

**C1 — `/summarize` re-clones with no limits at all.**
`app/api/semantic_routes.py::summarize` → `_root_for()` → `ingest(repo_url)`,
a second full clone of the repository. Every ceiling added in the previous
phase was attached to `POST /analyze` and this route went through none of
them. Snapshot ids are small integers and `GET /repos` lists them.

*Attack:* analyse one repo, then loop `POST /api/repos/1/summarize`. Each call
clones again and issues one LLM call per module against the operator's key,
while `/analyze` politely returns 429. Confirmed with a traceback showing
`ingest` reached while the analyse quota was exhausted.
*Condition:* requires narration configured (`get_llm()` 503s first without a
key) — which the README and compose file actively invite.

### HIGH

**H1 — The repository size limit is enforced after the clone lands.**
`MAX_REPO_SIZE_MB` was checked in `ingestion/__init__.py::snapshot_directory`,
which runs *after* `shallow_clone` returns. A 50 GB repository was downloaded
in full and then refused. The only real bound was `CLONE_TIMEOUT_SECONDS`, so
the true ceiling was "whatever fits in 300s of bandwidth". `DEPLOYING.md`
claimed this setting prevented "the clone is the denial of service"; it did not.

**H2 — One large file is read whole into memory.**
`ingestion/inventory.py::walk_source_files` calls `path.read_bytes()` on every
file to hash and sniff it, with no per-file cap. A single generated 2 GB `.ts`
file inside an otherwise small repository is 2 GB of RSS.

**H3 — No per-job timeout.**
`core/pipeline.py` bounded the clone (300s) and `git log` (60s), but parse and
metrics were unbounded. A pathological repository held its concurrency slot
forever; `MAX_CONCURRENT_ANALYSES` of them bricked the instance permanently
while every request returned a polite 503.

**H4 — No container resource limits.**
`docker-compose.yml` set no `mem_limit`, `cpus` or `pids_limit`, and the root
filesystem was writable. One repository could OOM the host rather than its own
container.

**H5 — No request body limit.**
Starlette buffers a body into memory before any validator runs; neither
FastAPI nor uvicorn caps it. A 200 MB POST to `/api/analyze` was 200 MB of RSS.

**H6 — GitPython 3.1.50 carries 15 known advisories.**
`pip-audit -r backend/requirements.txt`. Directly reachable:
`ingestion/clone.py::read_git_metadata` uses `git.Repo` to read a repository
cloned from an untrusted URL.

### MEDIUM

**M1 — Unbounded concept-index cache.** `semantic_routes._INDEXES` was a plain
dict keyed by snapshot id, holding an index per snapshot for the life of the
process, and the number of snapshots is chosen by strangers.

**M2 — Unbounded request parameters.** `SearchRequest.top` and `.text`,
`SummarizeRequest.max_nodes`, `BlastStoryRequest.max_depth` had no ceilings.
`max_nodes` is one LLM call each against the operator's key.

**M3 — Internal exception text returned to clients.**
`jobs.run_in_background` set `error=str(exc)`, so any internal failure returned
whatever the raising library said — a container path, a database location.

**M4 — No security headers.** No CSP, `X-Content-Type-Options`,
`X-Frame-Options`, or `Referrer-Policy` on the only public origin.

**M5 — `sharp`/`libvips` shipped with four open high-severity CVEs.**
Next's standalone build includes `sharp` and serves `/_next/image` whether or
not the app uses `next/image`.

### LOW

**L1 — `GET /api/repos` lists every analysed repository.** Public repos only,
but it discloses what other users have looked at.

**L2 — `/api/docs` and `/api/redoc` are public.** Not a vulnerability; an
attack aid.

---

## 3. What was verified as already sound

Reported because "we found nothing" is a finding, and because these were the
most likely places to find something.

**Repository URL validation is genuinely strong** (`ingestion/clone.py::normalize_repo_url`).
Probed with 30 hostile inputs — every one rejected, no exceptions:

- schemes: `file://`, `ssh://`, `git://`, `ftp://`, `http://`, `ext::sh -c`, `git::`, scp-style `git@host:path`
- SSRF: `127.0.0.1`, `localhost`, `[::1]`, `10.x`, `192.168.x`, `169.254.169.254`, `metadata.google.internal`, internal DNS names
- host confusion: `github.com.evil.com`, `evil.com/github.com`, `github.com@evil.com`, `user:pass@github.com`
- option injection: `-upload-pack`, `--upload-pack` as path segments
- traversal: `../../../etc`, `%2f..%2f..`

**No repository code is executed.** The entire codebase contains exactly two
subprocess call sites — `git clone` (`ingestion/clone.py`) and
`git rev-parse` / `git log` (`ingestion/git_history.py`). All use fixed argv
lists, none uses `shell=True`, and there is no `os.system`, `eval`, `exec`, or
any invocation of pip, npm, make or a build system. Submodules are never
initialised (`git clone` does not recurse by default). Git hooks are not
transferred by clone, and filter drivers require local config that clone
writes itself.

**Also sound:** SQL is fully parameterised (`graph/store.py`); zip extraction
checks every member against the destination (`ingestion/archive.py`); symlinks
are skipped during inventory; the graph cache is a bounded LRU; local
filesystem paths are refused unless `CODELENS_ALLOW_LOCAL_ANALYSIS=1`.

---

## 4. Findings fixed

| # | Fix | Where |
|---|---|---|
| C1 | All expensive routes share one admission point; `/summarize` now takes an analysis slot | `app/api/admission.py` (new), `semantic_routes.py` |
| H1 | Clone size watched *while git runs*; killed at the ceiling, partial tree removed | `ingestion/clone.py::shallow_clone` |
| H2 | `MAX_FILE_SIZE_MB` (4 MB) skip; `MAX_FILES` (50k) cap, both logged | `ingestion/inventory.py` |
| H3 | `ANALYSIS_TIMEOUT_SECONDS` (900s) marks the job failed and releases the slot | `core/jobs.py::run_in_background` |
| H4 | `read_only`, `cap_drop: ALL`, `no-new-privileges`, mem/cpu/pids limits, `noexec` tmpfs | `docker-compose.yml` |
| H5 | Streaming ASGI body cap (64 KB), counts bytes rather than trusting `Content-Length` | `app/api/body_limit.py` (new) |
| H6 | GitPython 3.1.50 → 3.1.58; `pip-audit` now clean | `requirements.txt` |
| M1 | Bounded LRU of 2, matching the graph cache | `semantic_routes.py` |
| M2 | Ceilings on `top`, `text`, `max_nodes`, `max_depth`, `node_id` | `semantic_routes.py` |
| M3 | Ingestion errors returned verbatim; everything else becomes "job {id} failed", detail to the log | `api/routes.py` |
| M4 | CSP, `nosniff`, `DENY`, `no-referrer`, Permissions-Policy | `frontend/next.config.mjs` |
| M5 | `images: { unoptimized: true }` — `/_next/image` now 404s | `frontend/next.config.mjs` |

Two additional bugs surfaced while testing the fixes and were fixed:

- A `503` for a busy server **consumed the client's rate-limit quota**, so a
  saturated machine spent the quota of everyone who arrived while it was
  saturated. Capacity is now settled before quota.
- `RATE_LIMIT_ANALYSES=0` — a legitimate "analysis is off" setting — indexed
  an empty deque and returned 500 instead of refusing.

---

## 5. Findings intentionally accepted

**L1 — `GET /repos` discloses analysed repositories.** Public GitHub repos
only. Hiding it would mean per-user ownership, which means accounts, which is
§10's blocker rather than a patch.

**L2 — `/api/docs` public.** The API is meant to be usable. Nothing in the
schema is secret.

**Rate limiting keys on a spoofable header.** `X-Forwarded-For`'s left-most
entry is the client behind a proxy and is forgeable by a direct caller. This
is a speed bump for accidents and casual abuse, not an authentication
boundary, and the code says so rather than implying strength it lacks.

**Limits are per process.** One backend, one set of counters; the container
runs a single uvicorn worker for exactly this reason. A second replica needs a
shared store.

**An overrunning job leaks its thread.** Python threads cannot be killed. The
timeout stops the work from *mattering* — the job fails, the slot returns, the
service keeps answering — but the thread burns CPU until it finishes. Bounding
it properly means a killable subprocess. The container CPU limit caps the
blast radius meanwhile.

---

## 6. Tests added

`backend/tests/test_security.py`, 15 tests, no network, no malicious downloads:

- **C1 regression** ×3 — `/summarize` is rate-limited, is refused when
  saturated, and returns its slot
- narration endpoints are metered separately
- **H1** — a fake growing clone proves the watchdog kills git and cleans the
  partial tree (the real loop, including the kill path, under test)
- **H2** — an oversized file is skipped, not truncated; file count is capped
- **H5** — a 200 KB body returns 413
- **M2** — `top`, `text`, `max_nodes` ceilings return 422
- **The no-execution invariant** — a fixture containing `setup.py`,
  `conftest.py`, `sitecustomize.py`, a `Makefile` and `package.json` with
  `preinstall`/`postinstall` hooks, all of which would write a marker file if
  run. The marker never appears, and the harmless file still parses — so the
  test proves the pipeline ran rather than that it did nothing.
- **Durability** — a parser crash frees the slot; an internal failure returns
  a job id rather than its message; an overrunning job releases its slot; a
  missing snapshot is 404

Plus the 12 pre-existing tests in `test_limits.py`. **Total: 307 passing.**

---

## 7. Resource limits, as they now stand

| Layer | Limit | Default | Enforced |
|---|---|---|---|
| Request rate | `RATE_LIMIT_ANALYSES` | 5 / 5 min | `admission.acquire` |
| Narration rate | `RATE_LIMIT_NARRATIONS` | 20 / 5 min | `admission.charge_narration` |
| Concurrency | `MAX_CONCURRENT_ANALYSES` | 2 | `ConcurrencyGate`, refused not queued |
| Request body | `MAX_REQUEST_BODY_BYTES` | 64 KB | streaming ASGI middleware |
| Clone size | `MAX_REPO_SIZE_MB` | 500 MB | **during** the clone |
| Clone time | `CLONE_TIMEOUT_SECONDS` | 300 s | `Popen` + deadline |
| File size | `MAX_FILE_SIZE_MB` | 4 MB | inventory skip |
| File count | `MAX_FILES` | 50,000 | inventory cap |
| History | `HISTORY_DEPTH` | 400 commits | `git clone --depth` |
| Job wall clock | `ANALYSIS_TIMEOUT_SECONDS` | 900 s | job watchdog |
| Disk | `MAX_CLONE_CACHE_MB` | 4 GB | LRU reclaim after each job |
| Job table | `MAX_FINISHED_JOBS` | 200 | eviction on create |
| Memory | `mem_limit` | 2 GB backend | container |
| CPU | `cpus` | 2.0 backend | container |
| Processes | `pids_limit` | 256 backend | container |

---

## 8. Data and secrets

**What is persisted:** the derived graph (nodes, edges, metrics), commit SHAs,
and author *digests* — `graph/ownership.py` hashes the email rather than
storing it. Optional LLM summaries. The clone cache holds working trees of
public repositories and is reclaimed oldest-first.

**Raw source is never stored.** It is read to parse and discarded; only
structure survives into SQLite.

**Secrets:** read from the environment, never logged, never in a response.
`backend/.env` is gitignored and `.dockerignore`d out of the image. Nothing
echoes `settings`. The one caveat: a repository's *own* committed secrets
would appear in the graph as ordinary identifiers — CodeLens analyses public
code, so this is public information, but it is worth knowing.

**Logs** carry stage names, durations, counts and reclaimed clone names. Since
M3, internal exception text goes to the log and a job id goes to the client.

---

## 9. Remaining risks

1. **No authentication or multi-tenancy.** Every snapshot is visible to
   everyone. Anyone can trigger work on the operator's machine and, if
   narration is configured, spend the operator's money up to the quota.
2. **A single process.** Restart drops in-flight jobs; all limits are
   per-process and do not survive horizontal scaling.
3. **Spoofable rate-limit identity** without a trusted proxy in front.
4. **Orphaned threads** after a timeout (§5).
5. **Unbounded parse memory within a repository.** Per-file and total-size
   caps exist, but a repository at exactly the limits with pathological
   structure could still spike RSS. The container `mem_limit` is the backstop:
   the container dies, the host does not.
6. **`sharp` remains in the frontend image** even with the optimiser off. The
   code path is gone; the bytes are not.
7. **The developer venv drifts from `requirements.txt`** — it contained
   `weasyprint`, `chromadb`, `aiohttp` and `pillow`, none of them shipped. CI
   now audits the requirements file specifically, but a local `pip-audit`
   still reports software the image does not contain.

---

## 10. Public-deployment blockers

**Verdict: NOT READY FOR PUBLIC BETA.**

Every finding above is fixed or consciously accepted, and the machine is now
defensible. The blockers are not vulnerabilities — they are missing
*operational* properties that a public beta needs and this system does not
have.

In priority order:

1. **No abuse response.** Rate limiting is per-process and keyed on a
   forgeable header. There is no way to ban a client, no alerting, and no
   dashboard — so the first sign of abuse is a bill or a full disk. **Needs:**
   a trusted proxy (Cloudflare/nginx) terminating TLS and providing a
   real client identity, plus one alert on disk and on analysis failure rate.

2. **No cost ceiling on the LLM key.** If narration is enabled, the quota
   bounds requests per client but nothing bounds *total* spend. **Needs:**
   either a hard provider-side spend cap, or ship the beta with narration
   disabled — the graph and every Ledger number are deterministic and need no
   key, so this costs nothing but prose.

3. **No backup of `codelens-data`.** A volume loss re-analyses everything, and
   nothing currently notices the volume is gone. **Needs:** a snapshot
   schedule and one restore rehearsal.

4. **Untested at any real concurrency.** Every limit is verified functionally
   — the right status code, the slot returned — but the system has never run
   under sustained parallel load. **Needs:** a load test at
   `MAX_CONCURRENT_ANALYSES` saturation for an hour, watching RSS and disk.

5. **The single-process restart hole.** A deploy or crash silently drops
   in-flight analyses; the client polls a job id that no longer exists and
   gets 404. **Needs:** either a durable job store, or the frontend handling
   "job vanished" as a real state instead of an error.

None of these is large. Items 2 and 5 are an afternoon; 1 and 3 are
configuration; 4 is a machine and an hour. **What is not acceptable is
shipping without them and calling the tests a substitute** — 307 passing tests
say the code does what it was asked to do, which is a different claim from
"this is safe to leave on the internet unattended".

---

## Reproducing this audit

```bash
cd backend && .venv/bin/python -m pytest tests/test_security.py tests/test_limits.py -q
cd backend && .venv/bin/pip-audit -r requirements.txt
docker compose up -d --wait && docker inspect codelens-backend-1 \
  --format 'readonly={{.HostConfig.ReadonlyRootfs}} caps={{.HostConfig.CapDrop}} mem={{.HostConfig.Memory}} pids={{.HostConfig.PidsLimit}}'
```
