"""JS emitter — the behaviours the golden fixture doesn't already pin.

tiny_js covers the structural mirror of tiny_python. These tests cover the
CommonJS reality: assignment-defined APIs, exports-to-require linkage,
index.js resolution, and the things that must produce NO edge.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from app.graph.schema import EdgeKind, KnowledgeGraph
from app.parser import parse_repository


def build(tmp_path: Path, files: dict[str, str]) -> KnowledgeGraph:
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(content).lstrip())
    return parse_repository(tmp_path)


def calls(graph: KnowledgeGraph) -> dict[tuple[str, str], str]:
    return {
        (e.source_id, e.target_id): e.confidence.value
        for e in graph.edges
        if e.kind is EdgeKind.CALLS
    }


# ── TypeScript: same walker, superset grammar ─────────────────────────────


def test_typescript_functions_and_types_resolve(tmp_path: Path) -> None:
    """.ts files parse through the TS grammar; type annotations don't break
    function/class/call extraction, and type-only constructs produce nothing."""
    graph = build(
        tmp_path,
        {
            "util.ts": "export function helper(n: number): number { return n + 1; }\n",
            "main.ts": """
            import { helper } from './util';

            interface Config { size: number }
            type Id = string;

            export function run(cfg: Config): number {
              return helper(cfg.size);
            }
            """,
        },
    )
    node_ids = {n.id for n in graph.nodes}
    assert "function:util.helper" in node_ids
    assert "function:main.run" in node_ids
    assert calls(graph)[("function:main.run", "function:util.helper")] == "resolved"
    # interface/type produced no spurious nodes
    assert not any("Config" in nid or "Id" in nid for nid in node_ids)
    assert "TypeScript" in {n.language for n in graph.nodes if n.language}


def test_tsx_components_parse_and_imports_resolve(tmp_path: Path) -> None:
    """.tsx files (JSX) parse; a relative import becomes a real edge."""
    graph = build(
        tmp_path,
        {
            "Button.tsx": "export function Button() { return <button>x</button>; }\n",
            "App.tsx": """
            import { Button } from './Button';

            export function App() {
              return <div><Button /></div>;
            }
            """,
        },
    )
    node_ids = {n.id for n in graph.nodes}
    assert "function:Button.Button" in node_ids
    assert "function:App.App" in node_ids
    assert ("file:App.tsx", "file:Button.tsx") in {
        (e.source_id, e.target_id) for e in graph.edges if e.kind is EdgeKind.IMPORTS
    }


def test_tsconfig_path_alias_becomes_a_real_import(tmp_path: Path) -> None:
    """`@/lib/x` (a Next.js path alias) must resolve to the real file, not be
    written off as an external package."""
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}'
    )
    graph = build(
        tmp_path,
        {
            "src/lib/api.ts": "export function fetchThing() { return 1; }\n",
            "src/app/page.tsx": """
            import { fetchThing } from '@/lib/api';

            export function Page() { return fetchThing(); }
            """,
        },
    )
    imports = {(e.source_id, e.target_id) for e in graph.edges if e.kind is EdgeKind.IMPORTS}
    assert ("file:src/app/page.tsx", "file:src/lib/api.ts") in imports
    call = ("function:src.app.page.Page", "function:src.lib.api.fetchThing")
    assert calls(graph)[call] == "resolved"


def test_mixed_python_js_ts_share_one_graph(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "tool.py": "def helper():\n    return 1\n",
            "web.js": "function handler() { return 2; }\n",
            "app.ts": "export function serve(): number { return 3; }\n",
        },
    )
    languages = {n.language for n in graph.nodes if n.kind.value == "function"}
    assert languages == {"Python", "JavaScript", "TypeScript"}


# ── CommonJS definition forms ─────────────────────────────────────────────


def test_exports_assignment_defines_a_requireable_function(tmp_path: Path) -> None:
    """`exports.helper = function` in one file, destructured require in
    another: the whole CommonJS round trip must produce a resolved edge."""
    graph = build(
        tmp_path,
        {
            "util.js": """
            exports.helper = function () {
              return 1;
            };
            """,
            "main.js": """
            const { helper } = require('./util');

            function run() {
              return helper();
            }
            """,
        },
    )
    assert calls(graph)[("function:main.run", "function:util.helper")] == "resolved"
    assert ("file:main.js", "file:util.js") in {
        (e.source_id, e.target_id) for e in graph.edges if e.kind is EdgeKind.IMPORTS
    }


def test_module_exports_dot_form_is_equivalent(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "util.js": "module.exports.helper = () => 2;\n",
            "main.js": (
                "const { helper } = require('./util');\n"
                "function run() { return helper(); }\n"
            ),
        },
    )
    assert calls(graph)[("function:main.run", "function:util.helper")] == "resolved"


def test_prototype_assignment_is_a_method(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "app.js": """
            class App {}
            App.prototype.listen = function (port) {
              return port;
            };
            """,
        },
    )
    node_ids = {n.id for n in graph.nodes}
    assert "function:app.App.listen" in node_ids


def test_object_method_assignment_and_this_resolution(tmp_path: Path) -> None:
    """express's own style: res.json calls this.send, both defined by
    assignment. `this.` must resolve within the shared owner."""
    graph = build(
        tmp_path,
        {
            "response.js": """
            var res = {};

            res.send = function send(body) {
              return body;
            };

            res.json = function json(obj) {
              return this.send(obj);
            };
            """,
        },
    )
    assert (
        calls(graph)[("function:response.res.json", "function:response.res.send")]
        == "resolved"
    )


# ── module resolution ─────────────────────────────────────────────────────


def test_index_js_answers_for_its_directory(tmp_path: Path) -> None:
    """`require('./store')` must find store/index.js — JS's __init__.py."""
    graph = build(
        tmp_path,
        {
            "store/index.js": "exports.load = function () { return 1; };\n",
            "main.js": (
                "const { load } = require('./store');\n"
                "function run() { return load(); }\n"
            ),
        },
    )
    assert calls(graph)[("function:main.run", "function:store.index.load")] == "resolved"


def test_bare_specifiers_never_bind(tmp_path: Path) -> None:
    """`require('react')` is an external package — and a repo file named
    react.js must NOT be mistaken for it."""
    graph = build(
        tmp_path,
        {
            "react.js": "exports.render = function () { return 1; };\n",
            "main.js": (
                "const { render } = require('react');\n"
                "function run() { return render(); }\n"
            ),
        },
    )
    assert ("function:main.run", "function:react.render") not in calls(graph)


# ── honest absences ───────────────────────────────────────────────────────


def test_console_and_new_produce_no_edges(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "m.js": """
            function run() {
              console.log('hi');
              return new Error('x');
            }
            """,
        },
    )
    assert calls(graph) == {}


def test_broken_js_does_not_fail_the_repository(tmp_path: Path) -> None:
    graph = build(
        tmp_path,
        {
            "broken.js": "function ( {{{\n",
            "fine.js": "function works() { return 1; }\n",
        },
    )
    assert "function:fine.works" in {n.id for n in graph.nodes}


def test_mixed_language_repo_shares_one_graph(tmp_path: Path) -> None:
    """Python and JS files coexist in one graph, one schema, no collisions."""
    graph = build(
        tmp_path,
        {
            "tool.py": "def helper():\n    return 1\n",
            "web.js": "function handler() { return 2; }\n",
        },
    )
    languages = {n.language for n in graph.nodes if n.kind.value == "function"}
    assert languages == {"Python", "JavaScript"}
