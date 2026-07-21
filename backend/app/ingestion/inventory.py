"""File inventory — the content-hash keyed view of a working tree.

Constitution 4 (incremental everything) starts here: every file carries a
SHA-256 of its bytes, so an unchanged file can be recognised and skipped by
every later stage without re-reading it.

`SourceFile` deliberately lives here rather than in `graph/schema.py`. It is a
pipeline artifact, not a graph fact, and `schema.py` is frozen.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel

from app.ingestion.languages import IGNORED_DIRECTORIES, language_for_extension

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


def walk_source_files(root: Path) -> list[SourceFile]:
    """Inventory every non-ignored text file under `root`, sorted by path.

    Binary files are excluded outright: CodeLens understands source, and a PNG
    has no place in a code census.
    """
    root = root.resolve()
    files: list[SourceFile] = []

    for path in _iter_candidate_files(root):
        try:
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
