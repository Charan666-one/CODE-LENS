"""Parser engine — source in, graph facts out.

ARCHITECTURE.md §2 names this the hardest system, and the honest reason is
call resolution. This checkpoint (CP-1.2) resolves what can be resolved
statically and emits nothing for the rest; CP-1.3 adds the `heuristic` and
`dynamic_unknown` confidence levels that make the uncertain cases visible
rather than silent.

Zero LLM calls, zero database. The graph must be fully buildable and testable
without either (ARCHITECTURE.md corollary 1).
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any

import tree_sitter_typescript
from tree_sitter import Language

from app.graph.schema import Edge, EdgeKind, KnowledgeGraph, Node, NodeKind
from app.ingestion import IngestedRepo, snapshot_directory
from app.parser.facts import FileFacts
from app.parser.js_emitter import JsEmitter
from app.parser.python_emitter import PythonEmitter, module_qname_for
from app.parser.resolution import apply_entrypoints, resolve

__all__ = ["PARSED_EXTENSIONS", "module_qname_for", "parse_ingested", "parse_repository"]

_PYTHON_EXTENSIONS = frozenset({"py", "pyi"})
_JS_EXTENSIONS = frozenset({"js", "jsx", "mjs", "cjs"})
_TS_EXTENSIONS = frozenset({"ts", "tsx"})

#: Everything the engine can read today: Python, JavaScript, TypeScript.
PARSED_EXTENSIONS = _PYTHON_EXTENSIONS | _JS_EXTENSIONS | _TS_EXTENSIONS

#: The two TypeScript grammars — .ts is the plain grammar, .tsx allows JSX.
_TS_LANGUAGE = Language(tree_sitter_typescript.language_typescript())
_TSX_LANGUAGE = Language(tree_sitter_typescript.language_tsx())


def parse_repository(root: Path | str, *, max_size_mb: int | None = None) -> KnowledgeGraph:
    """Ingest and parse a working tree in one call.

    The convenience entry point used by tests and the golden-fixture harness.
    """
    ingested = snapshot_directory(Path(root), max_size_mb=max_size_mb)
    return parse_ingested(ingested)


#: Below this many files, a process pool costs more (spawn + IPC) than the
#: parsing it parallelises. Above it, tree-sitter is CPU-bound and scales.
_PARALLEL_THRESHOLD = 400


def _build_emitters(aliases: dict[str, str]) -> dict[str, PythonEmitter | JsEmitter]:
    """Per-language emitters. Rebuilt inside each worker process, because
    tree-sitter Language handles cannot cross a process boundary."""
    return {
        **dict.fromkeys(_PYTHON_EXTENSIONS, PythonEmitter()),
        **dict.fromkeys(_JS_EXTENSIONS, JsEmitter(aliases=aliases)),
        "ts": JsEmitter(_TS_LANGUAGE, "TypeScript", aliases),
        "tsx": JsEmitter(_TSX_LANGUAGE, "TypeScript", aliases),
    }


_WORKER_EMITTERS: dict[str, PythonEmitter | JsEmitter] = {}
_WORKER_ROOT: Path | None = None


def _worker_init(root: Path, aliases: dict[str, str]) -> None:
    global _WORKER_ROOT
    _WORKER_ROOT = root
    _WORKER_EMITTERS.update(_build_emitters(aliases))


def _worker_parse(job: tuple[str, str, str, int]) -> FileFacts | None:
    """Parse one file inside a pool worker. Returns None for anything
    unreadable — one bad file must never fail the repository."""
    path, extension, content_hash, loc = job
    emitter = _WORKER_EMITTERS.get(extension)
    if emitter is None or _WORKER_ROOT is None:
        return None
    try:
        source = (_WORKER_ROOT / path).read_bytes()
    except OSError:
        return None
    return emitter.emit(path=path, source=source, content_hash=content_hash, loc=loc)


def parse_ingested(ingested: IngestedRepo) -> KnowledgeGraph:
    """Parse an already-ingested repository into a KnowledgeGraph.

    One emitter per language, one schema for all of them: the dispatch below
    is the *entire* per-language surface of the pipeline.

    Large repos parse across processes — tree-sitter is CPU-bound and a
    monorepo is tens of thousands of files (n8n: ~19k, ~30s single-threaded).
    Results are re-sorted by path afterwards, so the graph is byte-identical
    to the sequential build regardless of how the work was scheduled
    (Constitution 4: the same input always gives the same graph).
    """
    aliases = _read_path_aliases(ingested.root)
    by_extension = _build_emitters(aliases)
    facts: list[FileFacts] = []

    parseable = [f for f in ingested.files if f.extension in by_extension]
    if len(parseable) >= _PARALLEL_THRESHOLD:
        parsed = _parse_in_parallel(ingested, parseable, aliases)
        if parsed is not None:
            return _assemble(ingested, parsed)

    for source_file in ingested.files:
        emitter = by_extension.get(source_file.extension)
        if emitter is None:
            continue
        try:
            source = (ingested.root / source_file.path).read_bytes()
        except OSError:
            continue  # vanished or unreadable: skip the file, not the repo
        facts.append(
            emitter.emit(
                path=source_file.path,
                source=source,
                content_hash=source_file.content_hash,
                loc=source_file.loc,
            )
        )

    return _assemble(ingested, facts)


def _assemble(ingested: IngestedRepo, facts: list[FileFacts]) -> KnowledgeGraph:
    """Resolve facts into edges and build the structural spine. Shared by the
    sequential and parallel paths so both produce an identical graph."""
    edges, entrypoints = resolve(facts)
    nodes = _structural_nodes(ingested, facts, edges)
    apply_entrypoints(nodes, entrypoints)
    return KnowledgeGraph(snapshot=ingested.snapshot, nodes=nodes, edges=edges)


def _parse_in_parallel(
    ingested: IngestedRepo, parseable: list[Any], aliases: dict[str, str]
) -> list[FileFacts] | None:
    """Parse across processes. Returns None if a pool can't be used, so the
    caller falls back to the sequential path rather than failing."""
    jobs = [(f.path, f.extension, f.content_hash, f.loc) for f in parseable]
    workers = min(os.cpu_count() or 2, 8)
    try:
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            initargs=(ingested.root, aliases),
        ) as pool:
            results = list(pool.map(_worker_parse, jobs, chunksize=64))
    except Exception:  # noqa: BLE001 - any pool failure degrades to sequential
        return None
    facts = [fact for fact in results if fact is not None]
    # Scheduling order must never reach the graph: sort back to path order.
    facts.sort(key=lambda fact: fact.path)
    return facts


def _read_path_aliases(root: Path) -> dict[str, str]:
    """tsconfig/jsconfig `compilerOptions.paths` -> {alias_prefix: module_prefix}.

    Turns `@/components/*` -> `./src/components/*` into `{"@/": "src."}`, so the
    emitter can resolve `@/components/Navbar` to the real file. tsconfig is
    JSON-with-comments; comments and trailing commas are stripped before
    parsing, and any failure falls back to the near-universal Next.js default
    (`@/` -> `src.` when a src/ dir exists, else repo root). Best-effort by
    design: a missing alias just means a missing edge, never a crash.
    """
    aliases: dict[str, str] = {}
    for name in ("tsconfig.json", "jsconfig.json"):
        config_path = root / name
        if not config_path.is_file():
            continue
        try:
            text = re.sub(r"//[^\n]*", "", config_path.read_text(errors="replace"))
            text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
            text = re.sub(r",(\s*[}\]])", r"\1", text)  # trailing commas
            config = json.loads(text)
            options = config.get("compilerOptions", {})
            base = str(options.get("baseUrl", ".")).strip("./")
            for pattern, targets in (options.get("paths") or {}).items():
                if not targets:
                    continue
                prefix = pattern.replace("*", "")
                target = str(targets[0]).replace("*", "").strip("./").replace("/", ".")
                if base and base != ".":
                    target = f"{base}.{target}" if target else base
                aliases[prefix] = f"{target}." if target and not target.endswith(".") else target
        except (OSError, ValueError):
            continue
        if aliases:
            return aliases

    # No usable config: the Next.js convention.
    return {"@/": "src." if (root / "src").is_dir() else ""}


def _structural_nodes(
    ingested: IngestedRepo, facts: list[FileFacts], edges: list[Edge]
) -> list[Node]:
    """Assemble the CONTAINS spine: repository -> module -> file -> class -> function."""
    repo_name = ingested.root.name
    repository = Node(
        id=f"{NodeKind.REPOSITORY.value}:{repo_name}",
        kind=NodeKind.REPOSITORY,
        name=repo_name,
        qualified_name=repo_name,
        language=ingested.snapshot.primary_language,
        loc=ingested.snapshot.total_loc,
    )

    nodes: list[Node] = [repository]
    module_nodes: dict[str, Node] = {}

    for file_facts in facts:
        for directory in _ancestor_directories(file_facts.path):
            if directory in module_nodes:
                continue
            module_nodes[directory] = Node(
                id=f"{NodeKind.MODULE.value}:{directory}",
                kind=NodeKind.MODULE,
                name=PurePosixPath(directory).name,
                qualified_name=directory,
                file_path=directory,
                language="Python",
            )

    nodes.extend(module_nodes[key] for key in sorted(module_nodes))

    # module -> parent module, or repository for top-level directories
    for directory, module_node in sorted(module_nodes.items()):
        parent = str(PurePosixPath(directory).parent)
        parent_id = repository.id if parent == "." else f"{NodeKind.MODULE.value}:{parent}"
        edges.append(_contains(parent_id, module_node.id))

    for file_facts in facts:
        parent = str(PurePosixPath(file_facts.path).parent)
        parent_id = repository.id if parent == "." else f"{NodeKind.MODULE.value}:{parent}"
        edges.append(_contains(parent_id, file_facts.file_node.id))

        nodes.append(file_facts.file_node)
        nodes.extend(file_facts.nodes)
        for container_id, child_id in file_facts.contains:
            edges.append(_contains(container_id, child_id))

    return nodes


def _ancestor_directories(path: str) -> list[str]:
    directories: list[str] = []
    parent = PurePosixPath(path).parent
    while str(parent) != ".":
        directories.append(str(parent))
        parent = parent.parent
    return directories


def _contains(parent_id: str, child_id: str) -> Edge:
    return Edge(source_id=parent_id, target_id=child_id, kind=EdgeKind.CONTAINS)
