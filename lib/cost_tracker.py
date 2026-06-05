"""Thread-safe, session-scoped LLM cost accumulator.

P0-2 (Go-Live audit): bridges the gap between the observability layer
(which ALREADY computes per-request cost via ``_llm_request_cost_usd``)
and the execution layer (which returns ``ExecutionResult.cost_usd=0.0``
because nothing threads the cost back).

Usage in ``app/core/graph.py`` fan_out::

    tracker = CostTracker()
    # ... orchestrate(request, capability, cost_tracker=tracker) ...
    result = ExecutionResult(..., cost_usd=tracker.total_usd, ...)

The tracker accumulates cost across multiple LLM calls within a single
task execution (a leaf in the DAG may make multiple model calls). It is
constructed per-leaf in fan_out (not shared across leaves).

Thread safety: ``threading.Lock`` protects the mutable ``_total`` field.
This is correct for fan_out's ``asyncio.gather`` dispatch because each
leaf gets its own tracker instance. The lock guards against concurrent
``record()`` calls within a single leaf's execution if it spawns threads.
"""

from __future__ import annotations

import contextvars
import logging
import threading
from typing import Optional

from lib.cost import llm_request_cost_usd

logger = logging.getLogger(__name__)


active_tracker: contextvars.ContextVar[Optional[CostTracker]] = contextvars.ContextVar(
    "active_tracker", default=None
)


class CostTracker:
    """Session-scoped cost accumulator for a single task execution.

    Constructed per-leaf in fan_out; accumulates cost from zero or more
    LLM calls during that leaf's execution.

    Attributes:
        total_usd: The running total cost in USD.
        call_count: Number of LLM calls recorded (priced or unpriced).
        priced_count: Number of calls that had a valid price.
    """

    __slots__ = ("_total", "_call_count", "_priced_count", "_lock")

    def __init__(self) -> None:
        self._total: float = 0.0
        self._call_count: int = 0
        self._priced_count: int = 0
        self._lock = threading.Lock()

    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Optional[float]:
        """Record the cost of one LLM call.

        Returns the per-call cost in USD (or None if unpriced).
        Accumulates into ``total_usd``.
        """
        cost = llm_request_cost_usd(model, prompt_tokens, completion_tokens)
        with self._lock:
            self._call_count += 1
            if cost is not None:
                self._total += cost
                self._priced_count += 1
        return cost

    @property
    def total_usd(self) -> float:
        """Running total cost in USD. Thread-safe read."""
        with self._lock:
            return self._total

    @property
    def call_count(self) -> int:
        with self._lock:
            return self._call_count

    @property
    def priced_count(self) -> int:
        with self._lock:
            return self._priced_count

    def reset(self) -> None:
        """Clear the accumulator (for reuse across tasks)."""
        with self._lock:
            self._total = 0.0
            self._call_count = 0
            self._priced_count = 0

    def __repr__(self) -> str:
        return (
            f"CostTracker(total_usd={self.total_usd:.6f}, "
            f"calls={self.call_count}, priced={self.priced_count})"
        )
