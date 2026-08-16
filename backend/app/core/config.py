"""CodeLens configuration — deliberately minimal.

ARCHITECTURE.md §"Two corrections": SQLite + NetworkX now; Postgres, Neo4j,
Qdrant and Redis arrive only when real load justifies each one individually
(CHECKPOINTS.md CP-6.1 / CP-9.2).

Nothing here may reference a service that does not exist yet. Config that
boots five databases for zero users is the exact failure mode this project
rejected in STRATEGY.md §7.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    VERSION: str = "2.0"

    # ── Storage (CP-1.4) ──────────────────────────────────────────────────
    # SQLite is persistence, NetworkX is traversal. One file, no daemon.
    # All access goes through the GraphStore interface so the backend can be
    # swapped later without touching a single query.
    SQLITE_PATH: Path = Path("data/codelens.db")

    # ── Ingestion limits (CP-1.1) ─────────────────────────────────────────
    CLONE_DIR: Path = Path("/tmp/codelens")
    MAX_REPO_SIZE_MB: int = 500
    CLONE_TIMEOUT_SECONDS: int = 300
    #: Largest single file the inventory will read. Every file is read whole
    #: into memory to hash and sniff it, so without a ceiling one generated
    #: 2 GB .ts file is 2 GB of resident memory — a repository well under the
    #: total size limit can still be an OOM.
    MAX_FILE_SIZE_MB: int = 4
    #: Most files one repository may contribute. Total size does not bound
    #: this: half a million tiny files fit comfortably under 500 MB and each
    #: one still costs a parse, a hash and a node.
    MAX_FILES: int = 50_000
    #: Wall-clock ceiling on one analysis, clone included. Without it a
    #: pathological repository holds a concurrency slot forever, and two of
    #: them wedge the service permanently.
    ANALYSIS_TIMEOUT_SECONDS: int = 900

    # ── Client identity, for rate limiting ────────────────────────────────
    #: Peers whose `X-Forwarded-For` header may be believed, as IPs or CIDRs.
    #: Empty means believe nobody, which is the safe default: a header is a
    #: claim by whoever sent it, and trusting it unconditionally lets any
    #: caller mint a fresh identity per request and defeat the rate limit
    #: entirely. Only the socket peer is unforgeable, so the header is used
    #: exactly when the peer is a proxy the operator has vouched for.
    #:
    #: Under docker compose the only thing that can reach the backend is the
    #: frontend on the internal network, so the compose file sets this to the
    #: private ranges. Behind a public proxy, set it to that proxy's address.
    TRUSTED_PROXY_IPS: str = ""

    #: Clients refused outright, as IPs or CIDRs. The operational lever for
    #: "this one is abusing us" — no database, no admin UI, just a value the
    #: operator can set and restart. Deliberately small: a real ban system is
    #: the proxy's job, and this exists so the answer to abuse is never
    #: "nothing until we build something".
    BLOCKED_CLIENTS: str = ""

    # ── Serving ───────────────────────────────────────────────────────────
    #: Browser origins allowed to call this API. The default is the local
    #: frontend; a deployment must set its own, and setting `*` alongside
    #: credentialled requests is rejected by browsers anyway.
    #: Comma-separated in the environment: `CORS_ORIGINS=https://a,https://b`.
    CORS_ORIGINS: str = "http://localhost:3000"

    # ── Public-instance limits ────────────────────────────────────────────
    # This API clones whatever repository it is handed. On a machine only its
    # author can reach that is fine; on a public one it is disk, CPU and
    # network on someone else's terms, so every one of these has a ceiling.
    #
    #: Analyze requests one client may start per window. Polling job status
    #: is unmetered — that is the whole point of the async design.
    RATE_LIMIT_ANALYSES: int = 5
    RATE_LIMIT_WINDOW_SECONDS: int = 300
    #: Narrated answers per client per window. These spend the operator's LLM
    #: credit one call at a time and start no expensive work, so they are
    #: metered separately and more generously than analyses — sharing one
    #: quota would let five narrations lock out an analysis.
    RATE_LIMIT_NARRATIONS: int = 20
    #: Pipelines running at once, across all clients. Each one is a clone
    #: plus a full parse; without a cap, ten simultaneous monorepos is a
    #: machine that stops answering its own health check.
    MAX_CONCURRENT_ANALYSES: int = 2
    #: Clone cache ceiling. Old working trees are reclaimed oldest-first once
    #: the directory exceeds this. Nothing else ever deletes them.
    MAX_CLONE_CACHE_MB: int = 4_000
    #: Finished jobs kept for polling. The registry is a dict that only ever
    #: grew; a long-lived process would hold every job it had ever run.
    MAX_FINISHED_JOBS: int = 200
    #: Largest request body accepted. Every endpoint here takes a small JSON
    #: object; anything approaching this is a mistake or an attempt.
    MAX_REQUEST_BODY_BYTES: int = 64 * 1024

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    # ── Semantic layer (Stage 3) ──────────────────────────────────────────
    #: **Narration is off unless switched on.** Everything CodeLens claims —
    #: the graph, blast radius, ranking, risk, cycles, health, evidence and
    #: every number in LEDGER.md — is deterministic and needs no model. Only
    #: the prose *about* those facts costs money, and a key sitting in the
    #: environment should not by itself expose a public endpoint that spends
    #: it. Enabling requires saying so, in one variable, on purpose.
    NARRATION_ENABLED: bool = False
    #: Hard ceiling on model calls for the life of the process, across every
    #: client. The per-client rate limit bounds requests; this bounds the
    #: bill. Restarting resets it, which is the honest scope of an in-process
    #: budget — a durable one needs the provider's own spend cap (DEPLOYING.md).
    NARRATION_MAX_CALLS: int = 500

    # Unset until CP-3.2. Absence must never break the graph pipeline.
    ANTHROPIC_API_KEY: str | None = None

    # ── Provider selection (CP-3.x) ───────────────────────────────────────
    # "auto" picks whichever provider is configured, cheapest-to-start first:
    # ollama (local, free) -> groq (free tier) -> openrouter -> anthropic.
    # Set explicitly to pin one. Everything deterministic ignores all of it.
    LLM_PROVIDER: str = "auto"
    GROQ_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    LLM_MODEL: str | None = None  # override the provider's default model

    @property
    def sqlite_url(self) -> str:
        """SQLAlchemy-style URL, for whenever a driver actually needs one."""
        return f"sqlite:///{self.SQLITE_PATH}"


settings = Settings()
