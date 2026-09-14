"""Ownership and bus factor — who knows this code, and how few of them.

`author_count` already sits on every file node, but a count cannot tell the
difference between the two situations that matter most:

    three people have touched this, evenly           -> healthy
    three people have touched this, one wrote 95%    -> bus factor 1

Both report `author_count = 3`. Only the share distinguishes them, and the
second is the one that ends a project when that person leaves. So this pass
records shares, and names the people, so "what does this person alone know?"
becomes a question the graph can answer.

**On identity.** Git history in a public repo is public, but a knowledge
graph that republishes contributor email addresses is compiling personal
data for no purpose the product needs. Ownership only requires that people
be told *apart* and *named*. So the node carries the display name from
git, and identity is keyed on a short digest of the email — distinct
contributors stay distinct, `name@company.com` never appears in the graph,
the API, or a shared map link. If a future feature genuinely needs to reach
a person, that is a feature that should ask for consent, not one that should
find the address already lying in a graph it can read.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from app.graph.schema import Edge, EdgeKind, Node, NodeKind
from app.ingestion.git_history import Commit

#: Below this share of a file's commits, a person is a passer-by rather than
#: an owner — a typo fix should not make someone a stakeholder in a module.
MIN_OWNERSHIP_SHARE = 0.15

#: Owners recorded per file. Past a handful the tail is noise, and on a large
#: repo the edge count is files × this.
MAX_OWNERS_PER_FILE = 4

#: At or above this share for a single person, the file's knowledge is
#: effectively held by one head. Not a law of nature — a threshold, named so
#: the number on screen can be traced to a decision rather than a vibe.
BUS_FACTOR_ONE_SHARE = 0.8


def _author_id(email: str) -> str:
    """Stable, distinct, and not an email address."""
    digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:10]
    return f"{NodeKind.AUTHOR.value}:{digest}"


def ownership(
    commits: list[Commit],
    nodes: list[Node],
    *,
    min_share: float = MIN_OWNERSHIP_SHARE,
    max_owners: int = MAX_OWNERS_PER_FILE,
) -> tuple[list[Node], list[Edge]]:
    """AUTHOR nodes and AUTHORED_BY edges, plus ownership facts on file nodes.

    Also stamps `extra["primary_author_share"]` on each file so the common
    question — how concentrated is this file's knowledge — needs no traversal.
    """
    file_nodes = {
        node.file_path: node
        for node in nodes
        if node.kind is NodeKind.FILE and node.file_path
    }

    # path -> {author email -> commits touching it}
    per_file: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    display_names: dict[str, str] = {}
    for commit in commits:
        if not commit.author:
            continue
        display_names.setdefault(commit.author, commit.display_name or commit.author)
        for path in commit.files:
            if path in file_nodes:
                per_file[path][commit.author] += 1

    author_nodes: dict[str, Node] = {}
    edges: list[Edge] = []

    for path, counts in sorted(per_file.items()):
        total = sum(counts.values())
        if total == 0:
            continue
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

        top_share = ranked[0][1] / total
        node = file_nodes[path]
        node.extra["primary_author_share"] = round(top_share, 3)
        node.extra["primary_author"] = _display(display_names, ranked[0][0])

        for email, count in ranked[:max_owners]:
            share = count / total
            if share < min_share:
                continue
            author_id = _author_id(email)
            if author_id not in author_nodes:
                name = _display(display_names, email)
                author_nodes[author_id] = Node(
                    id=author_id,
                    kind=NodeKind.AUTHOR,
                    name=name,
                    qualified_name=author_id,  # never the email
                )
            edges.append(
                Edge(
                    source_id=node.id,
                    target_id=author_id,
                    kind=EdgeKind.AUTHORED_BY,
                    weight=round(share, 3),
                    file_path=path,
                )
            )

    return [author_nodes[key] for key in sorted(author_nodes)], edges


def _display(names: dict[str, str], email: str) -> str:
    """A human name, never an address. Falls back to the mailbox's local part
    when git recorded no name — still not the full address."""
    name = names.get(email, "")
    if name and "@" not in name:
        return name
    return email.split("@", 1)[0] or "unknown"
