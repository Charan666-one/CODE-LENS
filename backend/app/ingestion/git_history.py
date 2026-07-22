"""Git-lite — the temporal layer's first slice (FOUNDATION.md §Q3).

One `git log --numstat` pass yields, per file: how often it changes (churn),
how many people touch it (author count), and when it last moved. These are
deterministic facts from history — Layer C's down payment, feeding the risk
formula (complexity × fan-in × churn) and the map's activity glow.

Honest limitation, stated where it matters: a `--depth 1` shallow clone has
one commit of history, so churn there is 1 for everything. Real churn needs
real history — local working trees (dogfooding) and the GitHub App's clones
(CP-8.1) have it; the free drive-by analysis may not. The graph records what
the evidence supports and nothing more.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.graph.schema import Node, NodeKind

#: `{old => new}` rename markers inside numstat paths.
_RENAME_BRACES = re.compile(r"\{[^{}]* => ([^{}]*)\}")


@dataclass
class FileHistory:
    """What git remembers about one file."""

    churn_count: int = 0
    last_modified: str | None = None  # ISO date of the newest commit touching it
    authors: set[str] = field(default_factory=set)

    @property
    def author_count(self) -> int:
        return len(self.authors)


def collect_history(root: Path, timeout_seconds: int = 60) -> dict[str, FileHistory]:
    """Per-file history for the working tree at `root`, keyed by repo-relative
    path (relative to `root`, matching the inventory and the graph).

    Returns an empty mapping when `root` has no git history — absence of
    evidence is recorded as absence, never invented.
    """
    toplevel = _git_toplevel(root)
    if toplevel is None:
        return {}

    # Paths in git output are relative to the repository top level; the graph's
    # paths are relative to `root`. Strip the difference.
    try:
        prefix = root.resolve().relative_to(toplevel).as_posix()
    except ValueError:
        return {}
    prefix = "" if prefix == "." else prefix + "/"

    output = _run_git_log(root, timeout_seconds)
    if output is None:
        return {}

    histories: dict[str, FileHistory] = {}
    author = ""
    date = ""
    for line in output.splitlines():
        if line.startswith("\x01"):  # commit header: \x01<email>|<iso date>
            author, _, date = line[1:].partition("|")
            continue
        if not line.strip():
            continue
        # numstat rows: "<added>\t<deleted>\t<path>"
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        path = _normalise_rename(parts[2])
        if prefix:
            if not path.startswith(prefix):
                continue  # a file outside the analysed subtree
            path = path[len(prefix) :]

        history = histories.setdefault(path, FileHistory())
        history.churn_count += 1
        if author:
            history.authors.add(author)
        if history.last_modified is None:  # log is newest-first; first seen wins
            history.last_modified = date or None

    return histories


def apply_history(nodes: list[Node], histories: dict[str, FileHistory]) -> int:
    """Stamp temporal facts onto file nodes. Returns how many were annotated.

    Only File nodes carry churn: history is recorded per path, and pretending
    per-function churn from per-file data would be manufactured precision.
    """
    annotated = 0
    for node in nodes:
        if node.kind is not NodeKind.FILE or node.file_path is None:
            continue
        history = histories.get(node.file_path)
        if history is None:
            continue
        node.churn_count = history.churn_count
        node.author_count = history.author_count
        node.last_modified = history.last_modified
        annotated += 1
    return annotated


def _git_toplevel(root: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _run_git_log(root: Path, timeout_seconds: int) -> str | None:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "--no-merges",
                "--format=%x01%aE|%aI",
                "--numstat",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def _normalise_rename(path: str) -> str:
    """`src/{old => new}/f.py` -> `src/new/f.py`; `old => new` -> `new`."""
    if "=>" not in path:
        return path
    if "{" in path:
        return _RENAME_BRACES.sub(r"\1", path).replace("//", "/")
    return path.split(" => ")[-1]
