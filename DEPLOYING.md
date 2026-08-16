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
