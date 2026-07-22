"""CP-1.5 — git-lite temporal pass. Gate: churn matches `git log` itself."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.graph.schema import NodeKind
from app.ingestion.git_history import (
    _normalise_rename,
    apply_history,
    collect_history,
)
from app.parser import parse_repository

BACKEND_DIR = Path(__file__).resolve().parent.parent
CODELENS_ROOT = BACKEND_DIR.parent


def manual_churn(path_from_root: str) -> int:
    """The verification oracle: count commits with `git log` directly."""
    out = subprocess.run(
        ["git", "-C", str(CODELENS_ROOT), "log", "--no-merges", "--oneline", "--follow",
         "--", path_from_root],
        capture_output=True,
        text=True,
        check=True,
    )
    return len(out.stdout.splitlines())


def test_churn_matches_git_log_spot_checks() -> None:
    """The CP-1.5 gate, on CodeLens's own history."""
    histories = collect_history(CODELENS_ROOT)
    assert histories, "CodeLens's own repo must yield history"

    for path in [
        "backend/app/core/config.py",
        "backend/requirements.txt",
        "backend/app/parser/resolution.py",
    ]:
        assert path in histories, f"{path} missing from history"
        # --follow can only find MORE commits (across renames), never fewer.
        assert histories[path].churn_count <= manual_churn(path)
        # And a plain no-follow count must match exactly.
        plain = subprocess.run(
            ["git", "-C", str(CODELENS_ROOT), "log", "--no-merges", "--oneline", "--", path],
            capture_output=True,
            text=True,
            check=True,
        )
        assert histories[path].churn_count == len(plain.stdout.splitlines())


def test_history_is_scoped_to_the_analysed_subtree() -> None:
    """Analysing backend/ must yield backend-relative paths only."""
    histories = collect_history(BACKEND_DIR)
    assert "app/core/config.py" in histories
    assert not any(path.startswith("backend/") for path in histories)
    assert not any(path.startswith("PLANNING") for path in histories)


def test_authors_and_dates_are_recorded() -> None:
    histories = collect_history(CODELENS_ROOT)
    config = histories["backend/app/core/config.py"]
    assert config.author_count >= 1
    assert config.last_modified is not None  # ISO date from the newest commit


def test_apply_history_stamps_file_nodes_only() -> None:
    graph = parse_repository(BACKEND_DIR, max_size_mb=5_000)
    histories = collect_history(BACKEND_DIR)
    annotated = apply_history(graph.nodes, histories)
    assert annotated > 0

    for node in graph.nodes:
        if node.kind is NodeKind.FILE and node.file_path == "app/core/config.py":
            assert node.churn_count is not None and node.churn_count >= 1
            assert node.author_count is not None and node.author_count >= 1
        elif node.kind is not NodeKind.FILE:
            assert node.churn_count is None  # no manufactured per-function churn


def test_no_git_history_yields_empty_not_invented(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    assert collect_history(tmp_path) == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/app.py", "src/app.py"),
        ("src/{old => new}/app.py", "src/new/app.py"),
        ("old.py => new.py", "new.py"),
        ("src/{ => sub}/app.py", "src/sub/app.py"),
    ],
)
def test_rename_paths_are_normalised(raw: str, expected: str) -> None:
    assert _normalise_rename(raw).replace("//", "/") == expected
