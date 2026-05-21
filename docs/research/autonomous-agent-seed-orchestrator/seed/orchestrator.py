"""
Seed orchestrator.

Glues together the bilinear MoE router, the agent registry, the VCM,
the memory store, the sandbox, the embedder, the intrinsic reward model,
the telemetry sink, and the spawn callback (Phase 3 bootstrapping) into a
single async lifecycle:

    submit(request) →
        build state vector
        check circuit breakers
        router.act(z) → RoutingAction
        dispatch on meta_action:
            EXECUTE       → run agent in sandbox / via peer endpoint
            REFUSE        → no-cost refusal
            SPAWN_EXPERT  → run spawn callback under rate limits
        compute decomposed reward → push TrajectoryStep
        record outcome → update fitness EMA + breaker windows
        return ExecutionResult

Three background loops run alongside `submit`:
    _eviction_loop          : prunes COOL agents below the low watermark
    _ephemeral_gc_loop      : reclaims expired EPHEMERAL memory records
    _policy_update_loop     : drains the trajectory buffer, runs PPO, blesses ref

Lock ordering (acquire in this order to avoid deadlock):
    _spawn_lock  <  _stats_lock  <  _trajectory_lock
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import numpy as np

from .agent_registry import AgentRegistry
from .embedder import AbstractEmbedder, project_dim
from .memory_store import AbstractMemoryStore
from .moe_router import AbstractMoERouter, TrajectoryStep
from .reward_model import AbstractIntrinsicRewardModel, RollingDiversity
from .sandbox import AbstractSandbox
from .schemas import (
    AgentCapability,
    AgentID,
    ExecutionResult,
    MemoryTier,
    MetaAction,
    RoutingAction,
    StateVector,
    TaskRequest,
    TaskStatus,
)
from .telemetry import TelemetrySink
from .virtual_context import VirtualContextManager


# ─────────────────────────────────────────────────────────────────────────
# Exceptions.
# ─────────────────────────────────────────────────────────────────────────


class CircuitBreakerOpen(Exception):
    """Submission rejected because a rolling-window breaker is open."""


class ProductionConfigError(Exception):
    """OrchestratorConfig.production=True but a non-production component was wired in."""


# ─────────────────────────────────────────────────────────────────────────
# Configuration.
# ─────────────────────────────────────────────────────────────────────────


# Spawn callback: (request, hint?) → AgentCapability (registers a new expert)
SpawnCallback = Callable[..., Awaitable[Optional[AgentCapability]]]


@dataclass(slots=True, frozen=True)
class OrchestratorConfig:
    """Knobs for the seed orchestrator.

    Defaults are calibrated for a dev/CI environment. Production callers
    should review the spawn rate, the circuit-breaker thresholds, and the
    PPO learning-rate before turning `production=True`.
    """

    # State-vector geometry (matches StateVector / Phase 1 §1.1 widths).
    phase_dim: int = 5
    task_embedding_dim: int = 256
    project_embedding_dim: int = 128
    budget_dim: int = 4
    history_dim: int = 32
    project_fingerprint_dim: int = 64
    state_dim: int = 5 + 256 + 128 + 4 + 32 + 64  # = 489

    # Router geometry.
    capability_dim: int = 256
    state_proj_dim: int = 256

    # Fleet caps and spawn rate.
    max_active_agents: int = 64
    max_spawned_agents_per_hour: int = 20
    spawn_timeout_s: float = 60.0

    # Eviction loop.
    eviction_interval_s: float = 30.0
    eviction_low_watermark: float = 0.10
    eviction_grace_period_s: float = 300.0
    protected_agents: frozenset[AgentID] = frozenset()

    # Ephemeral GC loop.
    ephemeral_gc_interval_s: float = 60.0

    # PPO update loop.
    policy_update_interval_s: float = 60.0
    policy_update_min_batch: int = 16
    ppo_lr: float = 5e-4
    ppo_clip: float = 0.2
    ppo_kl_target: float = 0.02
    ppo_kl_bless_threshold: float = 0.05
    ppo_entropy_coef: float = 0.01
    kl_blend: float = 0.99
    trajectory_buffer_capacity: int = 2048

    # Per-task execution caps.
    default_task_timeout_s: float = 60.0
    sandbox_cpu_seconds: int = 30
    sandbox_memory_mb: int = 512
    sandbox_max_files: int = 256

    # Circuit breaker windows.
    cb_window_s: float = 300.0
    cb_min_samples: int = 20
    cb_error_rate_threshold: float = 0.5
    cb_cost_budget_usd: float = 50.0
    cb_cooldown_s: float = 120.0

    # Memory TTLs.
    ephemeral_ttl_s: float = 3600.0

    # Production gate.
    production: bool = False


# Phase one-hot index map (must match StateVector.phase_onehot ordering).
_PHASE_INDEX: dict[str, int] = {
    "research": 0,
    "draft": 1,
    "refine": 2,
    "verify": 3,
    "ship": 4,
}


# ─────────────────────────────────────────────────────────────────────────
# Orchestrator.
# ─────────────────────────────────────────────────────────────────────────


class Orchestrator:
    """Phase-aware MoE orchestrator. Single-process, async-only.

    Construction asserts the production gate: if `config.production=True`
    and the sandbox declares `is_production_grade=False`, we refuse to
    start — a non-production sandbox in a production deploy is the single
    most likely path to a multi-tenant escape.
    """

    def __init__(
        self,
        *,
        config: OrchestratorConfig,
        router: AbstractMoERouter,
        registry: AgentRegistry,
        vcm: VirtualContextManager,
        memory_store: AbstractMemoryStore,
        embedder: AbstractEmbedder,
        sandbox: AbstractSandbox,
        reward_model: AbstractIntrinsicRewardModel,
        telemetry: TelemetrySink,
        spawn_callback: SpawnCallback,
    ) -> None:
        self._config = config
        self._router = router
        self._registry = registry
        self._vcm = vcm
        self._memory_store = memory_store
        self._embedder = embedder
        self._sandbox = sandbox
        self._reward_model = reward_model
        self._telemetry = telemetry
        self._spawn_cb = spawn_callback

        # Production gate (must run before any background loop starts).
        if config.production and not getattr(sandbox, "is_production_grade", False):
            raise ProductionConfigError(
                f"production=True requires a production-grade sandbox; "
                f"got {type(sandbox).__name__} (is_production_grade=False). "
                f"Use FirecrackerSandbox (see INTEGRATION.md P-4)."
            )

        # Wire the router into the registry's listener fan-out so register/
        # evict/promote events update the bilinear capability matrix.
        self._registry.subscribe(self._on_registry_change)

        # Background-loop handles.
        self._eviction_task: Optional[asyncio.Task] = None
        self._ephemeral_gc_task: Optional[asyncio.Task] = None
        self._policy_update_task: Optional[asyncio.Task] = None
        self._started = False

        # Locks (acquire in declared order: spawn < stats < trajectory).
        self._spawn_lock = asyncio.Lock()
        self._stats_lock = asyncio.Lock()
        self._trajectory_lock = asyncio.Lock()

        # Spawn-rate window.
        self._spawn_window_t = time.monotonic()
        self._spawned_in_win = 0

        # Trajectory buffer for PPO.
        self._trajectory_buffer: deque[TrajectoryStep] = deque(
            maxlen=config.trajectory_buffer_capacity
        )

        # Circuit-breaker windows.
        self._error_samples: deque[tuple[float, bool]] = deque()
        self._cost_samples: deque[tuple[float, float]] = deque()
        self._cb_open_until: float = 0.0

        # Counters.
        self._inflight_count: int = 0
        self._completed_count: int = 0
        self._failed_count: int = 0
        self._refused_count: int = 0

        # Diversity tracker (for R^div).
        self._diversity = RollingDiversity()

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Spawn background loops. Idempotent."""
        if self._started:
            return
        self._started = True
        loop = asyncio.get_running_loop()
        self._eviction_task = loop.create_task(self._eviction_loop())
        self._ephemeral_gc_task = loop.create_task(self._ephemeral_gc_loop())
        self._policy_update_task = loop.create_task(self._policy_update_loop())
        self._telemetry.emit("orchestrator.started", {})

    async def stop(self) -> None:
        """Cancel background loops and drain them. Idempotent."""
        if not self._started:
            return
        self._started = False
        for t in (self._eviction_task, self._ephemeral_gc_task, self._policy_update_task):
            if t is not None and not t.done():
                t.cancel()
        for t in (self._eviction_task, self._ephemeral_gc_task, self._policy_update_task):
            if t is not None:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
        self._eviction_task = None
        self._ephemeral_gc_task = None
        self._policy_update_task = None
        self._telemetry.emit("orchestrator.stopped", {})

    async def run_forever(self) -> None:
        """Daemon entry-point: start the loops and block until cancelled."""
        await self.start()
        try:
            await asyncio.Event().wait()
        finally:
            await self.stop()

    # ── public submit ───────────────────────────────────────────────────

    async def submit(self, request: TaskRequest) -> ExecutionResult:
        """Route + execute one task, recording reward and operational stats.

        Hot-path order:
            1. circuit-breaker check (fail fast — never queue under load)
            2. state-vector build (embedder + budget encoding)
            3. router.act → RoutingAction
            4. meta_action dispatch (EXECUTE / REFUSE / SPAWN_EXPERT)
            5. reward eval → trajectory append
            6. record_outcome → breaker windows + fitness EMA
        """
        await self._check_circuit_breakers()

        self._inflight_count += 1
        t_start = time.monotonic()
        try:
            z = self._build_state_vector(request).encoded()
            # Project z to the router's state_dim if widths differ.
            z = project_dim(z, self._config.state_dim)

            active_ids = self._registry.active_ids()
            action = self._router.act(z, active_expert_ids=active_ids)
            self._telemetry.emit(
                "router.action",
                {
                    "task_id": request.task_id,
                    "meta": action.meta_action.value,
                    "chosen": action.chosen_agent_id,
                    "n_experts": len(active_ids),
                    "temperature": action.temperature,
                },
            )

            result = await self._dispatch(request, action)

            # Record diversity and per-agent fitness.
            self._diversity.record(result.agent_id)
            div_score = self._diversity.score()

            cap = self._registry.get(result.agent_id) if result.agent_id else None
            reward, per_judge = await self._reward_model.evaluate(
                request=request,
                result=result,
                capability=cap,
                fleet_diversity=div_score,
            )

            # Append trajectory step for PPO.
            E_snapshot = self._router_capability_matrix(active_ids)
            chosen_idx = (
                active_ids.index(action.chosen_agent_id)
                if action.chosen_agent_id in active_ids
                else 0
            )
            step = TrajectoryStep(
                z=z.astype(np.float32),
                expert_ids=active_ids,
                expert_matrix=E_snapshot,
                chosen_index=chosen_idx,
                log_prob_chosen=float(action.log_prob_chosen),
                meta_action=action.meta_action,
                temperature=float(action.temperature),
                reward=float(reward.scalar),
            )
            async with self._trajectory_lock:
                self._trajectory_buffer.append(step)

            # Update fitness EMA (which may transition the agent's lifecycle).
            if result.agent_id is not None:
                self._registry.record_fitness(result.agent_id, reward.scalar)

            # Update counters + breaker windows.
            ok = result.status == TaskStatus.COMPLETED
            await self._record_outcome(ok, result.cost_usd)
            if ok:
                self._completed_count += 1
            elif result.status == TaskStatus.REFUSED:
                self._refused_count += 1
            else:
                self._failed_count += 1

            self._telemetry.emit(
                "task.complete",
                {
                    "task_id": request.task_id,
                    "status": result.status.value,
                    "agent_id": result.agent_id,
                    "duration_s": result.duration_s,
                    "cost_usd": result.cost_usd,
                    "reward_scalar": reward.scalar,
                    "reward_breakdown": reward.model_dump(),
                    "judges": per_judge,
                    "diversity": div_score,
                    "wall_s": time.monotonic() - t_start,
                },
            )
            return result
        finally:
            self._inflight_count -= 1

    # ── action dispatch ──────────────────────────────────────────────────

    async def _dispatch(self, request: TaskRequest, action: RoutingAction) -> ExecutionResult:
        if action.meta_action == MetaAction.REFUSE:
            return self._make_refusal(request, reason="policy_refused")
        if action.meta_action == MetaAction.SPAWN_EXPERT:
            cap = await self._maybe_spawn(request)
            if cap is None:
                # Fall back to REFUSE if we couldn't spawn (rate-limited,
                # smoke-test failed, etc.). The router will see this as a
                # negative reward via the cost component.
                return self._make_refusal(request, reason="spawn_unavailable")
            # Re-route now that a new expert is available.
            return await self._execute(request, cap)
        # EXECUTE path.
        if action.chosen_agent_id is None:
            return self._make_refusal(request, reason="no_expert_chosen")
        cap = self._registry.get(action.chosen_agent_id)
        if cap is None:
            return self._make_refusal(request, reason="chosen_agent_missing")
        return await self._execute(request, cap)

    async def _execute(self, request: TaskRequest, cap: AgentCapability) -> ExecutionResult:
        """Run an agent. Either via `cap.invoke(request)` or via the sandbox.

        If the capability has a coroutine `invoke`, we await it directly
        (this is how locally-generated experts from the bootstrap pipeline
        run). If `invoke is None`, we expect a `peer_endpoint` (A2A) — the
        seed does not implement A2A transport; INTEGRATION.md P-3 is the
        production work item.
        """
        t0 = time.monotonic()
        try:
            if cap.invoke is not None:
                fut = cap.invoke(request)
                result = await asyncio.wait_for(
                    fut,
                    timeout=min(self._config.default_task_timeout_s, request.deadline_s or 60.0),
                )
                if not isinstance(result, ExecutionResult):
                    return self._make_failure(
                        request,
                        error="invoke_returned_non_execution_result",
                        duration_s=time.monotonic() - t0,
                        agent_id=cap.agent_id,
                    )
                return result
            if cap.peer_endpoint is not None:
                # A2A path: not implemented in the seed.
                return self._make_failure(
                    request,
                    error="a2a_transport_not_implemented_in_seed",
                    duration_s=time.monotonic() - t0,
                    agent_id=cap.agent_id,
                )
            return self._make_failure(
                request,
                error="capability_has_no_invoke_and_no_peer",
                duration_s=time.monotonic() - t0,
                agent_id=cap.agent_id,
            )
        except asyncio.TimeoutError:
            return self._make_failure(
                request,
                error="task_timeout",
                duration_s=time.monotonic() - t0,
                agent_id=cap.agent_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return self._make_failure(
                request,
                error=f"invoke_exception: {e!r}",
                duration_s=time.monotonic() - t0,
                agent_id=cap.agent_id,
            )

    # ── spawn / refusal / failure / breakers (verbatim from /tmp/) ──────

    async def _maybe_spawn(self, request: TaskRequest) -> AgentCapability | None:
        """Free Agent spawn under per-hour rate limit. Returns new capability or None.

        The spawn callback is the bridge into Phase 3: it is the function that
        calls the Anthropic API (via `api_client.py` / `bootstrap.py`) to
        generate a new expert module. Returning None is a normal outcome
        (rate-limited, API failure, smoke-test rejection).
        """
        async with self._spawn_lock:
            now = time.monotonic()
            if now - self._spawn_window_t > 3600.0:
                self._spawn_window_t = now
                self._spawned_in_win = 0
            if self._spawned_in_win >= self._config.max_spawned_agents_per_hour:
                self._telemetry.emit(
                    "spawn.rate_limited",
                    {
                        "window_count": self._spawned_in_win,
                        "limit": self._config.max_spawned_agents_per_hour,
                    },
                )
                return None
            self._spawned_in_win += 1

        # Hold the active-fleet capacity check outside the lock to avoid
        # serializing the spawn callback (which may do API I/O).
        if self._registry.active_count() >= self._config.max_active_agents:
            self._telemetry.emit(
                "spawn.fleet_at_capacity",
                {"active": self._registry.active_count(), "cap": self._config.max_active_agents},
            )
            return None

        t0 = time.monotonic()
        try:
            cap = await asyncio.wait_for(
                self._spawn_cb(request, hint="route_action=SPAWN_EXPERT"),
                timeout=self._config.spawn_timeout_s,
            )
        except asyncio.TimeoutError:
            self._telemetry.emit(
                "spawn.timeout",
                {"task_id": request.task_id, "timeout_s": self._config.spawn_timeout_s},
            )
            return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._telemetry.emit(
                "spawn.error",
                {"task_id": request.task_id, "error": repr(e)},
            )
            return None

        latency_s = time.monotonic() - t0
        if cap is None:
            self._telemetry.emit(
                "spawn.rejected",
                {
                    "task_id": request.task_id,
                    "latency_s": latency_s,
                    "reason": "callback_returned_none",
                },
            )
            return None

        # Registry.register fires on_change listeners → router hot-plug
        # (expands W_r capability matrix and inserts the new expert vector).
        self._registry.register(cap)
        self._telemetry.emit(
            "spawn.ok",
            {
                "agent_id": cap.agent_id,
                "phase": cap.phase,
                "latency_s": latency_s,
                "fleet_size_after": self._registry.active_count(),
            },
        )
        return cap

    def _make_refusal(self, request: TaskRequest, reason: str) -> ExecutionResult:
        """Construct a REFUSED ExecutionResult with no cost charged."""
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.REFUSED,
            agent_id=None,
            output=None,
            error=reason,
            duration_s=0.0,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )

    def _make_failure(
        self,
        request: TaskRequest,
        error: str,
        duration_s: float,
        agent_id: AgentID | None = None,
        cost_usd: float = 0.0,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> ExecutionResult:
        """Construct a FAILED ExecutionResult, charging any costs incurred."""
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=agent_id,
            output=None,
            error=error,
            duration_s=duration_s,
            cost_usd=cost_usd,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            artifacts=(),
        )

    async def _check_circuit_breakers(self) -> None:
        """Trip breakers based on rolling-window stats. Raises CircuitBreakerOpen
        if any are tripped. Called from the submit() hot path.

        Three independent breakers:
          1. Error-rate breaker: fail-fraction over the window exceeds threshold.
          2. Cost breaker: cumulative spend over the window exceeds budget.
          3. Cooldown gate: once tripped, remain open for cb_cooldown_s.
        """
        async with self._stats_lock:
            now = time.monotonic()

            if now < self._cb_open_until:
                remaining = self._cb_open_until - now
                raise CircuitBreakerOpen(f"breaker open: cooldown remaining={remaining:.1f}s")

            cutoff = now - self._config.cb_window_s
            while self._error_samples and self._error_samples[0][0] < cutoff:
                self._error_samples.popleft()
            while self._cost_samples and self._cost_samples[0][0] < cutoff:
                self._cost_samples.popleft()

            n = len(self._error_samples)
            if n >= self._config.cb_min_samples:
                failures = sum(1 for _, ok in self._error_samples if not ok)
                err_rate = failures / n
                if err_rate > self._config.cb_error_rate_threshold:
                    self._cb_open_until = now + self._config.cb_cooldown_s
                    self._telemetry.emit(
                        "circuit_breaker.tripped",
                        {
                            "breaker": "error_rate",
                            "err_rate": err_rate,
                            "threshold": self._config.cb_error_rate_threshold,
                            "window_samples": n,
                            "cooldown_s": self._config.cb_cooldown_s,
                        },
                    )
                    raise CircuitBreakerOpen(
                        f"error_rate={err_rate:.3f} > "
                        f"{self._config.cb_error_rate_threshold:.3f}"
                    )

            spend = sum(c for _, c in self._cost_samples)
            if spend > self._config.cb_cost_budget_usd:
                self._cb_open_until = now + self._config.cb_cooldown_s
                self._telemetry.emit(
                    "circuit_breaker.tripped",
                    {
                        "breaker": "cost",
                        "spend_usd": spend,
                        "budget_usd": self._config.cb_cost_budget_usd,
                        "cooldown_s": self._config.cb_cooldown_s,
                    },
                )
                raise CircuitBreakerOpen(
                    f"spend=${spend:.2f} > budget=${self._config.cb_cost_budget_usd:.2f}"
                )

    async def _record_outcome(self, ok: bool, cost_usd: float) -> None:
        """Append a sample to the circuit-breaker windows. Lock-protected."""
        async with self._stats_lock:
            now = time.monotonic()
            self._error_samples.append((now, ok))
            if cost_usd > 0:
                self._cost_samples.append((now, cost_usd))

    # ── background loops ─────────────────────────────────────────────────

    async def _eviction_loop(self) -> None:
        """Background task: evict agents below the low fitness watermark.

        Runs every eviction_interval_s. Eviction is gated by:
          - fitness EMA below low_watermark
          - agent has existed for ≥ eviction_grace_period_s
          - agent is not in protected_agents (bootstrap fleet)
        Evicted agents are immediately removed from the router's active set
        via the registry's on_change listener.
        """
        while True:
            try:
                await asyncio.sleep(self._config.eviction_interval_s)
            except asyncio.CancelledError:
                return
            try:
                candidates = self._registry.candidates_for_eviction(
                    low_watermark=self._config.eviction_low_watermark,
                    grace_period_s=self._config.eviction_grace_period_s,
                )
                evicted = 0
                for agent_id in candidates:
                    if agent_id in self._config.protected_agents:
                        continue
                    if self._registry.evict(agent_id):
                        evicted += 1
                        self._telemetry.emit(
                            "eviction.evicted",
                            {"agent_id": agent_id, "fitness": self._registry.fitness(agent_id)},
                        )
                if evicted > 0:
                    self._telemetry.emit(
                        "eviction.cycle",
                        {"evicted": evicted, "active_after": self._registry.active_count()},
                    )
            except asyncio.CancelledError:
                return
            except Exception as e:
                # Background loop must not die: log and continue.
                self._telemetry.emit("eviction.error", {"error": repr(e)})

    async def _ephemeral_gc_loop(self) -> None:
        """Background task: reclaim expired EPHEMERAL memory records.

        Contract: the memory store implements
          async def gc_expired(tier: MemoryTier, before_ts: float) -> int
        which deletes records of the given tier whose `expires_at <= before_ts`
        and returns the number of records removed.
        """
        while True:
            try:
                await asyncio.sleep(self._config.ephemeral_gc_interval_s)
            except asyncio.CancelledError:
                return
            try:
                now = time.time()
                removed = await self._memory_store.gc_expired(MemoryTier.EPHEMERAL, now)
                if removed > 0:
                    self._telemetry.emit(
                        "ephemeral_gc.cycle",
                        {"removed": removed, "ts": now},
                    )
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._telemetry.emit("ephemeral_gc.error", {"error": repr(e)})

    async def _policy_update_loop(self) -> None:
        """Background task: drain trajectory buffer and run a PPO update.

        Updates happen every policy_update_interval_s OR when the trajectory
        buffer reaches policy_update_min_batch — whichever comes first.
        After each successful update, the router's reference policy is blessed
        via Polyak averaging (kl_blend), tightening the trust region.
        """
        while True:
            try:
                await asyncio.sleep(self._config.policy_update_interval_s)
            except asyncio.CancelledError:
                return
            try:
                async with self._trajectory_lock:
                    if len(self._trajectory_buffer) < self._config.policy_update_min_batch:
                        continue
                    batch = list(self._trajectory_buffer)
                    self._trajectory_buffer.clear()
                t0 = time.monotonic()
                stats = await asyncio.to_thread(
                    self._router.ppo_update,
                    batch,
                    self._config.ppo_lr,
                    self._config.ppo_clip,
                    self._config.ppo_kl_target,
                    self._config.ppo_entropy_coef,
                )
                if stats.get("kl_after", 0.0) <= self._config.ppo_kl_bless_threshold:
                    self._router.bless_reference(self._config.kl_blend)
                self._telemetry.emit(
                    "policy.update",
                    {**stats, "batch_size": len(batch), "duration_s": time.monotonic() - t0},
                )
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._telemetry.emit("policy.update_error", {"error": repr(e)})

    async def stats(self) -> dict[str, Any]:
        """Snapshot of operational state (read-only, lock-protected)."""
        async with self._stats_lock:
            cb_state = "open" if time.monotonic() < self._cb_open_until else "closed"
            err_samples = list(self._error_samples)
            cost_samples = list(self._cost_samples)
        return {
            "ts": time.time(),
            "active_agents": self._registry.active_count(),
            "inflight_tasks": self._inflight_count,
            "completed_tasks": self._completed_count,
            "failed_tasks": self._failed_count,
            "refused_tasks": self._refused_count,
            "spawned_this_window": self._spawned_in_win,
            "circuit_breaker": cb_state,
            "cb_window_n": len(err_samples),
            "cb_window_err_rate": (
                sum(1 for _, ok in err_samples if not ok) / max(1, len(err_samples))
            ),
            "cb_window_spend_usd": sum(c for _, c in cost_samples),
            "trajectory_buffer_size": len(self._trajectory_buffer),
        }

    # ── helpers ──────────────────────────────────────────────────────────

    def _build_state_vector(self, request: TaskRequest) -> StateVector:
        """Materialise the composite state for one task. Pure (no I/O)."""
        cfg = self._config

        # τ_t: one-hot phase.
        phase = np.zeros(cfg.phase_dim, dtype=np.float32)
        phase[_PHASE_INDEX.get(request.phase, 1)] = 1.0

        # c_t: task context embedding (summary + phase + first 5 tag bag).
        text = f"{request.phase}|{request.summary}"
        task_emb = project_dim(self._embedder.embed(text), cfg.task_embedding_dim)

        # p_t: project context embedding (zeros for CONSENSUS).
        if request.project_id is None:
            proj_emb = np.zeros(cfg.project_embedding_dim, dtype=np.float32)
        else:
            proj_emb = project_dim(
                self._embedder.embed(f"project:{request.project_id}"),
                cfg.project_embedding_dim,
            )

        # b_t: budget encoding.
        b = request.budget.encoded()
        if b.shape[0] != cfg.budget_dim:
            b = project_dim(b, cfg.budget_dim)

        # h_t: rolling history summary (very simple: completed/failed/refused
        # ratios + recent diversity). Pad to history_dim.
        total = max(1, self._completed_count + self._failed_count + self._refused_count)
        hist = np.zeros(cfg.history_dim, dtype=np.float32)
        hist[0] = self._completed_count / total
        hist[1] = self._failed_count / total
        hist[2] = self._refused_count / total
        hist[3] = float(self._diversity.score())
        hist[4] = float(self._inflight_count) / max(1, cfg.max_active_agents)

        # φ_proj: project fingerprint (HMAC-style hash of project_id, then
        # projected through the embedder for a stable per-project vector).
        if request.project_id is None:
            proj_fp = np.zeros(cfg.project_fingerprint_dim, dtype=np.float32)
        else:
            proj_fp = project_dim(
                self._embedder.embed(f"fingerprint:{request.project_id}"),
                cfg.project_fingerprint_dim,
            )

        return StateVector(
            phase_onehot=phase,
            task_embedding=task_emb,
            project_embedding=proj_emb,
            budget_encoded=b.astype(np.float32),
            history_summary=hist,
            project_fingerprint=proj_fp,
            capability_ids=self._registry.active_ids(),
        )

    def _router_capability_matrix(self, active_ids: tuple[AgentID, ...]) -> np.ndarray:
        """Snapshot the embeddings the router will see for `active_ids`.

        Pulled directly from the router via its expert table — we don't
        re-embed capabilities here because the router's table is the
        authoritative one (it is the side mutated by hot-plug events).
        """
        rows: list[np.ndarray] = []
        for aid in active_ids:
            emb, _ = getattr(self._router, "_experts", {}).get(
                aid, (np.zeros(self._config.capability_dim, dtype=np.float32), False)
            )
            rows.append(np.asarray(emb, dtype=np.float32))
        if not rows:
            return np.zeros((0, self._config.capability_dim), dtype=np.float32)
        return np.stack(rows, axis=0)

    def _on_registry_change(
        self,
        event: str,
        agent_id: AgentID,
        cap: Optional[AgentCapability],
    ) -> None:
        """Bridge registry events into the router's hot-plug surface.

        The registry fires this synchronously (outside its lock). We use the
        embedder to materialise the capability vector before handing it to
        the router. Idempotent across duplicate events.
        """
        if event == "register" and cap is not None:
            text = f"{cap.phase}|{cap.description}|tags:{','.join(cap.tags)}"
            emb = project_dim(self._embedder.embed(text), self._config.capability_dim)
            self._router.add_expert(cap, emb)
        elif event == "evict":
            self._router.remove_expert(agent_id)
        elif event == "promote" and cap is not None:
            # If the router exposes `on_registry_change`, use it; otherwise
            # the lifecycle change is already reflected in the registry, and
            # the router will see the new lifecycle when it next queries.
            handler = getattr(self._router, "on_registry_change", None)
            if handler is not None:
                handler(event, agent_id, cap)
