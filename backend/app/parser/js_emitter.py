"""JavaScript/TypeScript emitter — same schema for both (CP: JS + TS support).

The architectural promise being kept here (ARCHITECTURE.md §2): "each new
language is a new emitter into the *same* schema." TypeScript proves it hard:
its grammar is a superset of JavaScript's, so the *same* walker serves both —
only the tree-sitter grammar and the reported language name are injected
(see `app.parser.__init__`). This module emits the same FileFacts the Python
emitter does; the same resolution pass turns them into edges. Nothing
downstream knows a third language exists.

Coverage, stated honestly:
* ESM imports (named, default, namespace, aliased) and CommonJS
  `require('./x')` bindings. Relative specifiers *and* tsconfig path aliases
  (`@/components/Navbar`) resolve; bare specifiers (`react`, `lodash`) are
  external packages — never a fact edge.
* Functions: declarations, arrow/function expressions assigned to a
  variable, and class methods. Classes with `extends`.
* `this.method()` and `super.method()` normalise to the same self/super
  resolution the Python side uses.
* `if (require.main === module)` is Node's `__main__` guard: entrypoint.
* TypeScript type-only constructs (interfaces, type aliases, annotations)
  have no runtime nodes and simply produce nothing — correct, not missing.
* JSX composition (`<Component/>`) is not a function call and is not modelled
  as an edge yet, so React apps show structure and imports but few CALLS —
  honest for that framework's model.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import tree_sitter_javascript
from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from app.graph.schema import Node, NodeKind
from app.parser.facts import FileFacts, RawBase, RawCall, RawImport

_LANGUAGE = Language(tree_sitter_javascript.language())

#: Callee shapes we interpret; anything fancier stays unresolved, not guessed.
_DOTTED_NAME = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(\.[A-Za-z_$][A-Za-z0-9_$]*)*$")

#: Relative-import extensions to strip. TypeScript imports usually omit the
#: extension entirely (`from './util'`), which needs no stripping; these cover
#: the explicit-extension cases in both ecosystems.
_JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")

#: Decision points for the cyclomatic approximation (the lizard-style count):
#: branches, loops, case arms, catches, short-circuits, ternaries.
_DECISION = re.compile(r"\b(?:if|for|while|case|catch)\b|&&|\|\||\?[^.?]")


def js_module_qname(path: str) -> str:
    """src/utils/calc.js -> src.utils.calc  ·  src/store/index.js keeps its
    `.index` tail here; SymbolTable aliases the parent (Python's __init__
    analog lives in resolution, where the whole repo is visible)."""
    pure = PurePosixPath(path)
    parts = list(pure.parts)
    parts[-1] = pure.stem
    return ".".join(parts)


def resolve_alias(specifier: str, aliases: dict[str, str]) -> str | None:
    """Resolve a tsconfig path alias to a repo-relative module qname.

    Next.js and most TS apps import their own code through aliases like
    `@/components/Navbar`, mapped in tsconfig `compilerOptions.paths`. Without
    this they look external and the app's real wiring is invisible. `aliases`
    maps an alias prefix ("@/") to a dotted module prefix ("src."); the longest
    matching prefix wins.
    """
    for prefix in sorted(aliases, key=len, reverse=True):
        if specifier.startswith(prefix):
            rest = specifier[len(prefix) :].replace("/", ".")
            base = aliases[prefix]
            combined = f"{base}{rest}" if base else rest
            return combined.strip(".") or None
    return None


def _resolve_relative(specifier: str, importing_path: str) -> str | None:
    """'./calc.js' seen from src/main.js -> 'src.calc'. None for bare/external."""
    if not specifier.startswith("."):
        return None
    base = PurePosixPath(importing_path).parent
    combined: list[str] = list(base.parts)
    for segment in specifier.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if combined:
                combined.pop()
            continue
        combined.append(segment)
    if not combined:
        return None
    last = combined[-1]
    for extension in _JS_EXTENSIONS:
        if last.endswith(extension):
            combined[-1] = last[: -len(extension)]
            break
    return ".".join(combined)


@dataclass
class _Context:
    prefix: str
    container_id: str
    scope_id: str
    class_qname: str | None


class JsEmitter:
    """Walks one JavaScript/TypeScript file and records what it asserts.

    TypeScript's grammar is a superset of JavaScript's — the same node types
    (imports, functions, classes, calls) with type annotations layered on — so
    one walker serves both. The grammar and the reported language name are
    injected: `.js/.jsx/.mjs/.cjs` use tree-sitter-javascript, `.ts` and `.tsx`
    use tree-sitter-typescript. Type-only constructs (interfaces, type aliases)
    have no runtime nodes and simply produce nothing.
    """

    def __init__(
        self,
        language: Language | None = None,
        language_name: str = "JavaScript",
        aliases: dict[str, str] | None = None,
    ) -> None:
        self._parser = Parser(language or _LANGUAGE)
        self._language_name = language_name
        self._aliases = aliases or {}

    def emit(self, path: str, source: bytes, content_hash: str, loc: int) -> FileFacts:
        module_qname = js_module_qname(path)
        file_node = Node(
            id=f"{NodeKind.FILE.value}:{path}",
            kind=NodeKind.FILE,
            name=PurePosixPath(path).name,
            qualified_name=path,
            file_path=path,
            language=self._language_name,
            content_hash=content_hash,
            loc=loc,
        )
        facts = FileFacts(
            path=path,
            module_qname=module_qname,
            is_package=False,
            file_node=file_node,
        )
        walker = _JsWalker(facts, source, self._language_name, self._aliases)
        tree = self._parser.parse(source)
        walker.visit(
            tree.root_node,
            _Context(
                prefix=module_qname,
                container_id=file_node.id,
                scope_id=file_node.id,
                class_qname=None,
            ),
        )
        return facts


class _JsWalker:
    def __init__(
        self,
        facts: FileFacts,
        source: bytes,
        language_name: str = "JavaScript",
        aliases: dict[str, str] | None = None,
    ) -> None:
        self.facts = facts
        self.source = source
        self.language_name = language_name
        self.aliases = aliases or {}
        self._seen_ids: set[str] = set()

    # ── traversal ─────────────────────────────────────────────────────────

    def visit(self, node: TSNode, ctx: _Context) -> None:
        kind = node.type

        if kind == "import_statement":
            self._record_esm_import(node)
            return
        if kind == "export_statement":
            for child in node.children:  # unwrap: `export function f` etc.
                if child.is_named:
                    self.visit(child, ctx)
            return
        if kind == "function_declaration":
            self._emit_function(node, self._name_of(node), node, ctx)
            return
        if kind == "class_declaration":
            self._emit_class(node, ctx)
            return
        if kind in ("lexical_declaration", "variable_declaration"):
            self._visit_declaration(node, ctx)
            return
        # Non-definition assignments fall through and recurse normally.
        if kind == "assignment_expression" and self._visit_assignment(node, ctx):
            return
        if kind == "if_statement" and self._is_main_guard(node):
            self._visit_main_guard(node, ctx)
            return
        if kind == "call_expression":
            self._record_call(node, ctx)
            # fall through: arguments may hold callbacks and further calls

        for child in node.children:
            if child.is_named:
                self.visit(child, ctx)

    def _visit_declaration(self, node: TSNode, ctx: _Context) -> None:
        for declarator in node.children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            value = declarator.child_by_field_name("value")
            if value is None:
                continue
            if value.type in ("arrow_function", "function_expression") and (
                name_node is not None and name_node.type == "identifier"
            ):
                self._emit_function(declarator, name_node, value, ctx)
                continue
            if value.type == "call_expression" and self._is_require(value):
                self._record_require(declarator, value)
                continue
            self.visit(value, ctx)

    def _visit_assignment(self, node: TSNode, ctx: _Context) -> bool:
        """CommonJS's other way of defining functions:

            exports.foo = function () {}          -> module function `foo`
            module.exports.foo = () => {}         -> module function `foo`
            App.prototype.listen = function () {} -> method `App.listen`
            res.send = function send(body) {}     -> method-ish `res.send`

        Express and most pre-ESM Node code define their entire public API
        this way; without this, lib/response.js has zero functions. Returns
        True when a definition was emitted (the walker stops descending —
        the function body is walked by _emit_function).
        """
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None or left.text is None:
            return False
        if right.type not in ("arrow_function", "function_expression"):
            return False
        target = left.text.decode("utf-8", errors="replace")
        if not _DOTTED_NAME.match(target):
            return False

        segments = target.split(".")
        if segments[0] == "module" and len(segments) >= 2 and segments[1] == "exports":
            segments = segments[1:]  # module.exports.foo ≡ exports.foo
        if segments[0] == "exports" and len(segments) == 2:
            # An export IS a module-level function: require() bindings and
            # `from`-style resolution find it by `module.name`.
            self._emit_assigned_function(node, right, segments[1], ctx, as_module_fn=True)
            return True
        if len(segments) == 3 and segments[1] == "prototype":
            # Class method after the fact: X.prototype.y -> X.y
            owner = segments[0]
            method_ctx = _Context(
                prefix=f"{ctx.prefix}.{owner}" if ctx.prefix else owner,
                container_id=ctx.container_id,
                scope_id=ctx.scope_id,
                class_qname=f"{ctx.prefix}.{owner}" if ctx.prefix else owner,
            )
            self._emit_assigned_function(node, right, segments[2], method_ctx)
            return True
        if len(segments) == 2 and segments[0] not in ("exports", "module", "window", "globalThis"):
            # `res.send = function ...`: a method on some object. Qualified
            # under the object's name; registered as a method so `x.send()`
            # can match it in the heuristic tier.
            owner_ctx = _Context(
                prefix=f"{ctx.prefix}.{segments[0]}" if ctx.prefix else segments[0],
                container_id=ctx.container_id,
                scope_id=ctx.scope_id,
                class_qname=f"{ctx.prefix}.{segments[0]}" if ctx.prefix else segments[0],
            )
            self._emit_assigned_function(node, right, segments[1], owner_ctx)
            return True
        return False

    def _emit_assigned_function(
        self,
        span: TSNode,
        function_node: TSNode,
        name: str,
        ctx: _Context,
        *,
        as_module_fn: bool = False,
    ) -> None:
        qualified_name = f"{ctx.prefix}.{name}" if ctx.prefix else name
        node_id = f"{NodeKind.FUNCTION.value}:{qualified_name}"
        if node_id in self._seen_ids:
            return
        self._seen_ids.add(node_id)

        start_line = span.start_point[0] + 1
        end_line = span.end_point[0] + 1
        snippet = self.source[span.start_byte : span.end_byte]
        self.facts.nodes.append(
            Node(
                id=node_id,
                kind=NodeKind.FUNCTION,
                name=name,
                qualified_name=qualified_name,
                file_path=self.facts.path,
                start_line=start_line,
                end_line=end_line,
                language=self.language_name,
                content_hash=hashlib.sha256(snippet).hexdigest(),
                loc=end_line - start_line + 1,
                complexity=self._complexity(snippet),
                extra=self._parameters(function_node),
            )
        )
        self.facts.contains.append((self.facts.file_node.id, node_id))
        if as_module_fn:
            self.facts.module_functions.add(name)
        else:
            self.facts.methods.add(qualified_name)

        inner = _Context(
            prefix=qualified_name,
            container_id=node_id,
            scope_id=node_id,
            class_qname=ctx.class_qname,
        )
        body = function_node.child_by_field_name("body")
        if body is not None:
            self.visit(body, inner)

    # ── definitions ───────────────────────────────────────────────────────

    def _emit_function(
        self, span: TSNode, name_node: TSNode | None, body_holder: TSNode, ctx: _Context
    ) -> None:
        if name_node is None or name_node.text is None:
            return
        name = name_node.text.decode("utf-8", errors="replace")
        qualified_name = f"{ctx.prefix}.{name}" if ctx.prefix else name
        node_id = f"{NodeKind.FUNCTION.value}:{qualified_name}"
        if node_id in self._seen_ids:
            return
        self._seen_ids.add(node_id)

        start_line = span.start_point[0] + 1
        end_line = span.end_point[0] + 1
        snippet = self.source[span.start_byte : span.end_byte]

        graph_node = Node(
            id=node_id,
            kind=NodeKind.FUNCTION,
            name=name,
            qualified_name=qualified_name,
            file_path=self.facts.path,
            start_line=start_line,
            end_line=end_line,
            language=self.language_name,
            content_hash=hashlib.sha256(snippet).hexdigest(),
            loc=end_line - start_line + 1,
            complexity=self._complexity(snippet),
            extra=self._parameters(body_holder),
        )
        self.facts.nodes.append(graph_node)
        self.facts.contains.append((ctx.container_id, node_id))
        if ctx.container_id == self.facts.file_node.id:
            self.facts.module_functions.add(name)
        if ctx.class_qname is not None:
            self.facts.methods.add(qualified_name)

        inner = _Context(
            prefix=qualified_name,
            container_id=node_id,
            scope_id=node_id,
            class_qname=ctx.class_qname,
        )
        body = body_holder.child_by_field_name("body")
        if body is not None:
            self.visit(body, inner)

    def _emit_class(self, node: TSNode, ctx: _Context) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None or name_node.text is None:
            return
        name = name_node.text.decode("utf-8", errors="replace")
        qualified_name = f"{ctx.prefix}.{name}" if ctx.prefix else name
        node_id = f"{NodeKind.CLASS.value}:{qualified_name}"
        if node_id in self._seen_ids:
            return
        self._seen_ids.add(node_id)

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        snippet = self.source[node.start_byte : node.end_byte]
        self.facts.nodes.append(
            Node(
                id=node_id,
                kind=NodeKind.CLASS,
                name=name,
                qualified_name=qualified_name,
                file_path=self.facts.path,
                start_line=start_line,
                end_line=end_line,
                language=self.language_name,
                content_hash=hashlib.sha256(snippet).hexdigest(),
                loc=end_line - start_line + 1,
            )
        )
        self.facts.contains.append((ctx.container_id, node_id))
        if ctx.container_id == self.facts.file_node.id:
            self.facts.module_classes.add(name)

        for heritage in node.children:
            if heritage.type == "class_heritage":
                for base in heritage.children:
                    if base.is_named and base.text is not None:
                        base_text = base.text.decode("utf-8", errors="replace")
                        if _DOTTED_NAME.match(base_text):
                            self.facts.bases.append(
                                RawBase(
                                    class_id=node_id,
                                    base=base_text,
                                    line=start_line,
                                )
                            )

        inner = _Context(
            prefix=qualified_name,
            container_id=node_id,
            scope_id=ctx.scope_id,
            class_qname=qualified_name,
        )
        body = node.child_by_field_name("body")
        if body is not None:
            for member in body.children:
                if member.type == "method_definition":
                    method_name = member.child_by_field_name("name")
                    self._emit_function(member, method_name, member, inner)

    # ── imports ───────────────────────────────────────────────────────────

    def _record_esm_import(self, node: TSNode) -> None:
        line = node.start_point[0] + 1
        source_node = node.child_by_field_name("source")
        if source_node is None or source_node.text is None:
            return
        specifier = source_node.text.decode("utf-8", errors="replace").strip("'\"")
        target = _resolve_relative(specifier, self.facts.path) or resolve_alias(
            specifier, self.aliases
        )
        if target is None:
            return  # bare specifier: external package, Layer B's business

        names: list[tuple[str, str]] = []
        for clause in node.children:
            if clause.type != "import_clause":
                continue
            for item in clause.children:
                if item.type == "identifier" and item.text is not None:
                    # default import: binds the module's default export; treat
                    # the local name as a namespace-ish module binding.
                    names.append(("", item.text.decode("utf-8", errors="replace")))
                elif item.type == "namespace_import":
                    for inner in item.children:
                        if inner.type == "identifier" and inner.text is not None:
                            names.append(
                                ("", inner.text.decode("utf-8", errors="replace"))
                            )
                elif item.type == "named_imports":
                    for spec in item.children:
                        if spec.type != "import_specifier":
                            continue
                        identifiers = [
                            child.text.decode("utf-8", errors="replace")
                            for child in spec.children
                            if child.type == "identifier" and child.text is not None
                        ]
                        if len(identifiers) == 1:
                            names.append((identifiers[0], identifiers[0]))
                        elif len(identifiers) >= 2:
                            names.append((identifiers[0], identifiers[1]))

        if not names:  # side-effect import: `import './setup.js'`
            self.facts.imports.append(
                RawImport(module=target, level=0, names=[], line=line)
            )
            return
        for original, alias in names:
            if original == "":
                # module-object binding (default/namespace): alias -> module
                self.facts.imports.append(
                    RawImport(module=target, level=0, names=[("", alias)], line=line)
                )
            else:
                self.facts.imports.append(
                    RawImport(
                        module=target, level=0, names=[(original, alias)], line=line
                    )
                )

    def _record_require(self, declarator: TSNode, call: TSNode) -> None:
        line = declarator.start_point[0] + 1
        arguments = call.child_by_field_name("arguments")
        if arguments is None:
            return
        specifier: str | None = None
        for argument in arguments.children:
            if argument.type == "string" and argument.text is not None:
                specifier = argument.text.decode("utf-8", errors="replace").strip("'\"")
                break
        if specifier is None:
            return
        target = _resolve_relative(specifier, self.facts.path) or resolve_alias(
            specifier, self.aliases
        )
        if target is None:
            return

        name_node = declarator.child_by_field_name("name")
        if name_node is None:
            return
        if name_node.type == "identifier" and name_node.text is not None:
            alias = name_node.text.decode("utf-8", errors="replace")
            self.facts.imports.append(
                RawImport(module=target, level=0, names=[("", alias)], line=line)
            )
        elif name_node.type == "object_pattern":
            # const { one, two } = require('./pair')  ->  named bindings
            for prop in name_node.children:
                if (
                    prop.type == "shorthand_property_identifier_pattern"
                    and prop.text is not None
                ):
                    bound = prop.text.decode("utf-8", errors="replace")
                    self.facts.imports.append(
                        RawImport(module=target, level=0, names=[(bound, bound)], line=line)
                    )

    # ── calls & entrypoints ───────────────────────────────────────────────

    def _record_call(self, node: TSNode, ctx: _Context) -> None:
        callee = self._callee_text(node)
        if callee is None:
            return
        self.facts.calls.append(
            RawCall(
                scope_id=ctx.scope_id,
                callee=callee,
                line=node.start_point[0] + 1,
                class_qname=ctx.class_qname,
            )
        )

    def _callee_text(self, call: TSNode) -> str | None:
        function = call.child_by_field_name("function")
        if function is None or function.text is None:
            return None
        text = function.text.decode("utf-8", errors="replace")
        # Same resolution machinery as Python: `this` is `self`,
        # `super.x` is `super().x`.
        if text.startswith("this."):
            text = "self." + text[len("this.") :]
        elif text.startswith("super."):
            return "super()." + text[len("super.") :]
        return text if _DOTTED_NAME.match(text) else None

    def _is_require(self, call: TSNode) -> bool:
        function = call.child_by_field_name("function")
        return (
            function is not None
            and function.type == "identifier"
            and function.text == b"require"
        )

    def _is_main_guard(self, node: TSNode) -> bool:
        """`if (require.main === module)` — Node's `__main__` idiom."""
        condition = node.child_by_field_name("condition")
        if condition is None or condition.text is None:
            return False
        text = condition.text.decode("utf-8", errors="replace")
        return "require.main" in text and "module" in text

    def _visit_main_guard(self, node: TSNode, ctx: _Context) -> None:
        consequence = node.child_by_field_name("consequence")
        if consequence is not None:
            stack = [consequence]
            while stack:
                current = stack.pop()
                if current.type == "call_expression":
                    callee = self._callee_text(current)
                    if callee is not None:
                        self.facts.entrypoint_calls.append(callee)
                stack.extend(child for child in current.children if child.is_named)
        for child in node.children:
            if child.is_named:
                self.visit(child, ctx)

    # ── metadata ──────────────────────────────────────────────────────────

    def _complexity(self, snippet: bytes) -> int:
        """Cyclomatic approximation: decision points + 1, counted lexically.

        radon cannot read JavaScript; counting branch/loop/case/catch/
        short-circuit tokens is the standard lizard-style estimate. It can
        over-count tokens inside strings — an accepted, documented error bar,
        used for *ranking* risk, never presented as an exact measurement.
        """
        text = snippet.decode("utf-8", errors="replace")
        return 1 + len(_DECISION.findall(text))

    def _parameters(self, holder: TSNode) -> dict:
        parameters = holder.child_by_field_name("parameters")
        if parameters is not None and parameters.text is not None:
            return {"parameters": parameters.text.decode("utf-8", errors="replace")}
        return {}

    def _name_of(self, node: TSNode) -> TSNode | None:
        return node.child_by_field_name("name")
