"""Zip ingestion, with path-traversal protection.

A zip is untrusted input: entries may contain `../` or absolute paths that
escape the extraction directory ("zip slip"). Every member is checked before
anything is written.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from app.ingestion.errors import InvalidSourceError


def extract_zip(archive_path: Path, dest: Path) -> Path:
    """Extract `archive_path` into `dest`, returning the repository root.

    GitHub's zip exports wrap everything in a single `<repo>-<ref>/` directory;
    when that shape is detected the wrapper is transparently descended into so
    callers always receive the directory that actually holds the source.
    """
    if not zipfile.is_zipfile(archive_path):
        raise InvalidSourceError(f"{archive_path.name} is not a valid zip archive.")

    dest = dest.resolve()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = (dest / member.filename).resolve()
            if not target.is_relative_to(dest):
                raise InvalidSourceError(
                    f"Archive entry {member.filename!r} escapes the extraction directory."
                )
        archive.extractall(dest)

    return _descend_single_wrapper(dest)


def _descend_single_wrapper(root: Path) -> Path:
    children = [child for child in root.iterdir() if not child.name.startswith("__MACOSX")]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return root
