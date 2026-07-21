"""CP-1.1 — ingestion: repository in, RepoSnapshot out.

The gate: snapshot real repositories (CodeLens itself included), produce a
census that matches a manual count, and fail *gracefully* on oversized,
malformed or hostile input.
"""

from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

import pytest

from app.graph.schema import SCHEMA_VERSION
from app.ingestion import (
    InvalidSourceError,
    RepoTooLargeError,
    ingest,
    snapshot_directory,
)
from app.ingestion.clone import normalize_repo_url
from app.ingestion.inventory import build_census, walk_source_files
from app.ingestion.languages import pick_primary_language

BACKEND_DIR = Path(__file__).resolve().parent.parent
CODELENS_ROOT = BACKEND_DIR.parent
TINY_PYTHON = BACKEND_DIR / "fixtures" / "tiny_python" / "repo"

needs_network = pytest.mark.skipif(
    os.environ.get("CODELENS_NETWORK_TESTS") != "1",
    reason="network test; set CODELENS_NETWORK_TESTS=1 to run",
)


# ── URL validation is a security boundary ─────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://github.com/psf/requests", "https://github.com/psf/requests"),
        ("https://github.com/psf/requests.git", "https://github.com/psf/requests"),
        ("https://github.com/psf/requests/", "https://github.com/psf/requests"),
        ("  https://www.github.com/psf/requests  ", "https://github.com/psf/requests"),
    ],
)
def test_normalize_accepts_and_canonicalises_github_urls(raw: str, expected: str) -> None:
    assert normalize_repo_url(raw) == expected


@pytest.mark.parametrize(
    "hostile",
    [
        "ext::sh -c 'touch /tmp/pwned'",  # arbitrary command execution
        "file:///etc/passwd",  # local file disclosure
        "git://github.com/psf/requests",  # unauthenticated protocol
        "ssh://git@github.com/psf/requests",
        "http://github.com/psf/requests",  # downgraded transport
        "https://evil.example.com/psf/requests",  # host not allowlisted
        "https://github.com/psf",  # not owner/repo
        "https://github.com/psf/requests/tree/main",  # deeper path
        "--upload-pack=touch /tmp/pwned",  # option injection
        "https://github.com/-evil/repo",  # option-shaped segment
        "https://github.com/psf/../../etc",  # traversal
    ],
)
def test_normalize_rejects_hostile_urls(hostile: str) -> None:
    with pytest.raises(InvalidSourceError):
        normalize_repo_url(hostile)


# ── Census and language detection ─────────────────────────────────────────


def test_census_matches_manual_count_on_fixture() -> None:
    """The gate's "matches a manual find-count", pinned to a frozen fixture."""
    manual = sorted(p.name for p in TINY_PYTHON.glob("*.py"))
    assert manual == ["calculator.py", "main.py", "shapes.py"]

    files = walk_source_files(TINY_PYTHON)
    assert build_census(files) == {"py": 3}
    assert len(files) == 3


def test_primary_language_prefers_code_over_markup() -> None:
    assert pick_primary_language({"md": 400, "py": 40}) == "Python"
    assert pick_primary_language({"ts": 10, "py": 3}) == "TypeScript"
    assert pick_primary_language({}) == "unknown"
    assert pick_primary_language({"xyz": 5}) == "unknown"


def test_census_is_ordered_by_descending_count() -> None:
    census = build_census(walk_source_files(BACKEND_DIR / "app"))
    assert list(census.values()) == sorted(census.values(), reverse=True)


# ── Inventory correctness ─────────────────────────────────────────────────


def test_content_hash_matches_sha256_of_bytes() -> None:
    files = {f.path: f for f in walk_source_files(TINY_PYTHON)}
    expected = hashlib.sha256((TINY_PYTHON / "calculator.py").read_bytes()).hexdigest()
    assert files["calculator.py"].content_hash == expected


def test_content_hashes_are_stable_and_distinct() -> None:
    """Constitution 4: the hash is the incremental key, so it must be both."""
    first = {f.path: f.content_hash for f in walk_source_files(TINY_PYTHON)}
    second = {f.path: f.content_hash for f in walk_source_files(TINY_PYTHON)}
    assert first == second
    assert len(set(first.values())) == len(first)


def test_ignored_directories_and_binaries_are_excluded(tmp_path: Path) -> None:
    (tmp_path / "real.py").write_text("x = 1\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")

    assert [f.path for f in walk_source_files(tmp_path)] == ["real.py"]


def test_loc_counts_physical_lines() -> None:
    files = {f.path: f for f in walk_source_files(TINY_PYTHON)}
    source = (TINY_PYTHON / "shapes.py").read_text().splitlines()
    assert files["shapes.py"].loc == len(source)


# ── Snapshotting ──────────────────────────────────────────────────────────


def test_snapshot_of_fixture_repo() -> None:
    ingested = snapshot_directory(TINY_PYTHON)
    snapshot = ingested.snapshot

    assert snapshot.primary_language == "Python"
    assert snapshot.languages == {"py": 3}
    assert snapshot.file_count == 3
    assert snapshot.total_loc == sum(f.loc for f in ingested.files)
    assert snapshot.schema_version == SCHEMA_VERSION
    assert snapshot.commit_sha == "unknown"  # fixture dir is not its own repo
    assert ingested.parseable_files == ingested.files


def test_snapshot_of_codelens_itself() -> None:
    """Dogfood from commit one (FOUNDATION.md §"Immediate next steps" #2)."""
    ingested = snapshot_directory(CODELENS_ROOT, max_size_mb=5_000)
    snapshot = ingested.snapshot

    assert snapshot.primary_language == "Python"
    assert snapshot.file_count > 10
    assert len(snapshot.commit_sha) == 40  # a real resolved HEAD
    assert "backend/app/graph/schema.py" in {f.path for f in ingested.files}
    # The heavy virtualenv must never be inventoried as project source.
    assert not any(".venv" in f.path for f in ingested.files)


def test_analyzed_at_is_iso_utc() -> None:
    from datetime import datetime

    stamp = snapshot_directory(TINY_PYTHON).snapshot.analyzed_at
    assert datetime.fromisoformat(stamp).tzinfo is not None


# ── Graceful failure ──────────────────────────────────────────────────────


def test_oversized_repository_is_rejected() -> None:
    with pytest.raises(RepoTooLargeError, match="over the 0 MB limit"):
        snapshot_directory(TINY_PYTHON, max_size_mb=0)


def test_missing_source_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidSourceError):
        ingest(tmp_path / "does-not-exist")


def test_non_zip_file_is_rejected(tmp_path: Path) -> None:
    stray = tmp_path / "notes.txt"
    stray.write_text("hello")
    with pytest.raises(InvalidSourceError):
        ingest(stray)


@pytest.mark.parametrize(
    "remote",
    [
        "ext::sh -c 'touch /tmp/pwned'",  # transport-helper command execution
        "git::https://evil.example.com/x/y",
        "git@github.com:psf/requests.git",  # scp-style ssh
    ],
)
def test_remote_like_sources_are_rejected_as_urls_not_paths(remote: str) -> None:
    """These must fail URL validation, not fall through to the path branch.

    Rejection by accident (no such file) is the right outcome for the wrong
    reason, and produces a misleading error.
    """
    with pytest.raises(InvalidSourceError, match="https"):
        ingest(remote)


# ── Zip ingestion ─────────────────────────────────────────────────────────


def test_zip_source_is_extracted_and_snapshotted(tmp_path: Path) -> None:
    archive = tmp_path / "tiny.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for source in TINY_PYTHON.glob("*.py"):
            zf.write(source, f"tiny-main/{source.name}")

    ingested = ingest(archive, workdir=tmp_path / "work")
    assert ingested.snapshot.file_count == 3
    assert ingested.snapshot.primary_language == "Python"


def test_zip_slip_is_rejected(tmp_path: Path) -> None:
    """A malicious archive must never write outside the extraction directory."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escaped.txt", "pwned")

    with pytest.raises(InvalidSourceError, match="escapes"):
        ingest(archive, workdir=tmp_path / "work")
    assert not (tmp_path / "escaped.txt").exists()


# ── Real clone (opt-in) ───────────────────────────────────────────────────


@needs_network
def test_clones_a_real_repository(tmp_path: Path) -> None:
    ingested = ingest("https://github.com/psf/requests", workdir=tmp_path)
    assert ingested.snapshot.primary_language == "Python"
    assert len(ingested.snapshot.commit_sha) == 40
    assert ingested.snapshot.repo_url == "https://github.com/psf/requests"
