"""Query engine — the registry and the launch plans.

Importing this package registers every plan (registration is declarative, at
import time). `run_query(name, view, **params)` is the single entry point.
"""

from __future__ import annotations

from app.queries import (  # noqa: F401  (imported for their @register side effect)
    blast_radius,
    centrality,
    coupling,
    dependencies,
    endpoints,
    explain,
    quality,
    risk,
    structure,
)
from app.queries.base import (
    QueryError,
    RankedNode,
    ResultGraph,
    registered_queries,
    run_query,
)

__all__ = [
    "QueryError",
    "RankedNode",
    "ResultGraph",
    "registered_queries",
    "run_query",
]
