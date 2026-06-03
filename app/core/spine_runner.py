"""SpineRunner — the checkpointer/durability injection seam.

A future FastAPI lifespan constructs `SpineRunner(InMemoryCheckpointer())` (or the
prod provider) ONCE and shares it. graph.py stays env-agnostic: durability comes
from the provider, never hardcoded. Mirrors OrchestratorConfig-style injection.

SP-R1 enforcement: the runner scrubs the untrusted goal through lib/scrubber.py and
asserts the initial state carries no callables BEFORE it enters the graph/checkpoint
(the full serde-level scrub is the deferred SP-R1 hardening).
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any, Optional

from langgraph.types import Command

from app.adapters.inmemory.board import AlwaysReadyGate, InMemoryBoard
from app.core import graph_state as gs
from app.core.board import AbstractBoard, GateReader
from app.core.checkpointer import AbstractCheckpointer, DurabilityMode
from app.core.graph import build_spine
from app.core.reaper import WorkspaceReaper, reap_orphaned_worktrees
from app.core.sandbox import AbstractSandbox
from app.core.schemas import AgentCapability
from app.core.steering import SteeringEventBus
from lib.durability.branch_lease import BranchLease, GlobalThreadCap
from lib.scrubber import scrub_string

logger = logging.getLogger(__name__)


def _default_cap() -> GlobalThreadCap:
    """SP-11/SP-R6 fan-out concurrency cap. SPINE_MAX_ACTIVE bounds simultaneously-active
    sub-agent worktrees+sandboxes (default 4 — keeps a wide DAG from exhausting disk/FDs;
    acquired BEFORE WorkspaceSession.create so disk is bounded). Operator-tunable per env."""
    try:
        n = int(os.environ.get("SPINE_MAX_ACTIVE", "4"))
    except ValueError:
        n = 4
    return GlobalThreadCap(max(1, n))


def _default_budget() -> Optional[float]:
    """SP-R2 per-graph USD budget. SPINE_BUDGET_USD caps the cumulative cost_accumulator a SINGLE
    graph may spend before fan_out pre-empts the next wave (INLINE, independent of the F21 daily
    poller). Unset => None == budget OFF (opt-in; existing deployments unaffected). A SET-but-invalid
    value (non-positive or unparseable) is a MISCONFIG, not "off" — it logs a loud WARNING and falls
    back to OFF (we never silently treat ``SPINE_BUDGET_USD=0`` as "block all spend").

    HONEST LIMIT (post-audit I-1): the per-graph cap is a LIVE NO-OP until a capability reports a real
    ``ExecutionResult.cost_usd`` — the default/stub capability and every app.core.orchestrator path
    return ``cost_usd=0.0``, so ``aggregate_spend`` is 0.0 and ``budget_verdict`` never pre-empts even
    with SPINE_BUDGET_USD set. The pre-emption MECHANISM is wired + tested (with a cost-reporting
    capability); making it bite in production needs real LLM/sandbox cost threaded into cost_usd
    (reuse lib/observability._llm_request_cost_usd) — DEFERRED. So setting a budget today is dormant,
    not enforcing; ship is still gated by the SP-06 eval_gate + the operator's HITL approval."""
    raw = os.environ.get("SPINE_BUDGET_USD")
    if raw is None:
        return None
    try:
        v = float(raw)
    except ValueError:
        logger.warning(
            "SPINE_BUDGET_USD=%r is not a number — the per-graph budget is OFF (no cost guardrail)",
            raw,
        )
        return None
    if v <= 0:
        logger.warning(
            "SPINE_BUDGET_USD=%s is non-positive — the per-graph budget is OFF (it does NOT block "
            "all spend); set a positive USD cap to enforce",
            raw,
        )
        return None
    return v


def _initial_state(thread_id: str, goal: str) -> dict:
    # Optional fields absent at start (populated by the first node that writes them):
    #   triage_card_id (SP-B1): goal_intake creates the board entry card when a board is injected.
    #   board_cards (SP-16): decompose writes {parent, nodes} after the plan is locked.
    #   budget_verdict (SP-R2): fan_out writes the GraphBudgetVerdict after each wave.
    state = {
        "thread_id": thread_id,
        "goal": scrub_string(goal, source="goal_intake"),  # scrub-before-persist (SP-R1)
        "clarifications": [],
        "tasks": [],
        "ledger": [],
        "execution_counts": {},
        "decision_record": [],
        "audit": [],
        "steering_events": [],
        "cost_accumulator": {},
        "fix_attempts": 0,
        "scrubbed": True,
    }
    gs.assert_serializable_state(state)  # no callables enter the checkpoint
    return state


class SpineRunner:
    def __init__(
        self,
        checkpointer: AbstractCheckpointer,
        *,
        capability: Optional[AgentCapability] = None,
        sandbox: Optional[AbstractSandbox] = None,
        board: Optional[AbstractBoard] = None,
        gate: Optional[GateReader] = None,
        bus: Optional[SteeringEventBus] = None,
    ) -> None:
        self._provider = checkpointer
        self._saver = checkpointer.build_saver()
        self._capability = capability
        self._sandbox = sandbox
        # SP-17: the SteeringEventBus routes inbound Telegram/board events to open interrupts
        # (C15 dedup + arbitration). None => bus-less skeleton (tests that don't need steering).
        # The live bus is constructed by the caller (FastAPI lifespan, nightly integration tests).
        self._bus = bus
        # SP-16 (slice 2): the kanban board the LIVE spine projects its DAG onto. Default to an
        # InMemoryBoard so the entrypoint always renders cards (a build_spine closure arg like
        # cap/lease/sandbox — NEVER in SpineState). The DEFERRED Hermes kanban_db adapter is the
        # prod concretion. C15: outbound only — no graph node reads it.
        self._board = board or InMemoryBoard()
        # SP-16 (slice 3): the C14 GateReader ship_effect consults to close the root goal card.
        # Default to the AlwaysReadyGate CI stub so the live entrypoint closes shipped roots (the
        # spine reaches ship_effect only after eval_gate + the operator's ship approval — gate-
        # derived). The real `gh pr checks --required` reader is the DEFERRED prod sibling.
        self._gate = gate or AlwaysReadyGate()
        # SP-11/SP-R6: one fan-out cap + one per-branch lease per runner, constructed ONCE and
        # shared by every fan_out super-step. The lease dir is per-runner ephemeral (single-
        # process spine — a cross-process shared dir is the deferred multi-process tier). Both
        # are build_spine closure args (like capability/sandbox) — NEVER in SpineState.
        self._cap = _default_cap()
        self._lease = BranchLease(tempfile.mkdtemp(prefix="aa-lease-"))
        # SP-R2: the per-graph USD budget (SPINE_BUDGET_USD) — a build_spine closure arg like
        # cap/lease, NEVER in SpineState. None => budget OFF (zero behaviour change).
        self._budget = _default_budget()
        # SP-05d: per-runner lifecycle reaper — tracks active WorkspaceSessions + the lease dir
        # for atexit panic teardown. Also prunes orphaned aa-ws-* worktrees from prior crashed runs.
        from pathlib import Path as _Path

        self._reaper = WorkspaceReaper()
        self._reaper.register_lease_dir(_Path(self._lease._dir))
        try:
            reap_orphaned_worktrees(_Path(".").resolve())
        except Exception:  # noqa: BLE001
            pass  # fail-open: orphan sweep never blocks runner startup
        # SP-05 (F-1): thread the sandbox to build_spine so the LIVE entrypoint actually runs
        # in a sandbox (production previously ran sandbox=None — the in-process path).
        # assert_serializable_state's no-callable invariant is preserved (cap/lease/sandbox are
        # closure args, never state). sandbox=None keeps the legacy in-process skeleton.
        self._app = build_spine(
            self._saver,
            capability=capability,
            sandbox=sandbox,
            cap=self._cap,
            lease=self._lease,
            budget_usd=self._budget,
            board=self._board,
            gate=self._gate,
            reaper=self._reaper,
        )

    @property
    def durability(self) -> DurabilityMode:
        return self._provider.durability_mode

    def _cfg(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    async def start(
        self, *, thread_id: str, goal: str, durability: Optional[DurabilityMode] = None
    ) -> dict:
        return await self._app.ainvoke(
            _initial_state(thread_id, goal),
            self._cfg(thread_id),
            durability=durability or self.durability,
        )

    async def resume(
        self,
        *,
        thread_id: str,
        interrupt_id: str,
        decision: Any,
        durability: Optional[DurabilityMode] = None,
    ) -> dict:
        # Stamp the resumed value with the REAL Interrupt.id so the decision-record
        # is keyed by the id the operator resumed with (not __pregel_task_id).
        if isinstance(decision, dict):
            decision = {**decision, "interrupt_id": interrupt_id}
        return await self._app.ainvoke(
            Command(resume={interrupt_id: decision}),
            self._cfg(thread_id),
            durability=durability or self.durability,
        )

    def get_state(self, thread_id: str):
        return self._app.get_state(self._cfg(thread_id))
