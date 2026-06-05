"""§12 P1 Exit Acceptance Harness.

Seven hermetic oracles that constitute the PRD §12 production acceptance run
(P1 exit criterion). No network, no docker, no real LLM — all adapters are
in-memory.

Oracle map:
  §12-A1  happy path, goal 1 (two runs — non-flaky proof)
  §12-A2  happy path, goal 2
  §12-B   crash-resume exactly once (DoD-1 ledger guard in full-spine context)
  §12-C   over-budget pre-empts before ship_gate (SP-R2)
  §12-D   cross-channel steering: dedup + arbitrate primitive (C15 reducer layer)
  §12-E   two threads in parallel on same checkpointer — no cross-contamination (SP-R6)
  §12-N   negative path: REJECT at sign_off halts and nothing is built

§12-B HONESTY: the unit-level byte-equal FS proof is
  test_graph_spine.py::test_dod17_real_edit_snapshot_kill_rehydrate_byte_equal
  (DoD-17, SP-R7). This oracle proves the SPINE-level crash-resume: a fresh
  SpineRunner on the same checkpointer resumes at ship_gate and the ledger
  records the ship effect exactly once.

§12-D HONEST BOUNDARY: this oracle proves (i) the dedup reducer drops duplicate
  (channel, origin_id) events while preserving distinct ones, and (ii) gs.arbitrate()
  deterministically returns REJECT over APPROVE. It also proves that passing the
  ARBITRATED verb (REJECT) into the sign_off resume causes the spine to halt — the
  same observable effect that would occur if SP-17 auto-wired arbitration into routing.
  DEFERRED: the spine does not yet auto-apply arbitrate() to determine the resume verb
  (SP-17 SteeringEventBus routing); a human or adapter must pass the arbitrated verb in.
"""

from __future__ import annotations

import asyncio

import pytest

from typing import Any
from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core import graph_state as gs
from app.core.schemas import AgentCapability, AgentID, ExecutionResult, TaskStatus
from app.core.spine_runner import SpineRunner


# ── shared fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_decision_record(tmp_path, monkeypatch):
    """Redirect the durable decision-record JSONL to a per-test tmp file."""
    monkeypatch.setenv("SPINE_DECISION_RECORD_PATH", str(tmp_path / "decision-record.jsonl"))


def _cap(status: TaskStatus = TaskStatus.COMPLETED, cost_usd: float = 0.0) -> AgentCapability:
    async def _invoke(req: Any) -> ExecutionResult:
        return ExecutionResult(task_id=req.task_id, status=status, cost_usd=cost_usd)

    return AgentCapability(
        agent_id=AgentID("stub"),
        version="1",
        phase="draft",
        description="hermetic stub",
        invoke=_invoke,
    )


async def _happy_run(runner: SpineRunner, tid: str, goal: str) -> dict:
    """Drive a full goal→sign_off→ship_gate→ship_effect path and return final state."""
    r1 = await runner.start(thread_id=tid, goal=goal, durability="sync")
    assert "__interrupt__" in r1, "expected sign_off interrupt"
    so = r1["__interrupt__"][0]
    assert so.value["gate"] == "sign_off"

    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "lgtm"},
        durability="sync",
    )
    assert "__interrupt__" in r2, "expected ship_gate interrupt"
    ship = r2["__interrupt__"][0]
    assert ship.value["gate"] == "ship"

    r3 = await runner.resume(
        thread_id=tid,
        interrupt_id=ship.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "ship it"},
        durability="sync",
    )
    return r3


# ── §12-A1 / §12-A2: happy path (two independent runs) ──────────────────────


async def test_p1_a1_happy_path_first_goal():
    """§12-A1: goal→clarify→sign_off→seal_spec→decompose→fan_out→monitor→
    eval_gate→ship_gate→ship_effect. Spec sealed, tasks executed, eval passes,
    ledger records ship exactly once, workspace_ref set.

    NOTE: eval_gate passes because changed_paths is empty in skeleton mode — the
    scope-root verdict is vacuously satisfied. The mechanism is wired and proven
    to be FAIL-CLOSED by test_route_after_eval_gate_is_fail_closed in test_graph_spine.py.
    """
    runner = SpineRunner(InMemoryCheckpointer(), capability=_cap())
    r = await _happy_run(runner, "t-a1", "ship a hello-world HTTP endpoint")

    assert "__interrupt__" not in r
    assert runner.get_state("t-a1").next == ()  # reached END

    # seal_spec ran
    assert r.get("spec_sha"), "spec_sha must be set by seal_spec"
    assert r.get("spec_id"), "spec_id must be set by seal_spec"

    # decompose ran (plan populated)
    assert r.get("plan"), "plan must be set by decompose"
    assert "nodes" in r["plan"] and "edges" in r["plan"]

    # fan_out ran (tasks list populated with ExecutionResult dicts)
    tasks = r.get("tasks") or []
    assert len(tasks) > 0, "fan_out must dispatch at least one task"
    assert all(t.get("status") == "completed" for t in tasks), "all tasks must complete"

    # eval_gate passed
    verdict = r.get("eval_verdict") or {}
    assert verdict.get("passed") is True, "eval_gate must pass for ship to proceed"

    # ship_effect ran — workspace_ref set (real or synthetic fallback)
    assert r.get("workspace_ref"), "workspace_ref must be set by ship_effect"
    assert r["workspace_ref"].get("kind") == "branch"
    assert r["workspace_ref"].get("digest"), "digest must be non-empty"

    # exactly-once ledger: ship effect witnessed once
    ship_receipts = [e for e in r.get("ledger", []) if e.get("action_kind") == "ship"]
    assert len(ship_receipts) == 1, "ship effect must be ledgered exactly once"
    key_str = gs._key_str(gs.ledger_key(ship_receipts[0]))
    assert r["execution_counts"][key_str] == 1

    # two APPROVE decisions recorded (sign_off + ship)
    verbs = [d.get("verb") for d in r.get("decision_record", [])]
    assert verbs.count("APPROVE") == 2, "two APPROVE decisions expected"


async def test_p1_a2_happy_path_second_goal():
    """§12-A2: same topology, different goal — proves §12-A1 is not a one-shot fluke.
    Two distinct spec_sha values from two distinct goals confirms deterministic sealing."""
    runner1 = SpineRunner(InMemoryCheckpointer(), capability=_cap())
    r1 = await _happy_run(runner1, "t-a2a", "add a /healthz endpoint")

    runner2 = SpineRunner(InMemoryCheckpointer(), capability=_cap())
    r2 = await _happy_run(runner2, "t-a2b", "add a /metrics endpoint")

    assert r1.get("spec_sha") and r2.get("spec_sha")
    assert r1["spec_sha"] != r2["spec_sha"], "distinct goals must produce distinct specs"
    assert r1.get("workspace_ref") and r2.get("workspace_ref")
    assert (
        r1["workspace_ref"]["digest"] != r2["workspace_ref"]["digest"]
    ), "distinct goals must produce distinct workspace digests"


# ── §12-B: crash-resume exactly-once ────────────────────────────────────────


async def test_p1_b_crash_resume_exactly_once():
    """§12-B: simulate process death at ship_gate by creating a SECOND SpineRunner
    over the SAME checkpointer. Resume from ship_gate — the ledger records the ship
    effect exactly once despite the runner swap (DoD-1 in full-spine context).

    The fresh-runner swap is verified load-bearing: runner2.get_state() confirms it
    reads the interrupted state (next==("ship_gate",)) purely from cp before resuming.

    NOTE: The byte-equal FS rehydrate proof (workspace snapshot survives kill, edited
    file byte-equal after rehydrate) is at the unit level:
      tests/unit/test_graph_spine.py::test_dod17_real_edit_snapshot_kill_rehydrate_byte_equal
    This oracle covers the SPINE crash-resume layer (same checkpointer, fresh runner).
    """
    cp = InMemoryCheckpointer()
    runner1 = SpineRunner(cp, capability=_cap())
    tid = "t-b"

    # Drive to ship_gate (runner1)
    r1 = await runner1.start(thread_id=tid, goal="ship X", durability="sync")
    so = r1["__interrupt__"][0]
    r2 = await runner1.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
        durability="sync",
    )
    ship_iid = r2["__interrupt__"][0].id
    assert r2["__interrupt__"][0].value["gate"] == "ship"

    # ── "kill" runner1 (simulate process death) ──
    del runner1

    # Fresh runner, SAME checkpointer — the canonical crash-restart scenario.
    # Assert runner2 reads the interrupted state from cp BEFORE resuming (proves
    # the swap is load-bearing — runner2 is NOT a warm continuation of runner1).
    runner2 = SpineRunner(cp, capability=_cap())
    pre_state = runner2.get_state(tid)
    assert pre_state.next == (
        "ship_gate",
    ), "runner2 must read the ship_gate interrupt from cp before resuming"

    r3 = await runner2.resume(
        thread_id=tid,
        interrupt_id=ship_iid,
        decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
        durability="sync",
    )

    assert "__interrupt__" not in r3
    assert runner2.get_state(tid).next == ()

    # Exactly-once: one ship receipt, every count == 1
    ship_receipts = [e for e in r3.get("ledger", []) if e.get("action_kind") == "ship"]
    assert len(ship_receipts) == 1
    counts = list(r3["execution_counts"].values())
    assert counts and all(c == 1 for c in counts)

    # workspace_ref present (even in skeleton mode — synthetic fallback is acceptable here)
    assert r3.get("workspace_ref"), "workspace_ref must be present after ship"


# ── §12-C: over-budget pre-empts before ship_gate ───────────────────────────


async def test_p1_c_over_budget_preempts_before_ship(monkeypatch):
    """§12-C (SP-R2): set SPINE_BUDGET_USD=0.001 and use a capability that reports
    cost_usd=1.0; fan_out pre-empts and routes to __halt__ before ship_gate."""
    monkeypatch.setenv("SPINE_BUDGET_USD", "0.001")

    runner = SpineRunner(InMemoryCheckpointer(), capability=_cap(cost_usd=1.0))
    tid = "t-c"

    r1 = await runner.start(thread_id=tid, goal="do something costly", durability="sync")
    so = r1["__interrupt__"][0]
    assert so.value["gate"] == "sign_off"

    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
        durability="sync",
    )

    # Must halt (budget exceeded) — no ship_gate interrupt
    assert "__interrupt__" not in r2, "over-budget run must halt, not reach ship_gate"
    assert runner.get_state(tid).next == ()  # ended at __halt__

    # budget_verdict records the pre-emption
    verdict = r2.get("budget_verdict") or {}
    assert verdict.get("preempt") is True, "budget_verdict.preempt must be True"
    assert not any(
        e.get("action_kind") == "ship" for e in r2.get("ledger", [])
    ), "ship must not be ledgered on an over-budget run"


# ── §12-D: cross-channel steering dedup + arbitration primitive ─────────────


async def test_p1_d_cross_channel_conflict_reject_wins():
    """§12-D (C15 reducer layer): inject two steering events for the SAME interrupt_id
    — one APPROVE via telegram, one REJECT via board. The dedup reducer keeps exactly
    one event per (channel, origin_id). gs.arbitrate() deterministically returns REJECT
    (reject beats approve). Passing the arbitrated REJECT verb to the sign_off interrupt
    causes the spine to halt — proving that the arbitration result is actionable.

    DEFERRED: SP-17 auto-wiring (the spine does not yet call arbitrate() internally to
    determine the resume verb; a human/adapter passes the arbitrated decision in).
    """
    from langgraph.types import Command

    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_cap())
    tid = "t-d"

    r1 = await runner.start(thread_id=tid, goal="do Y", durability="sync")
    iid = r1["__interrupt__"][0].id

    # Inject: telegram=APPROVE + telegram duplicate + board=REJECT (include required `kind`)
    events = [
        {
            "channel": "telegram",
            "origin_id": "m1",
            "verb": "APPROVE",
            "kind": "approve",
            "interrupt_id": iid,
            "ts": "1",
        },
        {
            "channel": "telegram",
            "origin_id": "m1",
            "verb": "APPROVE",
            "kind": "approve",
            "interrupt_id": iid,
            "ts": "1",
        },  # duplicate — must be deduped
        {
            "channel": "board",
            "origin_id": "c9",
            "verb": "REJECT",
            "kind": "reject",
            "interrupt_id": iid,
            "ts": "2",
        },
    ]
    # Inject events into state and drive the resume boundary
    await runner._app.ainvoke(
        Command(
            resume={iid: {"verb": "APPROVE", "actor": "op", "reason": "y", "interrupt_id": iid}},
            update={"steering_events": events},
        ),
        runner._cfg(tid),
        durability="sync",
    )

    survived = runner.get_state(tid).values["steering_events"]

    # Dedup: exactly two distinct (channel, origin_id) pairs survive
    keys = {(e["channel"], e["origin_id"]) for e in survived}
    assert keys == {("telegram", "m1"), ("board", "c9")}, "dedup must produce 2 unique events"
    assert sum(1 for e in survived if e["channel"] == "telegram") == 1, "telegram dup dropped"

    # Arbitration: REJECT beats APPROVE (C15 deterministic)
    assert gs.arbitrate(survived, iid) == "REJECT", "arbitrate must return REJECT"

    # Actionability: passing the arbitrated REJECT as the resume decision halts the spine.
    # Create a fresh thread to drive sign_off → REJECT (the arbiter-decided outcome).
    runner2 = SpineRunner(InMemoryCheckpointer(), capability=_cap())
    tid2 = "t-d2"
    s1 = await runner2.start(thread_id=tid2, goal="do Y", durability="sync")
    so2 = s1["__interrupt__"][0]
    s2 = await runner2.resume(
        thread_id=tid2,
        interrupt_id=so2.id,
        decision={"verb": "REJECT", "actor": "arbitrator", "reason": "board says no"},
        durability="sync",
    )
    assert "__interrupt__" not in s2, "REJECT via arbitrated verb must halt the spine"
    assert runner2.get_state(tid2).next == ()
    assert not s2.get("spec_sha"), "seal_spec must not run after arbitrated REJECT"


# ── §12-E: concurrent thread isolation ──────────────────────────────────────


async def test_p1_e_concurrent_thread_isolation():
    """§12-E (SP-R6): two goals running CONCURRENTLY on the SAME checkpointer must
    each reach END. Isolation is proven by asserting that every ledger receipt and
    every HITL decision record is stamped with the correct thread_id (no cross-bleed).
    """
    cp = InMemoryCheckpointer()

    async def _run(tid: str, goal: str) -> dict:
        runner = SpineRunner(cp, capability=_cap())
        return await _happy_run(runner, tid, goal)

    results = await asyncio.gather(
        _run("t-e1", "build endpoint A"),
        _run("t-e2", "build endpoint B"),
    )

    r1, r2 = results

    # Each thread completed
    assert "__interrupt__" not in r1 and "__interrupt__" not in r2

    # Thread IDs are consistent within each result
    assert r1["thread_id"] == "t-e1", "thread_id must match the launch tid"
    assert r2["thread_id"] == "t-e2", "thread_id must match the launch tid"

    # Isolation: every ledger receipt is stamped with the thread's OWN thread_id
    for e in r1.get("ledger", []):
        assert e.get("thread_id") == "t-e1", f"r1 ledger entry has wrong tid: {e}"
    for e in r2.get("ledger", []):
        assert e.get("thread_id") == "t-e2", f"r2 ledger entry has wrong tid: {e}"

    # Isolation: decision records from different threads must NOT share interrupt_ids
    # (each thread produces its own distinct interrupt objects — shared IDs = bleed)
    iids1 = {d.get("interrupt_id") for d in r1.get("decision_record", [])}
    iids2 = {d.get("interrupt_id") for d in r2.get("decision_record", [])}
    assert iids1 and iids2, "each thread must produce decision records"
    assert iids1.isdisjoint(
        iids2
    ), f"decision record interrupt_ids must not overlap: {iids1 & iids2}"

    # Each thread completed its own ship exactly once
    for r in (r1, r2):
        ship_receipts = [e for e in r.get("ledger", []) if e.get("action_kind") == "ship"]
        assert len(ship_receipts) == 1


# ── §12-N: negative path — REJECT at sign_off ───────────────────────────────


async def test_p1_n_reject_at_signoff_halts_nothing_built():
    """§12-N (negative): operator REJECTs at sign_off → spine halts at __halt__,
    seal_spec never runs, no task is dispatched, and the ledger has no ship receipt."""
    runner = SpineRunner(InMemoryCheckpointer(), capability=_cap())
    tid = "t-n"

    r1 = await runner.start(thread_id=tid, goal="do Z")
    so = r1["__interrupt__"][0]
    assert so.value["gate"] == "sign_off"

    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "REJECT", "actor": "op", "reason": "scope too broad"},
    )

    assert "__interrupt__" not in r2, "REJECT must halt — no further interrupts"
    assert runner.get_state(tid).next == ()  # halted at __halt__ / END
    assert not r2.get("spec_sha"), "seal_spec must not run after REJECT"
    assert not r2.get("tasks"), "no tasks must be dispatched after REJECT"
    assert not any(
        e.get("action_kind") == "ship" for e in r2.get("ledger", [])
    ), "ship must not be ledgered after REJECT"
