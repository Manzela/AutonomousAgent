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


# ── Task 5: DoD-1 exactly-once ──────────────────────────────────────────────
def test_apply_once_guard_is_load_bearing_on_reentry():
    """The ledger guard is NON-VACUOUS: on re-entry with the receipt present it
    SKIPs the external effect (count stays 1). Negative control proves the guard —
    not langgraph — is what dedups: with no receipt in state the effect repeats."""
    from app.core.graph import apply_once

    calls = []
    state = {"ledger": []}
    d1 = apply_once(
        state,
        tid="T",
        ptid="p",
        kind="ship",
        node_label="ship_effect",
        effect=lambda: calls.append(1),
    )
    assert calls == [1] and "ledger" in d1  # first entry: effect ran, receipt written

    state2 = {
        "ledger": gs._ledger_union(state["ledger"], d1["ledger"])
    }  # accumulate as reducer would
    d2 = apply_once(
        state2,
        tid="T",
        ptid="p",
        kind="ship",
        node_label="ship_effect",
        effect=lambda: calls.append(1),
    )
    assert calls == [1]  # re-entry: SKIP'd, external effect NOT repeated
    assert "ledger" not in d2 and "SKIP" in d2["audit"][0]

    # NEGATIVE CONTROL: with no receipt in state the guard cannot fire -> effect repeats.
    apply_once(
        {"ledger": []},
        tid="T",
        ptid="p",
        kind="ship",
        node_label="ship_effect",
        effect=lambda: calls.append(1),
    )
    assert calls == [1, 1]  # the guard (not langgraph) is what makes it exactly-once


async def test_external_effect_before_interrupt_double_acts_RED():
    """Why the node-split exists: a REAL external effect placed BEFORE interrupt()
    in a node body re-runs on resume -> at-least-once (==2). (A state-channel write
    would be rolled back to 1 — verified — which is exactly why the proof must use
    an external observable.)"""
    seen = {"n": 0}

    class S(TypedDict):
        decision: str

    async def bad_gate(state, config):
        seen["n"] += 1  # EXTERNAL effect before interrupt -> at-least-once
        d = interrupt({"q": "?"})
        return {"decision": d}

    g = StateGraph(S)
    g.add_node("bad_gate", bad_gate)
    g.add_edge(START, "bad_gate")
    g.add_edge("bad_gate", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "red"}}
    r1 = await app.ainvoke({"decision": ""}, cfg, durability="sync")
    intr = r1["__interrupt__"][0]
    await app.ainvoke(Command(resume={intr.id: "APPROVE"}), cfg, durability="sync")
    assert seen["n"] == 2  # RED: the external effect double-acted (anti-pattern)


async def test_ship_effect_exactly_once_under_crash_resume():
    """DoD-1: drive to ship_gate, simulate process death (a fresh runner over the
    SAME checkpointer provider), resume APPROVE; the ship effect is witnessed exactly
    once. (Run-once here is enforced by langgraph re-running only the interrupted
    node; the apply_once guard's forward-compat idempotency is proven separately by
    test_apply_once_guard_is_load_bearing_on_reentry.)"""
    cp = InMemoryCheckpointer()
    tid = "goal-eo"
    runner = SpineRunner(cp, capability=_stub_capability())
    r1 = await runner.start(thread_id=tid, goal="ship X", durability="sync")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
        durability="sync",
    )
    ship = r2["__interrupt__"][0]

    runner2 = SpineRunner(cp, capability=_stub_capability())  # crash + restart, same saver
    r3 = await runner2.resume(
        thread_id=tid,
        interrupt_id=ship.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
        durability="sync",
    )
    ship_receipts = [r for r in r3["ledger"] if r["action_kind"] == "ship"]
    assert len(ship_receipts) == 1
    counts = list(r3["execution_counts"].values())
    assert counts and all(c == 1 for c in counts)  # every effect witnessed exactly once


# ── Task 6: REJECT + REPLAN ─────────────────────────────────────────────────
async def test_reject_at_sign_off_halts_nothing_built():
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-reject"
    r1 = await runner.start(thread_id=tid, goal="do X")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "REJECT", "actor": "op", "reason": "no"},
    )
    assert "__interrupt__" not in r2  # no ship gate reached
    assert runner.get_state(tid).next == ()  # halted at END
    assert not r2.get("spec_sha")  # seal_spec never ran (nothing built before approval)
    assert not [r for r in r2.get("ledger", []) if r["action_kind"] == "ship"]
    assert any("HALTED" in a for a in r2["audit"])
    assert r2["decision_record"][-1]["verb"] == "REJECT"  # audited


async def test_replan_at_sign_off_marks_fork_old_thread_immutable():
    """REPLAN marks the fork (replan_parent set) and leaves the old thread + its
    (unsealed) spec immutable. The actual continue-as-new child-thread spawn is the
    deferred REPLAN native-time-travel layer."""
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    old = "goal-replan"
    r1 = await runner.start(thread_id=old, goal="do X")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=old,
        interrupt_id=so.id,
        decision={"verb": "REPLAN", "actor": "op", "reason": "redo"},
    )
    assert r2["replan_parent"] == old
    assert runner.get_state(old).next == ()  # old thread terminal
    assert not r2.get("spec_sha")  # nothing sealed on REPLAN
    # a fresh thread_id starts independently on the same saver (continue-as-new)
    r3 = await runner.start(thread_id="goal-replan-2", goal="do X (replanned)")
    assert "__interrupt__" in r3
    # old thread state is preserved/immutable
    assert runner.get_state(old).values["thread_id"] == old
    assert runner.get_state(old).values["replan_parent"] == old


# ── Task 7: approval-receipt invariant + TIMEOUT->safe-default REJECT ────────
async def test_crash_after_approve_does_not_reprompt_sign_off():
    """Approval-receipt invariant: once sign_off is APPROVE'd the graph advances past
    it (checkpoint persists the decision), so a resume after a crash surfaces the
    NEXT gate (ship), never sign_off again, and the sign_off decision is durable once."""
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    tid = "goal-receipt"
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
        durability="sync",
    )
    assert runner.get_state(tid).next == ("ship_gate",)  # advanced, NOT back at sign_off
    sign_off_iid = r2["sign_off"]["interrupt_id"]
    assert sum(1 for d in r2["decision_record"] if d["interrupt_id"] == sign_off_iid) == 1


async def test_timeout_maps_to_safe_default_reject():
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-timeout"
    r1 = await runner.start(thread_id=tid, goal="g")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "TIMEOUT", "actor": "system", "reason": "no response"},
    )
    assert runner.get_state(tid).next == ()  # halted (TIMEOUT -> safe-default reject)
    assert not r2.get("spec_sha")


# ── Task 8: DoD-4 steering primitive + DoD-17 workspace rehydrate ───────────
async def test_dod4_steering_dedup_survives_resume_and_arbitrate_picks_reject():
    """DoD-4 (scoped to the proven primitive): pre-normalized SteeringEvents dedup on
    (channel, origin_id), SURVIVE the resume boundary at-most-once, and arbitrate()
    deterministically picks REJECT over APPROVE for one interrupt_id. Wiring arbitrate()
    as the graph's routing signal (so REJECT steering overrides an APPROVE resume) is
    the deferred SP-17 bus; this proves the reducer + arbitration only."""
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    tid = "goal-steer"
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    iid = r1["__interrupt__"][0].id
    events = [
        {
            "channel": "telegram",
            "origin_id": "m1",
            "verb": "APPROVE",
            "interrupt_id": iid,
            "ts": "1",
        },
        {
            "channel": "telegram",
            "origin_id": "m1",
            "verb": "APPROVE",
            "interrupt_id": iid,
            "ts": "1",
        },
        {"channel": "board", "origin_id": "c9", "verb": "REJECT", "interrupt_id": iid, "ts": "2"},
    ]
    await runner._app.ainvoke(
        Command(
            resume={iid: {"verb": "APPROVE", "actor": "op", "reason": "y", "interrupt_id": iid}},
            update={"steering_events": events},
        ),
        runner._cfg(tid),
        durability="sync",
    )
    survived = runner.get_state(tid).values["steering_events"]
    keys = {(e["channel"], e["origin_id"]) for e in survived}
    assert keys == {("telegram", "m1"), ("board", "c9")}  # dedup survived resume
    assert sum(1 for e in survived if (e["channel"], e["origin_id"]) == ("telegram", "m1")) == 1
    assert gs.arbitrate(survived, iid) == "REJECT"  # reject beats approve (deterministic C15)


async def test_dod17_workspace_ref_byte_equal_across_crash_resume():
    """DoD-17: the workspace_ref digest (the FS resume piece) is byte-equal after a
    crash+resume — the second, independent resume-state proof distinct from DoD-1's
    conversation counter."""
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    tid = "goal-ws"
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    so = r1["__interrupt__"][0]
    digest = "deadbeef" * 8
    r2 = await runner._app.ainvoke(
        Command(
            resume={
                so.id: {"verb": "APPROVE", "actor": "op", "reason": "y", "interrupt_id": so.id}
            },
            update={"workspace_ref": {"kind": "branch", "ref": f"agent/{tid}", "digest": digest}},
        ),
        runner._cfg(tid),
        durability="sync",
    )
    pre = runner.get_state(tid).values["workspace_ref"]["digest"]
    sh = r2["__interrupt__"][0]
    runner2 = SpineRunner(cp, capability=_stub_capability())  # crash + restart, same saver
    r3 = await runner2.resume(
        thread_id=tid,
        interrupt_id=sh.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
        durability="sync",
    )
    post = r3["workspace_ref"]["digest"]
    assert pre == digest and post == digest  # byte-equal rehydrate across the crash


# ── C9 review fix: deterministic spec_id (idempotent re-seal) ────────────────
def test_spec_id_is_deterministic_for_idempotent_reseal():
    """C9 finding 3: seal_spec derives spec_id from (thread_id, __pregel_task_id) so a
    crash-retry of the node (same stable ptid) overwrites the SAME SpecStore file rather
    than minting a duplicate spec on each retry."""
    from app.core.graph import _spec_id_for

    a = _spec_id_for("T", "ptid-1")
    assert _spec_id_for("T", "ptid-1") == a  # deterministic: same (tid,ptid) -> same id
    assert _spec_id_for("T", "ptid-2") != a  # distinct ptid -> distinct id
    assert _spec_id_for("U", "ptid-1") != a  # distinct thread -> distinct id
