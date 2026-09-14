"""File inventory — the content-hash keyed view of a working tree.

Constitution 4 (incremental everything) starts here: every file carries a
SHA-256 of its bytes, so an unchanged file can be recognised and skipped by
every later stage without re-reading it.

`SourceFile` deliberately lives here rather than in `graph/schema.py`. It is a
pipeline artifact, not a graph fact, and `schema.py` is frozen.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from pydantic import BaseModel

from app.core.config import settings
from app.ingestion.languages import IGNORED_DIRECTORIES, language_for_extension

logger = logging.getLogger(__name__)

#: Bytes sampled when deciding whether a file is binary.
_BINARY_SNIFF_BYTES = 8192


class SourceFile(BaseModel):
    """One text file in the working tree."""

    path: str  # repo-relative, POSIX separators
    extension: str  # bare, lowercase, no dot ("" if none)
    language: str | None
    size_bytes: int
    loc: int  # physical lines
    content_hash: str  # sha256 hex of the raw bytes


def _is_binary(data: bytes) -> bool:
    """A NUL byte in the first block is the classic, cheap binary test."""
    return b"\x00" in data[:_BINARY_SNIFF_BYTES]


def _iter_candidate_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in IGNORED_DIRECTORIES for part in path.relative_to(root).parts[:-1]):
            continue
        found.append(path)
    return found


def walk_source_files(
    root: Path,
    *,
    max_file_bytes: int | None = None,
    max_files: int | None = None,
) -> list[SourceFile]:
    """Inventory every non-ignored text file under `root`, sorted by path.

    Binary files are excluded outright: CodeLens understands source, and a PNG
    has no place in a code census.

    Two ceilings, because this function reads every file *whole* into memory
    to hash and sniff it:

    * `max_file_bytes` — one generated 2 GB `.ts` file is otherwise 2 GB of
      resident memory, inside a repository comfortably under the total size
      limit. The file is skipped, not truncated: a half-read file would parse
      into a confidently wrong graph, which is worse than an absent one.
    * `max_files` — total size does not bound the count, and half a million
      tiny files each cost a hash, a parse and a node.

    Both default to the configured limits. Skipping is silent to the caller
    but not to the operator: it is logged, because a repository that analysed
    partially and said nothing is a graph nobody can trust.
    """
    root = root.resolve()
    byte_ceiling = (
        settings.MAX_FILE_SIZE_MB * 1024 * 1024 if max_file_bytes is None else max_file_bytes
    )
    count_ceiling = settings.MAX_FILES if max_files is None else max_files
    files: list[SourceFile] = []
    skipped_large = 0

    for path in _iter_candidate_files(root):
        if len(files) >= count_ceiling:
            logger.warning(
                "file limit reached (%d); the rest of %s was not inventoried",
                count_ceiling,
                root,
            )
            break
        try:
            if path.stat().st_size > byte_ceiling:
                skipped_large += 1
                continue
            data = path.read_bytes()
        except OSError:
            continue  # unreadable file: not fatal, just not inventoried
        if _is_binary(data):
            continue

        extension = path.suffix.lstrip(".").lower()
        files.append(
            SourceFile(
                path=path.relative_to(root).as_posix(),
                extension=extension,
                language=language_for_extension(extension),
                size_bytes=len(data),
                loc=len(data.splitlines()),
                content_hash=hashlib.sha256(data).hexdigest(),
            )
        )

    if skipped_large:
        logger.info(
            "skipped %d file(s) over %.1f MB in %s", skipped_large, byte_ceiling / 1048576, root
        )
    files.sort(key=lambda f: f.path)
    return files


def build_census(files: list[SourceFile]) -> dict[str, int]:
    """Extension census, e.g. `{"py": 120, "ts": 40}`. Files without an
    extension are omitted — they cannot be attributed to a language."""
    census: dict[str, int] = {}
    for source_file in files:
        if not source_file.extension:
            continue
        census[source_file.extension] = census.get(source_file.extension, 0) + 1
    return dict(sorted(census.items(), key=lambda item: (-item[1], item[0])))


def working_tree_size_bytes(root: Path) -> int:
    """Size of the inventoried surface, excluding ignored directories."""
    return sum(path.stat().st_size for path in _iter_candidate_files(root.resolve()))
