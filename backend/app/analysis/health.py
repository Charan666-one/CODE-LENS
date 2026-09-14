"""Architecture health — four measured sub-scores and the arithmetic between.

The temptation with a health score is to invent one: pick some numbers, weight
them by feel, print `82/100`, and let the confidence of the presentation do the
work. That number would be unfalsifiable, and a product whose other headline
feature is an honesty ledger cannot ship it.

So every sub-score here is a standard, named metric with a stated formula, and
the composite always travels with its parts and their weights. A reader who
disagrees with the weighting can recompute from the parts; a reader who
disagrees with a part can recompute it from the edges.

## The four

**Coupling** — from Robert Martin's afferent/efferent coupling. For each
module, `Ca` is how many outside things depend on it and `Ce` is how many
outside things it depends on. A module with high values of *both* is the
problem: it cannot be changed without breaking others and cannot be understood
without reading others. Score falls as more modules sit in that corner.

**Cohesion** — the share of a module's dependency edges that stay inside it.
A module whose files mostly talk to each other is a module; one whose files
mostly talk outward is a folder that happens to exist.

**Circularity** — 100 when nothing is circular, falling with the number and
the length of cycles found (`analysis/cycles.py`). Weighted toward short
cycles, which are both the worst and the most fixable.

**Complexity** — the share of code sitting in files of ordinary size and
cyclomatic complexity, against the parser's own measurements. Not a style
opinion: it counts how much of the repository is in files big enough that
nobody reads them fully before editing.

## What the number is not

It is not comparable between repositories of different kinds — a CLI and a
monorepo have different natural shapes — and nothing here should imply
otherwise. It is useful as *this repo, over time* and as a pointer at which
sub-score is dragging.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.analysis.cycles import DEPENDENCY_KINDS, find_cycles
from app.graph.schema import NodeKind
from app.graph.traversal import GraphView

#: Published with every score so the composite can be recomputed or argued
#: with. Circularity carries the most because a cycle is the one finding here
#: that is a defect rather than a judgement call.
WEIGHTS: dict[str, float] = {
    "circularity": 0.3,
    "coupling": 0.3,
    "cohesion": 0.2,
    "complexity": 0.2,
}

#: A file above both of these is one nobody reads fully before editing.
BIG_FILE_LOC = 400
BIG_FILE_COMPLEXITY = 40

#: A module both heavily depended upon and heavily depending is the shape that
#: makes a codebase rigid. These are the thresholds for "heavily".
HIGH_CA = 5
HIGH_CE = 5


@dataclass
class ModuleMetrics:
    """One module's coupling and cohesion, all four numbers checkable."""

    module_id: str
    name: str
    files: int
    afferent: int  # Ca — outside things that depend on this
    efferent: int  # Ce — outside things this depends on
    internal_edges: int
    crossing_edges: int

    @property
    def instability(self) -> float:
        """`Ce / (Ca + Ce)` — 0 is maximally stable (everyone depends on it,
        it depends on nobody), 1 is maximally unstable. Neither extreme is
        wrong; a module in the middle with high values of both is."""
        total = self.afferent + self.efferent
        return self.efferent / total if total else 0.0

    @property
    def cohesion(self) -> float:
        """Share of this module's dependency edges that stay inside it."""
        total = self.internal_edges + self.crossing_edges
        return self.internal_edges / total if total else 1.0

    @property
    def rigid(self) -> bool:
        """Depended upon heavily *and* depending heavily — the corner that
        makes a codebase hard to change in either direction."""
        return self.afferent >= HIGH_CA and self.efferent >= HIGH_CE


@dataclass
class HealthReport:
    """Sub-scores, the composite, and everything needed to check both."""

    scores: dict[str, int] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=lambda: dict(WEIGHTS))
    modules: list[ModuleMetrics] = field(default_factory=list)
    cycle_count: int = 0
    cycles_truncated: bool = False
    #: Sub-scores that could not be measured for this repo, and why. Reported
    #: rather than defaulted — a metric with no data is not a score of 100.
    unavailable: dict[str, str] = field(default_factory=dict)

    @property
    def overall(self) -> int:
        """Weighted mean of the sub-scores that could actually be measured,
        with the weights renormalised over those. An unmeasurable metric
        neither helps nor hurts."""
        usable = {k: v for k, v in self.scores.items() if k not in self.unavailable}
        if not usable:
            return 0
        total_weight = sum(self.weights[k] for k in usable)
        if total_weight == 0:
            return 0
        return round(sum(self.scores[k] * self.weights[k] for k in usable) / total_weight)


def measure_health(view: GraphView) -> HealthReport:
    """Compute every sub-score from the graph. No estimation anywhere."""
    modules = _module_metrics(view)
    cycles, truncated = find_cycles(view)

    report = HealthReport(
        modules=modules,
        cycle_count=len(cycles),
        cycles_truncated=truncated,
    )

    # ── circularity ──────────────────────────────────────────────────────
    # Each cycle costs more the shorter it is: a two-file loop is the worst
    # kind and the easiest to fix, so it should dominate the score it drags.
    penalty = sum(12 / cycle.length for cycle in cycles)
    report.scores["circularity"] = max(0, round(100 - penalty))

    # ── coupling ─────────────────────────────────────────────────────────
    if modules:
        rigid = sum(1 for module in modules if module.rigid)
        report.scores["coupling"] = round(100 * (1 - rigid / len(modules)))
    else:
        report.scores["coupling"] = 0
        report.unavailable["coupling"] = "no modules with dependency edges"

    # ── cohesion ─────────────────────────────────────────────────────────
    measurable = [m for m in modules if m.internal_edges + m.crossing_edges > 0]
    if measurable:
        report.scores["cohesion"] = round(
            100 * sum(m.cohesion for m in measurable) / len(measurable)
        )
    else:
        report.scores["cohesion"] = 0
        report.unavailable["cohesion"] = "no module has dependency edges to weigh"

    # ── complexity ───────────────────────────────────────────────────────
    files = [
        node
        for node in view.nodes_by_id.values()
        if node.kind is NodeKind.FILE and node.loc is not None
    ]
    if files:
        heavy = sum(
            1
            for node in files
            if (node.loc or 0) > BIG_FILE_LOC
            or _contained_complexity(view, node.id) > BIG_FILE_COMPLEXITY
        )
        report.scores["complexity"] = round(100 * (1 - heavy / len(files)))
    else:
        report.scores["complexity"] = 0
        report.unavailable["complexity"] = "no files reported a line count"

    return report


def _module_metrics(view: GraphView) -> list[ModuleMetrics]:
    """Ca, Ce, and internal/crossing edge counts for every module.

    Reuses the same boundary rule `explain.py` applies to a folder: what
    stays inside is implementation, what crosses is architecture.
    """
    memo = view.memo.get("module_metrics")
    if isinstance(memo, list):
        return memo

    members: dict[str, set[str]] = {}
    for node in view.nodes_by_id.values():
        if node.kind is not NodeKind.MODULE:
            continue
        members[node.id] = _members_of(view, node.id)

    owner_of: dict[str, str] = {}
    for module_id, contained in members.items():
        for member in contained:
            # Deepest module wins, so a file counts toward `src/services`
            # rather than toward `src` — the boundary people mean is the
            # closest one.
            current = owner_of.get(member)
            if current is None or len(module_id) > len(current):
                owner_of[member] = module_id

    afferent: dict[str, set[str]] = defaultdict(set)
    efferent: dict[str, set[str]] = defaultdict(set)
    internal: dict[str, int] = defaultdict(int)
    crossing: dict[str, int] = defaultdict(int)

    for source, target, attributes in view.g.edges(data=True):
        if attributes["kind"] not in DEPENDENCY_KINDS:
            continue
        source_module = owner_of.get(source)
        target_module = owner_of.get(target)
        if source_module is None or target_module is None:
            continue
        if source_module == target_module:
            internal[source_module] += 1
            continue
        crossing[source_module] += 1
        crossing[target_module] += 1
        efferent[source_module].add(target_module)
        afferent[target_module].add(source_module)

    metrics: list[ModuleMetrics] = []
    for module_id, contained in sorted(members.items()):
        module_node = view.node(module_id)
        if module_node is None:
            continue
        file_count = sum(
            1
            for member in contained
            if (member_node := view.node(member)) and member_node.kind is NodeKind.FILE
        )
        if file_count == 0:
            continue  # a directory holding only directories is not a module
        metrics.append(
            ModuleMetrics(
                module_id=module_id,
                name=module_node.name,
                files=file_count,
                afferent=len(afferent[module_id]),
                efferent=len(efferent[module_id]),
                internal_edges=internal[module_id],
                crossing_edges=crossing[module_id],
            )
        )

    view.memo["module_metrics"] = metrics
    return metrics


def _members_of(view: GraphView, node_id: str) -> set[str]:
    found: set[str] = set()
    stack = [node_id]
    while stack:
        for child_id in view.children_of(stack.pop()):
            if child_id not in found:
                found.add(child_id)
                stack.append(child_id)
    return found


def _contained_complexity(view: GraphView, file_id: str) -> int:
    total = 0
    for child_id in view.children_of(file_id):
        child = view.node(child_id)
        if child is None:
            continue
        total += child.complexity or 0
        total += _contained_complexity(view, child_id)
    return total
