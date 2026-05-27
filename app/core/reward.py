"""Abstract intrinsic reward model interface (seed §2.5 / adapter-plan §3.4).

The reward model provides the intrinsic reward signal used in the PPO policy
update loop.  It combines multiple reward components (user feedback,
self-consistency, task completion) into a scalar per the weights defined in
``config/limits.yaml rl_rewards.weights``.

Concretions:
  - app/adapters/inmemory/reward.py  → WeightedSumReward (CI/tests)
  - app/adapters/gcp/reward.py       → VertexRewardModel (prod — P-13)

Per CLAUDE.md builder-agent rule: keep this ABC exactly as written.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AbstractIntrinsicRewardModel(ABC):
    """Compute the intrinsic reward signal for a completed trajectory.

    Called once per trajectory (session end) to provide a scalar reward for
    the PPO policy update.  Implementations may incorporate LLM judges,
    heuristics, user feedback, or any combination thereof.

    Closing A-3: this ABC surface was absent from app/core/; all three missing
    ABCs (router, judge, reward) are added together per audit P1.G finding.
    """

    @abstractmethod
    async def compute(self, trajectory: Any) -> float:
        """Compute the intrinsic reward for a completed trajectory.

        Args:
            trajectory: A completed session trajectory.  Typically a sequence
                of TurnRecord objects (lib/trajectory/schemas.TurnRecord) plus
                session metadata (model, timestamps, judge verdicts).

        Returns:
            A scalar reward in [-1.0, 1.0] per seed §2.5 normalisation
            contract.  Higher is better.  The PPO update loop clips this
            value before use; callers must still return within [-1, 1].

        Raises:
            NotImplementedError: Raised by concrete subclasses that have not
                yet implemented compute() for a particular trajectory type.
        """

    @abstractmethod
    def components(self) -> dict[str, float]:
        """Return the most recent per-component reward breakdown.

        Useful for observability: after each ``compute()`` call the reward
        model records the individual component contributions so callers can
        emit them as OTel span attributes or JSONL to the trajectory shipper.

        Returns:
            A dict mapping component name → scalar value, e.g.
            ``{"user_explicit": 1.0, "self_consistency": 0.2, ...}``.
            Keys match ``config/limits.yaml rl_rewards.weights`` entries.
            Returns an empty dict before the first ``compute()`` call.
        """
