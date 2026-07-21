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

from pathlib import Path, PurePosixPath

from app.graph.schema import Edge, EdgeKind, KnowledgeGraph, Node, NodeKind
from app.ingestion import IngestedRepo, snapshot_directory
from app.parser.facts import FileFacts
from app.parser.python_emitter import PythonEmitter, module_qname_for
from app.parser.resolution import apply_entrypoints, resolve

__all__ = ["module_qname_for", "parse_ingested", "parse_repository"]

#: Extensions this engine can read today. JS/TS joins at CP-9.1.
_PYTHON_EXTENSIONS = frozenset({"py", "pyi"})


def parse_repository(root: Path | str, *, max_size_mb: int | None = None) -> KnowledgeGraph:
    """Ingest and parse a working tree in one call.

    The convenience entry point used by tests and the golden-fixture harness.
    """
    ingested = snapshot_directory(Path(root), max_size_mb=max_size_mb)
    return parse_ingested(ingested)


def parse_ingested(ingested: IngestedRepo) -> KnowledgeGraph:
    """Parse an already-ingested repository into a KnowledgeGraph."""
    emitter = PythonEmitter()
    facts: list[FileFacts] = []

    for source_file in ingested.files:
        if source_file.extension not in _PYTHON_EXTENSIONS:
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

    edges, entrypoints = resolve(facts)
    nodes = _structural_nodes(ingested, facts, edges)
    apply_entrypoints(nodes, entrypoints)

    return KnowledgeGraph(snapshot=ingested.snapshot, nodes=nodes, edges=edges)


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
