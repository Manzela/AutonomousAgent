"""SP-01 spine oracles.

Skeleton: goal_intake -> sign_off[interrupt] -> seal_spec -> execute
          -> ship_gate[interrupt] -> ship_effect -> END.
"""

from __future__ import annotations

import operator
from typing import Annotated

from typing_extensions import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core import graph_state as gs
from app.core.schemas import AgentCapability, ExecutionResult, TaskStatus
from app.core.spine_runner import SpineRunner


def _stub_capability(status=TaskStatus.COMPLETED):
    async def _invoke(request):
        return ExecutionResult(task_id=request.task_id, status=status, cost_usd=0.01)

    return AgentCapability(
        agent_id="stub-agent",
        version="1",
        phase="draft",
        description="skeleton stub capability",
        invoke=_invoke,
    )


# ── Task 0: harness smoke ───────────────────────────────────────────────────
async def test_async_node_interrupt_idmap_resume_and_pregel_task_id():
    class S(TypedDict):
        log: Annotated[list, operator.add]
        seen_ptid: str
        decision: str

    async def gate(state, config):
        d = interrupt({"q": "ok?"})
        return {"decision": d, "log": ["gate resumed"]}

    async def effect(state, config):
        ptid = config["configurable"]["__pregel_task_id"]
        return {"seen_ptid": ptid, "log": [f"effect ptid={bool(ptid)}"]}

    g = StateGraph(S)
    g.add_node("gate", gate)
    g.add_node("effect", effect)
    g.add_edge(START, "gate")
    g.add_edge("gate", "effect")
    g.add_edge("effect", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "smoke"}}

    r1 = await app.ainvoke({"log": [], "seen_ptid": "", "decision": ""}, cfg, durability="sync")
    intr = r1["__interrupt__"][0]
    assert intr.id and intr.value == {"q": "ok?"}
    assert app.get_state(cfg).next == ("gate",)

    r2 = await app.ainvoke(Command(resume={intr.id: "APPROVE"}), cfg, durability="sync")
    assert r2["decision"] == "APPROVE"
    assert r2["seen_ptid"]  # __pregel_task_id was non-empty inside the node


# ── Task 4: skeleton happy path ─────────────────────────────────────────────
async def test_skeleton_happy_path_two_interrupts_seal_execute_ship():
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-happy"

    r1 = await runner.start(thread_id=tid, goal="ship a hello-world endpoint")
    assert "__interrupt__" in r1
    signoff = r1["__interrupt__"][0]
    assert signoff.id and signoff.value["gate"] == "sign_off"
    assert runner.get_state(tid).next == ("sign_off",)  # nothing built before approval

    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=signoff.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "lgtm"},
    )
    assert "__interrupt__" in r2  # paused again at ship_gate
    ship = r2["__interrupt__"][0]
    assert ship.id != signoff.id and ship.value["gate"] == "ship"
    assert runner.get_state(tid).next == ("ship_gate",)
    assert r2.get("spec_sha")  # seal_spec ran (sha-pinned)
    assert any(t["status"] == "completed" for t in r2.get("tasks", []))  # execute ran

    r3 = await runner.resume(
        thread_id=tid,
        interrupt_id=ship.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "ship it"},
    )
    assert "__interrupt__" not in r3  # reached END
    assert runner.get_state(tid).next == ()
    # exactly-once: the ship effect is witnessed once in the ledger
    ship_receipts = [r for r in r3["ledger"] if r["action_kind"] == "ship"]
    assert len(ship_receipts) == 1
    assert r3["execution_counts"][gs._key_str(gs.ledger_key(ship_receipts[0]))] == 1
    # two HITL decisions recorded in state (sign_off + ship), both keyed by the real id
    verbs = [d["verb"] for d in r3["decision_record"]]
    assert verbs.count("APPROVE") == 2
    assert r3["decision_record"][0]["interrupt_id"] == signoff.id
    assert r3["decision_record"][1]["interrupt_id"] == ship.id


# ── Task 4: SP-R1 scrub-before-persist oracle (review-added) ────────────────
async def test_secret_in_goal_absent_from_persisted_checkpoint():
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-scrub"
    secret = "sk-ABCDEF0123456789abcdef0123456789abcd"  # pragma: allowlist secret
    await runner.start(thread_id=tid, goal=f"deploy using {secret} please")
    persisted_goal = runner.get_state(tid).values["goal"]
    assert secret not in persisted_goal  # scrubbed before it entered the checkpoint
    assert runner.get_state(tid).values["scrubbed"] is True
