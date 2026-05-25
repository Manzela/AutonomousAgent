"""
Pydantic v2 schemas, enums, and type aliases.

These are the wire-level contracts shared across the seed orchestrator. They
encode the invariants from Phase 1 spec §1 (state/action/reward) and §2
(memory schema + 5-layer isolation defense). The schema-level validators are
defence layer 1 of the cross-project contamination defence — they make
illegal states unrepresentable at construction time.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Awaitable, Callable, Literal, NewType, Optional

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from typing_extensions import Annotated, TypeAlias


# ─────────────────────────────────────────────────────────────────────────
# Type aliases (NewType for nominal typing where useful).
# ─────────────────────────────────────────────────────────────────────────

ProjectID = NewType("ProjectID", str)
AgentID = NewType("AgentID", str)
TaskID = NewType("TaskID", str)
ContentHash = NewType("ContentHash", str)

NonEmptyStr: TypeAlias = Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]


# ─────────────────────────────────────────────────────────────────────────
# Enums.
# ─────────────────────────────────────────────────────────────────────────


class MemoryTier(str, Enum):
    """Three-tier hierarchical memory.

    - CONSENSUS: shared across projects; `project_id` MUST be None. Immutable
      once written (append-only). High-fitness behaviors get promoted here.
    - EPISODIC: per-project; `project_id` MUST be set. Mutable.
    - EPHEMERAL: per-project; expires after TTL ≤ 1 hour. Working-set scratch.
    """

    CONSENSUS = "consensus"
    EPISODIC = "episodic"
    EPHEMERAL = "ephemeral"


class TaskStatus(str, Enum):
    PENDING = "pending"
    INFLIGHT = "inflight"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUSED = "refused"


class MetaAction(str, Enum):
    """Top-level meta-action chosen by the router (action component m_t)."""

    EXECUTE = "execute"
    REFUSE = "refuse"
    SPAWN_EXPERT = "spawn_expert"


# ─────────────────────────────────────────────────────────────────────────
# Task / budget schemas.
# ─────────────────────────────────────────────────────────────────────────


class BudgetVector(BaseModel):
    """4-dim budget snapshot (b_t in the MDP state)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    remaining_usd: Annotated[float, Field(ge=0.0)] = 0.0
    remaining_latency_s: Annotated[float, Field(ge=0.0)] = 0.0
    remaining_tokens: Annotated[int, Field(ge=0)] = 0
    remaining_calls: Annotated[int, Field(ge=0)] = 0

    def encoded(self) -> np.ndarray:
        """Project to a 4-dim float vector (the b_t encoding for state build)."""
        return np.asarray(
            [
                np.log1p(self.remaining_usd),
                np.log1p(self.remaining_latency_s),
                np.log1p(float(self.remaining_tokens)),
                np.log1p(float(self.remaining_calls)),
            ],
            dtype=np.float32,
        )


class TaskRequest(BaseModel):
    """Single task to route. Pre-state-vector form."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: NonEmptyStr
    project_id: Optional[ProjectID] = None
    phase: Literal["research", "draft", "refine", "verify", "ship"] = "draft"
    summary: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    budget: BudgetVector = Field(default_factory=BudgetVector)
    deadline_s: Annotated[float, Field(ge=0.0)] = 60.0


# ─────────────────────────────────────────────────────────────────────────
# Agent capability + execution.
# ─────────────────────────────────────────────────────────────────────────


# Lifecycle states per the Free Agent FSM (Phase 1 §1.4, §3.3).
Lifecycle = Literal["spawn", "probation", "active", "cool", "evicted", "promoted"]


class AgentCapability(BaseModel):
    """Hot-pluggable expert descriptor.

    `invoke` is the runtime hook the orchestrator calls; everything else is
    metadata that goes into the bilinear router's capability matrix
    (E ∈ ℝ^(K×256)) via the embedder.

    When `peer_endpoint` is set, the orchestrator routes this capability's
    tasks via the A2A boundary (`lib.a2a.client.send_message`) instead of
    local dispatch. See INTEGRATION.md §P-3.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    agent_id: AgentID
    version: NonEmptyStr
    phase: Literal["research", "draft", "refine", "verify", "ship"]
    description: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    tags: tuple[str, ...] = Field(default_factory=tuple)
    est_cost_usd: Annotated[float, Field(ge=0.0)] = 0.0
    est_latency_s: Annotated[float, Field(ge=0.0)] = 0.0
    lifecycle: Lifecycle = "probation"
    invoke: Callable[..., Awaitable["ExecutionResult"]] | None = None
    source_sha256: NonEmptyStr = "0" * 64
    spawned_at: float = Field(default_factory=time.time)
    # A2A peer-execution endpoint (P-3). None = local dispatch (default).
    # When set, _execute() routes tasks via lib.a2a.client.send_message.
    peer_endpoint: Optional[str] = None

    @model_validator(mode="after")
    def _validate_peer_endpoint(self) -> "AgentCapability":
        if self.peer_endpoint is not None:
            if not self.peer_endpoint.startswith(("http://", "https://")):
                raise ValueError(
                    f"peer_endpoint must start with http:// or https://, "
                    f"got {self.peer_endpoint!r}"
                )
        return self


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    task_id: NonEmptyStr
    status: TaskStatus
    agent_id: Optional[AgentID] = None
    output: Any = None
    error: Optional[str] = None
    duration_s: Annotated[float, Field(ge=0.0)] = 0.0
    cost_usd: Annotated[float, Field(ge=0.0)] = 0.0
    tokens_in: Annotated[int, Field(ge=0)] = 0
    tokens_out: Annotated[int, Field(ge=0)] = 0
    artifacts: tuple[dict[str, str], ...] = Field(default_factory=tuple)


# ─────────────────────────────────────────────────────────────────────────
# State / routing action / reward (MDP).
# ─────────────────────────────────────────────────────────────────────────


class StateVector(BaseModel):
    """Composite state s_t built by the orchestrator each step.

    Components (per Phase 1 §1.1 table):
        τ_t        : one-hot 5-dim phase
        c_t        : 256-dim task context embedding
        E_t        : K×256 capability matrix (carried by reference; not packed here)
        p_t        : 128-dim project context (zeros for CONSENSUS)
        b_t        : 4-dim budget encoding (BudgetVector.encoded())
        h_t        : 32-dim recent-history summary (e.g., rolling fitness)
        φ_proj     : 64-dim project-fingerprint
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    phase_onehot: np.ndarray
    task_embedding: np.ndarray
    project_embedding: np.ndarray
    budget_encoded: np.ndarray
    history_summary: np.ndarray
    project_fingerprint: np.ndarray
    # The capability matrix is large and changes mid-episode; carried by ref.
    capability_ids: tuple[AgentID, ...] = Field(default_factory=tuple)

    def encoded(self) -> np.ndarray:
        """Concatenate all dense components into the state input z ∈ ℝ^256.

        The router consumes a 256-dim projection of this; concrete projection
        lives in the router (so we keep StateVector wire-stable).
        """
        return np.concatenate(
            [
                self.phase_onehot.astype(np.float32),
                self.task_embedding.astype(np.float32),
                self.project_embedding.astype(np.float32),
                self.budget_encoded.astype(np.float32),
                self.history_summary.astype(np.float32),
                self.project_fingerprint.astype(np.float32),
            ]
        )


class RoutingAction(BaseModel):
    """Composite action a_t = (π_t, m_t, T_t).

    `expert_distribution` is a simplex vector of length K (Σ p_k = 1).
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    expert_distribution: np.ndarray
    chosen_agent_id: Optional[AgentID] = None
    meta_action: MetaAction = MetaAction.EXECUTE
    temperature: Annotated[float, Field(gt=0.0, le=10.0)] = 1.0
    # The log-prob of the sampled action under the policy that emitted it.
    log_prob_chosen: float = 0.0

    @model_validator(mode="after")
    def _check_simplex(self) -> "RoutingAction":
        if self.expert_distribution.ndim != 1:
            raise ValueError("expert_distribution must be 1-D")
        s = float(self.expert_distribution.sum())
        if not (0.999 <= s <= 1.001):
            raise ValueError(f"expert_distribution must sum to 1, got {s:.6f}")
        if float(self.expert_distribution.min()) < -1e-6:
            raise ValueError("expert_distribution must be non-negative")
        return self


class Reward(BaseModel):
    """Decomposed reward R = α·R^out + β·R^eff + γ·R^div − δ·R^cost − ε·R^safe."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    r_out: float = 0.0
    r_eff: float = 0.0
    r_div: float = 0.0
    r_cost: float = 0.0
    r_safe: float = 0.0
    alpha: float = 1.0
    beta: float = 0.3
    gamma: float = 0.1
    delta: float = 0.5
    epsilon: float = 10.0

    @property
    def scalar(self) -> float:
        return (
            self.alpha * self.r_out
            + self.beta * self.r_eff
            + self.gamma * self.r_div
            - self.delta * self.r_cost
            - self.epsilon * self.r_safe
        )


# ─────────────────────────────────────────────────────────────────────────
# Memory record (the 5-layer isolation defence anchors here).
# ─────────────────────────────────────────────────────────────────────────


class MemoryRecord(BaseModel):
    """Atomic memory record with enforced tier/namespace invariant.

    Layer-1 defence against cross-project contamination: it is impossible to
    construct a `CONSENSUS` record with a non-None `project_id`, or an
    `EPISODIC`/`EPHEMERAL` record without one. This is enforced before
    anything touches the store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    record_id: NonEmptyStr
    tier: MemoryTier
    project_id: Optional[ProjectID] = None
    agent_id: Optional[AgentID] = None
    task_id: Optional[TaskID] = None
    content: NonEmptyStr
    embedding: np.ndarray
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    expires_at: Optional[float] = None
    # Hash of `content` for dedup / forensics; orchestrator fills if absent.
    content_hash: Optional[ContentHash] = None
    # HMAC-derived namespace token (layer 2); store-side opaque.
    namespace_token: Optional[str] = None

    @model_validator(mode="after")
    def _tier_namespace_invariant(self) -> "MemoryRecord":
        if self.tier == MemoryTier.CONSENSUS and self.project_id is not None:
            raise ValueError("CONSENSUS records MUST have project_id=None (layer-1 invariant)")
        if self.tier in (MemoryTier.EPISODIC, MemoryTier.EPHEMERAL) and self.project_id is None:
            raise ValueError(
                f"{self.tier.value} records MUST have a project_id (layer-1 invariant)"
            )
        if self.tier == MemoryTier.EPHEMERAL and self.expires_at is None:
            raise ValueError(
                "EPHEMERAL records MUST set expires_at (TTL ≤ 1h enforced at write-time)"
            )
        if self.embedding.ndim != 1:
            raise ValueError("embedding must be a 1-D vector")
        return self
