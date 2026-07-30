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

    # ── Semantic layer (Stage 3) ──────────────────────────────────────────
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
