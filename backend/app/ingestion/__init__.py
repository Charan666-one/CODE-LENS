"""Ingestion — a repository goes in, a normalized snapshot comes out.

ARCHITECTURE.md §1: "Knows nothing about AI, nothing about graphs."
That constraint is load-bearing: this package must stay importable and fully
testable with no LLM key, no database, and no network (for local sources).

Accepted sources:
  * an https GitHub URL  -> blobless clone, bounded history (timeout-guarded)
  * a local .zip archive -> extracted with path-traversal protection
  * a local directory    -> analysed in place, never mutated
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.core.config import settings
from app.graph.schema import RepoSnapshot
from app.ingestion.archive import extract_zip
from app.ingestion.clone import normalize_repo_url, read_git_metadata, shallow_clone
from app.ingestion.errors import (
    CloneFailedError,
    CloneTimeoutError,
    IngestionError,
    InvalidSourceError,
    RepoTooLargeError,
)
from app.ingestion.inventory import (
    SourceFile,
    build_census,
    walk_source_files,
    working_tree_size_bytes,
)
from app.ingestion.languages import PARSEABLE_LANGUAGES, pick_primary_language

__all__ = [
    "CloneFailedError",
    "CloneTimeoutError",
    "IngestedRepo",
    "IngestionError",
    "InvalidSourceError",
    "RepoTooLargeError",
    "SourceFile",
    "ingest",
    "looks_like_remote",
    "snapshot_directory",
]

_BYTES_PER_MB = 1024 * 1024

#: Strings that are git *remotes* rather than local paths: transport-helper
#: syntax (`ext::`, `git::`) and scp-style ssh (`git@host:path`). These must be
#: routed to URL validation and rejected there with a truthful message, never
#: fall through to the local-path branch and get refused by accident.
_REMOTE_LIKE = re.compile(r"^[A-Za-z0-9_+.-]+::|^[^/\s]+@[^/\s]+:")


def looks_like_remote(text: str) -> bool:
    """True when `text` must be treated as a repository remote, not a local
    path — the API layer uses this to route strings to URL validation."""
    return "://" in text or bool(_REMOTE_LIKE.search(text))


class IngestedRepo(BaseModel):
    """The unit of work handed to the parser (CP-1.2).

    `snapshot` is the frozen contract from `graph/schema.py`. `files` is the
    content-hash keyed inventory ARCHITECTURE.md §1 calls for, kept here rather
    than bolted onto the frozen model.
    """

    snapshot: RepoSnapshot
    root: Path
    files: list[SourceFile]

    @property
    def parseable_files(self) -> list[SourceFile]:
        """Files the parser engine can actually read today (CP-1.2)."""
        return [f for f in self.files if f.language in PARSEABLE_LANGUAGES]


def ingest(
    source: str | Path,
    *,
    workdir: Path | None = None,
    max_size_mb: int | None = None,
    timeout_seconds: int | None = None,
) -> IngestedRepo:
    """Acquire `source` and snapshot it. The one entry point to this package.

    Trust boundary: local directory and .zip sources exist for dogfooding,
    fixtures and CLI use, and they are *trusted* input. The HTTP API added in
    CP-6.2 must pass only URL sources here — handing a caller-supplied string
    straight to this function would let a request name any path on disk.
    """
    destination = Path(workdir) if workdir is not None else settings.CLONE_DIR
    timeout = settings.CLONE_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds

    limit = settings.MAX_REPO_SIZE_MB if max_size_mb is None else max_size_mb
    root, repo_url = _acquire(source, destination, timeout, limit)
    return snapshot_directory(root, repo_url=repo_url, max_size_mb=max_size_mb)


def snapshot_directory(
    root: Path,
    *,
    repo_url: str | None = None,
    max_size_mb: int | None = None,
) -> IngestedRepo:
    """Inventory an existing working tree into a `RepoSnapshot`."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise InvalidSourceError(f"{root} is not a directory.")

    limit = settings.MAX_REPO_SIZE_MB if max_size_mb is None else max_size_mb
    size_mb = working_tree_size_bytes(root) / _BYTES_PER_MB
    if size_mb > limit:
        raise RepoTooLargeError(f"Working tree is {size_mb:.1f} MB, over the {limit} MB limit.")

    files = walk_source_files(root)
    census = build_census(files)
    commit_sha, origin_url = read_git_metadata(root)

    snapshot = RepoSnapshot(
        repo_url=repo_url or origin_url or root.as_uri(),
        commit_sha=commit_sha,
        primary_language=pick_primary_language(census),
        languages=census,
        file_count=len(files),
        total_loc=sum(source_file.loc for source_file in files),
        analyzed_at=datetime.now(UTC).isoformat(),
    )
    return IngestedRepo(snapshot=snapshot, root=root, files=files)


def _acquire(
    source: str | Path, workdir: Path, timeout: int, max_size_mb: int
) -> tuple[Path, str | None]:
    """Resolve any supported source to `(root_directory, canonical_url)`."""
    if isinstance(source, Path):
        return _acquire_local(source, workdir)

    text = str(source).strip()
    if "://" in text or _REMOTE_LIKE.search(text):
        canonical = normalize_repo_url(text)
        destination = Path(workdir) / canonical.rpartition("/")[2]
        cloned = shallow_clone(canonical, destination, timeout, max_size_mb=max_size_mb)
        return cloned, canonical

    return _acquire_local(Path(text), workdir)


def _acquire_local(path: Path, workdir: Path) -> tuple[Path, str | None]:
    resolved = path.expanduser().resolve()
    if resolved.is_dir():
        return resolved, None
    if resolved.is_file() and resolved.suffix.lower() == ".zip":
        return extract_zip(resolved, Path(workdir) / resolved.stem), None
    raise InvalidSourceError(
        f"{resolved} is not a directory, a .zip archive, or an https repository URL."
    )
