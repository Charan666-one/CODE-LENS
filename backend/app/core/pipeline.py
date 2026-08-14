"""The checkpointed pipeline — ARCHITECTURE.md's "pipeline with checkpoints".

Sequential, resumable stages, each reporting completion:

    cloned -> parsed -> metrics -> graph_built           (CP-1.4 + CP-1.5)
    ... -> embeddings -> summaries -> ready              (Stage 3)

Two properties are load-bearing:

* **Idempotent and hash-keyed.** The commit sha is the key. If the store
  already holds a graph for (repo_url, commit_sha), the run skips straight to
  done — re-analysis costs nothing when nothing changed (Constitution 4).
* **Observable.** Every stage completion calls the progress callback. This is
  the single source EXPERIENCE.md's "Understanding…" UI reads from — the
  progress theater and the engineering telemetry are the same events, so the
  UI cannot lie about progress (honest theater).
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app.graph.co_change import co_change_edges
from app.graph.schema import KnowledgeGraph, NodeKind
from app.graph.store import GraphStore
from app.ingestion import IngestedRepo, ingest
from app.ingestion.git_history import apply_history, histories_from_commits, read_log
from app.parser import PARSED_EXTENSIONS, parse_ingested


class Stage(str, Enum):
    CLONED = "cloned"
    PARSED = "parsed"
    METRICS = "metrics"  # git-lite temporal pass (CP-1.5)
    GRAPH_BUILT = "graph_built"


#: Called after every stage: (stage, seconds_taken, skipped)
ProgressCallback = Callable[[Stage, float, bool], None]


@dataclass
class PipelineResult:
    """What a run produced, and what it cost."""

    graph: KnowledgeGraph
    snapshot_id: int
    skipped: bool  # True when the hash key made the whole run a no-op
    stages: list[tuple[Stage, float, bool]] = field(default_factory=list)


def run_pipeline(
    source: str | Path,
    store: GraphStore,
    *,
    workdir: Path | None = None,
    max_size_mb: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> PipelineResult:
    """Run source -> snapshot -> facts -> stored graph, skipping paid-for work."""
    stages: list[tuple[Stage, float, bool]] = []

    def report(stage: Stage, seconds: float, skipped: bool) -> None:
        stages.append((stage, seconds, skipped))
        if on_progress is not None:
            on_progress(stage, seconds, skipped)

    # Stage 1 — cloned. Always runs: acquiring the source is what tells us the
    # commit sha, and the sha is what everything after keys on.
    started = time.monotonic()
    ingested: IngestedRepo = ingest(source, workdir=workdir, max_size_mb=max_size_mb)
    report(Stage.CLONED, time.monotonic() - started, False)

    # The skip check — is this exact repository state already analysed? The
    # commit sha finds the candidate row, but the *content digest* decides:
    # a local working tree can be dirty (HEAD unchanged, files edited), and a
    # tree outside git has no sha at all ("unknown"). Matching file hashes
    # prove identical parser input either way. Content is the truth; the sha
    # is only the lookup key.
    snapshot = ingested.snapshot
    existing_id = store.find_snapshot(snapshot.repo_url, snapshot.commit_sha)

    if existing_id is not None:
        graph = store.load_graph(snapshot.repo_url, snapshot.commit_sha)
        assert graph is not None  # find_snapshot just said it exists
        if _stored_digest(graph) == _inventory_digest(ingested):
            report(Stage.PARSED, 0.0, True)
            report(Stage.METRICS, 0.0, True)
            report(Stage.GRAPH_BUILT, 0.0, True)
            return PipelineResult(
                graph=graph, snapshot_id=existing_id, skipped=True, stages=stages
            )

    # Stage 2 — parsed.
    started = time.monotonic()
    graph = parse_ingested(ingested)
    report(Stage.PARSED, time.monotonic() - started, False)

    # Stage 3 — metrics: the git-lite temporal pass (CP-1.5). Fact source is
    # git history, so it lives outside the parser (which only reads the AST).
    # One log read, two derived facts: per-file churn, and the co-change
    # coupling that only whole-commit file sets can reveal.
    started = time.monotonic()
    commits = read_log(ingested.root)
    apply_history(graph.nodes, histories_from_commits(commits))
    coupling, _ = co_change_edges(commits, graph.nodes)
    graph.edges.extend(coupling)
    report(Stage.METRICS, time.monotonic() - started, False)

    # Stage 4 — graph_built (persisted; a graph that only lives in RAM isn't built).
    started = time.monotonic()
    snapshot_id = store.save_graph(graph)
    report(Stage.GRAPH_BUILT, time.monotonic() - started, False)

    return PipelineResult(graph=graph, snapshot_id=snapshot_id, skipped=False, stages=stages)


def _inventory_digest(ingested: IngestedRepo) -> str:
    """Digest of exactly what the parser would read, in every language it
    speaks. Keyed to PARSED_EXTENSIONS so adding a language automatically
    invalidates stale graphs that predate it."""
    lines = sorted(
        f"{f.path}:{f.content_hash}"
        for f in ingested.files
        if f.extension in PARSED_EXTENSIONS
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _stored_digest(graph: KnowledgeGraph) -> str:
    """The same digest, recomputed from a stored graph's file nodes."""
    lines = sorted(
        f"{node.file_path}:{node.content_hash}"
        for node in graph.nodes
        if node.kind is NodeKind.FILE and node.file_path and node.content_hash
    )
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()
