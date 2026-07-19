"""CodeLens Knowledge Graph schema — the frozen contract.

This is the universal, language-agnostic schema every parser emits into and
every query reads from (FOUNDATION.md §Q1, ARCHITECTURE.md §3).

Rules enforced here:
- Facts and annotations never mix: parser-emitted fields are deterministic;
  AI-derived fields live in `SemanticAnnotation` and always carry
  `derived_from` evidence pointers.
- Every node keys to a content hash for incremental re-analysis.
- CALLS edges carry a confidence level — the graph is honest about its
  own certainty.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


# ── Enums ────────────────────────────────────────────────────────────────


class NodeKind(str, Enum):
    REPOSITORY = "repository"
    MODULE = "module"          # directory / logical grouping — the unit humans reason about
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"      # includes methods
    ENDPOINT = "endpoint"      # HTTP route / CLI command / event handler
    # Layer B/C (post-MVP, enumerated now so the schema never breaks):
    EXTERNAL_DEPENDENCY = "external_dependency"
    EXTERNAL_SERVICE = "external_service"
    CONFIG_VALUE = "config_value"
    AUTHOR = "author"


class EdgeKind(str, Enum):
    # Layer A — structural (MVP)
    CONTAINS = "contains"
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    # Layer A — structural (post-MVP)
    IMPLEMENTS = "implements"
    INSTANTIATES = "instantiates"
    ROUTES_TO = "routes_to"
    TESTS = "tests"
    # Layer B — external (post-MVP)
    DEPENDS_ON = "depends_on"
    TALKS_TO = "talks_to"
    READS_CONFIG = "reads_config"
    # Layer C — temporal (post-MVP)
    CO_CHANGES = "co_changes"
    AUTHORED_BY = "authored_by"


class CallConfidence(str, Enum):
    RESOLVED = "resolved"            # definition found via static resolution
    HEURISTIC = "heuristic"          # name/arity match, not fully proven
    DYNAMIC_UNKNOWN = "dynamic_unknown"  # dynamic dispatch — target uncertain


class EntrypointKind(str, Enum):
    MAIN = "main"              # __main__ guard / main()
    HTTP_ROUTE = "http_route"  # framework route handler
    CLI = "cli"                # CLI command registration
    SCRIPT = "script"          # package.json script target etc.


# ── Nodes ────────────────────────────────────────────────────────────────


class Node(BaseModel):
    """Deterministic fact node. Everything here comes from AST/git/config."""

    id: str                          # stable: f"{kind}:{qualified_name}"
    kind: NodeKind
    name: str
    qualified_name: str              # e.g. "app.services.auth.login"
    file_path: str | None = None     # repo-relative; None for Repository/Module
    start_line: int | None = None
    end_line: int | None = None
    language: str | None = None
    content_hash: str | None = None  # sha256 of source; incremental-update key

    # Metrics (deterministic, parser/git-computed)
    loc: int | None = None
    complexity: int | None = None    # cyclomatic (functions)
    docstring: str | None = None
    is_entrypoint: bool = False
    entrypoint_kind: EntrypointKind | None = None

    # Temporal layer (git-lite; None until Layer C runs)
    churn_count: int | None = None
    author_count: int | None = None
    last_modified: str | None = None  # ISO date

    extra: dict = Field(default_factory=dict)  # language-specific facts (decorators, params…)


# ── Edges ────────────────────────────────────────────────────────────────


class Edge(BaseModel):
    """Deterministic fact edge between two nodes."""

    source_id: str
    target_id: str
    kind: EdgeKind
    # Where in the code this relationship is asserted (evidence):
    file_path: str | None = None
    line: int | None = None
    # Only meaningful for CALLS (and future dynamic edges):
    confidence: CallConfidence = CallConfidence.RESOLVED
    # Only meaningful for CO_CHANGES (co-change strength) — post-MVP:
    weight: float | None = None


# ── Semantic layer (derived — never mixed with facts) ────────────────────


class SemanticAnnotation(BaseModel):
    """AI-derived annotation. MUST point back at the facts it's derived from."""

    node_id: str
    summary: str                       # one-sentence plain-English story
    derived_from: list[str]            # node ids used as context — evidence pointers
    content_hash: str                  # hash of source at generation time (cache key)
    model: str                         # which LLM produced it
    generated_at: str                  # ISO timestamp


# ── Snapshot (pipeline unit of work) ─────────────────────────────────────


class RepoSnapshot(BaseModel):
    """Output of ingestion; input to the parser. One analyzed state of a repo."""

    repo_url: str
    commit_sha: str
    primary_language: str
    languages: dict[str, int]          # extension census: {"py": 120, "ts": 40}
    file_count: int
    total_loc: int | None = None
    analyzed_at: str                   # ISO timestamp
    schema_version: str = SCHEMA_VERSION


class KnowledgeGraph(BaseModel):
    """A complete parsed graph for one snapshot. The product, as data."""

    snapshot: RepoSnapshot
    nodes: list[Node]
    edges: list[Edge]
    annotations: list[SemanticAnnotation] = Field(default_factory=list)
