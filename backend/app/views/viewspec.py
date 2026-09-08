"""The Visualization Engine's core: KnowledgeGraph -> ViewSpec (CP-4.1).

ARCHITECTURE.md §6: the frontend renders ViewSpecs; it never computes truth.
Everything a pixel shows is decided here, server-side, deterministically:

* **Semantic zoom** — zooming changes *what exists*, not magnification
  (EXPERIENCE.md §"city model"). L1 shows districts (top-level modules) with
  aggregated flows; L2 resolves to files; L3 adds classes and functions.
* **Layout** — precomputed. Clusters sit on a ring (the city's districts);
  members fill each district on a golden-angle spiral, hubs at the center.
  Pure trigonometry: deterministic, dependency-free, O(n).
* **Colors** — the risk ramp (cool blue -> hot red) is FOUNDATION's risk
  formula per file; entrypoints get the accent. Honest theater: a warm node
  IS a risky node, never decoration.
* **Choreography** — `assembly_index` replays real construction order:
  entrypoint files first, then BFS outward through IMPORTS, exactly as
  EXPERIENCE.md specifies the hero moment.
"""

from __future__ import annotations

import math
from collections import deque

from pydantic import BaseModel, Field

from app.graph.schema import CallConfidence, EdgeKind, KnowledgeGraph, Node, NodeKind
from app.graph.traversal import GraphView

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}

#: Weakest-first, for aggregating parallel edges honestly.
_CONFIDENCE_ORDER = [
    CallConfidence.DYNAMIC_UNKNOWN,
    CallConfidence.HEURISTIC,
    CallConfidence.RESOLVED,
]

_ACCENT_ENTRYPOINT = "#34d399"  # where execution starts: the green doors
_CLUSTER_COLOR = "#1e293b"

#: Above this many files, thin the PICTURE — not the graph. EXPERIENCE.md's
#: non-negotiables are "60fps or reduce detail" and "nobody is ever
#: overwhelmed"; a monorepo like n8n (18,767 parseable files) would otherwise
#: hand WebGL an unrenderable node count. Queries (blast radius, risk,
#: centrality...) always run against the full graph regardless — this cap is
#: presentation-only, the same split ARCHITECTURE.md draws between the
#: renderer and the truth it renders.
_MAX_RENDERED_FILES = 600

#: A district holding more than this share of the repo gets split one level
#: deeper (see _assign_clusters) — the monorepo fix.
_MAX_DISTRICT_SHARE = 0.35
_MAX_DISTRICT_DEPTH = 4

#: L3 adds classes and functions orbiting each file. Unbounded, n8n produced
#: 5,035 nodes — a hairball no one can read and WebGL struggles to draw. Only
#: the most-connected files get their members expanded, and each file's
#: orbit is bounded, so L3 stays a readable "street view" instead of a blob.
_MAX_L3_MEMBER_FILES = 120
_MAX_MEMBERS_PER_FILE = 12

#: How tall the depth axis stands, as a share of how wide the map is.
#:
#: A fixed number of world units cannot work: the plan is laid out on a ring
#: whose size follows the repository, so 220 units of drop reads as a real
#: third axis on a 60-file library and as a flat sheet across n8n's 1,464-unit
#: sprawl. Tying the two together keeps the same picture at every scale —
#: clearly stacked, never a tower.
_DEPTH_SHARE = 0.55


def _cap_by_fan_in(files: list[Node], view: GraphView, cap: int) -> list[Node]:
    """Keep the `cap` most-connected files — the hubs a person would look for
    first — dropping leaves. Deterministic: ties broken by id."""
    if len(files) <= cap:
        return files
    ranked = sorted(files, key=lambda f: (-view.fan_in(f.id, DEPENDENCY_KINDS), f.id))
    return ranked[:cap]


class ViewNode(BaseModel):
    id: str
    label: str
    kind: str
    x: float
    y: float
    size: float
    color: str
    cluster: str
    assembly_index: int
    #: The third axis, for renderers that have one. `depth` is the fact —
    #: hops through IMPORTS from the nearest entrypoint file — and `z` is that
    #: fact placed in the same coordinate space as x and y, the same way
    #: `risk` is the fact behind `color`. A 2D renderer ignores both.
    depth: int = 0
    z: float = 0.0
    risk: float = 0.0
    fan_in: int = 0
    is_entrypoint: bool = False
    # Every visual claim clickable down to code (EXPERIENCE non-negotiable):
    file_path: str | None = None
    start_line: int | None = None
    #: The real graph node this stands for. Cluster nodes are a view-layer
    #: invention ("cluster:src") with no node behind them, so anything that
    #: queries the graph must follow this instead of the render id.
    explain_id: str | None = None


class ViewEdge(BaseModel):
    source: str
    target: str
    kind: str
    weight: int = 1  # aggregated relationship count at this zoom
    confidence: str = CallConfidence.RESOLVED.value  # weakest among aggregated


class ViewCluster(BaseModel):
    id: str
    label: str
    x: float
    y: float
    radius: float
    color: str = _CLUSTER_COLOR
    members: int = 0


class ViewSpec(BaseModel):
    zoom: int
    repo_url: str
    commit_sha: str
    nodes: list[ViewNode] = Field(default_factory=list)
    edges: list[ViewEdge] = Field(default_factory=list)
    clusters: list[ViewCluster] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)


def compile_viewspec(graph: KnowledgeGraph, *, zoom: int = 2) -> ViewSpec:
    """The one entry point: a stored graph in, a renderable ViewSpec out."""
    if zoom not in (1, 2, 3):
        raise ValueError(f"zoom must be 1, 2 or 3, got {zoom}")

    view = GraphView(graph)
    files = [n for n in graph.nodes if n.kind is NodeKind.FILE]

    # Districts are split deep only for L1, where naming the real packages is
    # the whole point. L2/L3 keep the coarse top-level grouping: they lay out
    # every file, and 60+ tiny districts scatter the map into disconnected
    # specks instead of the single dense "brain" that makes the street view
    # readable (and screenshot-worthy — EXPERIENCE.md §the hero moment).
    cluster_of = _assign_clusters(files, deep=zoom == 1)
    risk_of = _risk_per_file(view, files)
    assembly = _assembly_order(view, files)
    depth_of = _depth_layers(view, files)
    depth_meta = {"depth_max": max(depth_of.values(), default=0)}

    cluster_ids = sorted(set(cluster_of.values()))
    centers = _ring_positions(cluster_ids, [
        sum(1 for f in files if cluster_of[f.id] == cid) for cid in cluster_ids
    ])

    if zoom == 1:
        spec = _district_view(
            graph, view, files, cluster_of, centers, risk_of, assembly, depth_of,
            depth_meta,
        )
    else:
        spec = _street_view(
            graph, view, files, cluster_of, centers, risk_of, assembly, depth_of,
            depth_meta, include_members=zoom == 3,
        )
    return _recenter(_scale_depth(spec))


def _recenter(spec: ViewSpec) -> ViewSpec:
    """Shift everything so the node centroid sits at the origin.

    Clusters are placed on a ring by geometric position, but real repos are
    lopsided — a 19-file `src` and a 1-file `docs` get equal arcs, leaving the
    visual mass off to one side. The renderer fits the geometric bounding box,
    so without this the dense districts drift to a corner. Centering on the
    centroid (which the dense districts dominate) puts the mass on screen.
    Deterministic: a pure translation of already-deterministic positions.
    """
    if not spec.nodes:
        return spec
    cx = sum(node.x for node in spec.nodes) / len(spec.nodes)
    cy = sum(node.y for node in spec.nodes) / len(spec.nodes)
    # The depth axis is centred for the same reason the other two are: a
    # repository whose layers all sit below zero would hang off the bottom of
    # whatever box a renderer fits around it. `depth` keeps the unshifted
    # fact, so nothing is lost by moving `z`.
    cz = sum(node.z for node in spec.nodes) / len(spec.nodes)
    for node in spec.nodes:
        node.x -= cx
        node.y -= cy
        node.z -= cz
    for cluster in spec.clusters:
        cluster.x -= cx
        cluster.y -= cy
    return spec


# ── zoom 1: districts ─────────────────────────────────────────────────────


def _district_view(
    graph: KnowledgeGraph,
    view: GraphView,
    files: list[Node],
    cluster_of: dict[str, str],
    centers: dict[str, tuple[float, float, float]],
    risk_of: dict[str, float],
    assembly: dict[str, int],
    depth_of: dict[str, int],
    depth_meta: dict[str, object],
) -> ViewSpec:
    """Top-level modules as single nodes; cross-module dependencies as flows."""
    members: dict[str, list[Node]] = {}
    for file in files:
        members.setdefault(cluster_of[file.id], []).append(file)

    nodes: list[ViewNode] = []
    for cluster_id, cluster_files in sorted(members.items()):
        x, y, radius = centers[cluster_id]
        risk = max((risk_of.get(f.id, 0.0) for f in cluster_files), default=0.0)
        # The shallowest member decides, the same way `assembly_index` below
        # takes the earliest: a district is as near the surface as its nearest
        # way in, not as far down as its deepest corner.
        depth = min((depth_of.get(f.id, 0) for f in cluster_files), default=0)
        nodes.append(
            ViewNode(
                id=f"cluster:{cluster_id}",
                label=cluster_id,
                kind="cluster",
                x=x,
                y=y,
                size=radius,
                depth=depth,
                color=_risk_color(risk),
                cluster=cluster_id,
                assembly_index=min(assembly.get(f.id, 10_000) for f in cluster_files),
                risk=risk,
                fan_in=sum(view.fan_in(f.id, DEPENDENCY_KINDS) for f in cluster_files),
                is_entrypoint=any(_contains_entrypoint(view, f) for f in cluster_files),
                explain_id=(
                    f"{NodeKind.MODULE.value}:{cluster_id}"
                    if view.has_node(f"{NodeKind.MODULE.value}:{cluster_id}")
                    else None
                ),
            )
        )

    flows: dict[tuple[str, str], ViewEdge] = {}
    for edge in graph.edges:
        if edge.kind not in DEPENDENCY_KINDS:
            continue
        source_cluster = cluster_of.get(_file_of(view, edge.source_id) or "")
        target_cluster = cluster_of.get(_file_of(view, edge.target_id) or "")
        if not source_cluster or not target_cluster or source_cluster == target_cluster:
            continue
        key = (source_cluster, target_cluster)
        flow = flows.get(key)
        if flow is None:
            flows[key] = ViewEdge(
                source=f"cluster:{source_cluster}",
                target=f"cluster:{target_cluster}",
                kind="flow",
                weight=1,
                confidence=edge.confidence.value,
            )
        else:
            flow.weight += 1
            flow.confidence = _weaker(flow.confidence, edge.confidence.value)

    return ViewSpec(
        zoom=1,
        repo_url=graph.snapshot.repo_url,
        commit_sha=graph.snapshot.commit_sha,
        nodes=nodes,
        edges=sorted(flows.values(), key=lambda e: (-e.weight, e.source, e.target)),
        clusters=[],
        meta={"districts": len(members), "files": len(files), **depth_meta},
    )


# ── zoom 2/3: streets and buildings ───────────────────────────────────────


def _street_view(
    graph: KnowledgeGraph,
    view: GraphView,
    files: list[Node],
    cluster_of: dict[str, str],
    centers: dict[str, tuple[float, float, float]],
    risk_of: dict[str, float],
    assembly: dict[str, int],
    depth_of: dict[str, int],
    depth_meta: dict[str, object],
    *,
    include_members: bool,
) -> ViewSpec:
    """Files laid out inside their districts; L3 adds classes and functions."""
    total_files = len(files)
    files = _cap_by_fan_in(files, view, _MAX_RENDERED_FILES)
    truncated = len(files) < total_files

    members: dict[str, list[Node]] = {}
    for file in files:
        members.setdefault(cluster_of[file.id], []).append(file)

    nodes: list[ViewNode] = []
    positions: dict[str, tuple[float, float]] = {}
    clusters: list[ViewCluster] = []

    for cluster_id in sorted(members):
        cluster_files = members[cluster_id]
        # Hubs to the center of the district: order by fan-in descending.
        cluster_files.sort(
            key=lambda f: (-view.fan_in(f.id, DEPENDENCY_KINDS), f.id)
        )
        cx, cy, radius = centers[cluster_id]
        clusters.append(
            ViewCluster(
                id=cluster_id,
                label=cluster_id,
                x=cx,
                y=cy,
                radius=radius,
                members=len(cluster_files),
            )
        )
        for position, file in enumerate(cluster_files):
            x, y = _spiral_position(cx, cy, radius, position, len(cluster_files))
            positions[file.id] = (x, y)
            fan_in = view.fan_in(file.id, DEPENDENCY_KINDS)
            depth = depth_of.get(file.id, 0)
            nodes.append(
                ViewNode(
                    id=file.id,
                    label=file.name,
                    kind=file.kind.value,
                    x=x,
                    y=y,
                    depth=depth,
                    size=3.0 + min(9.0, 1.5 * math.sqrt(fan_in)),
                    color=(
                        _ACCENT_ENTRYPOINT
                        if _contains_entrypoint(view, file)
                        else _risk_color(risk_of.get(file.id, 0.0))
                    ),
                    cluster=cluster_id,
                    assembly_index=assembly.get(file.id, 10_000),
                    risk=risk_of.get(file.id, 0.0),
                    fan_in=fan_in,
                    is_entrypoint=_contains_entrypoint(view, file),
                    file_path=file.file_path,
                    start_line=1,
                )
            )

    if include_members:
        nodes.extend(
            _member_nodes(view, files, positions, cluster_of, assembly, depth_of)
        )

    node_ids = {n.id for n in nodes}
    edges = _aggregate_edges(graph, view, node_ids, include_members)

    return ViewSpec(
        zoom=3 if include_members else 2,
        repo_url=graph.snapshot.repo_url,
        commit_sha=graph.snapshot.commit_sha,
        nodes=nodes,
        edges=edges,
        clusters=clusters,
        meta={
            "files": total_files,
            "rendered_nodes": len(nodes),
            "truncated": truncated,
            # How tall the stack is. A renderer drawing the depth axis needs
            # it to scale the axis, and deriving it from the nodes would be the
            # frontend computing truth.
            **depth_meta,
        },
    )


def _member_nodes(
    view: GraphView,
    files: list[Node],
    positions: dict[str, tuple[float, float]],
    cluster_of: dict[str, str],
    assembly: dict[str, int],
    depth_of: dict[str, int],
) -> list[ViewNode]:
    """Classes and functions orbit their file at L3 — for the files worth
    expanding. Only the most-connected files get an orbit, and each orbit is
    bounded: unbounded, a monorepo's L3 is an unreadable hairball."""
    found: list[ViewNode] = []
    expandable = _cap_by_fan_in(files, view, _MAX_L3_MEMBER_FILES)
    for file in expandable:
        fx, fy = positions[file.id]
        children = [
            child
            for child_id in sorted(view.children_of(file.id))
            if (child := view.node(child_id)) is not None
        ]
        satellites: list[Node] = []
        for child in children:
            satellites.append(child)
            if child.kind is NodeKind.CLASS:  # methods orbit too
                satellites.extend(
                    grand
                    for grand_id in sorted(view.children_of(child.id))
                    if (grand := view.node(grand_id)) is not None
                )
        # Most-connected members first, then bound the orbit.
        satellites.sort(key=lambda m: (-view.fan_in(m.id, DEPENDENCY_KINDS), m.id))
        depth = depth_of.get(file.id, 0)
        for position, member in enumerate(satellites[:_MAX_MEMBERS_PER_FILE]):
            angle = position * 2.399963  # golden angle: no two satellites overlap
            orbit = 4.0 + 1.2 * (position % 5)
            fan_in = view.fan_in(member.id, DEPENDENCY_KINDS)
            found.append(
                ViewNode(
                    id=member.id,
                    label=member.name,
                    kind=member.kind.value,
                    x=fx + orbit * math.cos(angle),
                    y=fy + orbit * math.sin(angle),
                    # A member sits at its file's depth exactly — it *is* that
                    # file, one level in. Spreading the orbit vertically would
                    # look better and mean nothing: layers are counted between
                    # files, so a per-satellite height would be the one thing
                    # this axis is not allowed to be, which is decoration on a
                    # truth axis. The orbit is already in x and y; here it is a
                    # flat disc, and a flat disc is the honest picture.
                    depth=depth,
                    size=1.2 + min(4.0, 0.8 * math.sqrt(fan_in)),
                    color=_ACCENT_ENTRYPOINT if member.is_entrypoint else "#94a3b8",
                    cluster=cluster_of[file.id],
                    assembly_index=assembly.get(file.id, 10_000),
                    fan_in=fan_in,
                    is_entrypoint=member.is_entrypoint,
                    file_path=member.file_path,
                    start_line=member.start_line,
                )
            )
    return found


def _aggregate_edges(
    graph: KnowledgeGraph,
    view: GraphView,
    node_ids: set[str],
    include_members: bool,
) -> list[ViewEdge]:
    """Dependency edges between rendered nodes; below-zoom edges lift to files.

    Semantic zoom's other half: at L2 a function->function call renders as its
    files' relationship (aggregated, weighted); at L3 it renders as itself.
    """
    aggregated: dict[tuple[str, str, str], ViewEdge] = {}
    for edge in graph.edges:
        if edge.kind not in DEPENDENCY_KINDS:
            continue
        source: str | None = edge.source_id
        target: str | None = edge.target_id
        if not include_members:
            source = source if source in node_ids else _file_of(view, edge.source_id)
            target = target if target in node_ids else _file_of(view, edge.target_id)
        if (
            source is None
            or target is None
            or source == target
            or source not in node_ids
            or target not in node_ids
        ):
            continue
        key = (source, target, edge.kind.value)
        existing = aggregated.get(key)
        if existing is None:
            aggregated[key] = ViewEdge(
                source=source,
                target=target,
                kind=edge.kind.value,
                weight=1,
                confidence=edge.confidence.value,
            )
        else:
            existing.weight += 1
            existing.confidence = _weaker(existing.confidence, edge.confidence.value)
    return sorted(aggregated.values(), key=lambda e: (-e.weight, e.source, e.target))


# ── the deterministic facts behind the pixels ─────────────────────────────


def _cluster_key(file: Node, depth: int = 1) -> str:
    """District = the first `depth` path segments; root files share '(root)'."""
    path = file.file_path or file.qualified_name
    parts = path.split("/")
    if len(parts) <= 1:
        return "(root)"
    return "/".join(parts[: min(depth, len(parts) - 1)])


def _assign_clusters(files: list[Node], *, deep: bool = True) -> dict[str, str]:
    """Districts that stay meaningful on monorepos.

    A flat top-level split is useless where one directory holds nearly
    everything: n8n puts 18,658 of its 18,779 files under `packages/`, so a
    depth-1 split renders one giant blob and four specks — L1 tells you
    nothing. Any district holding more than `_MAX_DISTRICT_SHARE` of the repo
    is therefore re-split one level deeper, repeatedly, until the districts
    are informative or the paths run out. Small repos are unaffected: nothing
    exceeds the share, so this is exactly the old depth-1 behaviour.

    `deep=False` keeps the plain top-level split. The zoomed-in views want it:
    they place every file, and many small districts fling the map apart into
    specks rather than one legible mass.
    """
    assigned = {file.id: _cluster_key(file) for file in files}
    if not deep:
        return assigned
    by_id = {file.id: file for file in files}
    total = max(len(files), 1)

    for _ in range(_MAX_DISTRICT_DEPTH - 1):
        counts: dict[str, int] = {}
        for key in assigned.values():
            counts[key] = counts.get(key, 0) + 1
        oversized = {
            key for key, count in counts.items() if count / total > _MAX_DISTRICT_SHARE
        }
        if not oversized:
            break
        progressed = False
        for file_id, key in list(assigned.items()):
            if key not in oversized:
                continue
            deeper = _cluster_key(by_id[file_id], depth=key.count("/") + 2)
            if deeper != key:
                assigned[file_id] = deeper
                progressed = True
        if not progressed:
            break  # paths exhausted: the directory really is that flat
    return assigned


def _risk_per_file(view: GraphView, files: list[Node]) -> dict[str, float]:
    """FOUNDATION's formula, normalised — the same math the risk query runs."""
    raw: dict[str, float] = {}
    for file in files:
        complexity = _contained_complexity(view, file.id)
        fan_in = view.fan_in(file.id, DEPENDENCY_KINDS)
        churn = file.churn_count or 1
        raw[file.id] = float(max(complexity, 1) * max(fan_in, 1) * max(churn, 1))
    ceiling = max(raw.values(), default=1.0)
    return {file_id: value / ceiling for file_id, value in raw.items()}


def _contained_complexity(view: GraphView, file_id: str) -> int:
    total = 0
    stack = [file_id]
    while stack:
        for child_id in view.children_of(stack.pop()):
            child = view.node(child_id)
            if child is None:
                continue
            if child.kind is NodeKind.FUNCTION and child.complexity:
                total += child.complexity
            stack.append(child_id)
    return total


def _depth_layers(view: GraphView, files: list[Node]) -> dict[str, int]:
    """The vertical axis: how deep in the import stack each file sits.

    A file's layer is the **longest** chain of imports that arrives at it. Not
    the shortest — that was the first attempt and it draws the wrong picture.
    Shortest distance puts a shared helper directly under whoever imports it
    first, so `types.py`, imported by a top-level module *and* by everything
    else, lands at the top of the stack next to the code that starts the
    program. The longest chain says the true thing instead: a file sits below
    everything that leans on it, however far the longest path to it runs, so
    every import on screen points downward and the leaves settle at the floor.

    Layer 0 is the surface: entrypoint files, and files nothing in the
    repository imports. Both are "nothing rests on this" — one because
    execution starts there, the other because it is the top of a dependency
    chain. A library has no `__main__` anywhere and would otherwise have no
    surface at all; this is why jinja rendered as two flat bands before.

    Entrypoints are pinned to the surface even when something does import them
    — a test importing `main.py` does not make `main.py` a detail of the test.

    Import cycles have no layering; that is what a cycle *is*. Their members
    take one layer below the deepest thing outside the loop that reaches them,
    which is where the loop as a whole sits.
    """
    file_ids = {file.id for file in files}
    pinned = {file.id for file in files if _contains_entrypoint(view, file)}

    incoming: dict[str, set[str]] = {file_id: set() for file_id in file_ids}
    outgoing: dict[str, set[str]] = {file_id: set() for file_id in file_ids}
    for source, target in view.subgraph({EdgeKind.IMPORTS}).edges():
        if source not in file_ids or target not in file_ids or source == target:
            continue
        if target in pinned:
            continue  # the surface stays the surface
        incoming[target].add(source)
        outgoing[source].add(target)

    # Longest-path layering, by Kahn's algorithm: a file's layer is settled
    # only once every file that imports it has been placed.
    layer: dict[str, int] = {}
    remaining = {file_id: len(incoming[file_id]) for file_id in file_ids}
    queue: deque[str] = deque(sorted(f for f in file_ids if remaining[f] == 0))
    for file_id in queue:
        layer[file_id] = 0
    while queue:
        current = queue.popleft()
        for nxt in sorted(outgoing[current]):
            layer[nxt] = max(layer.get(nxt, 0), layer[current] + 1)
            remaining[nxt] -= 1
            if remaining[nxt] == 0:
                queue.append(nxt)

    # Whatever is left is inside a cycle: it never reaches zero predecessors.
    # Deterministic, and in id order so the answer never depends on hashing.
    for file_id in sorted(f for f in file_ids if f not in layer):
        placed = [layer[source] for source in incoming[file_id] if source in layer]
        layer[file_id] = (max(placed) + 1) if placed else 0
    return layer


def _scale_depth(spec: ViewSpec) -> ViewSpec:
    """Turn each node's layer into a height, once the plan is known.

    Layers are counted before anything is positioned, but how far apart they
    should sit is a question about *this* layout: the answer has to be read
    off the map that was actually drawn. So the axis is scaled last, against
    the width of what it stands over.

    Deterministic: a fixed share of an already-deterministic extent.
    """
    deepest = max((node.depth for node in spec.nodes), default=0)
    if deepest == 0:
        return spec  # one layer: there is no stack to space out
    xs = [node.x for node in spec.nodes]
    ys = [node.y for node in spec.nodes]
    plan = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    gap = plan * _DEPTH_SHARE / deepest
    for node in spec.nodes:
        node.z = -node.depth * gap
    return spec


def _assembly_order(view: GraphView, files: list[Node]) -> dict[str, int]:
    """Entrypoint files first, then BFS outward through IMPORTS — the reveal
    replays how execution actually reaches the code (EXPERIENCE.md)."""
    imports_view = view.subgraph({EdgeKind.IMPORTS})
    seeds = sorted(f.id for f in files if _contains_entrypoint(view, f))
    order: dict[str, int] = {seed: 0 for seed in seeds}
    queue: deque[str] = deque(seeds)
    while queue:
        current = queue.popleft()
        for _, neighbour in imports_view.out_edges(current):
            if neighbour not in order:
                order[neighbour] = order[current] + 1
                queue.append(neighbour)
    # Files no entrypoint reaches: appended after, hubs first — the city's
    # outskirts light up last.
    unreached = sorted(
        (f.id for f in files if f.id not in order),
        key=lambda fid: (-view.fan_in(fid, DEPENDENCY_KINDS), fid),
    )
    tail_start = (max(order.values()) + 1) if order else 0
    for offset, file_id in enumerate(unreached):
        order[file_id] = tail_start + offset
    return order


def _contains_entrypoint(view: GraphView, file: Node) -> bool:
    if file.is_entrypoint:
        return True
    stack = [file.id]
    while stack:
        for child_id in view.children_of(stack.pop()):
            child = view.node(child_id)
            if child is None:
                continue
            if child.is_entrypoint:
                return True
            stack.append(child_id)
    return False


def _file_of(view: GraphView, node_id: str) -> str | None:
    """Lift any node to its containing file (identity for file nodes)."""
    node = view.node(node_id)
    if node is None:
        return None
    if node.kind is NodeKind.FILE:
        return node.id
    if node.file_path is None:
        return None
    candidate = f"{NodeKind.FILE.value}:{node.file_path}"
    return candidate if view.has_node(candidate) else None


# ── geometry & color ──────────────────────────────────────────────────────


def _ring_positions(
    cluster_ids: list[str], sizes: list[int]
) -> dict[str, tuple[float, float, float]]:
    """Clusters on a ring, radius proportional to member count. Sorted input
    -> identical output, every run (Constitution: deterministic)."""
    count = max(len(cluster_ids), 1)
    radii = [12.0 + 6.0 * math.sqrt(size) for size in sizes]
    ring = max(60.0, sum(radii) * 1.2 / math.pi)
    placed: dict[str, tuple[float, float, float]] = {}
    for index, cluster_id in enumerate(cluster_ids):
        angle = (2 * math.pi * index) / count - math.pi / 2
        placed[cluster_id] = (
            ring * math.cos(angle),
            ring * math.sin(angle),
            radii[index],
        )
    return placed


def _spiral_position(
    cx: float, cy: float, radius: float, index: int, total: int
) -> tuple[float, float]:
    """Golden-angle sunflower spiral: evenly fills the disc, index 0 (the
    biggest hub) at the exact center."""
    if index == 0:
        return cx, cy
    fraction = math.sqrt(index / max(total, 1))
    angle = index * 2.399963
    return (
        cx + radius * 0.85 * fraction * math.cos(angle),
        cy + radius * 0.85 * fraction * math.sin(angle),
    )


def _risk_color(risk: float) -> str:
    """Cool slate-blue (calm) -> hot red (dangerous), linearly in HSL."""
    risk = min(max(risk, 0.0), 1.0)
    hue = 215.0 - 215.0 * risk  # 215 (blue) -> 0 (red)
    saturation = 55 + 30 * risk
    lightness = 62 - 10 * risk
    return _hsl_to_hex(hue, saturation / 100, lightness / 100)


def _hsl_to_hex(hue: float, saturation: float, lightness: float) -> str:
    chroma = (1 - abs(2 * lightness - 1)) * saturation
    x = chroma * (1 - abs((hue / 60) % 2 - 1))
    m = lightness - chroma / 2
    segment = int(hue // 60) % 6
    r, g, b = [
        (chroma, x, 0.0), (x, chroma, 0.0), (0.0, chroma, x),
        (0.0, x, chroma), (x, 0.0, chroma), (chroma, 0.0, x),
    ][segment]
    return f"#{round((r + m) * 255):02x}{round((g + m) * 255):02x}{round((b + m) * 255):02x}"


def _weaker(a: str, b: str) -> str:
    order = [c.value for c in _CONFIDENCE_ORDER]
    return a if order.index(a) <= order.index(b) else b
