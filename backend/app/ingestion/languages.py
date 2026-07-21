"""Extension → language mapping for the census.

FOUNDATION.md's schema keys `RepoSnapshot.languages` by *extension*
(`{"py": 120, "ts": 40}`); `primary_language` is a language *name* derived
from it. This module owns both halves of that translation.
"""

from __future__ import annotations

EXTENSION_LANGUAGE: dict[str, str] = {
    "py": "Python",
    "pyi": "Python",
    "js": "JavaScript",
    "jsx": "JavaScript",
    "mjs": "JavaScript",
    "cjs": "JavaScript",
    "ts": "TypeScript",
    "tsx": "TypeScript",
    "go": "Go",
    "rs": "Rust",
    "java": "Java",
    "kt": "Kotlin",
    "rb": "Ruby",
    "php": "PHP",
    "cs": "C#",
    "swift": "Swift",
    "scala": "Scala",
    "c": "C",
    "h": "C",
    "cpp": "C++",
    "cc": "C++",
    "hpp": "C++",
    "sh": "Shell",
    "bash": "Shell",
    "html": "HTML",
    "css": "CSS",
    "scss": "CSS",
    "sql": "SQL",
    "md": "Markdown",
    "rst": "reStructuredText",
    "json": "JSON",
    "yaml": "YAML",
    "yml": "YAML",
    "toml": "TOML",
}

#: Languages the parser engine can actually read today (CP-1.2; more at CP-9.1).
PARSEABLE_LANGUAGES: frozenset[str] = frozenset({"Python", "JavaScript", "TypeScript"})

#: Markup, config and prose. Present in the census, but never allowed to win
#: `primary_language` — a repo of 400 JSON fixtures and 40 Python files is a
#: Python repo.
NON_CODE_LANGUAGES: frozenset[str] = frozenset(
    {"Markdown", "reStructuredText", "JSON", "YAML", "TOML", "HTML", "CSS", "SQL", "Shell"}
)

#: Directories that are never source. Skipped wholesale during the walk.
IGNORED_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "site-packages",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "target",
        ".next",
        ".nuxt",
        "vendor",
        "coverage",
        ".idea",
        ".vscode",
    }
)


def language_for_extension(extension: str) -> str | None:
    """Language name for a bare extension (no dot), or None if unrecognised."""
    return EXTENSION_LANGUAGE.get(extension.lower())


def pick_primary_language(census: dict[str, int]) -> str:
    """Derive the primary language from an extension census.

    Code beats non-code; ties break alphabetically so the result is stable
    across runs (Constitution 4 — the same input must give the same answer).
    """
    totals: dict[str, int] = {}
    for extension, count in census.items():
        language = language_for_extension(extension)
        if language is not None:
            totals[language] = totals.get(language, 0) + count

    code_only = {name: n for name, n in totals.items() if name not in NON_CODE_LANGUAGES}
    pool = code_only or totals
    if not pool:
        return "unknown"
    return max(pool.items(), key=lambda item: (item[1], item[0]))[0]
