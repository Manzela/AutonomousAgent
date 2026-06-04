"""SP-03 clarify ⇄ Q-gen WIRING into the live spine (audit-2026-06-03 F-3).

The drafter/driver oracles (≤5 Qs, token-referencing questions, confidence
monotonicity, applied_standards, anti-over-clarification, C18 false-premise/override)
are covered at the unit level by tests/unit/test_sp03_clarification_driver.py +
audit/acceptance/SP-03.yaml. THIS suite proves the missing piece the audit flagged:
the clarify node is on the RUNTIME graph path, so R3/R4/U-2/C18 are enforced by the
spine, and sign_off gates on the DRAFTED PRD instead of the raw goal.

Topology under test (PRD §3 journey / SP-04: "interrupt AFTER clarify; nothing builds
before resume"):

    goal_intake -> clarify ⇄[interrupt, ask_next] -> sign_off[interrupt on spec_draft]
                -> seal_spec -> ...

NON-VACUOUS: on origin/main the graph is goal_intake -> sign_off (no clarify node; the
SP-03 modules are registered dead code in config/dead_code_entrypoints.txt), so a
planted-ambiguity goal NEVER surfaces a clarify interrupt and sign_off fires on the raw
goal. These tests fail there and pass once clarify is wired.
"""

from __future__ import annotations

import pytest

from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core.schemas import AgentCapability, ExecutionResult, TaskStatus
from app.core.spine_runner import SpineRunner
from lib.anchors.spec_store import SpecStore


@pytest.fixture(autouse=True)
def _isolate_decision_record(tmp_path, monkeypatch):
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


def _runner():
    return SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())


def _intr(result):
    """The single pending interrupt's (payload, id), or (None, None) at END."""
    intrs = result.get("__interrupt__")
    if not intrs:
        return None, None
    return intrs[0].value, intrs[0].id


# ── R3/R4 false-positive control: a clear goal asks NOTHING and goes to sign-off ──
async def test_clear_goal_skips_clarify_and_signs_off_on_the_draft():
    runner = _runner()
    tid = "t-clear"
    r1 = await runner.start(thread_id=tid, goal="Add a /healthz endpoint returning 200 OK")
    payload, _ = _intr(r1)

    # First interrupt is sign_off (clarify locked immediately — zero questions), and it
    # gates on the DRAFTED PRD, not the raw goal.
    assert payload is not None and payload["gate"] == "sign_off"
    assert runner.get_state(tid).next == ("sign_off",)
    assert "spec_draft" in payload and payload["spec_draft"], "sign_off must carry the drafted PRD"
    assert payload["spec_draft"]["confidence"] == pytest.approx(1.0)
    # no clarify round happened
    assert (runner.get_state(tid).values.get("clarifications") or []) == []


# ── R3/R4: a planted ambiguity surfaces a clarify question, then the loop locks ──
async def test_planted_ambiguity_surfaces_clarify_question_then_locks():
    runner = _runner()
    tid = "t-ambig"
    r1 = await runner.start(
        thread_id=tid, goal="Build the login flow. AMBIG:sessionttl@non_functional"
    )
    payload, iid = _intr(r1)

    # The FIRST interrupt is a clarify round (NOT sign_off) whose question references the
    # planted token — proves R3 (gap found) + R4 (clarifying question) on the live spine.
    assert payload is not None and payload["gate"] == "clarify"
    assert runner.get_state(tid).next == ("clarify",)
    qs = payload["questions"]
    assert len(qs) == 1 and qs[0]["references_token"] == "sessionttl"
    assert qs[0]["category"] == "non_functional"

    # Operator answers the question (keyed by token) → confidence rises → loop locks →
    # the next interrupt is sign_off on the now-locked draft.
    r2 = await runner.resume(thread_id=tid, interrupt_id=iid, decision={"sessionttl": "30 minutes"})
    payload2, _ = _intr(r2)
    assert payload2 is not None and payload2["gate"] == "sign_off"
    assert runner.get_state(tid).next == ("sign_off",)
    assert payload2["spec_draft"]["confidence"] == pytest.approx(1.0)


# ── C18: a planted FALSE premise is surfaced at sign-off, not silently encoded ──
async def test_false_premise_challenge_surfaced_on_the_spine():
    runner = _runner()
    tid = "t-false"
    r1 = await runner.start(
        thread_id=tid, goal="Wire auth using FALSE:os.fastopen for the token cache"
    )
    payload, _ = _intr(r1)

    # FALSE doesn't lower confidence, so clarify locks and routes straight to sign_off —
    # but the kind=clarification challenge rides on the drafted PRD the operator approves.
    assert payload["gate"] == "sign_off"
    challenges = [a for a in payload["spec_draft"]["ambiguities"] if a["kind"] == "clarification"]
    assert any(
        a["references_token"] == "os.fastopen" for a in challenges
    ), "the false-premise challenge must be surfaced on the spine's sign-off gate (C18)"


# ── C18: a planted DEPRECATED choice surfaces exactly one override at sign-off ──
async def test_deprecated_choice_surfaces_one_override_on_the_spine():
    runner = _runner()
    tid = "t-dep"
    r1 = await runner.start(thread_id=tid, goal="Hash passwords with DEPRECATED:md5")
    payload, _ = _intr(r1)
    assert payload["gate"] == "sign_off"
    overrides = [a for a in payload["spec_draft"]["ambiguities"] if a["kind"] == "override"]
    assert len(overrides) == 1
    assert overrides[0]["references_token"] == "md5"
    assert overrides[0]["recommended_alternative"]  # SOTA alternative present


# ── Termination: an operator who never resolves still converges (budget draft_lock) ──
async def test_clarify_loop_terminates_on_budget_without_resolution():
    runner = _runner()
    tid = "t-budget"
    result = await runner.start(thread_id=tid, goal="Ship it. AMBIG:retentiondays")
    payload, iid = _intr(result)

    rounds = 0
    # Keep answering with an IRRELEVANT key (resolves nothing → confidence stays low) and
    # assert the loop still terminates at sign_off via the question-budget draft_lock —
    # i.e. no infinite clarify ⇄ loop.
    while payload is not None and payload["gate"] == "clarify":
        rounds += 1
        assert rounds <= 10, "clarify loop did not converge — possible infinite loop"
        result = await runner.resume(
            thread_id=tid, interrupt_id=iid, decision={"unrelated": "no-op answer"}
        )
        payload, iid = _intr(result)

    assert payload is not None and payload["gate"] == "sign_off"
    # The drafter's circuit-breaker draft_locks at the question budget (6), so it must have
    # taken more than one round but well within the cap.
    assert 1 < rounds <= 8


# ── The CLARIFIED draft is what gets sealed (clarify shapes the locked TaskSpec) ──
async def test_clarified_draft_flows_into_the_sealed_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_DATA_DIR", str(tmp_path / "spine"))
    runner = _runner()
    tid = "t-seal"
    goal = "Add structured logging to the API"
    r1 = await runner.start(thread_id=tid, goal=goal)
    _, iid = _intr(r1)
    # Approve → seal_spec runs and locks the drafted TaskSpec.
    await runner.resume(thread_id=tid, interrupt_id=iid, decision={"verb": "APPROVE"})

    spec_id = runner.get_state(tid).values.get("spec_id")
    assert spec_id, "seal_spec must persist a spec_id after approval"
    sealed = SpecStore(tmp_path / "spine" / "specs").get_by_id(spec_id)
    assert sealed is not None and sealed.status == "locked"
    # The sealed spec carries the DRAFTED intent (the clarify draft), not a hardcoded stub.
    assert sealed.intent == goal


# ── SP-R1: operator answers in the clarify loop are scrubbed before persistence ──
async def test_clarify_answers_are_scrubbed_before_persist():
    runner = _runner()
    tid = "t-scrub"
    r1 = await runner.start(thread_id=tid, goal="Configure the deploy. AMBIG:deploytoken")
    _, iid = _intr(r1)
    secret = "sk-ABCDEF0123456789abcdef0123456789abcd"  # pragma: allowlist secret
    await runner.resume(thread_id=tid, interrupt_id=iid, decision={"deploytoken": f"use {secret}"})
    persisted = runner.get_state(tid).values.get("clarifications") or []
    import json

    blob = json.dumps(persisted)
    assert (
        secret not in blob
    ), "an operator-supplied secret leaked into the persisted clarifications"


# ── F-3 gap fix: custom spec drafter injection ───────────────────────────────
async def test_custom_spec_drafter_injection():
    """Verify that a custom spec drafter is propagated and called during the clarify node execution."""
    from app.core.spec_drafter import AbstractSpecDrafter, DraftResult
    from app.core.spine_runner import SpineRunner
    from app.adapters.inmemory.checkpointer import InMemoryCheckpointer

    mock_draft_result = DraftResult(
        intent="test custom intent",
        confidence=1.0,
        ambiguities=[],
        questions=[],
        applied_standards=[],
        assumptions=[],
    )

    class CustomSpecDrafter(AbstractSpecDrafter):
        is_production_grade = True

        def __init__(self):
            self.draft_called = False

        def draft(self, intent, *, answers=None, round_index=0):
            self.draft_called = True
            return mock_draft_result

    custom_drafter = CustomSpecDrafter()
    runner = SpineRunner(
        InMemoryCheckpointer(),
        capability=_stub_capability(),
        drafter=custom_drafter,
    )

    assert runner._drafter is custom_drafter

    # Start a run — this will trigger goal_intake then clarify node.
    # Since clarify node invokes the drafter, our mock draft method will be called.
    tid = "t-custom-drafter"
    await runner.start(thread_id=tid, goal="hello world")
    assert custom_drafter.draft_called is True
