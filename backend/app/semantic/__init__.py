"""Semantic layer — where AI enters, and not before (ARCHITECTURE.md §4).

Input is always a graph-selected subgraph, never "the repository". Output
always carries evidence pointers. The layers below this package neither
import it nor know it exists.
"""

from __future__ import annotations

from app.semantic.context import ContextBundle, assemble
from app.semantic.embeddings import ConceptIndex, LocalHashEmbedding
from app.semantic.llm import AnthropicClient, CountingFakeLLM, LLMClient, LLMError
from app.semantic.narration import (
    NarratedAnswer,
    learning_path,
    narrate_blast_radius,
    narrate_project,
)
from app.semantic.summaries import SummaryReport, summarize_graph

__all__ = [
    "AnthropicClient",
    "ConceptIndex",
    "ContextBundle",
    "CountingFakeLLM",
    "LLMClient",
    "LLMError",
    "LocalHashEmbedding",
    "NarratedAnswer",
    "SummaryReport",
    "assemble",
    "learning_path",
    "narrate_blast_radius",
    "narrate_project",
    "summarize_graph",
]
