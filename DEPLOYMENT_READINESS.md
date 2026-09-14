# CodeLens Final Deployment Readiness Report

**Date:** 2026-08-16 · **Scope:** the five operational gaps `SECURITY.md` left open.
**Verdict: READY FOR STAGING.** Not public beta — one prerequisite is
infrastructure this repository cannot provide. §8 and §9.

The security audit closed twelve findings and then stopped at five properties
that were *documented* rather than *true*. All five are now implemented and
exercised against the running containers. Nothing in the product experience
changed: no UI, no graph behaviour, no ranking, no analysis semantics.

---

## 1. Abuse response

### What was changed

**Client identity is no longer forgeable** (`backend/app/core/clients.py`).
The limiter counted against `X-Forwarded-For`'s left-most entry, taken on
faith — so a caller could send a different value per request, mint an
unlimited number of fresh quotas, and reduce the rate limit to decoration.
The rule now:

```
no trusted proxies      -> socket peer, always
peer is trusted         -> left-most X-Forwarded-For entry
peer is not trusted     -> socket peer, and the header is logged
```

`TRUSTED_PROXY_IPS` is empty by default (believe nobody) and set to the
private ranges in `docker-compose.yml`, where the only thing that can reach
the backend is the frontend on the internal network.

**A blocklist** (`BLOCKED_CLIENTS`, IPs or CIDRs) matched against the peer
*and* the resolved identity, so a blocked address cannot escape by forging a
header. Verified in a real container: `403 {"detail":"Blocked."}` both ways.

**Structured refusal logging.** One line per refusal, carrying who, what and
why — no bodies, no repository contents, no secrets:

```
request_refused reason=rate_limit identity=203.0.113.7 peer=172.18.0.3 path=/api/analyze method=POST
request_blocked peer=198.51.100.9 identity=198.51.100.9 path=/api/analyze
untrusted_forwarded_header peer=198.51.100.9 header_present=true
```

No new dependencies (`ipaddress` is stdlib). No authentication system. The API
is unchanged.

### What remains deployment configuration

- **A trusted proxy terminating TLS**, and `TRUSTED_PROXY_IPS` narrowed to it.
  Getting this wrong is quiet in both directions: too narrow and the whole
  internet shares one bucket, too wide and the header is forgeable again.
- **Alerting** on `request_refused` counts. A log line nobody reads is not a
  response. This belongs in a log drain, not in the application.
- **Edge banning** (WAF / Cloudflare rules). `BLOCKED_CLIENTS` is the smallest
  possible lever so the answer to abuse is never "nothing until we build
  something" — it is not a ban system.

---

## 2. LLM cost control

### Default state: **OFF**

`NARRATION_ENABLED` defaults to `false` and `docker-compose.yml` sets it
explicitly. Verified live against the running stack:

```
POST answers/project  -> 503 "Narration is disabled on this instance…"
POST summarize        -> 503 "Narration is disabled on this instance…"

GET  viewspec?zoom=2       -> 200
GET  answers/learning_path -> 200
POST query/risk            -> 200
POST search                -> 200
```

The graph, blast radius, ranking, risk, cycles, health, evidence, search,
learning path and every number in `LEDGER.md` are deterministic and unaffected.
**The frontend calls no narration endpoint at all**, so the gate has zero UI
impact — confirmed by reading every `fetch` in `frontend/lib/api.ts`.

No narration code was removed. A pinned test
(`test_deterministic_answers_do_not_need_narration`) fails if the gate ever
starts removing product rather than removing spend.

### Enabling it safely later

1. **Set a hard spend cap at the provider first.** That is the only durable
   ceiling; everything in-process resets on restart.
2. `NARRATION_ENABLED=true`, plus `NARRATION_MAX_CALLS` (default 500, model
   calls for the life of the process, across all clients) and
   `RATE_LIMIT_NARRATIONS` (default 20 per client per window).
3. Watch for `narration_budget_exhausted` in the logs.

Keys are read from the environment, never sent to the frontend, never logged,
and `.dockerignore`d out of the image. Full procedure in `DEPLOYING.md`.

---

## 3. Backup and restore

### What is backed up

| volume | contents | backed up |
|---|---|---|
| `codelens-data` | one SQLite file: `snapshots`, `nodes`, `edges`, `annotations`, `summary_cache` (paid-for LLM output), `jobs` | **yes** |
| `codelens-clones` | working trees of public repositories | **no** — re-fetchable from GitHub, much larger, and a stale tree restores worse than none |

No credentials are in the database. Author identities are digests
(`graph/ownership.py`); raw source is never persisted, only derived structure.

### Commands

```bash
ops/backup.sh                                    # ./backups/codelens-<UTC>.db
ops/restore.sh backups/codelens-<stamp>.db --verify-only   # throwaway container
ops/restore.sh backups/codelens-<stamp>.db                 # into the live stack
```

`sqlite3 .backup`, not `cp`: the database is written while the command runs,
and a mid-transaction copy restores as "database disk image is malformed".
`PRAGMA integrity_check` runs on both sides. Output is `0600` inside a `0700`
directory; `backups/` is gitignored.

### Actual restore verification

Performed, not described:

```
▸ checking the archive before trusting it
   archive is a valid CodeLens database
▸ restoring into a throwaway volume — live data untouched
▸ asking a real CodeLens process to read it
   2 snapshot(s) in the restored database
   loaded 'https://github.com/pallets/itsdangerous': 159 nodes, 418 edges
   blast_radius on function:src.itsdangerous.encoding.want_bytes: 46 ranked
✓ restore verified: a real CodeLens process read and queried it
```

The query deliberately targets a node that *has* dependents. An earlier
version picked an arbitrary file, got a leaf, and printed "0 ranked" — which
is indistinguishable from a restore that silently lost every edge.

---

## 4. Load test

`ops/loadtest.py` — standard library plus the `docker` CLI. Not a stress test:
the question is whether the configured limits behave as designed and the
service recovers, which is answered by pushing slightly past them with real
repositories.

### Configuration under test (read from the running container)

```
RATE_LIMIT_ANALYSES 5   WINDOW 300s   MAX_CONCURRENT_ANALYSES 2
ANALYSIS_TIMEOUT 900s   MAX_REPO_SIZE_MB 500   MAX_FILES 50000
MAX_FILE_SIZE_MB 4      MAX_CLONE_CACHE_MB 4000   MAX_FINISHED_JOBS 200
NARRATION_ENABLED False
```

### Results

| measure | result |
|---|---|
| burst of 6 simultaneous distinct repos | 2 × 202, 4 × 503, 0 × 429 |
| admission latency | min 24ms, median 29ms, max 35ms |
| service responsive under load | yes — `/api/queries` 200 in 11ms |
| peak memory | 72.9 MiB / 2 GiB (**3.6%** of the cap) |
| peak CPU | 36.9% of 2.0 cores |
| PIDs | 33 / 256 |
| accepted analyses completed | 2/2 |
| clone cache | 2 → 3 MB (ceiling 4000) |
| job rows | 3 (cap 200) |
| quota phase | `[202, 202, 429]` — "5 analyses per 5 minutes. Try again in 259s." |
| recovery | `/api/repos` 200, service usable |

**The most useful result is the one that needed a second phase.** The burst
produced zero 429s, which looked like a broken rate limit and is not: capacity
is settled before quota, so the four 503s never reached the limiter and
correctly spent no quota. But that means the burst alone never *observes* the
rate limit — so a sequential quota phase was added, and it is now part of the
pass/fail condition. A load test that cannot fail is not a test.

Nothing was tuned to make these numbers look better.

---

## 5. Restart durability

### Before

The registry was in memory. A restart — deploy, crash, OOM, orchestrator move
— dropped every in-flight job, and the browser polling `/analyze/{id}` got
**404**. "No such job" is indistinguishable from a typo, so the honest answer
did not exist.

### After

`backend/app/core/job_store.py`: one table, in the database the service
already has, in the volume that is already backed up. No queue, no broker, no
second service. A new `interrupted` status, distinct from `error` because
nothing went wrong with the analysis.

**Interrupted, not requeued.** Anything left `pending` or `running` at startup
is marked `interrupted` — nothing else can be true, since the thread died with
the process. Auto-restarting would guess at intent, cost a clone and a parse,
and turn a crash loop into a clone loop.

Persistence is **best-effort by design**: every write swallows `sqlite3.Error`
and logs. A read-only or full disk degrades to today's behaviour rather than
taking analysis down — pinned by
`test_a_persistence_failure_never_fails_an_analysis`.

### Verified with a real `docker kill` mid-analysis

```
job: be28a4eed4c44b679c8919c63b83a7a7
status before the kill: running
--- killing the backend container mid-analysis ---
--- backend restarted ---
status:  interrupted
error:   "Interrupted by a server restart. Please run the analysis again."
stages:  [cloned: 264 files · Python, 2.393s]     <- partial progress preserved

=== and the service still works afterwards ===
OK https://github.com/pallets/itsdangerous 159 nodes
```

The frontend handles the new status explicitly (`frontend/lib/api.ts`).
Without that branch the poll loop would spin forever on a job that can never
progress.

---

## 6. Complete test results

| gate | result |
|---|---|
| backend tests | **323 passed**, 1 skipped — **5 consecutive clean runs** |
| security regression tests | 15 passed (`test_security.py`) |
| operational regression tests | 14 passed (`test_operations.py`, new) |
| ruff | All checks passed |
| mypy | no issues in 66 source files |
| pip-audit (`requirements.txt`) | No known vulnerabilities |
| tsc | no type errors |
| `next build` | succeeded |
| Docker build | both images |
| Compose startup | both healthy |
| real repository analysis | 159 nodes, 418 edges through the hardened proxy |
| load test | limits behaved, service recovered |
| backup | 444K, integrity_check ok both sides |
| restore | verified in a clean container, query ran |
| restart during analysis | `interrupted` + message, service usable after |
| rate limit | `[202, 202, 429]` with Retry-After |
| concurrency | 4 × 503 at cap 2, no quota consumed |
| narration disabled | 503 on both endpoints, all deterministic surfaces 200 |
| `verify_system.py` | 23/23 |

**A flake was found and fixed while producing this.** The parser-crash test
asserted `gate.active == 0` the instant the job status flipped to `error`, but
the worker sets the status and *then* releases the slot in its `finally` —
about a 50% failure rate. The test now waits for the property it asserts. The
code was correct; the test assumed an atomicity it never claimed. I would not
have found this from one run, which is why five were done.

---

## 7. Remaining risks

1. **Limits are per process.** One backend, one set of counters; the container
   runs a single uvicorn worker for exactly this reason. Scaling out needs a
   shared store first.
2. **The narration budget resets on restart.** A crash loop with narration
   enabled could spend more than `NARRATION_MAX_CALLS` in aggregate. The
   provider-side cap is the real ceiling — hence prerequisite 2 in §8.
3. **Orphaned threads after a timeout.** Python threads cannot be killed; the
   timeout releases the slot and fails the job, but the thread runs until it
   finishes. The container CPU cap bounds the damage.
4. **Backups are local-only.** `ops/backup.sh` writes beside the machine it
   backs up. Off-host copying needs credentials this repository does not have.
5. **Load tested at small scale.** 6 concurrent requests against library-sized
   repositories on one laptop. Memory peaked at 3.6% of the cap, which is
   reassuring and is *not* evidence about a monorepo under sustained load.
6. **Rate-limit identity is only as good as the proxy.** With
   `TRUSTED_PROXY_IPS` set too wide on a public port, the header is forgeable
   again.
7. **No authentication or multi-tenancy.** Unchanged and by design: every
   snapshot is visible to everyone.

---

## 8. Exact deployment prerequisites

1. **A reverse proxy terminating TLS**, with `TRUSTED_PROXY_IPS` narrowed to
   its addresses and `CORS_ORIGINS` set to the real frontend origin.
2. **A provider-side spend cap** *before* `NARRATION_ENABLED=true`. Leaving
   narration off requires nothing.
3. **Off-host backups.** Schedule `ops/backup.sh`, copy the output somewhere
   else, and run `ops/restore.sh --verify-only` against a real archive once.
4. **A log drain with an alert** on `request_refused` volume and on disk usage
   of the `codelens-data` volume.
5. **`CODELENS_ALLOW_LOCAL_ANALYSIS` unset.** It is off by default; setting it
   on a public instance hands the filesystem to whoever asks.

---

## 9. Final verdict

# READY FOR STAGING

All five operational properties are implemented and **tested against the
running containers**, not theorised. Deploy behind a proxy, with narration
off, and this is safe to run.

**Not public beta**, for one reason that is not a code problem: **prerequisite
3 is unmet.** Backups exist and restoration is genuinely verified, but every
backup currently lives on the machine it backs up. A backup that shares a disk
with its source is not a backup, and "we have backups" would be a false claim
until one has been copied off-host and restored from there. That needs storage
credentials this repository does not have.

Do that, put a proxy in front, and the verdict is public beta.

### Assumptions that could not be verified

- ~~**Behaviour behind a real reverse proxy.**~~ **Now verified.** The full
  production stack (Caddy → Next → backend) was booted from
  `docker-compose.prod.yml` and identity resolution was read out of the
  backend's own log under load:
  `request_refused reason=capacity identity=172.18.0.1 peer=172.18.0.4` —
  the peer is the frontend container, the identity is the real client. The
  header survives both hops and the backend believes the right half of it.
  A public deployment with Let's Encrypt issuance is still unrun (it needs
  real DNS).
- **Off-host backup and restore.** The local path is verified end to end. S3,
  B2 and restic are documented and unrun.
- **Sustained multi-hour load.** The load test is a burst plus a quota phase,
  minutes not hours. Slow leaks would not appear.
- **A monorepo at the limits.** Tested with library-sized repositories
  (50–1,140 files). `MAX_FILES=50000` and `MAX_REPO_SIZE_MB=500` have not been
  approached from below by a real repository.
- **The narration path with a live provider.** Every narration test uses a
  fake client. The gate, the budget and the 503s are verified; a real Groq or
  Anthropic call was never made in this pass.
- **OOM behaviour.** `mem_limit` is set and verified as configuration; no
  container was actually pushed to 2 GB to watch it die.
