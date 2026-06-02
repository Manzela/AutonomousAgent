"""SpineRunner — the checkpointer/durability injection seam.

A future FastAPI lifespan constructs `SpineRunner(InMemoryCheckpointer())` (or the
prod provider) ONCE and shares it. graph.py stays env-agnostic: durability comes
from the provider, never hardcoded. Mirrors OrchestratorConfig-style injection.

SP-R1 enforcement: the runner scrubs the untrusted goal through lib/scrubber.py and
asserts the initial state carries no callables BEFORE it enters the graph/checkpoint
(the full serde-level scrub is the deferred SP-R1 hardening).
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.types import Command

from app.core import graph_state as gs
from app.core.checkpointer import AbstractCheckpointer, DurabilityMode
from app.core.graph import build_spine
from app.core.schemas import AgentCapability
from lib.scrubber import scrub_string


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
    ) -> None:
        self._provider = checkpointer
        self._saver = checkpointer.build_saver()
        self._capability = capability
        self._app = build_spine(self._saver, capability=capability)

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
