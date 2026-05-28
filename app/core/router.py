"""Abstract MoE routing interface (seed §2.3 / adapter-plan §3.2).

Concretions:
  - app/adapters/inmemory/router.py  → WeightedRandomRouter (CI/tests)
  - app/adapters/gcp/router.py       → VertexMoERouter (prod — P-12)

Per CLAUDE.md builder-agent rule: do NOT collapse this ABC when adding
concretions.  Keep the abstract surface here; put implementations in
app/adapters/gcp/ or app/adapters/inmemory/.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AbstractMoERouter(ABC):
    """Select an expert (model/tier) for a given task request.

    The router encapsulates: capability matching → fitness scoring →
    load-aware selection → PPO trust-region constraint enforcement.
    Implementations are swappable so CI runs a deterministic in-memory
    router while prod routes via Vertex/LiteLLM.

    Closing A-3: this surface was absent from app/core/; all three missing
    ABCs (router, judge, reward) are added together per audit P1.G finding.
    """

    @abstractmethod
    async def route(self, request: Any) -> Any:
        """Select a capability for the given task request.

        Args:
            request: A TaskRequest (app.core.schemas.TaskRequest) or
                compatible mapping describing the work to be done.

        Returns:
            An AgentCapability (app.core.schemas.AgentCapability) or
            compatible mapping.  Must include at minimum:
            - ``agent_id`` (str)
            - ``peer_endpoint`` (str | None) — None → local dispatch
            - ``fitness_score`` (float in [0, 1])
        """

    @abstractmethod
    async def record_outcome(self, agent_id: str, *, success: bool, latency_s: float) -> None:
        """Feed execution outcome back to the router for fitness-EMA updates.

        Called by the orchestrator after every task completes (success or fail).
        The router updates its internal fitness estimate (exponential moving
        average with α = 0.1 per seed §2.3).

        Args:
            agent_id: The ``AgentCapability.agent_id`` that was dispatched.
            success: True if the task completed with TaskStatus.COMPLETED.
            latency_s: Wall-clock execution time in seconds.
        """
