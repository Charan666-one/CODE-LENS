"""Co-change coupling — the relationship the AST cannot see (Layer C).

Static analysis finds the dependencies the code *declares*. History finds the
ones it *has*. When two files change together in commit after commit but
neither imports the other, something real connects them — a wire format, a
duplicated constant, a protocol both ends implement, an invariant no type
system is holding. That pair is invisible to every import-graph tool, and it
is exactly the pair that breaks when you change one and forget the other.

This module turns commits into `CO_CHANGES` edges. Four judgement calls are
baked in, each defending against a specific way this measure goes wrong:

* **Big commits prove nothing.** A commit touching 300 files is a reformat, a
  license header sweep, a lockfile bump, or a mass rename. It is not evidence
  that those files are coupled. Commits above `max_files_per_commit` are
  ignored — which also stops the O(n²) pair count from exploding.

* **Raw co-occurrence flatters busy files.** A file changed in half of all
  commits co-occurs with everything. Strength is therefore the Jaccard ratio
  `together / (either)`, so a file that changes constantly needs to change
  *with you specifically* to score.

* **Twice is a coincidence.** Below `min_commits` shared commits there is no
  signal, only noise, so nothing is emitted.

* **Old coupling is not today's coupling.** Only the most recent
  `max_commits` are considered. Two files that moved together in 2016 and
  never since should not colour a map of the code as it stands.

The edge is symmetric but the schema's edges are directed, so exactly one
edge is emitted per pair, ordered by node id. Readers must treat CO_CHANGES
as undirected; `hidden_coupling` does.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.graph.schema import CallConfidence, Edge, EdgeKind, Node, NodeKind
from app.ingestion.git_history import Commit

#: A commit touching more files than this is a sweep, not a change.
MAX_FILES_PER_COMMIT = 30

#: Only recent history speaks to how the code behaves now.
MAX_COMMITS = 3000

#: Fewer shared commits than this is coincidence.
MIN_SHARED_COMMITS = 3

#: Jaccard floor. 0.25 means "when either one changes, the other comes along
#: at least a quarter of the time" — loose enough to catch real coupling in
#: repos with broad commits, tight enough to exclude drive-by co-occurrence.
MIN_STRENGTH = 0.25

#: Hard ceiling on emitted edges, strongest first. A graph is a product
#: surface, not a data dump; 2000 coupling edges is already more than anyone
#: will read, and unbounded growth would hurt load time on huge repos.
MAX_EDGES = 2000


@dataclass
class CoChangeReport:
    """What the pass considered and what it kept — so the number on screen
    can always be traced back to the evidence behind it."""

    commits_considered: int
    commits_skipped_large: int
    pairs_examined: int
    edges_emitted: int


def co_change_edges(
    commits: list[Commit],
    nodes: list[Node],
    *,
    max_files_per_commit: int = MAX_FILES_PER_COMMIT,
    max_commits: int = MAX_COMMITS,
    min_shared_commits: int = MIN_SHARED_COMMITS,
    min_strength: float = MIN_STRENGTH,
    max_edges: int = MAX_EDGES,
) -> tuple[list[Edge], CoChangeReport]:
    """Derive CO_CHANGES edges from history.

    Only paths that exist as File nodes participate: coupling to a README or
    a lockfile is real but says nothing about the code's structure, and the
    graph has nowhere to hang an edge that points at a non-node anyway.
    """
    file_ids = {
        node.file_path: node.id
        for node in nodes
        if node.kind is NodeKind.FILE and node.file_path
    }

    recent = commits[:max_commits]
    together: dict[tuple[str, str], int] = defaultdict(int)
    changed_in: dict[str, int] = defaultdict(int)
    skipped_large = 0

    for commit in recent:
        touched = sorted({p for p in commit.files if p in file_ids})
        if len(touched) > max_files_per_commit:
            skipped_large += 1
            continue
        if len(touched) < 2:
            # Still counts toward the file's own change total: a file that
            # usually changes alone must not look coupled to the one thing it
            # occasionally ships with.
            for path in touched:
                changed_in[path] += 1
            continue
        for path in touched:
            changed_in[path] += 1
        for index, left in enumerate(touched):
            for right in touched[index + 1 :]:
                together[(left, right)] += 1

    scored: list[tuple[float, int, str, str]] = []
    for (left, right), shared in together.items():
        if shared < min_shared_commits:
            continue
        union = changed_in[left] + changed_in[right] - shared
        strength = shared / union if union else 0.0
        if strength < min_strength:
            continue
        scored.append((strength, shared, left, right))

    scored.sort(key=lambda row: (-row[0], -row[1], row[2], row[3]))

    edges: list[Edge] = []
    for strength, _shared, left, right in scored[:max_edges]:
        source_id, target_id = sorted((file_ids[left], file_ids[right]))
        edges.append(
            Edge(
                source_id=source_id,
                target_id=target_id,
                kind=EdgeKind.CO_CHANGES,
                weight=round(strength, 4),
                # History is evidence of correlation, never of a call. Marking
                # these `heuristic` keeps the confidence ladder honest: nothing
                # derived from co-occurrence may claim to be `resolved`.
                confidence=CallConfidence.HEURISTIC,
                file_path=left,
                line=None,
            )
        )

    return edges, CoChangeReport(
        commits_considered=len(recent),
        commits_skipped_large=skipped_large,
        pairs_examined=len(together),
        edges_emitted=len(edges),
    )
