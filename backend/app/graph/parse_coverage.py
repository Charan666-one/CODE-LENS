"""How much of a repository the parser actually read.

Derived from the stored graph rather than read off the snapshot, and that
choice is the whole point of the module.

`RepoSnapshot.parsed_file_count` is written at parse time and is correct for
every graph built since it existed — but it defaults to 0, so every snapshot
built *before* it reports "0 files read" with a perfectly good graph sitting
behind it. A field that cannot tell "none" from "never measured" is worse
than no field: the page it feeds would announce that nothing was read, which
is precisely the false claim the coverage work was added to stop making.

Both numbers can be recovered from what is already stored. The count of File
nodes *is* the count of files an emitter read — that is what a File node is.
And the extension census names every language on disk, so the unreadable
ones can be classified the same way ingestion classified them. Deriving both
means one answer for every snapshot, old or new, with no migration.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.graph.schema import Node, NodeKind, RepoSnapshot
from app.ingestion.languages import (
    NON_CODE_LANGUAGES,
    PARSEABLE_LANGUAGES,
    language_for_extension,
)


def parsed_file_count(nodes: Iterable[Node]) -> int:
    """How many files an emitter read. A File node exists for each one."""
    return sum(1 for node in nodes if node.kind is NodeKind.FILE)


def unsupported_languages(snapshot: RepoSnapshot) -> dict[str, int]:
    """Code on disk that this parser cannot read, as {"Java": 212}.

    Prefers what ingestion recorded; falls back to re-deriving it from the
    extension census for snapshots taken before that was stored. Markup,
    config and prose are excluded — they are skipped by design and counting
    them would cry wolf on every repository.
    """
    if snapshot.unsupported_languages:
        return dict(snapshot.unsupported_languages)

    missing: dict[str, int] = {}
    for extension, count in snapshot.languages.items():
        language = language_for_extension(extension)
        if (
            language
            and language not in PARSEABLE_LANGUAGES
            and language not in NON_CODE_LANGUAGES
        ):
            missing[language] = missing.get(language, 0) + count
    return dict(sorted(missing.items(), key=lambda pair: (-pair[1], pair[0])))
