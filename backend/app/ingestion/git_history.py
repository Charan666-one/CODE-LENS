"""Git-lite — the temporal layer's first slice (FOUNDATION.md §Q3).

One `git log --numstat` pass yields, per file: how often it changes (churn),
how many people touch it (author count), and when it last moved. These are
deterministic facts from history — Layer C's down payment, feeding the risk
formula (complexity × fan-in × churn) and the map's activity glow.

The same pass also yields something the per-file rollup throws away: *which
files changed together in one commit*. That is the raw material for co-change
coupling (`app/graph/co_change.py`), so the log is parsed once into commits
and both facts are derived from that — reading history twice for two views of
the same bytes would be silly.

History is bounded, not complete: clones fetch `HISTORY_DEPTH` commits
(clone.py), so churn counts are "within the last N commits", and a repo older
than that reports the recent past rather than all time. That is the right
trade — recent history is what predicts today's behaviour — but it is a
ceiling, and the numbers should be read as one.

The blobless-clone constraint lives here: history is read with `--name-only`
and must stay that way. See `_run_git_log`.
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


@dataclass(frozen=True)
class Commit:
    """One commit, reduced to what the temporal layer reasons about.

    `files` are repo-relative paths in the same coordinate system as the
    graph — the caller never has to think about git's top-level prefix.
    """

    author: str
    date: str  # ISO
    files: tuple[str, ...]


def read_log(root: Path, timeout_seconds: int = 60) -> list[Commit]:
    """Every non-merge commit touching the tree at `root`, newest first.

    Empty when `root` has no git history — absence of evidence is recorded
    as absence, never invented.
    """
    toplevel = _git_toplevel(root)
    if toplevel is None:
        return []

    # Paths in git output are relative to the repository top level; the graph's
    # paths are relative to `root`. Strip the difference.
    try:
        prefix = root.resolve().relative_to(toplevel).as_posix()
    except ValueError:
        return []
    prefix = "" if prefix == "." else prefix + "/"

    output = _run_git_log(root, timeout_seconds)
    if output is None:
        return []

    commits: list[Commit] = []
    author = ""
    date = ""
    files: list[str] = []

    def flush() -> None:
        if files:
            commits.append(Commit(author=author, date=date, files=tuple(files)))

    for line in output.splitlines():
        if line.startswith("\x01"):  # commit header: \x01<email>|<iso date>
            flush()
            files = []
            author, _, date = line[1:].partition("|")
            continue
        if not line.strip():
            continue
        # `--name-only` rows are the path alone.
        path = _normalise_rename(line.strip())
        if prefix:
            if not path.startswith(prefix):
                continue  # a file outside the analysed subtree
            path = path[len(prefix) :]
        files.append(path)
    flush()

    return commits


def histories_from_commits(commits: list[Commit]) -> dict[str, FileHistory]:
    """Roll commits up per file. `commits` must be newest-first, which is
    what makes the first date seen the last-modified date."""
    histories: dict[str, FileHistory] = {}
    for commit in commits:
        for path in commit.files:
            history = histories.setdefault(path, FileHistory())
            history.churn_count += 1
            if commit.author:
                history.authors.add(commit.author)
            if history.last_modified is None:  # newest-first: first seen wins
                history.last_modified = commit.date or None
    return histories


def collect_history(root: Path, timeout_seconds: int = 60) -> dict[str, FileHistory]:
    """Per-file history for the working tree at `root`, keyed by repo-relative
    path (relative to `root`, matching the inventory and the graph)."""
    return histories_from_commits(read_log(root, timeout_seconds))


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
                # `--name-only`, never `--numstat`. Clones are blobless
                # (clone.py `--filter=blob:none`), and numstat needs file
                # *contents* to count changed lines, so it would lazily
                # refetch every blob in the repository over the network —
                # minutes of hanging for two numbers nothing here reads.
                # Paths come from tree diffs, which a blobless clone has.
                "--name-only",
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
