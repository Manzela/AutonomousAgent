"""
MoE router with PPO bilinear gating.

Architecture (Phase 1 §1.2 + §3.3):

  state z ∈ ℝ^state_dim (linear projection of the encoded StateVector)
  expert e_k ∈ ℝ^capability_dim (from the embedder applied to AgentCapability)

  logit_k = z^T W_z W_r e_k                  # bilinear scorer
  meta_logit_m = z^T W_m                     # 3-way head: execute|refuse|spawn
  T = softplus(z^T w_T) + ε                  # temperature head
  p_k = softmax(logit_k / T)                 # expert distribution

The W_r ∈ ℝ^(state_proj_dim × capability_dim) bilinear is invariant to
expert reordering and accommodates *new* experts via column append — no
weight reshape is required when add_expert() fires.

`SoftmaxBilinearRouter` ships a NumPy PPO update. For production training
under the $5K GPU + Unsloth disposition, port the policy heads and the
PPO step to PyTorch (INTEGRATION.md work item P-1).
"""

from __future__ import annotations

import asyncio
import math
import pickle
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .schemas import AgentCapability, AgentID, MetaAction, RoutingAction


@dataclass(slots=True)
class TrajectoryStep:
    """One sampled (state, action, reward) tuple for PPO updates."""

    z: np.ndarray
    expert_ids: tuple[AgentID, ...]
    expert_matrix: np.ndarray  # K × capability_dim, snapshot at sample time
    chosen_index: int
    log_prob_chosen: float
    meta_action: MetaAction
    temperature: float
    reward: float
    advantage: float = 0.0  # filled by the orchestrator before ppo_update


class AbstractMoERouter(ABC):
    """Routing contract: stateful, hot-pluggable, snapshot-able."""

    @abstractmethod
    def add_expert(self, cap: AgentCapability, embedding: np.ndarray) -> None: ...

    @abstractmethod
    def remove_expert(self, agent_id: AgentID) -> None: ...

    @abstractmethod
    def act(
        self,
        z: np.ndarray,
        *,
        active_expert_ids: tuple[AgentID, ...],
    ) -> RoutingAction: ...

    @abstractmethod
    def act_deterministic(
        self,
        z: np.ndarray,
        *,
        active_expert_ids: tuple[AgentID, ...],
    ) -> RoutingAction: ...

    @abstractmethod
    def ppo_update(
        self,
        batch: list[TrajectoryStep],
        lr: float,
        clip: float,
        kl_target: float,
        entropy_coef: float,
    ) -> dict[str, float]: ...

    @abstractmethod
    def bless_reference(self, blend: float) -> None: ...

    @abstractmethod
    def snapshot(self) -> bytes: ...

    @abstractmethod
    def restore(self, blob: bytes) -> None: ...


class SoftmaxBilinearRouter(AbstractMoERouter):
    """NumPy reference implementation of the bilinear-PPO router.

    State projection (z' = W_z z) is a learned linear map shared between the
    expert scorer, the meta-action head, and the temperature head — keeps the
    parameter count tractable and lets the heads share the same context.
    """

    def __init__(
        self,
        *,
        state_dim: int,
        capability_dim: int,
        state_proj_dim: int = 256,
        seed: int = 0,
        probation_logit_multiplier: float = 0.35,
    ) -> None:
        self._rng = np.random.default_rng(seed)
        self._state_dim = state_dim
        self._cap_dim = capability_dim
        self._proj_dim = state_proj_dim
        self._probation_mult = probation_logit_multiplier

        # Live policy parameters.
        self._W_z = self._rng.normal(0.0, 0.02, (state_dim, state_proj_dim)).astype(np.float32)
        self._W_r = np.eye(state_proj_dim, capability_dim, dtype=np.float32) * 0.1
        self._W_m = self._rng.normal(0.0, 0.02, (state_proj_dim, 3)).astype(np.float32)
        self._w_T = self._rng.normal(0.0, 0.02, (state_proj_dim,)).astype(np.float32)
        # Reference policy (target of KL constraint).
        self._W_z_ref = self._W_z.copy()
        self._W_r_ref = self._W_r.copy()
        self._W_m_ref = self._W_m.copy()
        self._w_T_ref = self._w_T.copy()

        # Expert registry: id → (embedding, is_probation).
        self._experts: dict[AgentID, tuple[np.ndarray, bool]] = {}
        self._lock = asyncio.Lock()

    # ── expert management ─────────────────────────────────────────────────

    def add_expert(self, cap: AgentCapability, embedding: np.ndarray) -> None:
        if embedding.shape[0] != self._cap_dim:
            raise ValueError(
                f"embedding dim {embedding.shape[0]} != router cap_dim {self._cap_dim}"
            )
        is_prob = cap.lifecycle in ("probation", "spawn")
        self._experts[cap.agent_id] = (embedding.astype(np.float32), is_prob)

    def remove_expert(self, agent_id: AgentID) -> None:
        self._experts.pop(agent_id, None)

    def on_registry_change(
        self, event: str, agent_id: AgentID, cap: Optional[AgentCapability]
    ) -> None:
        """Listener entry-point wired by the AgentRegistry. Idempotent."""
        if event == "register" and cap is not None:
            # The registry knows the embedding; pass it via cap metadata if present.
            emb = cap.invoke and getattr(cap.invoke, "embedding", None)
            if emb is None:
                emb = np.zeros(self._cap_dim, dtype=np.float32)
            self.add_expert(cap, np.asarray(emb, dtype=np.float32))
        elif event in ("evict", "remove"):
            self.remove_expert(agent_id)
        elif event == "promote" and cap is not None:
            # Promote out of probation → multiplier no longer applies.
            emb, _ = self._experts.get(agent_id, (None, True))
            if emb is not None:
                self._experts[agent_id] = (emb, False)

    # ── core scoring ──────────────────────────────────────────────────────

    def _expert_matrix(self, ids: tuple[AgentID, ...]) -> tuple[np.ndarray, np.ndarray]:
        rows: list[np.ndarray] = []
        prob_mask: list[float] = []
        for aid in ids:
            e, is_prob = self._experts[aid]
            rows.append(e)
            prob_mask.append(self._probation_mult if is_prob else 1.0)
        if not rows:
            return (
                np.zeros((0, self._cap_dim), dtype=np.float32),
                np.zeros((0,), dtype=np.float32),
            )
        return np.stack(rows, axis=0), np.asarray(prob_mask, dtype=np.float32)

    def _scores(
        self,
        z: np.ndarray,
        E: np.ndarray,
        prob_mask: np.ndarray,
        *,
        ref: bool = False,
    ) -> tuple[np.ndarray, np.ndarray, float]:
        Wz, Wr, Wm, wT = (
            (self._W_z_ref, self._W_r_ref, self._W_m_ref, self._w_T_ref)
            if ref
            else (self._W_z, self._W_r, self._W_m, self._w_T)
        )
        zp = z @ Wz  # (state_proj_dim,)
        expert_logits = (zp @ Wr) @ E.T  # (K,)
        expert_logits = expert_logits * prob_mask
        meta_logits = zp @ Wm  # (3,)
        T = math.log1p(math.exp(float(zp @ wT))) + 1e-3  # softplus
        return expert_logits, meta_logits, T

    # ── sampling ─────────────────────────────────────────────────────────

    def act(
        self,
        z: np.ndarray,
        *,
        active_expert_ids: tuple[AgentID, ...],
    ) -> RoutingAction:
        E, prob_mask = self._expert_matrix(active_expert_ids)
        if E.shape[0] == 0:
            # No experts → forced SPAWN_EXPERT meta-action.
            return RoutingAction(
                expert_distribution=np.zeros((0,), dtype=np.float32),
                chosen_agent_id=None,
                meta_action=MetaAction.SPAWN_EXPERT,
                temperature=1.0,
                log_prob_chosen=0.0,
            )
        logits, meta_logits, T = self._scores(z, E, prob_mask)
        p = _softmax(logits / T)
        idx = int(self._rng.choice(len(active_expert_ids), p=p))
        meta_p = _softmax(meta_logits)
        meta_idx = int(self._rng.choice(3, p=meta_p))
        meta = (MetaAction.EXECUTE, MetaAction.REFUSE, MetaAction.SPAWN_EXPERT)[meta_idx]
        return RoutingAction(
            expert_distribution=p.astype(np.float32),
            chosen_agent_id=active_expert_ids[idx],
            meta_action=meta,
            temperature=float(T),
            log_prob_chosen=float(math.log(p[idx] + 1e-12)),
        )

    def act_deterministic(
        self,
        z: np.ndarray,
        *,
        active_expert_ids: tuple[AgentID, ...],
    ) -> RoutingAction:
        """Argmax over the REFERENCE policy. R1 trip-wire fallback."""
        E, prob_mask = self._expert_matrix(active_expert_ids)
        if E.shape[0] == 0:
            return RoutingAction(
                expert_distribution=np.zeros((0,), dtype=np.float32),
                chosen_agent_id=None,
                meta_action=MetaAction.SPAWN_EXPERT,
                temperature=1.0,
                log_prob_chosen=0.0,
            )
        logits, meta_logits, T = self._scores(z, E, prob_mask, ref=True)
        p = _softmax(logits / T)
        idx = int(np.argmax(p))
        meta_idx = int(np.argmax(meta_logits))
        meta = (MetaAction.EXECUTE, MetaAction.REFUSE, MetaAction.SPAWN_EXPERT)[meta_idx]
        return RoutingAction(
            expert_distribution=p.astype(np.float32),
            chosen_agent_id=active_expert_ids[idx],
            meta_action=meta,
            temperature=float(T),
            log_prob_chosen=float(math.log(p[idx] + 1e-12)),
        )

    # ── PPO update (NumPy reference) ─────────────────────────────────────

    def ppo_update(
        self,
        batch: list[TrajectoryStep],
        lr: float,
        clip: float,
        kl_target: float,
        entropy_coef: float,
    ) -> dict[str, float]:
        if not batch:
            return {"updates": 0.0, "kl_before": 0.0, "kl_after": 0.0, "reverted": 0.0, "loss": 0.0}

        # Save pre-update params for trip-wire revert.
        Wz_pre, Wr_pre = self._W_z.copy(), self._W_r.copy()
        Wm_pre, wT_pre = self._W_m.copy(), self._w_T.copy()

        kl_total = 0.0
        n_kl = 0
        loss_total = 0.0
        for step in batch:
            E = step.expert_matrix
            zp = step.z @ self._W_z
            logits = (zp @ self._W_r) @ E.T
            T = math.log1p(math.exp(float(zp @ self._w_T))) + 1e-3
            p_new = _softmax(logits / T)
            log_p_new = math.log(p_new[step.chosen_index] + 1e-12)
            ratio = math.exp(log_p_new - step.log_prob_chosen)
            adv = step.advantage if step.advantage != 0.0 else step.reward
            unclipped = ratio * adv
            clipped = max(min(ratio, 1.0 + clip), 1.0 - clip) * adv
            ppo_loss = -min(unclipped, clipped)

            # NumPy "gradient": rank-1 update on W_r in the direction of the
            # chosen expert (a true autodiff implementation would propagate
            # through the softmax; the seed implementation is intentionally
            # coarse to keep the spec runnable without a deep-learning dep).
            scale = lr * adv * (1.0 - p_new[step.chosen_index])
            e_chosen = E[step.chosen_index]
            self._W_r += scale * np.outer(zp, e_chosen).astype(np.float32)

            # Entropy regularisation on the expert distribution.
            ent = -float(np.sum(p_new * np.log(p_new + 1e-12)))
            loss_total += ppo_loss - entropy_coef * ent

            # KL to reference policy on this step.
            logits_ref = ((step.z @ self._W_z_ref) @ self._W_r_ref) @ E.T
            T_ref = math.log1p(math.exp(float(step.z @ self._W_z_ref @ self._w_T_ref))) + 1e-3
            p_ref = _softmax(logits_ref / T_ref)
            kl = float(np.sum(p_new * (np.log(p_new + 1e-12) - np.log(p_ref + 1e-12))))
            kl_total += kl
            n_kl += 1

        kl_after = kl_total / max(1, n_kl)
        reverted = False
        if kl_after > kl_target * 5.0:
            # Trip-wire: roll back the whole batch.
            self._W_z, self._W_r = Wz_pre, Wr_pre
            self._W_m, self._w_T = Wm_pre, wT_pre
            reverted = True
            kl_after = 0.0

        return {
            "updates": float(len(batch)),
            "kl_before": 0.0,  # reserved for paired-policy diagnostic
            "kl_after": float(kl_after),
            "reverted": 1.0 if reverted else 0.0,
            "loss": float(loss_total / max(1, len(batch))),
        }

    def bless_reference(self, blend: float) -> None:
        b = max(0.0, min(1.0, blend))
        self._W_z_ref = (b * self._W_z_ref + (1 - b) * self._W_z).astype(np.float32)
        self._W_r_ref = (b * self._W_r_ref + (1 - b) * self._W_r).astype(np.float32)
        self._W_m_ref = (b * self._W_m_ref + (1 - b) * self._W_m).astype(np.float32)
        self._w_T_ref = (b * self._w_T_ref + (1 - b) * self._w_T).astype(np.float32)

    # ── persistence ───────────────────────────────────────────────────────

    def snapshot(self) -> bytes:
        return pickle.dumps(
            {
                "W_z": self._W_z,
                "W_r": self._W_r,
                "W_m": self._W_m,
                "w_T": self._w_T,
                "W_z_ref": self._W_z_ref,
                "W_r_ref": self._W_r_ref,
                "W_m_ref": self._W_m_ref,
                "w_T_ref": self._w_T_ref,
                "experts": self._experts,
            }
        )

    def restore(self, blob: bytes) -> None:
        d = pickle.loads(blob)
        self._W_z = d["W_z"]
        self._W_r = d["W_r"]
        self._W_m = d["W_m"]
        self._w_T = d["w_T"]
        self._W_z_ref = d["W_z_ref"]
        self._W_r_ref = d["W_r_ref"]
        self._W_m_ref = d["W_m_ref"]
        self._w_T_ref = d["w_T_ref"]
        self._experts = d["experts"]


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - x.max()
    e = np.exp(x)
    return e / (e.sum() + 1e-12)
