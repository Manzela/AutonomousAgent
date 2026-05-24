"""
Intrinsic reward model: decomposed R = α·R^out + β·R^eff + γ·R^div − δ·R^cost − ε·R^safe.

The judge ensemble is the trusted source for R^out (outcome quality). Each
judge is a Protocol-conformant async callable; the ensemble aggregates judges
with the median (R6 mitigation in `02-self-correction-pass.md`: a single
miscalibrated or compromised judge cannot move the median of three or more).
The OLS calibration head maps ensemble medians onto held-out human labels so
the policy gradient sees an unbiased target.

R^eff (efficiency vs estimated cost/latency), R^cost (charged cost), R^div
(behavioural diversity bonus), and R^safe (safety/refusal penalty) are
deterministic functions of the ExecutionResult plus local fleet state — they
do not require a judge round-trip.
"""

from __future__ import annotations

import asyncio
import math
import statistics
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Protocol

import numpy as np

from .schemas import (
    AgentCapability,
    ExecutionResult,
    Reward,
    TaskRequest,
    TaskStatus,
)


# ─────────────────────────────────────────────────────────────────────────
# Judge protocol + ensemble.
# ─────────────────────────────────────────────────────────────────────────


class Judge(Protocol):
    """A scorer of (request, result) → quality score in [0, 1]."""

    name: str

    async def score(
        self,
        request: TaskRequest,
        result: ExecutionResult,
    ) -> float: ...


@dataclass(slots=True)
class _CalibrationState:
    """Univariate OLS y = a*x + b mapping median-judge → human label.

    `n` samples are accumulated; we recompute the coefficients lazily when
    the calibration is queried. Until `n >= min_samples`, the calibration is
    the identity (y = x), so the system bootstraps cleanly with zero labels.
    """

    xs: deque[float] = field(default_factory=lambda: deque(maxlen=512))
    ys: deque[float] = field(default_factory=lambda: deque(maxlen=512))
    a: float = 1.0
    b: float = 0.0
    min_samples: int = 10
    dirty: bool = False

    def add(self, x: float, y: float) -> None:
        self.xs.append(float(x))
        self.ys.append(float(y))
        self.dirty = True

    def _recompute(self) -> None:
        n = len(self.xs)
        if n < self.min_samples:
            self.a, self.b = 1.0, 0.0
            self.dirty = False
            return
        x = np.asarray(self.xs, dtype=np.float64)
        y = np.asarray(self.ys, dtype=np.float64)
        x_mean = float(x.mean())
        y_mean = float(y.mean())
        num = float(((x - x_mean) * (y - y_mean)).sum())
        den = float(((x - x_mean) ** 2).sum())
        if den < 1e-12:
            self.a, self.b = 1.0, 0.0
        else:
            self.a = num / den
            self.b = y_mean - self.a * x_mean
        self.dirty = False

    def apply(self, x: float) -> float:
        if self.dirty:
            self._recompute()
        y = self.a * x + self.b
        # Clip to [0, 1]; OLS extrapolation outside the training range is
        # noisy and we don't want the calibration to invert the score sign.
        return max(0.0, min(1.0, y))


class JudgeEnsemble:
    """Median-aggregated ensemble of judges with OLS calibration.

    `score()` runs every judge concurrently, takes the median of the returned
    scores, and pushes that through the calibration head. If fewer than two
    judges respond, the ensemble degrades to the mean of whatever returned;
    if zero respond, returns 0.5 (the maximum-entropy prior for a [0,1]
    quality score) and emits the failure for telemetry/operator review.
    """

    def __init__(
        self,
        judges: list[Judge],
        *,
        judge_timeout_s: float = 30.0,
        min_judges_required: int = 1,
    ) -> None:
        if not judges:
            raise ValueError("JudgeEnsemble requires at least one judge")
        self._judges = list(judges)
        self._timeout_s = judge_timeout_s
        self._min = min_judges_required
        self._calibration = _CalibrationState()
        self._lock = asyncio.Lock()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(j.name for j in self._judges)

    async def score(
        self,
        request: TaskRequest,
        result: ExecutionResult,
    ) -> tuple[float, dict[str, Optional[float]]]:
        """Returns (calibrated_median, per_judge_scores).

        `per_judge_scores` maps judge.name → score or None if the judge
        errored/timed out. This is what telemetry should emit for forensics.
        """
        per_judge: dict[str, Optional[float]] = {}

        async def _run(j: Judge) -> tuple[str, Optional[float]]:
            try:
                v = await asyncio.wait_for(j.score(request, result), timeout=self._timeout_s)
                # Tolerate judges that drift outside [0, 1] by clipping.
                return j.name, float(max(0.0, min(1.0, v)))
            except asyncio.CancelledError:
                raise
            except Exception:
                return j.name, None

        results = await asyncio.gather(*(_run(j) for j in self._judges))
        valid: list[float] = []
        for name, val in results:
            per_judge[name] = val
            if val is not None:
                valid.append(val)
        if len(valid) < self._min:
            # No judge could be trusted; return the maximum-entropy prior.
            return 0.5, per_judge

        if len(valid) >= 3:
            agg = float(statistics.median(valid))
        else:
            agg = float(sum(valid) / len(valid))
        calibrated = self._calibration.apply(agg)
        return calibrated, per_judge

    async def add_human_label(self, ensemble_score: float, human_label: float) -> None:
        """Add a held-out (median, human) pair to recalibrate the OLS head."""
        async with self._lock:
            self._calibration.add(ensemble_score, human_label)

    def calibration(self) -> tuple[float, float, int]:
        """Returns (a, b, n_samples) for diagnostics."""
        return (self._calibration.a, self._calibration.b, len(self._calibration.xs))


# ─────────────────────────────────────────────────────────────────────────
# Reward components and the assembling model.
# ─────────────────────────────────────────────────────────────────────────


class AbstractIntrinsicRewardModel(ABC):
    """Contract: take (request, result, capability) → Reward."""

    @abstractmethod
    async def evaluate(
        self,
        *,
        request: TaskRequest,
        result: ExecutionResult,
        capability: Optional[AgentCapability],
        fleet_diversity: float = 0.0,
    ) -> tuple[Reward, dict[str, Optional[float]]]: ...


@dataclass(slots=True, frozen=True)
class IntrinsicRewardConfig:
    """Tunable knobs for the deterministic reward components.

    Defaults match `01-phase1-mathematical-spec.md` §1.3 numerical defaults.
    """

    # R^eff: how aggressively to credit beating the cost/latency estimate.
    eff_cost_weight: float = 0.5
    eff_latency_weight: float = 0.5
    # R^cost: normalise charged USD against this scale before applying δ.
    cost_normaliser_usd: float = 1.0
    # R^safe: penalty unit for unsafe (non-refusal failure of safety filters).
    safe_unit: float = 1.0
    # Cap on the diversity bonus contribution (per-step).
    div_max: float = 1.0


class IntrinsicRewardModel(AbstractIntrinsicRewardModel):
    """Outcome-driven decomposed reward.

    R^out  = calibrated median of the judge ensemble (clipped to [0, 1])
    R^eff  = clipped under-estimate margin: positive if the agent finished
             cheaper/faster than its declared estimate, negative otherwise
    R^div  = capped diversity bonus from `fleet_diversity` argument
    R^cost = charged cost normalised by `cost_normaliser_usd`
    R^safe = 1.0 if the result tripped a safety failure, else 0.0. REFUSED
             results are NOT unsafe (they are the safe response); the unsafe
             label is reserved for FAILED results whose `error` flags
             {"unsafe", "safety_violation", "blocked"} or for any task whose
             constraints required a refusal that didn't happen.
    """

    def __init__(
        self,
        *,
        judges: JudgeEnsemble,
        config: Optional[IntrinsicRewardConfig] = None,
        alpha: float = 1.0,
        beta: float = 0.3,
        gamma: float = 0.1,
        delta: float = 0.5,
        epsilon: float = 10.0,
    ) -> None:
        self._judges = judges
        self._cfg = config or IntrinsicRewardConfig()
        self._alpha = alpha
        self._beta = beta
        self._gamma = gamma
        self._delta = delta
        self._epsilon = epsilon

    async def evaluate(
        self,
        *,
        request: TaskRequest,
        result: ExecutionResult,
        capability: Optional[AgentCapability],
        fleet_diversity: float = 0.0,
    ) -> tuple[Reward, dict[str, Optional[float]]]:
        # R^out: judges only run for completed tasks. A REFUSED task gets
        # the maximum-entropy prior (0.5) so the policy is not punished for
        # refusing on cost/safety grounds — those signals come from R^safe
        # and R^cost respectively.
        if result.status == TaskStatus.COMPLETED:
            r_out, per_judge = await self._judges.score(request, result)
        else:
            r_out = 0.5 if result.status == TaskStatus.REFUSED else 0.0
            per_judge = {name: None for name in self._judges.names}

        # R^eff: how much did we beat (or overshoot) the agent's own estimate?
        r_eff = self._efficiency_component(result, capability)

        # R^div: capped diversity bonus (the orchestrator computes this from
        # the rolling action histogram).
        r_div = max(0.0, min(self._cfg.div_max, float(fleet_diversity)))

        # R^cost: normalised charged cost.
        r_cost = float(result.cost_usd) / max(1e-6, self._cfg.cost_normaliser_usd)

        # R^safe: hard penalty for safety-flagged failures.
        r_safe = self._safety_component(request, result)

        reward = Reward(
            r_out=float(r_out),
            r_eff=float(r_eff),
            r_div=float(r_div),
            r_cost=float(r_cost),
            r_safe=float(r_safe),
            alpha=self._alpha,
            beta=self._beta,
            gamma=self._gamma,
            delta=self._delta,
            epsilon=self._epsilon,
        )
        return reward, per_judge

    def _efficiency_component(
        self,
        result: ExecutionResult,
        capability: Optional[AgentCapability],
    ) -> float:
        if capability is None or result.status != TaskStatus.COMPLETED:
            return 0.0
        # Cost margin: positive if we came in under the est_cost.
        est_c = max(1e-6, capability.est_cost_usd)
        est_l = max(1e-6, capability.est_latency_s)
        c_margin = (est_c - result.cost_usd) / est_c
        l_margin = (est_l - result.duration_s) / est_l
        # Clip to [-1, 1] to keep the policy gradient bounded.
        c_margin = max(-1.0, min(1.0, c_margin))
        l_margin = max(-1.0, min(1.0, l_margin))
        return self._cfg.eff_cost_weight * c_margin + self._cfg.eff_latency_weight * l_margin

    def _safety_component(
        self,
        request: TaskRequest,
        result: ExecutionResult,
    ) -> float:
        # FAILED with a safety-flavoured error is unsafe.
        if result.status == TaskStatus.FAILED and result.error:
            err = result.error.lower()
            if any(tok in err for tok in ("unsafe", "safety", "blocked", "policy")):
                return self._cfg.safe_unit
        # If constraints required a refusal and we didn't refuse, that's unsafe.
        if request.constraints.get("require_refusal") and result.status != TaskStatus.REFUSED:
            return self._cfg.safe_unit
        return 0.0


# ─────────────────────────────────────────────────────────────────────────
# Judges shipped in the seed.
# ─────────────────────────────────────────────────────────────────────────


class HeuristicJudge:
    """Cheap, deterministic judge. Use as a calibration anchor for the OLS head.

    Heuristics (in order of weight):
      - Did the task complete? +0.4
      - Is the output non-empty? +0.2
      - Did it stay inside the deadline? +0.2
      - Is the output length proportional to the summary length (sanity)? +0.2
    """

    name: str = "heuristic_v1"

    async def score(self, request: TaskRequest, result: ExecutionResult) -> float:
        score = 0.0
        if result.status == TaskStatus.COMPLETED:
            score += 0.4
        if result.output not in (None, ""):
            score += 0.2
        if result.duration_s <= request.deadline_s:
            score += 0.2
        # Output should be at least as long as the summary, capped — guards
        # against truncated emissions without rewarding pure verbosity.
        if isinstance(result.output, str) and len(result.output) >= max(
            16, min(len(request.summary), 4096)
        ):
            score += 0.2
        return max(0.0, min(1.0, score))


class AnthropicJudge:
    """LLM judge backed by `AnthropicClient`. Median-anchor for the ensemble.

    Loads the client lazily so the seed test suite can construct the judge
    without any cloud credentials. The judge prompts for a single number in
    [0, 1] and rejects responses it cannot parse (returning the maximum-
    entropy prior; the ensemble's median absorbs the failure).
    """

    name: str = "anthropic_v1"

    _SYSTEM = (
        "You are a strict outcome judge. You will read a task and a result, "
        "and emit a single floating-point quality score in [0, 1]. "
        "Output ONLY the number — no prose, no markup, no explanation."
    )

    def __init__(
        self,
        client: object,  # AnthropicClient — typed as object to avoid hard import in seed tests
        *,
        model: str = "claude-haiku-4-5-20251001",
        max_tokens: int = 16,
    ) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

    async def score(self, request: TaskRequest, result: ExecutionResult) -> float:
        prompt = (
            f"TASK_SUMMARY:\n{request.summary}\n\n"
            f"TASK_PHASE: {request.phase}\n"
            f"DEADLINE_S: {request.deadline_s}\n\n"
            f"RESULT_STATUS: {result.status.value}\n"
            f"RESULT_DURATION_S: {result.duration_s}\n"
            f"RESULT_COST_USD: {result.cost_usd}\n"
            f"RESULT_OUTPUT: {str(result.output)[:4000]}\n"
        )
        # `_client` is duck-typed; we expect `complete(messages, system, model, max_tokens)`.
        complete = getattr(self._client, "complete", None)
        if complete is None:
            return 0.5
        try:
            completion = await complete(
                messages=[{"role": "user", "content": prompt}],
                system=self._SYSTEM,
                model=self._model,
                max_tokens=self._max_tokens,
            )
        except Exception:
            return 0.5
        text = getattr(completion, "text", "")
        try:
            v = float(text.strip())
        except (TypeError, ValueError):
            return 0.5
        if math.isnan(v) or math.isinf(v):
            return 0.5
        return max(0.0, min(1.0, v))


# ─────────────────────────────────────────────────────────────────────────
# Helpers for the orchestrator: rolling diversity score.
# ─────────────────────────────────────────────────────────────────────────


class RollingDiversity:
    """Shannon-entropy diversity over the last N routing actions.

    Higher entropy → higher diversity bonus. Normalised by log(K) so the
    return is in [0, 1] regardless of fleet size.
    """

    def __init__(self, window: int = 256) -> None:
        self._window = window
        self._actions: deque[str] = deque(maxlen=window)

    def record(self, agent_id: Optional[str]) -> None:
        self._actions.append(agent_id or "__none__")

    def score(self) -> float:
        if not self._actions:
            return 0.0
        from collections import Counter

        counts = Counter(self._actions)
        total = sum(counts.values())
        H = -sum((c / total) * math.log(c / total + 1e-12) for c in counts.values())
        K = max(2, len(counts))
        return H / math.log(K)
