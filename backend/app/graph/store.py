"""GraphStore — the one door to persisted graphs.

ARCHITECTURE.md §3: "all graph access goes through a GraphStore interface so
the backend can swap without touching queries." SQLite is today's backend;
Neo4j is CP-9.2's problem, and when it arrives it implements this same
interface and nothing above it changes.

Storage model: one row per node/edge/annotation, keyed by snapshot. The full
pydantic payload is stored as JSON alongside the columns queries filter on —
the schema can grow fields without a migration, while lookups stay indexed.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

from app.graph.schema import (
    Edge,
    KnowledgeGraph,
    Node,
    RepoSnapshot,
    SemanticAnnotation,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_url    TEXT NOT NULL,
    commit_sha  TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    data        TEXT NOT NULL,
    UNIQUE (repo_url, commit_sha)
);

CREATE TABLE IF NOT EXISTS nodes (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    id          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    data        TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, id)
);

CREATE TABLE IF NOT EXISTS edges (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    data        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges (snapshot_id, source_id, kind);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (snapshot_id, target_id, kind);
CREATE INDEX IF NOT EXISTS idx_nodes_kind   ON nodes (snapshot_id, kind);

CREATE TABLE IF NOT EXISTS annotations (
    snapshot_id  INTEGER NOT NULL REFERENCES snapshots(snapshot_id) ON DELETE CASCADE,
    node_id      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    data         TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, node_id)
);

-- The summary cache is deliberately OUTSIDE the snapshot cascade: a summary
-- belongs to a content hash, not to an analysis run. Re-analysing a repo (or
-- deleting a snapshot) must not throw away paid-for LLM output that is still
-- valid for every unchanged definition.
CREATE TABLE IF NOT EXISTS summary_cache (
    content_hash TEXT PRIMARY KEY,
    data         TEXT NOT NULL
);
"""


class GraphStore(ABC):
    """What every storage backend must provide."""

    @abstractmethod
    def save_graph(self, graph: KnowledgeGraph) -> int:
        """Persist a graph, returning its snapshot id. Idempotent per
        (repo_url, commit_sha): saving the same snapshot twice replaces it."""

    @abstractmethod
    def load_graph(self, repo_url: str, commit_sha: str | None = None) -> KnowledgeGraph | None:
        """Load the latest (or a specific) snapshot of a repository."""

    @abstractmethod
    def find_snapshot(self, repo_url: str, commit_sha: str) -> int | None:
        """Snapshot id if this exact state is already stored — the skip check."""

    @abstractmethod
    def save_annotations(self, snapshot_id: int, annotations: list[SemanticAnnotation]) -> None:
        """Attach semantic annotations to a stored snapshot."""

    @abstractmethod
    def load_annotations(self, snapshot_id: int) -> list[SemanticAnnotation]:
        """All annotations for a snapshot."""

    @abstractmethod
    def cached_summary(self, content_hash: str) -> SemanticAnnotation | None:
        """A previously generated summary for this exact source text, if any."""

    @abstractmethod
    def cache_summary(self, annotation: SemanticAnnotation) -> None:
        """Remember a summary by content hash, across snapshots and repos."""


class SQLiteGraphStore(GraphStore):
    """The MVP backend: one file, no daemon, stdlib driver."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent and str(self.path.parent) not in (".", ""):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(_SCHEMA)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteGraphStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── graphs ────────────────────────────────────────────────────────────

    def save_graph(self, graph: KnowledgeGraph) -> int:
        snapshot = graph.snapshot
        with self._connection:  # one transaction: a graph is stored whole or not at all
            self._connection.execute(
                "DELETE FROM snapshots WHERE repo_url = ? AND commit_sha = ?",
                (snapshot.repo_url, snapshot.commit_sha),
            )
            cursor = self._connection.execute(
                "INSERT INTO snapshots (repo_url, commit_sha, analyzed_at, data)"
                " VALUES (?, ?, ?, ?)",
                (
                    snapshot.repo_url,
                    snapshot.commit_sha,
                    snapshot.analyzed_at,
                    snapshot.model_dump_json(),
                ),
            )
            snapshot_id = int(cursor.lastrowid or 0)
            self._connection.executemany(
                "INSERT INTO nodes (snapshot_id, id, kind, data) VALUES (?, ?, ?, ?)",
                [
                    (snapshot_id, node.id, node.kind.value, node.model_dump_json())
                    for node in graph.nodes
                ],
            )
            self._connection.executemany(
                "INSERT INTO edges (snapshot_id, source_id, target_id, kind, data)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        snapshot_id,
                        edge.source_id,
                        edge.target_id,
                        edge.kind.value,
                        edge.model_dump_json(),
                    )
                    for edge in graph.edges
                ],
            )
            if graph.annotations:
                self._insert_annotations(snapshot_id, graph.annotations)
        return snapshot_id

    def load_graph(self, repo_url: str, commit_sha: str | None = None) -> KnowledgeGraph | None:
        row = self._find_snapshot_row(repo_url, commit_sha)
        if row is None:
            return None
        snapshot_id, snapshot_json = row

        nodes = [
            Node.model_validate_json(data)
            for (data,) in self._connection.execute(
                "SELECT data FROM nodes WHERE snapshot_id = ? ORDER BY id", (snapshot_id,)
            )
        ]
        edges = [
            Edge.model_validate_json(data)
            for (data,) in self._connection.execute(
                "SELECT data FROM edges WHERE snapshot_id = ? ORDER BY rowid", (snapshot_id,)
            )
        ]
        return KnowledgeGraph(
            snapshot=RepoSnapshot.model_validate_json(snapshot_json),
            nodes=nodes,
            edges=edges,
            annotations=self.load_annotations(snapshot_id),
        )

    def find_snapshot(self, repo_url: str, commit_sha: str) -> int | None:
        row = self._connection.execute(
            "SELECT snapshot_id FROM snapshots WHERE repo_url = ? AND commit_sha = ?",
            (repo_url, commit_sha),
        ).fetchone()
        return int(row[0]) if row else None

    def _find_snapshot_row(
        self, repo_url: str, commit_sha: str | None
    ) -> tuple[int, str] | None:
        if commit_sha is not None:
            query = (
                "SELECT snapshot_id, data FROM snapshots"
                " WHERE repo_url = ? AND commit_sha = ?"
            )
            row = self._connection.execute(query, (repo_url, commit_sha)).fetchone()
        else:
            query = (
                "SELECT snapshot_id, data FROM snapshots WHERE repo_url = ?"
                " ORDER BY snapshot_id DESC LIMIT 1"
            )
            row = self._connection.execute(query, (repo_url,)).fetchone()
        return (int(row[0]), row[1]) if row else None

    # ── annotations ───────────────────────────────────────────────────────

    def save_annotations(self, snapshot_id: int, annotations: list[SemanticAnnotation]) -> None:
        with self._connection:
            self._insert_annotations(snapshot_id, annotations)

    def _insert_annotations(
        self, snapshot_id: int, annotations: list[SemanticAnnotation]
    ) -> None:
        self._connection.executemany(
            "INSERT OR REPLACE INTO annotations (snapshot_id, node_id, content_hash, data)"
            " VALUES (?, ?, ?, ?)",
            [
                (snapshot_id, a.node_id, a.content_hash, a.model_dump_json())
                for a in annotations
            ],
        )

    def load_annotations(self, snapshot_id: int) -> list[SemanticAnnotation]:
        return [
            SemanticAnnotation.model_validate_json(data)
            for (data,) in self._connection.execute(
                "SELECT data FROM annotations WHERE snapshot_id = ? ORDER BY node_id",
                (snapshot_id,),
            )
        ]

    # ── the cross-snapshot summary cache (Constitution 4) ─────────────────

    def cached_summary(self, content_hash: str) -> SemanticAnnotation | None:
        row = self._connection.execute(
            "SELECT data FROM summary_cache WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return SemanticAnnotation.model_validate_json(row[0]) if row else None

    def cache_summary(self, annotation: SemanticAnnotation) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO summary_cache (content_hash, data) VALUES (?, ?)",
                (annotation.content_hash, annotation.model_dump_json()),
            )

    # ── introspection ─────────────────────────────────────────────────────

    def list_snapshots(self) -> list[dict]:
        return [
            {"snapshot_id": sid, "repo_url": url, "commit_sha": sha, "analyzed_at": at}
            for sid, url, sha, at in self._connection.execute(
                "SELECT snapshot_id, repo_url, commit_sha, analyzed_at"
                " FROM snapshots ORDER BY snapshot_id"
            )
        ]


def _json_or_none(value: str | None) -> dict | None:  # pragma: no cover - debugging aid
    return json.loads(value) if value else None
