"""SpineRunner — the checkpointer/durability injection seam.

A future FastAPI lifespan constructs `SpineRunner(InMemoryCheckpointer())` (or the
prod provider) ONCE and shares it. graph.py stays env-agnostic: durability comes
from the provider, never hardcoded. Mirrors OrchestratorConfig-style injection.

SP-R1 enforcement: the runner scrubs the untrusted goal through lib/scrubber.py and
asserts the initial state carries no callables BEFORE it enters the graph/checkpoint
(the full serde-level scrub is the deferred SP-R1 hardening).
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Optional

from langgraph.types import Command

from app.core import graph_state as gs
from app.core.checkpointer import AbstractCheckpointer, DurabilityMode
from app.core.graph import build_spine
from app.core.sandbox import AbstractSandbox
from app.core.schemas import AgentCapability
from lib.durability.branch_lease import BranchLease, GlobalThreadCap
from lib.scrubber import scrub_string


def _default_cap() -> GlobalThreadCap:
    """SP-11/SP-R6 fan-out concurrency cap. SPINE_MAX_ACTIVE bounds simultaneously-active
    sub-agent worktrees+sandboxes (default 4 — keeps a wide DAG from exhausting disk/FDs;
    acquired BEFORE WorkspaceSession.create so disk is bounded). Operator-tunable per env."""
    try:
        n = int(os.environ.get("SPINE_MAX_ACTIVE", "4"))
    except ValueError:
        n = 4
    return GlobalThreadCap(max(1, n))


def _initial_state(thread_id: str, goal: str) -> dict:
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
    ) -> None:
        self._provider = checkpointer
        self._saver = checkpointer.build_saver()
        self._capability = capability
        self._sandbox = sandbox
        # SP-11/SP-R6: one fan-out cap + one per-branch lease per runner, constructed ONCE and
        # shared by every fan_out super-step. The lease dir is per-runner ephemeral (single-
        # process spine — a cross-process shared dir is the deferred multi-process tier). Both
        # are build_spine closure args (like capability/sandbox) — NEVER in SpineState.
        self._cap = _default_cap()
        self._lease = BranchLease(tempfile.mkdtemp(prefix="aa-lease-"))
        # SP-05 (F-1): thread the sandbox to build_spine so the LIVE entrypoint actually runs
        # in a sandbox (production previously ran sandbox=None — the in-process path).
        # assert_serializable_state's no-callable invariant is preserved (cap/lease/sandbox are
        # closure args, never state). sandbox=None keeps the legacy in-process skeleton.
        self._app = build_spine(
            self._saver, capability=capability, sandbox=sandbox, cap=self._cap, lease=self._lease
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
