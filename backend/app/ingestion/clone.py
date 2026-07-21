"""Shallow clone of a public repository, with the guards a public service needs.

Note on tooling: GitPython is used to *read* an existing repository, but the
clone itself goes through `subprocess` because `Repo.clone_from` offers no
timeout and CP-1.1's gate requires that a hung clone be killed, not waited on.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from app.ingestion.errors import CloneFailedError, CloneTimeoutError, InvalidSourceError

#: Hosts CodeLens will clone from. Deliberately tiny; widened by checkpoint.
ALLOWED_HOSTS: frozenset[str] = frozenset({"github.com", "www.github.com"})

#: A safe owner/repo segment. Notably excludes anything starting with '-',
#: which git would read as an option rather than a path.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalize_repo_url(url: str) -> str:
    """Validate and canonicalise a repository URL.

    This is a security boundary, not a convenience. CodeLens clones URLs typed
    by strangers, so anything that is not an https GitHub `owner/repo` is
    rejected outright — `ext::` command execution, `file://` local disclosure,
    ssh remotes, and option-shaped segments included.
    """
    raw = url.strip()
    if raw.startswith("-"):
        raise InvalidSourceError("Repository URL must not start with '-'.")

    parsed = urlparse(raw)
    if parsed.scheme != "https":
        got = parsed.scheme or "none"
        raise InvalidSourceError(f"Only https:// repository URLs are supported (got {got}).")

    host = parsed.netloc.lower()
    if host not in ALLOWED_HOSTS:
        raise InvalidSourceError(f"Unsupported host {host!r}. Supported hosts: github.com.")

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 2:
        raise InvalidSourceError("URL must look like https://github.com/<owner>/<repo>.")

    owner, repo = segments[0], segments[1].removesuffix(".git")
    for segment in (owner, repo):
        if not _SAFE_SEGMENT.match(segment):
            raise InvalidSourceError(f"Unsafe path segment {segment!r} in repository URL.")

    return f"https://github.com/{owner}/{repo}"


def shallow_clone(url: str, dest: Path, timeout_seconds: int) -> Path:
    """Clone `url` into `dest` at depth 1. Raises on timeout or git failure."""
    canonical = normalize_repo_url(url)
    dest = dest.resolve()
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "git",
        "clone",
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
        "--quiet",
        "--",  # everything after this is a positional argument, never an option
        canonical,
        str(dest),
    ]
    # Never let git block on an interactive credential prompt: a private repo
    # must fail fast and loudly, not hang until the timeout.
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        "GCM_INTERACTIVE": "never",
    }

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(dest, ignore_errors=True)
        raise CloneTimeoutError(
            f"Clone of {canonical} exceeded {timeout_seconds}s and was aborted."
        ) from exc

    if result.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        stderr_lines = (result.stderr or "").strip().splitlines()
        detail = stderr_lines[-1] if stderr_lines else "unknown error"
        raise CloneFailedError(f"git clone failed for {canonical}: {detail}")

    return dest


def read_git_metadata(root: Path) -> tuple[str, str | None]:
    """Return `(commit_sha, origin_url)` for a working tree.

    A plain directory is a legitimate source (local analysis, fixtures), so a
    missing repository is not an error — the sha is reported as "unknown"
    rather than invented.
    """
    try:
        from git import InvalidGitRepositoryError, NoSuchPathError, Repo
    except ImportError:  # pragma: no cover - GitPython is a hard dependency
        return "unknown", None

    try:
        repo = Repo(root, search_parent_directories=False)
        commit_sha = repo.head.commit.hexsha
    except (InvalidGitRepositoryError, NoSuchPathError, ValueError):
        return "unknown", None

    origin_url: str | None = None
    if "origin" in {remote.name for remote in repo.remotes}:
        origin_url = next(iter(repo.remotes.origin.urls), None)
    return commit_sha, origin_url
