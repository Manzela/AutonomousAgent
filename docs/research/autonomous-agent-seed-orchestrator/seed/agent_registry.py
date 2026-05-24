"""
Agent registry with Free Agent FSM lifecycle.

FSM (Phase 1 §1.4, §3.3):

    SPAWN ──▶ PROBATION ──▶ ACTIVE ──▶ COOL ──▶ EVICTED
                  │              │         │
                  └──────────────┴─────────┴──▶ PROMOTED  (to CONSENSUS)

Transitions are EMA-driven. Each completed task contributes to the agent's
fitness EMA; agents on PROBATION are graduated to ACTIVE once their EMA
clears `promotion_watermark` AND they have served at least
`probation_min_tasks`. ACTIVE agents that decay below `cool_watermark`
demote to COOL (still routable but down-weighted); COOL agents below
`eviction_watermark` after the grace period are removed.

The registry fires `on_change(event, agent_id, cap)` listeners after each
mutation. Listeners run OUTSIDE the registry lock so a slow listener (e.g.,
the router's hot-plug path that builds a fresh capability embedding) cannot
block subsequent register/evict calls. The listener fan-out is best-effort:
exceptions in one listener do NOT abort the fan-out to the others.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .schemas import AgentCapability, AgentID, Lifecycle


# Listener signature shared between registry, router (hot-plug), and
# observers (telemetry sinks).
Listener = Callable[[str, AgentID, Optional[AgentCapability]], None]


@dataclass(slots=True)
class _AgentEntry:
    cap: AgentCapability
    fitness: float = 0.0
    n_tasks: int = 0
    last_fitness_update_ts: float = field(default_factory=time.time)
    cool_since: Optional[float] = None  # set when we transition INTO COOL


@dataclass(slots=True, frozen=True)
class RegistryConfig:
    """Watermarks and EMA decay for the lifecycle transitions."""

    fitness_ema_alpha: float = 0.2  # 0 < α ≤ 1 — higher = react faster
    promotion_watermark: float = 0.6
    cool_watermark: float = 0.25
    eviction_watermark: float = 0.10
    probation_min_tasks: int = 5  # tasks before probation→active
    cool_recovery_grace_s: float = 300.0  # time in COOL before eviction is allowed
    consensus_promotion_watermark: float = 0.85  # very high bar for CONSENSUS
    consensus_promotion_min_tasks: int = 50  # and stable record


class AgentRegistry:
    """Thread- and coroutine-safe registry for hot-pluggable agents.

    Concurrency model: one `threading.RLock` guards the entry map and the
    listener set; mutations release the lock before fanning out. Async
    callers should use the `*_async` variants when available, but the sync
    methods are safe to call from coroutines because the lock is reentrant
    and the critical sections are short.
    """

    def __init__(self, *, config: Optional[RegistryConfig] = None) -> None:
        self._cfg = config or RegistryConfig()
        self._entries: dict[AgentID, _AgentEntry] = {}
        self._listeners: list[Listener] = []
        self._lock = threading.RLock()
        # Per-event throttle for noisy listeners (e.g., telemetry); the
        # registry itself never throttles, but exposes timings via stats.
        self._mutation_count: int = 0

    # ── listener wiring ─────────────────────────────────────────────────

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

    def _fanout(self, event: str, agent_id: AgentID, cap: Optional[AgentCapability]) -> None:
        # Snapshot listeners under the lock, then call outside it.
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(event, agent_id, cap)
            except Exception:
                # Best-effort fan-out: one bad listener cannot poison the rest.
                pass

    # ── CRUD ────────────────────────────────────────────────────────────

    def register(self, cap: AgentCapability) -> None:
        """Insert (or replace) an agent. Fires `register` listener event.

        A freshly-registered agent starts in its declared lifecycle, which
        for spawned experts will be `probation`. The router's listener turns
        this into a column append on the capability matrix.
        """
        with self._lock:
            self._entries[cap.agent_id] = _AgentEntry(cap=cap)
            self._mutation_count += 1
        self._fanout("register", cap.agent_id, cap)

    def evict(self, agent_id: AgentID) -> bool:
        """Remove an agent. Returns True if it was present."""
        with self._lock:
            entry = self._entries.pop(agent_id, None)
            if entry is None:
                return False
            self._mutation_count += 1
        # Emit a capability snapshot so listeners can know what was lost.
        cap_evicted = entry.cap.model_copy(update={"lifecycle": "evicted"})
        self._fanout("evict", agent_id, cap_evicted)
        return True

    def promote(self, agent_id: AgentID, *, to: Lifecycle = "active") -> bool:
        """Transition an agent's lifecycle. Returns True on success.

        `to="promoted"` denotes a CONSENSUS promotion (the meta-evaluator's
        decision); the registry does not itself decide which agents are
        promoted to CONSENSUS — that decision belongs to a separate process
        operating with a consensus-scoped VirtualContextHandle.
        """
        with self._lock:
            entry = self._entries.get(agent_id)
            if entry is None:
                return False
            old = entry.cap.lifecycle
            if old == to:
                return False
            entry.cap = entry.cap.model_copy(update={"lifecycle": to})
            self._mutation_count += 1
            cap_snapshot = entry.cap
        self._fanout("promote", agent_id, cap_snapshot)
        return True

    def get(self, agent_id: AgentID) -> Optional[AgentCapability]:
        with self._lock:
            entry = self._entries.get(agent_id)
            return entry.cap if entry is not None else None

    # ── fitness ─────────────────────────────────────────────────────────

    def record_fitness(self, agent_id: AgentID, reward_scalar: float) -> None:
        """Update the EMA-tracked fitness and trigger lifecycle transitions.

        The transitions are computed under the lock and the resulting event
        (if any) is fanned out after release. Multiple transitions can fire
        per call (e.g. PROBATION→ACTIVE→COOL within one step if the EMA
        crashes after a probationary success) — we cap to one transition
        per call to avoid pathological flapping.
        """
        events: list[tuple[str, AgentID, AgentCapability]] = []
        with self._lock:
            entry = self._entries.get(agent_id)
            if entry is None:
                return
            alpha = self._cfg.fitness_ema_alpha
            entry.fitness = (1.0 - alpha) * entry.fitness + alpha * float(reward_scalar)
            entry.n_tasks += 1
            entry.last_fitness_update_ts = time.time()
            transitioned = self._maybe_transition_locked(entry)
            if transitioned is not None:
                events.append(("promote", entry.cap.agent_id, transitioned))
        for ev, aid, cap in events:
            self._fanout(ev, aid, cap)

    def _maybe_transition_locked(self, entry: _AgentEntry) -> Optional[AgentCapability]:
        """Run one lifecycle transition for `entry` if warranted. Lock-held."""
        c = self._cfg
        lifecycle = entry.cap.lifecycle

        # Spawn (just constructed) → probation: handled at register time;
        # we still apply it lazily if a registrar forgot.
        if lifecycle == "spawn":
            entry.cap = entry.cap.model_copy(update={"lifecycle": "probation"})
            return entry.cap

        # Probation → active when fitness + tenure both clear watermarks.
        if (
            lifecycle == "probation"
            and entry.fitness >= c.promotion_watermark
            and entry.n_tasks >= c.probation_min_tasks
        ):
            entry.cap = entry.cap.model_copy(update={"lifecycle": "active"})
            entry.cool_since = None
            return entry.cap

        # Active → cool when fitness slips below the cool watermark.
        if lifecycle == "active" and entry.fitness < c.cool_watermark:
            entry.cap = entry.cap.model_copy(update={"lifecycle": "cool"})
            entry.cool_since = time.time()
            return entry.cap

        # Cool → active recovery when fitness climbs back above promotion.
        if lifecycle == "cool" and entry.fitness >= c.promotion_watermark:
            entry.cap = entry.cap.model_copy(update={"lifecycle": "active"})
            entry.cool_since = None
            return entry.cap

        # We do NOT fire COOL→EVICTED here — eviction is owned by the
        # orchestrator's background loop, which honours `protected_agents`
        # and respects per-cycle eviction batch sizes.
        return None

    def fitness(self, agent_id: AgentID) -> Optional[float]:
        with self._lock:
            entry = self._entries.get(agent_id)
            return entry.fitness if entry is not None else None

    # ── queries ─────────────────────────────────────────────────────────

    def active_count(self) -> int:
        """Count of agents the router should consider routable.

        That's ACTIVE + PROBATION + COOL — anything that hasn't been evicted
        and isn't a CONSENSUS promotion sink. The router applies its own
        per-lifecycle logit multiplier downstream.
        """
        with self._lock:
            return sum(
                1
                for e in self._entries.values()
                if e.cap.lifecycle in ("active", "probation", "cool", "spawn")
            )

    def active_ids(self) -> tuple[AgentID, ...]:
        with self._lock:
            return tuple(
                aid
                for aid, e in self._entries.items()
                if e.cap.lifecycle in ("active", "probation", "cool", "spawn")
            )

    def snapshot(self) -> dict[AgentID, dict]:
        """Read-only snapshot for telemetry / dashboards."""
        with self._lock:
            return {
                aid: {
                    "lifecycle": e.cap.lifecycle,
                    "fitness": e.fitness,
                    "n_tasks": e.n_tasks,
                    "phase": e.cap.phase,
                    "tags": list(e.cap.tags),
                    "spawned_at": e.cap.spawned_at,
                    "last_update": e.last_fitness_update_ts,
                }
                for aid, e in self._entries.items()
            }

    def candidates_for_eviction(
        self,
        *,
        low_watermark: float,
        grace_period_s: float,
    ) -> list[AgentID]:
        """List COOL agents that have been below `low_watermark` for `grace_period_s`.

        Called by the orchestrator's `_eviction_loop`. The orchestrator is
        responsible for filtering out `protected_agents` and enforcing any
        per-cycle batch size — the registry just enumerates candidates.
        """
        out: list[AgentID] = []
        now = time.time()
        with self._lock:
            for aid, e in self._entries.items():
                if e.cap.lifecycle != "cool":
                    continue
                if e.fitness > low_watermark:
                    continue
                if e.cool_since is None:
                    continue
                if (now - e.cool_since) < grace_period_s:
                    continue
                out.append(aid)
        return out

    def candidates_for_consensus(self) -> list[AgentID]:
        """Agents whose track record meets the CONSENSUS promotion bar.

        The orchestrator's meta-evaluator process actually performs the
        promotion (it owns the CONSENSUS-scoped VCM handle); this method
        just surfaces who qualifies on raw metrics.
        """
        c = self._cfg
        out: list[AgentID] = []
        with self._lock:
            for aid, e in self._entries.items():
                if e.cap.lifecycle not in ("active",):
                    continue
                if e.fitness < c.consensus_promotion_watermark:
                    continue
                if e.n_tasks < c.consensus_promotion_min_tasks:
                    continue
                out.append(aid)
        return out

    def mutation_count(self) -> int:
        with self._lock:
            return self._mutation_count
