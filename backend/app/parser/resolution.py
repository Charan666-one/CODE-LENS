"""Pass two — turn per-file observations into edges.

Only resolutions that land on a node actually present in this repository
become edges. A call to `print`, or to a third-party package, simply produces
nothing: an edge must have two real endpoints, and inventing a target would be
exactly the fabrication the fact stratum exists to prevent.

CP-1.3 widens this pass — name-based fallbacks and dynamic dispatch get
`heuristic` / `dynamic_unknown` confidence there. Everything emitted here is
statically resolved, so it is all `resolved`.
"""

from __future__ import annotations

from collections.abc import Callable

from app.graph.schema import CallConfidence, Edge, EdgeKind, EntrypointKind, Node, NodeKind
from app.parser.facts import FileFacts, RawImport

#: local name -> (qualified name it refers to, whether that name is a module)
ImportMap = dict[str, tuple[str, bool]]

#: Sink for a resolved edge.
EmitEdge = Callable[[Edge], None]


class SymbolTable:
    """Everything known about the repository once every file has been walked."""

    def __init__(self, facts: list[FileFacts]) -> None:
        self.modules: dict[str, str] = {f.module_qname: f.path for f in facts}
        self.file_ids: dict[str, str] = {f.path: f.file_node.id for f in facts}
        self.functions: dict[str, str] = {}
        self.classes: dict[str, str] = {}
        for file_facts in facts:
            for node in file_facts.nodes:
                if node.kind is NodeKind.FUNCTION:
                    self.functions.setdefault(node.qualified_name, node.id)
                elif node.kind is NodeKind.CLASS:
                    self.classes.setdefault(node.qualified_name, node.id)


def resolve(facts: list[FileFacts]) -> tuple[list[Edge], dict[str, EntrypointKind]]:
    """Resolve every file's observations into edges and entrypoint markings."""
    table = SymbolTable(facts)
    edges: list[Edge] = []
    seen: set[tuple[str, str, str]] = set()
    entrypoints: dict[str, EntrypointKind] = {}

    def emit(edge: Edge) -> None:
        key = (edge.source_id, edge.target_id, edge.kind.value)
        if key in seen:
            return  # one edge per relationship; the first site is the evidence
        seen.add(key)
        edges.append(edge)

    for file_facts in facts:
        import_map = _build_import_map(file_facts, table, emit)
        _resolve_calls(file_facts, table, import_map, emit)
        _resolve_bases(file_facts, table, import_map, emit)
        _mark_entrypoints(file_facts, table, import_map, entrypoints)

    return edges, entrypoints


# ── imports ───────────────────────────────────────────────────────────────


def _build_import_map(facts: FileFacts, table: SymbolTable, emit: EmitEdge) -> ImportMap:
    """Bind local names to qualified names, emitting IMPORTS as a side effect."""
    import_map: ImportMap = {}

    for raw in facts.imports:
        target_module = _target_module(raw, facts)

        if not raw.names:  # plain `import a.b.c`
            head = target_module.split(".")[0]
            if head:
                import_map[head] = (head, True)
            import_map[target_module] = (target_module, True)
            _emit_import_edge(facts, table, target_module, raw.line, emit)
            continue

        if len(raw.names) == 1 and raw.names[0][0] == "":  # `import a.b as c`
            import_map[raw.names[0][1]] = (target_module, True)
            _emit_import_edge(facts, table, target_module, raw.line, emit)
            continue

        # `from module import name[, name as alias]`
        _emit_import_edge(facts, table, target_module, raw.line, emit)
        for original, alias in raw.names:
            candidate = f"{target_module}.{original}" if target_module else original
            is_module = candidate in table.modules
            import_map[alias] = (candidate, is_module)
            if is_module:
                # `from package import submodule` depends on the submodule's file
                _emit_import_edge(facts, table, candidate, raw.line, emit)

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
    facts: FileFacts, table: SymbolTable, module_qname: str, line: int, emit: EmitEdge
) -> None:
    path = table.modules.get(module_qname)
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
        )
    )


# ── calls and bases ───────────────────────────────────────────────────────


def _resolve_calls(
    facts: FileFacts, table: SymbolTable, import_map: ImportMap, emit: EmitEdge
) -> None:
    for call in facts.calls:
        qualified = _resolve_name(call.callee, facts, import_map)
        if qualified is None:
            continue
        target_id = table.functions.get(qualified)
        if target_id is None:
            continue
        emit(
            Edge(
                source_id=call.scope_id,
                target_id=target_id,
                kind=EdgeKind.CALLS,
                file_path=facts.path,
                line=call.line,
                confidence=CallConfidence.RESOLVED,
            )
        )


def _resolve_bases(
    facts: FileFacts, table: SymbolTable, import_map: ImportMap, emit: EmitEdge
) -> None:
    for base in facts.bases:
        qualified = _resolve_name(base.base, facts, import_map, allow_bare_class=True)
        if qualified is None:
            continue
        target_id = table.classes.get(qualified)
        if target_id is None:
            continue
        emit(
            Edge(
                source_id=base.class_id,
                target_id=target_id,
                kind=EdgeKind.INHERITS,
                file_path=facts.path,
                line=base.line,
            )
        )


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

    if segments[0] == "self" and len(segments) >= 2:
        owner = _enclosing_class(facts)
        if owner is not None:
            return f"{owner}." + ".".join(segments[1:])

    if len(segments) == 1:
        if segments[0] in facts.module_functions:
            return f"{facts.module_qname}.{segments[0]}"
        if allow_bare_class and segments[0] in facts.module_classes:
            return f"{facts.module_qname}.{segments[0]}"
        return None

    if segments[0] in facts.module_classes:
        return f"{facts.module_qname}.{name}"

    return None


def _enclosing_class(facts: FileFacts) -> str | None:
    """Owner for `self.x`, only when the file leaves no ambiguity.

    Proper `self` resolution needs the class the call site sits in, which
    arrives with CP-1.3's scope-aware resolver. Until then a single-class file
    is the one case that cannot be wrong.
    """
    if len(facts.module_classes) == 1:
        return f"{facts.module_qname}.{next(iter(facts.module_classes))}"
    return None


# ── entrypoints ───────────────────────────────────────────────────────────


def _mark_entrypoints(
    facts: FileFacts,
    table: SymbolTable,
    import_map: ImportMap,
    entrypoints: dict[str, EntrypointKind],
) -> None:
    for callee in facts.entrypoint_calls:
        qualified = _resolve_name(callee, facts, import_map)
        if qualified is None:
            continue
        target_id = table.functions.get(qualified)
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
