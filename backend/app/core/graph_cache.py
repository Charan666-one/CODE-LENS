"""Loaded-graph cache — one read and one NetworkX build per snapshot.

Every API request used to reload the whole graph from SQLite and rebuild the
NetworkX MultiDiGraph from scratch. On n8n (73k nodes / 209k edges) that is
~5 seconds *per request*: switching zoom, running a query and opening an
explanation each paid the full cost, three times over, for a graph that had
not changed.

A stored snapshot is immutable in its structure — `save_graph` writes a new
`snapshot_id` rather than mutating one — so the graph and its traversal view
can be cached safely. Annotations are the one mutable part (summaries are
written later), so `invalidate` exists and is called when they land.

Deliberately a small bounded LRU, not Redis: this is one process with one
SQLite file, and an extra service for zero users is the weight CP-0.1
removed. The `GraphStore` interface is where a shared cache would live if
CP-6.1 ever needs one.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable

from app.graph.schema import KnowledgeGraph
from app.graph.store import GraphStore
from app.graph.traversal import GraphView

#: Big graphs are hundreds of MB in RAM; hold only a couple at once. Two lets
#: a user flip between the repo they are reading and the one they just opened
#: without a reload, while bounding worst-case memory.
_MAX_CACHED = 2


class _GraphCache:
    def __init__(self) -> None:
        self._graphs: OrderedDict[int, KnowledgeGraph] = OrderedDict()
        self._views: OrderedDict[int, GraphView] = OrderedDict()
        self._specs: OrderedDict[tuple[int, int], dict] = OrderedDict()
        self._lock = threading.Lock()

    def graph(self, store: GraphStore, snapshot_id: int) -> KnowledgeGraph | None:
        with self._lock:
            cached = self._graphs.get(snapshot_id)
            if cached is not None:
                self._graphs.move_to_end(snapshot_id)
                return cached
        # Load outside the lock: a cold read of a monorepo takes seconds and
        # must not block every other request.
        loaded = store.load_graph_by_id(snapshot_id)
        if loaded is None:
            return None
        with self._lock:
            self._graphs[snapshot_id] = loaded
            self._graphs.move_to_end(snapshot_id)
            self._evict()
        return loaded

    def view(self, store: GraphStore, snapshot_id: int) -> GraphView | None:
        with self._lock:
            cached = self._views.get(snapshot_id)
            if cached is not None:
                self._views.move_to_end(snapshot_id)
                return cached
        graph = self.graph(store, snapshot_id)
        if graph is None:
            return None
        built = GraphView(graph)
        with self._lock:
            self._views[snapshot_id] = built
            self._views.move_to_end(snapshot_id)
            self._evict()
        return built

    def viewspec(self, snapshot_id: int, zoom: int, build: Callable[[], dict]) -> dict:
        """Cache the compiled ViewSpec per (snapshot, zoom).

        Compiling n8n's zoom-2 spec is ~2.3s of layout, colour and capping
        maths over 73k nodes — and it is pure: same graph and zoom, same
        bytes. Without this, every flick between L1/L2/L3 pays it again.
        """
        key = (snapshot_id, zoom)
        with self._lock:
            hit = self._specs.get(key)
            if hit is not None:
                self._specs.move_to_end(key)
                return hit
        built = build()
        with self._lock:
            self._specs[key] = built
            self._specs.move_to_end(key)
            while len(self._specs) > _MAX_CACHED * 3:  # 3 zoom levels per repo
                self._specs.popitem(last=False)
        return built

    def invalidate(self, snapshot_id: int) -> None:
        """Drop a snapshot — call after writing annotations to it."""
        with self._lock:
            self._graphs.pop(snapshot_id, None)
            self._views.pop(snapshot_id, None)
            for key in [k for k in self._specs if k[0] == snapshot_id]:
                self._specs.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._graphs.clear()
            self._views.clear()
            self._specs.clear()

    def _evict(self) -> None:
        while len(self._graphs) > _MAX_CACHED:
            self._graphs.popitem(last=False)
        while len(self._views) > _MAX_CACHED:
            self._views.popitem(last=False)


cache = _GraphCache()
