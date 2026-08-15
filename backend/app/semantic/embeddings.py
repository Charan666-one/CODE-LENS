"""Concept search — "where is rate limiting?" when nothing is named that
(CP-3.3, FOUNDATION Q7).

Search returns **nodes on the graph**, never text snippets: the result of a
concept query is a place on the map you can click, traverse, and blast-radius.

The backend is a protocol. Today's implementation is a deterministic local
one — hashed bag-of-words over name + docstring + summary, cosine-scored.
Honest about what that is: lexical matching with a vector interface, good
enough to find docstring vocabulary ("rate limiting" -> `throttle_requests`
whose docstring says so), free, offline, and reproducible in CI. A neural
model (voyage, OpenAI embeddings) slots in behind the same protocol when
quality demands it — the interface, index and query plan do not change.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from app.graph.schema import NodeKind, SemanticAnnotation
from app.graph.traversal import GraphView
from app.queries.base import RankedNode, ResultGraph, register

_DIMENSIONS = 512
_TOKEN = re.compile(r"[a-z]{2,}")

#: Node kinds worth indexing — the ones a person would want to land on.
_SEARCHABLE = {NodeKind.FILE, NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.MODULE}


class EmbeddingBackend(Protocol):
    def embed(self, text: str) -> list[float]: ...


class LocalHashEmbedding:
    """Feature-hashed bag of words with sublinear term weighting.

    Identifiers are split on underscores and camelCase so `throttle_requests`
    contributes `throttle` and `requests` — the vocabulary a human searches by.
    """

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * _DIMENSIONS
        counts: dict[str, int] = {}
        for token in _tokenise(text):
            counts[token] = counts.get(token, 0) + 1
        for token, count in counts.items():
            digest = hashlib.md5(token.encode()).digest()
            slot = int.from_bytes(digest[:4], "little") % _DIMENSIONS
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[slot] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector


class ConceptIndex:
    """The searchable surface of one graph: node id -> embedded description."""

    def __init__(self, backend: EmbeddingBackend | None = None) -> None:
        self.backend = backend or LocalHashEmbedding()
        self._vectors: dict[str, list[float]] = {}

    def build(self, view: GraphView, annotations: list[SemanticAnnotation]) -> int:
        """Index name + docstring + summary per searchable node."""
        summaries = {a.node_id: a.summary for a in annotations}
        self._vectors.clear()
        for node in view.nodes_by_id.values():
            if node.kind not in _SEARCHABLE:
                continue
            text = " ".join(
                part
                for part in (node.qualified_name, node.docstring, summaries.get(node.id))
                if part
            )
            if text.strip():
                self._vectors[node.id] = self.backend.embed(text)
        return len(self._vectors)

    def search(self, query: str, *, top: int = 10) -> list[tuple[str, float]]:
        query_vector = self.backend.embed(query)
        scored = [
            (node_id, _dot(query_vector, vector))
            for node_id, vector in self._vectors.items()
        ]
        scored = [(node_id, score) for node_id, score in scored if score > 0.0]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:top]


@register("concept_search")
def concept_search(
    view: GraphView, *, index: ConceptIndex, text: str, top: int = 10
) -> ResultGraph:
    """Q7 as a query plan: concept in, ranked graph nodes out.

    Each hit carries the node's name, kind and path alongside the score. A
    result that is only an id and a similarity forces every caller to either
    re-look-up the node or display the raw qualified name — the command
    palette did the latter and showed
    `tests.test_session_interface.test_open_session_with_endpoint.MySessionInterface.save_session`
    where it wanted `save_session` and its file. The plan knows all of it; not
    returning it was the omission.
    """
    hits = index.search(text, top=top)
    ranked: list[RankedNode] = []
    for node_id, score in hits:
        node = view.node(node_id)
        ranked.append(
            RankedNode(
                node_id=node_id,
                score=score,
                reasons={
                    "similarity": round(score, 4),
                    "name": node.name if node else node_id,
                    "kind": node.kind.value if node else None,
                    "file_path": node.file_path if node else None,
                    "start_line": node.start_line if node else None,
                },
            )
        )
    return ResultGraph(
        query="concept_search",
        params={"text": text, "top": top},
        node_ids=[node_id for node_id, _ in hits],
        ranked=ranked,
        meta={"indexed_nodes": len(index._vectors)},
    )


def _tokenise(text: str) -> list[str]:
    # split camelCase, then underscores/punctuation fall to the regex
    decamel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return _TOKEN.findall(decamel.lower())


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))
