"""The RAG rule, enforced in code (CP-3.1).

ARCHITECTURE.md §"AI context assembly": never send the repository. Always
`graph query -> relevant nodes -> their snippets -> LLM`, ranked by graph
distance and fan-in, truncated against a hard token budget, never stuffed.

This module is that sentence as a function. Every LLM call in CodeLens gets
its context from here, so the rule cannot be bypassed accidentally — and the
bundle records exactly which nodes made the cut, which is where every
`derived_from` evidence list comes from.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.graph.schema import EdgeKind, Node
from app.graph.traversal import GraphView

#: chars-per-token estimate; conservative for code.
_CHARS_PER_TOKEN = 4

DEPENDENCY_KINDS = {EdgeKind.CALLS, EdgeKind.IMPORTS}


class ContextItem(BaseModel):
    node_id: str
    header: str  # "function calculator.add (calculator.py:4-6)"
    text: str  # source snippet / docstring / child listing
    tokens: int


class ContextBundle(BaseModel):
    """What an LLM will actually see, and the receipt of how it was chosen."""

    items: list[ContextItem] = Field(default_factory=list)
    included_ids: list[str] = Field(default_factory=list)  # -> derived_from
    dropped_ids: list[str] = Field(default_factory=list)  # ranked out by budget
    token_budget: int = 0
    tokens_used: int = 0

    def as_prompt_block(self) -> str:
        return "\n\n".join(f"### {item.header}\n{item.text}" for item in self.items)


def assemble(
    view: GraphView,
    root: Path,
    node_ids: list[str],
    *,
    token_budget: int = 2000,
) -> ContextBundle:
    """Build the context for `node_ids`, best evidence first, budget enforced.

    Ranking: callers keep their order (a query has already ranked by
    relevance); ties and unordered tails are broken by fan-in — a hub earns
    its slot before a leaf does.
    """
    ranked = sorted(
        enumerate(node_ids),
        key=lambda pair: (pair[0], -view.fan_in(pair[1], DEPENDENCY_KINDS)),
    )

    bundle = ContextBundle(token_budget=token_budget)
    for _, node_id in ranked:
        node = view.node(node_id)
        if node is None:
            continue
        text = _text_for(view, root, node)
        if not text:
            continue
        tokens = max(1, len(text) // _CHARS_PER_TOKEN)
        if bundle.tokens_used + tokens > token_budget:
            bundle.dropped_ids.append(node_id)
            continue
        bundle.items.append(
            ContextItem(node_id=node_id, header=_header_for(node), text=text, tokens=tokens)
        )
        bundle.included_ids.append(node_id)
        bundle.tokens_used += tokens

    return bundle


def _header_for(node: Node) -> str:
    location = ""
    if node.file_path:
        location = f" ({node.file_path}"
        if node.start_line:
            location += f":{node.start_line}-{node.end_line}"
        location += ")"
    return f"{node.kind.value} {node.qualified_name}{location}"


def _text_for(view: GraphView, root: Path, node: Node) -> str:
    """The best snippet the graph can offer for this node.

    Functions and classes read their real source lines. Files list what they
    contain and import. Modules list their files. Always evidence, never a
    paraphrase.
    """
    if node.start_line and node.end_line and node.file_path:
        source = _read_lines(root, node.file_path, node.start_line, node.end_line)
        if source:
            return source

    if node.kind.value == "file":
        children = [
            child.qualified_name
            for child_id in view.children_of(node.id)
            if (child := view.node(child_id)) is not None
        ]
        listing = f"contains: {', '.join(sorted(children))}" if children else "empty file"
        if node.docstring:
            listing = f'"""{node.docstring}"""\n{listing}'
        return listing

    if node.kind.value in ("module", "repository"):
        children = [
            child.name
            for child_id in view.children_of(node.id)
            if (child := view.node(child_id)) is not None
        ]
        return f"contains: {', '.join(sorted(children))}" if children else ""

    return node.docstring or ""


def _read_lines(root: Path, file_path: str, start: int, end: int) -> str:
    try:
        lines = (root / file_path).read_text(errors="replace").splitlines()
    except OSError:
        return ""
    if not (1 <= start <= len(lines)):
        return ""
    return "\n".join(lines[start - 1 : min(end, len(lines))])
