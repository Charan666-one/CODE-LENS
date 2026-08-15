"""Pass two — turn per-file observations into edges, honestly labelled.

ARCHITECTURE.md §2 calls call resolution the genuinely hard problem, and the
design answer is not to solve it perfectly but to *say how sure we are*. Every
CALLS edge carries a confidence:

    resolved         the definition was found by following real symbols —
                     imports, re-exports, the enclosing class, base classes
    heuristic        the callee's method name matches exactly one definition in
                     the repository; duck typing makes that a good bet, not a
                     proof
    dynamic_unknown  the name matches several definitions and the receiver's
                     type is not knowable statically, so the call could reach
                     any of them

Only the first is a claim. The other two are labelled guesses, which is what
lets the ripple UI render them differently and the Accuracy Ledger grade them
separately (Constitution 5: confidence is visible).

Resolution runs in ordered passes because the later ones need the earlier
ones: calls need the class hierarchy, which needs import maps.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from app.graph.schema import CallConfidence, Edge, EdgeKind, EntrypointKind, Node, NodeKind
from app.parser.facts import FileFacts, RawCall, RawImport

#: local name -> (qualified name it refers to, whether that name is a module)
ImportMap = dict[str, tuple[str, bool]]

#: Sink for a resolved edge.
EmitEdge = Callable[[Edge], None]

#: class qualified name -> its resolved base classes
Hierarchy = dict[str, list[str]]

#: Above this many same-named candidates, an edge to each is noise rather than
#: signal — `run()` matching thirty definitions tells a reader nothing.
_MAX_DYNAMIC_CANDIDATES = 4

#: `a` re-exports `b` re-exports `c`… stop before a cycle becomes a hang.
_MAX_REEXPORT_DEPTH = 4

#: Better evidence wins when the same relationship is seen twice.
_CONFIDENCE_RANK = {
    CallConfidence.RESOLVED: 3,
    CallConfidence.HEURISTIC: 2,
    CallConfidence.DYNAMIC_UNKNOWN: 1,
}

_SELF_PREFIX = "self."
_SUPER_PREFIX = "super()."

#: Method names too generic to bet on. These belong to stdlib types and
#: protocols far more often than to repository classes, so matching on them
#: manufactures confident-looking nonsense: `sock.close()` "calls"
#: Session.close, `proxies.copy()` "calls" PreparedRequest.copy,
#: `threading.Event().set()` "calls" RequestsCookieJar.set.
#:
#: This gate applies ONLY to the heuristic and dynamic tiers. A statically
#: resolved `self.close()` is still a proof and still emitted — being generic
#: is only disqualifying when the name is all the evidence there is.
_GENERIC_METHOD_NAMES: frozenset[str] = frozenset(
    {
        "accept", "acquire", "add", "append", "bind", "clear", "close", "connect",
        "copy", "count", "decode", "dump", "dumps", "encode", "endswith", "extend",
        "flush", "format", "get", "index", "insert", "items", "join", "keys",
        "list", "load", "loads", "lower", "next", "open", "pop", "put", "read",
        "readline", "readlines", "recv", "release", "remove", "replace", "reverse",
        "seek", "send", "set", "sort", "split", "startswith", "strip", "tell",
        "update", "upper", "values", "wait", "write",
    }
)


def _is_bettable(name: str) -> bool:
    """Is this method name distinctive enough to guess from?"""
    if name.startswith("__") and name.endswith("__"):
        return False  # dunders are protocol hooks, present on everything
    return name not in _GENERIC_METHOD_NAMES


#: A dotted module name is specific enough to match on its own. A *bare* one
#: is not: `json`, `types`, and `logging` are stdlib far more often than they
#: are a repository's own top-level package, and a repo containing
#: `vendor/json.py` must not capture every `import json` in the tree.
_MIN_SUFFIX_SEGMENTS = 2

#: The exception that makes `src/` layouts work. `import flask` inside the
#: Flask repository does mean `src/flask/__init__.py`, and refusing it left
#: the most-imported file in the project with no incoming edges at all.
#: A bare name is therefore allowed to match when both hold:
#:   * the file it would match is a *package root* — `__init__.py` or
#:     `index.ts` — so the name really is a package, not a stray module, and
#:   * the name is not a standard-library module.
#: `sys.stdlib_module_names` is the authoritative list, so this needs no
#: hand-maintained denylist that would rot with each Python release.
_PACKAGE_ROOT_FILES = (
    "__init__.py",
    "__init__.pyi",
    "index.js",
    "index.jsx",
    "index.mjs",
    "index.cjs",
    "index.ts",
    "index.tsx",
)
_STDLIB_NAMES = frozenset(sys.stdlib_module_names)


class SymbolTable:
    """Everything known about the repository once every file has been walked."""

    def __init__(self, facts: list[FileFacts]) -> None:
        self.modules: dict[str, str] = {f.module_qname: f.path for f in facts}
        # JS's `index.js` is Python's `__init__.py`: importing './store' should
        # find store/index.js. Alias the parent qname; setdefault so a real
        # module named `store` (store.js) always wins over the alias.
        for f in facts:
            if f.module_qname.endswith(".index") and f.path.endswith(
                ("index.js", "index.jsx", "index.mjs", "index.cjs", "index.ts", "index.tsx")
            ):
                self.modules.setdefault(f.module_qname[: -len(".index")], f.path)
        self.file_ids: dict[str, str] = {f.path: f.file_node.id for f in facts}
        self._qname_by_path: dict[str, str] = {f.path: f.module_qname for f in facts}
        self._init_suffix_index()
        self.functions: dict[str, str] = {}
        self.classes: dict[str, str] = {}
        for file_facts in facts:
            for node in file_facts.nodes:
                if node.kind is NodeKind.FUNCTION:
                    self.functions.setdefault(node.qualified_name, node.id)
                elif node.kind is NodeKind.CLASS:
                    self.classes.setdefault(node.qualified_name, node.id)
        self._init_methods_index(facts)

    def _init_suffix_index(self) -> None:
        """Index every module by each trailing part of its name.

        Module names here are derived from the path relative to the *analysis
        root*, but import statements are written relative to the language's
        own source root, and those are rarely the same directory. `backend/`,
        `src/`, `packages/core/src/`, a Next.js `@/` alias — each puts the
        code one or more levels below where imports start counting from.

        Without this, analysing a repo at its root instead of at its source
        directory silently loses almost every import edge: CodeLens's own
        tree went from 179 IMPORTS to 5, and nothing anywhere said so. That is
        the worst class of bug this project can have, because a graph missing
        its edges still looks like a graph.

        Ambiguous suffixes resolve to nothing. If two files could both answer
        to `utils.helpers`, guessing between them would trade a missing edge
        for a wrong one, and a wrong edge is the more expensive mistake.
        """
        counts: dict[str, list[str]] = {}
        for qname, path in self.modules.items():
            segments = qname.split(".")
            is_package_root = path.endswith(_PACKAGE_ROOT_FILES)
            # Every proper suffix; the full name is already an exact key.
            for start in range(1, len(segments)):
                suffix = ".".join(segments[start:])
                if suffix.count(".") + 1 < _MIN_SUFFIX_SEGMENTS and not (
                    is_package_root and suffix not in _STDLIB_NAMES
                ):
                    continue
                counts.setdefault(suffix, []).append(path)

        self._by_suffix: dict[str, str] = {
            suffix: paths[0]
            for suffix, paths in counts.items()
            if len(set(paths)) == 1 and suffix not in self.modules
        }

    def module_path(self, module_qname: str) -> str | None:
        """The file a module name lands on: exact match, else unique suffix."""
        path = self.modules.get(module_qname)
        if path is not None:
            return path
        return self._by_suffix.get(module_qname)

    def canonical_module(self, module_qname: str) -> str:
        """The real qname of the file a module name lands on.

        Identity when the name is already the file's own; otherwise the name
        the file actually carries — `store` -> `store.index` for JS, and
        `app.parser.resolution` -> `backend.app.parser.resolution` when the
        analysis root sits above the source root. Callers build symbol
        candidates from the result, so it must be a name that exists.
        """
        path = self.module_path(module_qname)
        if path is None:
            return module_qname
        return self._qname_by_path.get(path, module_qname)

    def _init_methods_index(self, facts: list[FileFacts]) -> None:
        # Bare method name -> every method with that name. The basis of the
        # heuristic and dynamic tiers. Sorted so output never depends on the
        # order files happened to be walked in.
        self.methods_by_name: dict[str, list[str]] = {}
        every_method: set[str] = set()
        for file_facts in facts:
            every_method |= file_facts.methods
        for qualified_name in sorted(every_method):
            bare = qualified_name.rsplit(".", 1)[-1]
            self.methods_by_name.setdefault(bare, []).append(qualified_name)


def resolve(facts: list[FileFacts]) -> tuple[list[Edge], dict[str, EntrypointKind]]:
    """Resolve every file's observations into edges and entrypoint markings."""
    table = SymbolTable(facts)
    edges: list[Edge] = []
    index: dict[tuple[str, str, str], int] = {}

    def emit(edge: Edge) -> None:
        key = (edge.source_id, edge.target_id, edge.kind.value)
        position = index.get(key)
        if position is None:
            index[key] = len(edges)
            edges.append(edge)
            return
        # Same relationship seen again: keep whichever evidence is stronger.
        if _CONFIDENCE_RANK[edge.confidence] > _CONFIDENCE_RANK[edges[position].confidence]:
            edges[position] = edge

    # 1. Import maps, and the IMPORTS edges they imply.
    import_maps: dict[str, ImportMap] = {
        f.path: _build_import_map(f, table, emit) for f in facts
    }
    reexports: ImportMapsByModule = {f.module_qname: import_maps[f.path] for f in facts}

    # 2. Base classes, which give us the hierarchy calls will need.
    hierarchy: Hierarchy = {}
    for file_facts in facts:
        _resolve_bases(file_facts, table, import_maps[file_facts.path], hierarchy, emit)

    # 3. Calls, now that both are available.
    for file_facts in facts:
        _resolve_calls(
            file_facts, table, import_maps[file_facts.path], reexports, hierarchy, emit
        )

    # 4. Entrypoints.
    entrypoints: dict[str, EntrypointKind] = {}
    for file_facts in facts:
        _mark_entrypoints(
            file_facts, table, import_maps[file_facts.path], reexports, entrypoints
        )

    return edges, entrypoints


#: module qualified name -> that module's import map (its re-export surface)
ImportMapsByModule = dict[str, ImportMap]


# ── imports ───────────────────────────────────────────────────────────────


def _build_import_map(facts: FileFacts, table: SymbolTable, emit: EmitEdge) -> ImportMap:
    """Bind local names to qualified names, emitting IMPORTS as a side effect."""
    import_map: ImportMap = {}

    for raw in facts.imports:
        # Canonicalise so symbol candidates use the qname the target file
        # really has ('store' -> 'store.index' when store/index.js answered).
        target_module = table.canonical_module(_target_module(raw, facts))

        if not raw.names:  # plain `import a.b.c`
            head = target_module.split(".")[0]
            if head:
                import_map[head] = (head, True)
            import_map[target_module] = (target_module, True)
            _emit_import_edge(facts, table, target_module, raw.line, emit, type_only=raw.type_only)
            continue

        if len(raw.names) == 1 and raw.names[0][0] == "":  # `import a.b as c`
            import_map[raw.names[0][1]] = (target_module, True)
            _emit_import_edge(facts, table, target_module, raw.line, emit, type_only=raw.type_only)
            continue

        # `from module import name[, name as alias]`
        submodules: list[str] = []
        for original, alias in raw.names:
            candidate = f"{target_module}.{original}" if target_module else original
            is_module = table.module_path(candidate) is not None
            import_map[alias] = (candidate, is_module)
            if is_module:
                submodules.append(candidate)

        if submodules:
            # `from . import cli` depends on `flask.cli`. It also *executes*
            # `flask/__init__.py` on the way, but that is a module-loading
            # detail rather than an architectural relationship — and recording
            # it made every Python package with a re-exporting `__init__`
            # look circular, because `__init__` imports the submodule right
            # back. Flask reported 20 cycles, essentially all of this shape.
            # The submodule is the dependency the author expressed; that is
            # the edge worth keeping.
            for candidate in submodules:
                _emit_import_edge(
                    facts, table, candidate, raw.line, emit, type_only=raw.type_only
                )
        else:
            # `from .app import Flask` — the names are symbols, so the file
            # that defines them is the dependency.
            _emit_import_edge(
                facts, table, target_module, raw.line, emit, type_only=raw.type_only
            )

    return import_map


def _target_module(raw: RawImport, facts: FileFacts) -> str:
    if raw.level == 0:
        return raw.module
    base = _relative_base(facts.module_qname, facts.is_package, raw.level)
    if not raw.module:
        return base
    return f"{base}.{raw.module}" if base else raw.module


def _relative_base(module_qname: str, is_package: bool, level: int) -> str:
    """Resolve `.`/`..` against the importing module, following Python's rules."""
    parts = module_qname.split(".") if module_qname else []
    if not is_package:
        parts = parts[:-1]  # a module's `.` means its containing package
    for _ in range(level - 1):
        if parts:
            parts.pop()
    return ".".join(parts)


def _emit_import_edge(
    facts: FileFacts,
    table: SymbolTable,
    module_qname: str,
    line: int,
    emit: EmitEdge,
    *,
    type_only: bool = False,
) -> None:
    path = table.module_path(module_qname)
    if path is None:  # third-party or stdlib: Layer B, not Layer A
        return
    target_id = table.file_ids[path]
    if target_id == facts.file_node.id:
        return
    emit(
        Edge(
            source_id=facts.file_node.id,
            target_id=target_id,
            kind=EdgeKind.IMPORTS,
            file_path=facts.path,
            line=line,
            type_only=type_only,
        )
    )


# ── base classes ──────────────────────────────────────────────────────────


def _resolve_bases(
    facts: FileFacts,
    table: SymbolTable,
    import_map: ImportMap,
    hierarchy: Hierarchy,
    emit: EmitEdge,
) -> None:
    for base in facts.bases:
        qualified = _resolve_name(base.base, facts, import_map, allow_bare_class=True)
        if qualified is None:
            continue
        target_id = table.classes.get(qualified)
        if target_id is None:
            continue
        child_qname = base.class_id.split(":", 1)[1]
        hierarchy.setdefault(child_qname, []).append(qualified)
        emit(
            Edge(
                source_id=base.class_id,
                target_id=target_id,
                kind=EdgeKind.INHERITS,
                file_path=facts.path,
                line=base.line,
            )
        )


# ── calls ─────────────────────────────────────────────────────────────────


def _resolve_calls(
    facts: FileFacts,
    table: SymbolTable,
    import_map: ImportMap,
    reexports: ImportMapsByModule,
    hierarchy: Hierarchy,
    emit: EmitEdge,
) -> None:
    for call in facts.calls:
        for target_id, confidence in _call_targets(
            call, facts, table, import_map, reexports, hierarchy
        ):
            emit(
                Edge(
                    source_id=call.scope_id,
                    target_id=target_id,
                    kind=EdgeKind.CALLS,
                    file_path=facts.path,
                    line=call.line,
                    confidence=confidence,
                )
            )


def _call_targets(
    call: RawCall,
    facts: FileFacts,
    table: SymbolTable,
    import_map: ImportMap,
    reexports: ImportMapsByModule,
    hierarchy: Hierarchy,
) -> list[tuple[str, CallConfidence]]:
    """The confidence ladder: prove it, else bet on it, else admit the ambiguity."""
    callee = call.callee

    # `super().method()` — skip the class itself and search its bases.
    if callee.startswith(_SUPER_PREFIX) and call.class_qname is not None:
        found = _lookup_through_bases(
            call.class_qname,
            callee[len(_SUPER_PREFIX) :],
            table,
            hierarchy,
            include_self=False,
        )
        return [(found, CallConfidence.RESOLVED)] if found else []

    # `self.method()` — the call site's own class, then everything it inherits.
    if callee.startswith(_SELF_PREFIX) and call.class_qname is not None:
        found = _lookup_through_bases(
            call.class_qname,
            callee[len(_SELF_PREFIX) :],
            table,
            hierarchy,
            include_self=True,
        )
        if found is not None:
            return [(found, CallConfidence.RESOLVED)]
        # Not on this class: fall through to the name-based tiers below.

    # Real symbols: module functions, imports, aliases, re-export chains.
    qualified = _resolve_name(callee, facts, import_map)
    if qualified is not None:
        found = _follow_reexports(qualified, table, reexports)
        if found is not None:
            return [(found, CallConfidence.RESOLVED)]

    # An attribute call on a value whose type we cannot know. Bet on the name,
    # but only when the name itself carries information.
    if "." in callee:
        bare = callee.rsplit(".", 1)[-1]
        if not _is_bettable(bare):
            return []
        candidates = table.methods_by_name.get(bare, [])
        if len(candidates) == 1:
            return [(table.functions[candidates[0]], CallConfidence.HEURISTIC)]
        if 2 <= len(candidates) <= _MAX_DYNAMIC_CANDIDATES:
            return [
                (table.functions[candidate], CallConfidence.DYNAMIC_UNKNOWN)
                for candidate in candidates
            ]

    # A bare unresolved name is a local variable, a builtin, or a star-import.
    # Guessing there would produce far more noise than signal.
    return []


def _lookup_through_bases(
    class_qname: str,
    attribute: str,
    table: SymbolTable,
    hierarchy: Hierarchy,
    *,
    include_self: bool,
) -> str | None:
    """Find `attribute` on a class or the classes it inherits from."""
    for current in _linearise(class_qname, hierarchy):
        if not include_self and current == class_qname:
            continue
        candidate = f"{current}.{attribute}"
        target_id = table.functions.get(candidate)
        if target_id is not None:
            return target_id
    return None


def _linearise(start: str, hierarchy: Hierarchy) -> list[str]:
    """Breadth-first walk of a class and its bases.

    Not a true C3 linearisation — Python's own MRO needs the full inheritance
    graph including builtins. Breadth-first matches C3 for the single- and
    simple-multiple-inheritance shapes that make up almost all real code, and
    is deterministic, which matters more here than exotic correctness.
    """
    order: list[str] = []
    queue = [start]
    seen: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        order.append(current)
        queue.extend(hierarchy.get(current, []))
    return order


def _follow_reexports(
    qualified_name: str,
    table: SymbolTable,
    reexports: ImportMapsByModule,
    depth: int = 0,
) -> str | None:
    """Resolve to a function id, following `__init__.py` re-export chains.

    `from pkg import helper` names `pkg.helper`, which is not where the
    function lives — pkg/__init__.py's own `from .impl import helper` is the
    hop that finds it.
    """
    direct = table.functions.get(qualified_name)
    if direct is not None:
        return direct
    if depth >= _MAX_REEXPORT_DEPTH or "." not in qualified_name:
        return None

    module_part, _, symbol = qualified_name.rpartition(".")
    binding = reexports.get(module_part, {}).get(symbol)
    if binding is None or binding[0] == qualified_name:
        return None
    return _follow_reexports(binding[0], table, reexports, depth + 1)


# ── shared name resolution ────────────────────────────────────────────────


def _resolve_name(
    name: str, facts: FileFacts, import_map: ImportMap, *, allow_bare_class: bool = False
) -> str | None:
    """Map a dotted source name to a repository-qualified name, or None.

    `allow_bare_class` distinguishes the two contexts a bare name appears in.
    In a call, `Rectangle(...)` is *instantiation* — INSTANTIATES is post-MVP,
    so it resolves to nothing rather than being mislabelled a CALLS edge. In a
    base-class list, the same bare name is precisely the class we want.
    """
    segments = name.split(".")

    # Longest imported prefix wins: `calculator.add` under `import calculator`.
    for cut in range(len(segments), 0, -1):
        prefix = ".".join(segments[:cut])
        if prefix in import_map:
            base_qname, _ = import_map[prefix]
            remainder = segments[cut:]
            return ".".join([base_qname, *remainder]) if remainder else base_qname

    if len(segments) == 1:
        if segments[0] in facts.module_functions:
            return f"{facts.module_qname}.{segments[0]}"
        if allow_bare_class and segments[0] in facts.module_classes:
            return f"{facts.module_qname}.{segments[0]}"
        return None

    if segments[0] in facts.module_classes:
        return f"{facts.module_qname}.{name}"

    return None


# ── entrypoints ───────────────────────────────────────────────────────────


def _mark_entrypoints(
    facts: FileFacts,
    table: SymbolTable,
    import_map: ImportMap,
    reexports: ImportMapsByModule,
    entrypoints: dict[str, EntrypointKind],
) -> None:
    for callee in facts.entrypoint_calls:
        qualified = _resolve_name(callee, facts, import_map)
        if qualified is None:
            continue
        target_id = _follow_reexports(qualified, table, reexports)
        if target_id is not None:
            entrypoints.setdefault(target_id, EntrypointKind.MAIN)

    for node in facts.nodes:
        declared = node.extra.get("entrypoint_kind")
        if declared is not None:
            entrypoints.setdefault(node.id, EntrypointKind(declared))


def apply_entrypoints(nodes: list[Node], entrypoints: dict[str, EntrypointKind]) -> None:
    for node in nodes:
        kind = entrypoints.get(node.id)
        if kind is not None:
            node.is_entrypoint = True
            node.entrypoint_kind = kind
