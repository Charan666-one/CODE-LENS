"""Raw, unresolved facts — the intermediate between walking and resolving.

The parser runs in two passes. Pass one walks each file alone and records what
it *saw* (this name was imported, this call site names `add`). Pass two, once
every file is known, resolves those observations into edges.

The split exists because a call site cannot be resolved from inside the file
that contains it: `multiply(...)` in shapes.py only means
`calculator.multiply` once calculator.py has also been read. Keeping the
unresolved observation as a first-class value is also what lets CP-1.3 attach
confidence levels without rewriting the walker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.graph.schema import Node


@dataclass
class RawImport:
    """An import statement, before the target module is known to exist."""

    module: str  # dotted module text ("" for `from . import x`)
    level: int  # 0 = absolute, 1 = `.`, 2 = `..`
    names: list[tuple[str, str]]  # (original, local_alias); empty => plain `import m`
    line: int


@dataclass
class RawCall:
    """A call site, before its target is known."""

    scope_id: str  # enclosing function node id, or the file node id at module scope
    callee: str  # dotted source text, e.g. "add", "self.area", "calculator.add"
    line: int
    # Qualified name of the class this call sits inside, if any. `self.x` and
    # `super().x` are only resolvable with it — the previous "file has exactly
    # one class" guess was wrong the moment a file had two.
    class_qname: str | None = None


@dataclass
class RawBase:
    """A base class named in a class definition, before it is resolved."""

    class_id: str
    base: str
    line: int


@dataclass
class FileFacts:
    """Everything one file asserts about itself."""

    path: str  # repo-relative, POSIX
    module_qname: str
    is_package: bool  # True for __init__.py
    file_node: Node
    nodes: list[Node] = field(default_factory=list)  # classes + functions
    contains: list[tuple[str, str]] = field(default_factory=list)  # (parent_id, child_id)
    imports: list[RawImport] = field(default_factory=list)
    calls: list[RawCall] = field(default_factory=list)
    bases: list[RawBase] = field(default_factory=list)
    module_functions: set[str] = field(default_factory=set)  # top-level def names
    module_classes: set[str] = field(default_factory=set)  # top-level class names
    methods: set[str] = field(default_factory=set)  # qualified names of methods
    entrypoint_calls: list[str] = field(default_factory=list)  # callees in __main__ guard
