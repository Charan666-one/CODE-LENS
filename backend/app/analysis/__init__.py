"""Derived architecture analysis — measured, never asserted.

Everything here is computed from the graph the parser produced. No LLM, no
heuristic scoring dressed up as a metric, and every number a reader can
recompute by hand from the edges. A score whose derivation cannot be shown is
a number the product has no right to display.
"""

from __future__ import annotations

from app.analysis.cycles import Cycle, find_cycles
from app.analysis.health import HealthReport, ModuleMetrics, measure_health

__all__ = [
    "Cycle",
    "HealthReport",
    "ModuleMetrics",
    "find_cycles",
    "measure_health",
]
