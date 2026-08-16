# Deploying CodeLens

Everything here has been run except the final step, which needs an account
this repository does not have. Where that is the case it says so, rather than
presenting an untested command as a verified one.

---

## What you are deploying

Two containers on one internal network, one public port.

```
        :3000                    (internal)
browser ──────▶ frontend ───────────────────▶ backend
                Next.js                       FastAPI
                proxies /api                  clones, parses, serves
                                                │
                                    ┌───────────┴───────────┐
                            codelens-data            codelens-clones
                            analysed graphs          clone cache
                            (keep)                   (droppable)
```

**The backend is not published.** The browser only ever talks to the frontend
origin, and `/api/*` is proxied server-side over the compose network. One port
to expose, one thing to firewall, and no CORS grant to keep in sync with a
frontend URL. If you publish the backend anyway, set `CORS_ORIGINS` to your
real frontend origin — never `*`, which browsers reject alongside credentials
regardless.

---

## Before it faces the internet

This service **clones arbitrary repositories on request**. That is the
product, and it is also the entire attack surface. Four ceilings exist for it
(`backend/app/core/limits.py`), and the defaults suit a small public demo:

| variable | default | without it |
|---|---|---|
| `RATE_LIMIT_ANALYSES` / `_WINDOW_SECONDS` | `5` / `300` | one client's loop is the whole machine |
| `MAX_CONCURRENT_ANALYSES` | `2` | ten simultaneous monorepos, no health check answered |
| `MAX_CLONE_CACHE_MB` | `4000` | a full disk, which takes SQLite down with it |
| `MAX_REPO_SIZE_MB` | `500` | the clone is the denial of service — now enforced *during* the clone, not after it |

Three things to know about them before you rely on them:

1. **They are per process.** One backend, one set of counters. A second
   replica gets its own, which halves nothing and doubles everything. This is
   why the container runs a single uvicorn worker and why scaling out needs a
   shared store first.
2. **The rate limiter keys on `X-Forwarded-For`, which a direct caller can
   forge.** It is a speed bump for accidents and casual abuse, not an
   authentication boundary. Put a real proxy in front if you need one.
3. **`CODELENS_ALLOW_LOCAL_ANALYSIS` must stay unset.** It lets the API read
   local filesystem paths. It is a dogfooding switch and is off by default;
   setting it on a public instance hands the filesystem to whoever asks.

There is no authentication. A public instance is a public instance.

### Client identity — set this or the rate limit is one global bucket

The limiter counts against an identity, and the only unforgeable one is the
socket peer. A forwarded header is believed **exactly** when it arrives from a
proxy you have vouched for:

```
TRUSTED_PROXY_IPS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16   # compose default
```

Get this wrong in either direction and something breaks quietly:

- **Too narrow** (empty, behind a proxy): every browser shares the proxy's
  address, so the whole internet is one bucket and the first five analyses
  lock out everyone.
- **Too wide** (`0.0.0.0/0` on a public port): any caller can write their own
  `X-Forwarded-For` and mint a fresh quota per request. The limit becomes
  decorative.

Behind Cloudflare or nginx, narrow it to that proxy's addresses.

### Blocking a client

```
BLOCKED_CLIENTS=203.0.113.7,198.51.100.0/24
```

Restart to apply. Matched against the peer *and* the resolved identity, so a
blocked address cannot escape by forging a header. This is deliberately the
smallest possible lever — a real ban system belongs in the proxy, and this
exists so the answer to abuse is never "nothing until we build something".

### Watching for abuse

Every refusal is one structured line on the backend logger:

```
request_refused reason=rate_limit identity=203.0.113.7 peer=172.18.0.3 path=/api/analyze method=POST
request_blocked peer=203.0.113.7 identity=203.0.113.7 path=/api/analyze
untrusted_forwarded_header peer=203.0.113.7 header_present=true
```

No bodies, no repository contents, no secrets — only who, what and why.

```bash
docker compose logs backend | grep -c request_refused          # is anyone hitting limits
docker compose logs backend | grep request_refused | awk '{print $3}' | sort | uniq -c | sort -rn
```

**What remains deployment configuration:** alerting on those counts, and an
actual ban at the edge. Neither belongs in this application — a WAF and a log
drain do both better — and neither is present here.

---

## Narration (LLM) — off by default

`NARRATION_ENABLED` defaults to **false**, and the container sets it
explicitly. Everything CodeLens claims is deterministic: the graph, blast
radius, ranking, risk, cycles, health, evidence, and every number in
`LEDGER.md`. Only the prose *about* those facts costs money.

With it off, the narration endpoints return `503` with a readable reason and
nothing else changes. The UI never calls them.

### Enabling it safely

1. Set a **hard spend cap at the provider** — Groq, OpenRouter and Anthropic
   all offer one. This is the only durable ceiling; everything below is a
   process-local approximation that a restart resets.
2. Then:

```yaml
NARRATION_ENABLED: "true"
NARRATION_MAX_CALLS: 500     # model calls for the life of the process
RATE_LIMIT_NARRATIONS: 20    # per client per window
GROQ_API_KEY: ${GROQ_API_KEY}
```

3. Watch `narration_budget_exhausted` in the logs. It fires once, when the
   process budget is gone, and narration 503s until a restart.

The key is read from the environment, is never sent to the frontend, and is
never logged. Do not bake it into an image.

---

## Backup and restore

### What is in the volumes

| volume | contents | backed up |
|---|---|---|
| `codelens-data` | one SQLite file: snapshots, nodes, edges, annotations, the paid-for summary cache, and job state | **yes** |
| `codelens-clones` | working trees of public repositories | **no** — every byte is re-fetchable from GitHub, and a stale tree restores worse than none |

The database contains no credentials. Author identities are stored as digests
(`backend/app/graph/ownership.py`), and raw source is never persisted — only
the structure derived from it.

### Backing up

```bash
ops/backup.sh                 # writes ./backups/codelens-<UTC timestamp>.db
```

Uses SQLite's own `.backup`, not `cp`: the database is being written while the
command runs, and copying the file mid-transaction restores as "database disk
image is malformed" at the worst possible moment. The result is verified with
`PRAGMA integrity_check` on both sides before the script calls it a backup.
Output is `chmod 600` in a `chmod 700` directory — a backup is a full copy of
every analysed repository's structure.

### Restoring

```bash
ops/restore.sh backups/codelens-<stamp>.db --verify-only   # throwaway container
ops/restore.sh backups/codelens-<stamp>.db                 # into the live stack
```

**Run `--verify-only` after every schema change.** It restores into a clean
disposable volume and asks a real CodeLens process to load a graph and run a
query against it — a backup nobody has restored is a hypothesis. The live path
stops the stack first, because restoring underneath an open SQLite handle
produces corruption that looks like a CodeLens bug.

### Scheduling it

```
0 3 * * *  cd /srv/codelens && ops/backup.sh /var/backups/codelens
```

**Not configured here.** Off-host copies (S3, B2, restic) need credentials
this repository does not have. A backup that lives only on the machine it
backs up is not a backup — that step is yours, and until it is done, item 3
in `SECURITY.md` remains open.

---

## Load testing

```bash
docker compose up -d --wait
python3 ops/loadtest.py --burst 8
```

Pushes slightly past the configured limits with real repositories and reports
successes, 429/503 behaviour, admission latency, container memory and CPU,
clone-cache growth, job-table size, and whether the service recovers. Standard
library plus the `docker` CLI; nothing to install. Results from the last run
are in `SECURITY.md`.

**Before a public beta, read [SECURITY.md](SECURITY.md).** It audits this
deployment against a hostile user and lists five operational blockers that
none of the limits above address.

---

## Deploy

### Anywhere that runs compose

A VM with Docker is the whole requirement.

```bash
git clone <this repo> && cd CODELENS
docker compose up -d --build --wait
```

Put TLS in front of `:3000` — Caddy or nginx, whichever you already run. The
frontend expects to be the origin the browser sees, so terminate TLS and
proxy to it rather than rewriting paths.

To update:

```bash
git pull && docker compose up -d --build --wait
```

The named volumes survive it. `codelens-data` holds the analysed graphs;
losing it means re-analysing everything, which is slow but not lossy.
`codelens-clones` is a cache and can be dropped at any time.

### Platforms that build from a Dockerfile

Fly.io, Render, Railway, Cloud Run and similar can each build these two images
directly. Two requirements that are easy to miss and produce confusing
failures rather than clean ones:

- **A persistent disk mounted at `/data`.** Every one of these platforms has
  an ephemeral filesystem by default. Without a volume the database is fine
  until the first restart, then silently empty — and "silently empty" looks
  like the analysis failing rather than the storage vanishing.
- **`CODELENS_API_URL` at *build* time for the frontend.** Next resolves
  rewrite destinations when the config loads during the build, so setting it
  as a runtime environment variable is read too late and the image keeps the
  localhost default. Pass it as a build argument.

*Not yet run against any of these platforms.* The two constraints above are
read off the code, not off a deployment.

### Sizing

itsdangerous (50 files) analyses in ~11s, most of it git history. FastAPI
(1,140 files) takes ~15s. Memory scales with the graph — FastAPI's is 8,254
nodes and 13,890 edges — so 1 GB is comfortable for library-sized
repositories and a large monorepo wants 2 GB. Disk is `MAX_CLONE_CACHE_MB`
plus the database.

---

## After it is up

```bash
curl -sf https://your-host/api/../health   # via the backend, if published
docker compose ps                          # both must read (healthy)
docker compose logs -f backend
```

Health is a real signal here, not a formality: the frontend's healthcheck
caught a bind-address bug that published ports hid completely, and the
backend's is what `depends_on: service_healthy` and `--wait` gate on.

**If analysis fails but the service is up**, the first suspect is `git`. It is
a runtime dependency of the backend image; an image missing it starts
cleanly, serves `/health`, and fails on the first clone with an error that
reads like a network problem.

---

## What this deployment is not

- **Not multi-worker.** One process, by design, until the limits and the job
  registry live in a shared store.
- **Not authenticated.** No accounts, no tokens, no per-user isolation.
- **Not durable across restarts for in-flight jobs.** The job registry is in
  memory; a restart drops running analyses. Finished graphs are safe in
  SQLite. `backend/app/core/jobs.py` states this rather than hiding it.
