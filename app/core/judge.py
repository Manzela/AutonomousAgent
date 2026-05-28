"""Judge protocol (seed §2.4 / adapter-plan §3.3).

The judge protocol defines the interface any judge implementation must satisfy.
Judges evaluate agent actions along configurable axes and return a verdict
with a confidence score.

Concretions:
  - LiteLLM-based judges: lib/evaluators/judge_panel.py
  - In-memory stub:        app/adapters/inmemory/judge.py (CI/tests)

Per CLAUDE.md builder-agent rule: keep this Protocol exactly as written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class JudgeResult:
    """Result from a single judge evaluation on a single axis."""

    verdict: str
    confidence: float
    axis: str
    rationale: str = field(default="")

    def __post_init__(self) -> None:
        if self.verdict not in ("accept", "reject"):
            raise ValueError(f"verdict must be 'accept' or 'reject', got {self.verdict!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")


@runtime_checkable
class Judge(Protocol):
    """Evaluate an agent action along a single safety/correctness axis.

    The Judge protocol is intentionally minimal — one axis per call — so
    the consensus computation in lib/evaluators/consensus.py remains
    axis-agnostic and N judges can run in parallel.

    Closing A-3: this Protocol surface was absent from app/core/; all three
    missing ABCs (router, judge, reward) are added together per audit P1.G.
    """

    async def evaluate(self, action: Any, *, axis: str) -> JudgeResult:
        """Evaluate ``action`` along ``axis``.

        Args:
            action: The agent action to evaluate.  Typically a dict with
                ``tool`` and ``args`` keys matching a Hermes tool-call.
            axis: One of the configured evaluation axes (e.g.
                ``code-correctness``, ``safety``, ``scope-fit``,
                ``completeness`` per ``config/limits.yaml evaluators.axes``).

        Returns:
            A :class:`JudgeResult` with ``verdict`` in ``{"accept", "reject"}``,
            ``confidence`` in [0, 1], and an optional ``rationale`` string.
        """
        ...  # pragma: no cover — Protocol body, not an implementation
