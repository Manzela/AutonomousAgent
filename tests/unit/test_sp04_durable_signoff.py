"""SP-04 oracles — Durable HITL sign-off + INPUT_REQUIRED parking.

PRD §6 EPIC-1 SP-04 ("Durable HITL sign-off + fix INPUT_REQUIRED"):
  - A LangGraph interrupt() gate after clarify; nothing builds before resume.
  - Acceptance: graph paused at sign-off survives restart, resumes only on
    /approve; the A2A peer-asking-for-input status parks (red-green); the
    pre-existing test_peer_dispatch.py assertion is updated; and the
    no-terminal-collapse grep guard returns empty.

This module proves the DURABILITY arm (the sign_off interrupt survives a fresh
SpineRunner over the same checkpointer provider == process death, and resumes
ONLY on APPROVE). The parking arm lives in app/tests/test_peer_dispatch.py
(test_a2a_status_mapping + test_input_required_is_non_terminal_and_parkable) and
is re-asserted here at the orchestrator boundary so SP-04's two halves are
co-located for the reviewer.
"""

from __future__ import annotations

import pytest

from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core.orchestrator import _map_a2a_status
from app.core.schemas import AgentCapability, ExecutionResult, TaskStatus
from app.core.spine_runner import SpineRunner


@pytest.fixture(autouse=True)
def _isolate_decision_record(tmp_path, monkeypatch):
    """Hermetic: redirect the spine's durable decision-record append off the
    real trajectories/ dir (mirrors test_graph_spine.py)."""
    monkeypatch.setenv("SPINE_DECISION_RECORD_PATH", str(tmp_path / "decision-record.jsonl"))


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


# ── INPUT_REQUIRED parking (the orchestrator boundary) ──────────────────────
def test_input_required_parks_not_fails():
    """SP-04 red-green: A2A INPUT_REQUIRED is the parkable, non-terminal status
    (routable to the human gate), NEVER terminal FAILED."""
    mapped = _map_a2a_status("INPUT_REQUIRED")
    assert mapped == TaskStatus.INPUT_REQUIRED
    assert mapped != TaskStatus.FAILED
    assert mapped.is_terminal is False  # parkable, the run is not dead


# ── Durable sign-off (the spine boundary) ───────────────────────────────────
async def test_signoff_interrupt_pauses_and_nothing_builds_before_resume():
    """The interrupt() gate after clarify pauses the graph at sign_off; NOTHING
    downstream (seal_spec / execute) runs before the operator resumes."""
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "sp04-pause"
    r1 = await runner.start(thread_id=tid, goal="ship a feature")
    assert "__interrupt__" in r1
    so = r1["__interrupt__"][0]
    assert so.value["gate"] == "sign_off"
    # paused AT sign_off, with nothing built (no spec sealed, no task executed)
    assert runner.get_state(tid).next == ("sign_off",)
    assert not r1.get("spec_sha")
    assert not r1.get("tasks")


async def test_signoff_paused_state_survives_fresh_runner_restart():
    """DURABILITY (PRD acceptance): a graph paused at sign_off SURVIVES process
    death — a fresh SpineRunner over the SAME checkpointer provider rehydrates the
    paused checkpoint, still parked at sign_off, with the same interrupt id."""
    cp = InMemoryCheckpointer()
    tid = "sp04-survive"
    runner = SpineRunner(cp, capability=_stub_capability())
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    so_id = r1["__interrupt__"][0].id

    # process death + restart: a brand-new runner, same shared saver provider
    runner2 = SpineRunner(cp, capability=_stub_capability())
    st = runner2.get_state(tid)
    assert st.next == ("sign_off",)  # still paused at sign_off after the restart
    # the open interrupt is rehydrated with the SAME id (resumable, not lost)
    assert st.tasks[0].interrupts[0].id == so_id
    assert not st.values.get("spec_sha")  # nothing was built across the crash


async def test_signoff_resumes_only_on_approve_via_fresh_runner():
    """The paused sign_off resumes ONLY on APPROVE — and the resume is driven by a
    FRESH runner (post-restart), proving the durable checkpoint is what carries the
    pause across process death. APPROVE advances to the next gate (ship)."""
    cp = InMemoryCheckpointer()
    tid = "sp04-approve"
    runner = SpineRunner(cp, capability=_stub_capability())
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    so = r1["__interrupt__"][0]

    runner2 = SpineRunner(cp, capability=_stub_capability())  # crash + restart
    r2 = await runner2.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "lgtm"},
        durability="sync",
    )
    # APPROVE advanced past sign_off to the NEXT gate; the spec is now sealed
    assert runner2.get_state(tid).next == ("ship_gate",)
    assert r2.get("spec_sha")
    assert r2["decision_record"][0]["verb"] == "APPROVE"
    assert r2["decision_record"][0]["interrupt_id"] == so.id


async def test_signoff_reject_halts_nothing_built():
    """REJECT at the durable sign_off halts the run with nothing built — the
    'only on /approve' contract's negative arm (resume on REJECT does not build)."""
    cp = InMemoryCheckpointer()
    tid = "sp04-reject"
    runner = SpineRunner(cp, capability=_stub_capability())
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    so = r1["__interrupt__"][0]

    runner2 = SpineRunner(cp, capability=_stub_capability())  # crash + restart
    r2 = await runner2.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "REJECT", "actor": "op", "reason": "no"},
        durability="sync",
    )
    assert "__interrupt__" not in r2  # did NOT advance to ship gate
    assert runner2.get_state(tid).next == ()  # halted at END
    assert not r2.get("spec_sha")  # nothing built before/without approval
    assert r2["decision_record"][-1]["verb"] == "REJECT"
