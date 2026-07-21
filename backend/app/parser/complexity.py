"""Cyclomatic complexity, via radon.

Keyed by definition line, which is the one identifier both radon and
tree-sitter agree on: radon reads `ast`, whose `FunctionDef.lineno` is the
`def` line (decorators carry their own line numbers), and the parser records
the same line for a definition node.
"""

from __future__ import annotations

from radon.complexity import cc_visit


def complexity_by_line(source: str) -> dict[int, int]:
    """Map definition line -> cyclomatic complexity.

    A file radon cannot parse yields an empty map rather than an error: a
    syntax error in one file must never fail the whole repository's analysis.
    """
    try:
        blocks = cc_visit(source)
    except (SyntaxError, ValueError, IndentationError, RecursionError):
        return {}
    return {block.lineno: block.complexity for block in blocks}
