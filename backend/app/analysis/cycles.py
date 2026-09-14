"""Circular dependencies — found at file level, with the evidence attached.

A cycle is the one architectural finding that is unambiguously a defect rather
than a judgement call: if A needs B and B needs A, neither can be understood,
tested, or replaced alone. It is also invisible without a graph, which is why
it belongs here rather than in a linter.

Two things make this tractable on a real repository:

* **File level, not symbol level.** A cycle between two functions in the same
  file is a recursion pattern, not an architecture problem. Projecting to
  files asks the question people actually mean.

* **Bounded search.** Enumerating every simple cycle in a dense graph is
  exponential — n8n would never finish. `length_bound` caps how long a cycle
  may be, and the result count is capped too. The bound is a real limitation
  and is reported, not hidden: a repo can hold cycles longer than the bound
  and this will not have found them.

Short cycles are ranked first because they are both the most damaging and the
most fixable. A two-file cycle is a decision someone can reverse this
afternoon; a nine-file cycle is a redesign.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from app.graph.schema import CallConfidence, EdgeKind, NodeKind
from app.graph.traversal import GraphView

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}

#: **Cycles are built from proven edges only.**
#:
#: This is not caution for its own sake — the first run on this repository
#: reported `python_emitter.py ⇄ js_emitter.py`, and neither file imports or
#: references the other. The loop came from two `dynamic_unknown` CALLS edges:
#: both emitters have a `_FileWalker` with a `visit` method, so `walker.visit()`
#: matched a definition in each file and the dynamic tier emitted an edge to
#: both, fabricating a mutual dependency out of a name collision.
#:
#: Aggregate metrics can absorb a few bad edges — one wrong edge among
#: hundreds barely moves a coupling average. A cycle cannot: it is a single
#: discrete claim that some files form a loop, presented as an unambiguous
#: defect, and one guessed edge is enough to invent one whole. A finding that
#: strong has to rest on the strongest evidence tier the parser has.
CYCLE_CONFIDENCE = {CallConfidence.RESOLVED}

#: Type-only imports are excluded for a related but distinct reason. Flask's
#: `config.py` imports `App` under `if TYPE_CHECKING:` so that
#: `sansio/app.py` can import `Config` at runtime — that guard IS the fix for
#: a circular import. Counting it as a cycle reports the remedy as the
#: disease, which is worse than reporting nothing. Same for TypeScript's
#: `import type`. See `Edge.type_only`.

#: Longer than this and a "cycle" stops being something anyone can act on.
#: It also keeps the search from exploding on a dense monorepo.
MAX_CYCLE_LENGTH = 6

#: Findings are a to-do list. Past this nobody reads, and the tail is always
#: longer variants of the cycles already listed.
MAX_CYCLES = 40


@dataclass
class Cycle:
    """One dependency loop, with the file:line that closes each hop."""

    files: list[str]  # node ids, in cycle order
    #: (source_id, target_id, file_path, line) for every hop, including the
    #: closing one — the receipt for "these really do depend on each other".
    evidence: list[tuple[str, str, str | None, int | None]] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.files)


def file_projection(
    view: GraphView,
    *,
    confidence: set[CallConfidence] | None = None,
    runtime_only: bool = False,
) -> nx.DiGraph:
    """Dependencies lifted to the files that hold them.

    A call from `a.py:foo` to `b.py:bar` is a dependency of `a.py` on `b.py`;
    without this lift, function-level cycles and file-level cycles get mixed
    together and neither question is answered.

    `confidence` filters which evidence tiers may contribute an edge. See
    `CYCLE_CONFIDENCE` for why cycle detection passes the strictest set.
    `runtime_only` drops type-only imports — see `Edge.type_only`.
    """
    key = "file_projection:" + (
        "all" if confidence is None else ",".join(sorted(c.value for c in confidence))
    ) + (":runtime" if runtime_only else "")
    memo = view.memo.get(key)
    if isinstance(memo, nx.DiGraph):
        return memo

    projection = nx.DiGraph()
    for node in view.nodes_by_id.values():
        if node.kind is NodeKind.FILE:
            projection.add_node(node.id)

    for source, target, attributes in view.g.edges(data=True):
        if attributes["kind"] not in DEPENDENCY_KINDS:
            continue
        if confidence is not None and attributes["confidence"] not in confidence:
            continue
        if runtime_only and attributes.get("type_only"):
            continue
        owner = _owning_file(view, source)
        owned = _owning_file(view, target)
        if owner is None or owned is None or owner == owned:
            continue
        if not projection.has_edge(owner, owned):
            projection.add_edge(
                owner,
                owned,
                file_path=attributes.get("file_path"),
                line=attributes.get("line"),
            )

    view.memo[key] = projection
    return projection


def _owning_file(view: GraphView, node_id: str) -> str | None:
    node = view.node(node_id)
    if node is None:
        return None
    if node.kind is NodeKind.FILE:
        return node.id
    if node.file_path:
        candidate = f"{NodeKind.FILE.value}:{node.file_path}"
        return candidate if view.has_node(candidate) else None
    return None


def find_cycles(
    view: GraphView,
    *,
    max_length: int = MAX_CYCLE_LENGTH,
    limit: int = MAX_CYCLES,
) -> tuple[list[Cycle], bool]:
    """Every dependency loop up to `max_length`, shortest first.

    Returns the cycles and whether the search hit its cap — a caller that
    reports "no cycles" must be able to tell that apart from "we stopped
    looking", and the difference matters to anyone trusting the number.
    """
    projection = file_projection(
        view, confidence=CYCLE_CONFIDENCE, runtime_only=True
    )

    found: list[Cycle] = []
    truncated = False
    for nodes in nx.simple_cycles(projection, length_bound=max_length):
        if len(found) >= limit:
            truncated = True
            break
        evidence: list[tuple[str, str, str | None, int | None]] = []
        for index, source in enumerate(nodes):
            target = nodes[(index + 1) % len(nodes)]
            data = projection.get_edge_data(source, target) or {}
            evidence.append((source, target, data.get("file_path"), data.get("line")))
        found.append(Cycle(files=list(nodes), evidence=evidence))

    # Shortest first: a two-file cycle is reversible this afternoon, a
    # six-file cycle is a redesign. Then by id, so output never depends on
    # the order networkx happened to walk in.
    found.sort(key=lambda cycle: (cycle.length, cycle.files))
    return found, truncated
