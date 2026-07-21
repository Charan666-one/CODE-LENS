"""Python emitter — tree-sitter walk producing facts only.

ARCHITECTURE.md §2: emitters produce facts, never explanations or scores, and
every language emits into the *same* schema. Nothing here knows what a graph
query or an LLM is.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import tree_sitter_python
from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from app.graph.schema import EntrypointKind, Node, NodeKind
from app.parser.complexity import complexity_by_line
from app.parser.facts import FileFacts, RawBase, RawCall, RawImport

_LANGUAGE = Language(tree_sitter_python.language())

#: A callee we are willing to interpret. Anything else (`a.b().c`, subscripts,
#: lambdas) is left unresolved rather than guessed at.
_DOTTED_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

#: Decorator attributes that mark an HTTP handler (FastAPI, Flask, Starlette).
_HTTP_DECORATORS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "route", "websocket"}
)

#: Decorator attributes that mark a CLI command (click, typer).
_CLI_DECORATORS = frozenset({"command", "group"})


def module_qname_for(path: str) -> str:
    """Repo-relative path -> dotted module name (fixtures/README.md convention)."""
    pure = PurePosixPath(path)
    parts = list(pure.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = pure.stem
    return ".".join(parts)


@dataclass
class _Context:
    """Where the walker currently is."""

    prefix: str  # qualified-name prefix for definitions found here
    container_id: str  # node that CONTAINS definitions found here
    scope_id: str  # node a call site at this level is attributed to
    class_name: str | None  # innermost enclosing class, for `self.x` resolution


class PythonEmitter:
    """Walks one Python file and records what it asserts."""

    def __init__(self) -> None:
        self._parser = Parser(_LANGUAGE)

    def emit(self, path: str, source: bytes, content_hash: str, loc: int) -> FileFacts:
        module_qname = module_qname_for(path)
        file_node = Node(
            id=f"{NodeKind.FILE.value}:{path}",
            kind=NodeKind.FILE,
            name=PurePosixPath(path).name,
            qualified_name=path,
            file_path=path,
            language="Python",
            content_hash=content_hash,
            loc=loc,
        )
        facts = FileFacts(
            path=path,
            module_qname=module_qname,
            is_package=PurePosixPath(path).name == "__init__.py",
            file_node=file_node,
        )

        text = source.decode("utf-8", errors="replace")
        walker = _FileWalker(facts, source, complexity_by_line(text))
        tree = self._parser.parse(source)
        walker.visit(
            tree.root_node,
            _Context(
                prefix=module_qname,
                container_id=file_node.id,
                scope_id=file_node.id,
                class_name=None,
            ),
        )
        return facts


class _FileWalker:
    def __init__(self, facts: FileFacts, source: bytes, complexity: dict[int, int]) -> None:
        self.facts = facts
        self.source = source
        self.complexity = complexity
        self._seen_ids: set[str] = set()

    # ── traversal ─────────────────────────────────────────────────────────

    def visit(self, node: TSNode, ctx: _Context) -> None:
        kind = node.type

        if kind in ("import_statement", "import_from_statement"):
            self._record_import(node)
            return
        if kind == "decorated_definition":
            self._visit_decorated(node, ctx)
            return
        if kind in ("class_definition", "function_definition"):
            self._visit_definition(node, ctx, decorators=[])
            return
        if kind == "if_statement" and self._is_main_guard(node):
            self._visit_main_guard(node, ctx)
            return
        if kind == "call":
            self._record_call(node, ctx)
            # fall through: arguments may contain further calls

        for child in node.children:
            if child.is_named:
                self.visit(child, ctx)

    def _visit_decorated(self, node: TSNode, ctx: _Context) -> None:
        decorators = [
            child.text.decode("utf-8", errors="replace")
            for child in node.children
            if child.type == "decorator" and child.text is not None
        ]
        definition = node.child_by_field_name("definition")
        if definition is not None:
            self._visit_definition(definition, ctx, decorators)

    def _visit_definition(self, node: TSNode, ctx: _Context, decorators: list[str]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None or name_node.text is None:
            return
        name = name_node.text.decode("utf-8", errors="replace")

        is_class = node.type == "class_definition"
        kind = NodeKind.CLASS if is_class else NodeKind.FUNCTION
        qualified_name = f"{ctx.prefix}.{name}" if ctx.prefix else name
        node_id = f"{kind.value}:{qualified_name}"

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        body = node.child_by_field_name("body")

        # A redefinition (conditional def, overload) would collide on id. Keep
        # the first: inventing suffixes would fabricate nodes that no call site
        # can ever name.
        if node_id in self._seen_ids:
            return
        self._seen_ids.add(node_id)

        graph_node = Node(
            id=node_id,
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            file_path=self.facts.path,
            start_line=start_line,
            end_line=end_line,
            language="Python",
            content_hash=self._hash_range(node),
            loc=end_line - start_line + 1,
            complexity=None if is_class else self.complexity.get(start_line),
            docstring=self._docstring(body),
            extra=self._extra(node, decorators),
        )
        self.facts.nodes.append(graph_node)
        self.facts.contains.append((ctx.container_id, node_id))

        if ctx.container_id == self.facts.file_node.id:
            target = self.facts.module_classes if is_class else self.facts.module_functions
            target.add(name)

        if is_class:
            self._record_bases(node, node_id)
            inner = _Context(
                prefix=qualified_name,
                container_id=node_id,
                # A call in a class body (a default, a decorator argument) is not
                # inside a function; attribute it to the enclosing scope.
                scope_id=ctx.scope_id,
                class_name=name,
            )
        else:
            inner = _Context(
                prefix=qualified_name,
                container_id=node_id,
                scope_id=node_id,
                class_name=ctx.class_name,
            )

        if body is not None:
            for child in body.children:
                if child.is_named:
                    self.visit(child, inner)

    def _visit_main_guard(self, node: TSNode, ctx: _Context) -> None:
        """`if __name__ == "__main__":` — whatever it calls is an entrypoint."""
        consequence = node.child_by_field_name("consequence")
        if consequence is not None:
            for call in self._descendant_calls(consequence):
                callee = self._callee_text(call)
                if callee is not None:
                    self.facts.entrypoint_calls.append(callee)
        for child in node.children:
            if child.is_named:
                self.visit(child, ctx)

    # ── recording ─────────────────────────────────────────────────────────

    def _record_import(self, node: TSNode) -> None:
        line = node.start_point[0] + 1

        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name" and child.text is not None:
                    module = child.text.decode("utf-8", errors="replace")
                    self.facts.imports.append(
                        RawImport(module=module, level=0, names=[], line=line)
                    )
                elif child.type == "aliased_import":
                    name_node = child.child_by_field_name("name")
                    alias_node = child.child_by_field_name("alias")
                    if name_node is not None and name_node.text is not None:
                        module = name_node.text.decode("utf-8", errors="replace")
                        alias = (
                            alias_node.text.decode("utf-8", errors="replace")
                            if alias_node is not None and alias_node.text is not None
                            else module
                        )
                        self.facts.imports.append(
                            RawImport(module=module, level=0, names=[("", alias)], line=line)
                        )
            return

        # import_from_statement
        module_node = node.child_by_field_name("module_name")
        level = 0
        module = ""
        if module_node is not None and module_node.text is not None:
            raw = module_node.text.decode("utf-8", errors="replace")
            level = len(raw) - len(raw.lstrip("."))
            module = raw.lstrip(".")

        names: list[tuple[str, str]] = []
        for child in node.children:
            if child is module_node:
                continue
            if child.type == "dotted_name" and child.text is not None:
                original = child.text.decode("utf-8", errors="replace")
                names.append((original, original))
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node is not None and name_node.text is not None:
                    original = name_node.text.decode("utf-8", errors="replace")
                    alias = (
                        alias_node.text.decode("utf-8", errors="replace")
                        if alias_node is not None and alias_node.text is not None
                        else original
                    )
                    names.append((original, alias))

        self.facts.imports.append(
            RawImport(module=module, level=level, names=names, line=line)
        )

    def _record_call(self, node: TSNode, ctx: _Context) -> None:
        callee = self._callee_text(node)
        if callee is None:
            return
        self.facts.calls.append(
            RawCall(scope_id=ctx.scope_id, callee=callee, line=node.start_point[0] + 1)
        )

    def _record_bases(self, node: TSNode, class_id: str) -> None:
        arguments = node.child_by_field_name("superclasses")
        if arguments is None:
            return
        line = node.start_point[0] + 1
        for child in arguments.children:
            if not child.is_named or child.text is None:
                continue
            base = child.text.decode("utf-8", errors="replace")
            if _DOTTED_NAME.match(base):
                self.facts.bases.append(RawBase(class_id=class_id, base=base, line=line))

    # ── helpers ───────────────────────────────────────────────────────────

    def _callee_text(self, call: TSNode) -> str | None:
        function = call.child_by_field_name("function")
        if function is None or function.text is None:
            return None
        text = function.text.decode("utf-8", errors="replace")
        return text if _DOTTED_NAME.match(text) else None

    def _descendant_calls(self, node: TSNode) -> list[TSNode]:
        found: list[TSNode] = []
        stack = [node]
        while stack:
            current = stack.pop()
            if current.type == "call":
                found.append(current)
            stack.extend(child for child in current.children if child.is_named)
        return found

    def _is_main_guard(self, node: TSNode) -> bool:
        condition = node.child_by_field_name("condition")
        if condition is None or condition.text is None:
            return False
        text = condition.text.decode("utf-8", errors="replace")
        return "__name__" in text and "__main__" in text

    def _docstring(self, body: TSNode | None) -> str | None:
        if body is None:
            return None
        for child in body.children:
            if not child.is_named:
                continue
            if child.type != "expression_statement":
                return None
            for inner in child.children:
                if inner.type == "string":
                    for part in inner.children:
                        if part.type == "string_content" and part.text is not None:
                            return part.text.decode("utf-8", errors="replace").strip()
            return None
        return None

    def _hash_range(self, node: TSNode) -> str:
        return hashlib.sha256(self.source[node.start_byte : node.end_byte]).hexdigest()

    def _extra(self, node: TSNode, decorators: list[str]) -> dict:
        extra: dict = {}
        if decorators:
            extra["decorators"] = decorators
            entrypoint = classify_decorators(decorators)
            if entrypoint is not None:
                extra["entrypoint_kind"] = entrypoint.value
        parameters = node.child_by_field_name("parameters")
        if parameters is not None and parameters.text is not None:
            extra["parameters"] = parameters.text.decode("utf-8", errors="replace")
        return extra


def classify_decorators(decorators: list[str]) -> EntrypointKind | None:
    """Map decorator source text to an entrypoint kind, if it marks one.

    Deliberately conservative: it keys on the *attribute* (`.get`, `.command`),
    so `@app.get("/x")` and `@router.websocket(...)` are recognised while an
    unrelated `@lru_cache` is not.
    """
    for decorator in decorators:
        stripped = decorator.lstrip("@").strip()
        head = stripped.split("(", 1)[0]
        segments = head.split(".")
        attribute = segments[-1]
        if attribute in _HTTP_DECORATORS and len(segments) > 1:
            return EntrypointKind.HTTP_ROUTE
        if attribute in _CLI_DECORATORS:
            return EntrypointKind.CLI
    return None
