"""SP-R1: scrub_state() is wired into the spine checkpoint path.

Proves that PII patterns (email, phone) injected into the goal survive through
the spine run but are redacted in the FINAL returned state snapshot. This is the
defence-in-depth layer: individual node scrubs exist (goal at input, tool output
at _scrub_persisted), but scrub_state sweeps the ENTIRE state dict.

Oracle map:
  test_goal_pii_redacted_in_start_result  — email in goal → redacted in start() return
  test_goal_phone_redacted              — phone number in goal → redacted
  test_resume_result_scrubbed           — PII in full round-trip start→resume
"""

from __future__ import annotations

import pytest

from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core.schemas import AgentCapability, ExecutionResult, TaskStatus
from app.core.spine_runner import SpineRunner


@pytest.fixture(autouse=True)
def _isolate_decision_record(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_DECISION_RECORD_PATH", str(tmp_path / "dr.jsonl"))


def _cap() -> AgentCapability:
    async def _invoke(req: object) -> ExecutionResult:
        return ExecutionResult(task_id=req.task_id, status=TaskStatus.COMPLETED)

    return AgentCapability(
        agent_id="stub",
        version="1",
        phase="draft",
        description="hermetic stub",
        invoke=_invoke,
    )


async def test_goal_pii_redacted_in_start_result():
    """Email embedded in the goal must be redacted in the returned state."""
    runner = SpineRunner(InMemoryCheckpointer(), capability=_cap())
    result = await runner.start(
        thread_id="t-pii-1",
        goal="deploy service for user john@example.com now",
        durability="sync",
    )
    # The goal should have the email redacted
    assert "john@example.com" not in result.get(
        "goal", ""
    ), "email PII must be redacted by scrub_state"
    assert "[REDACTED" in result.get("goal", ""), "redacted marker must be present"


async def test_goal_phone_redacted():
    """Phone number in goal must be redacted."""
    runner = SpineRunner(InMemoryCheckpointer(), capability=_cap())
    result = await runner.start(
        thread_id="t-pii-2",
        goal="call the user at +1-555-123-4567 when ready",
        durability="sync",
    )
    assert "+1-555-123-4567" not in result.get(
        "goal", ""
    ), "phone PII must be redacted by scrub_state"


async def test_resume_result_scrubbed():
    """PII in a full start→APPROVE→ship round-trip must be scrubbed."""
    runner = SpineRunner(InMemoryCheckpointer(), capability=_cap())
    r1 = await runner.start(
        thread_id="t-pii-3",
        goal="deploy for admin@corp.io",
        durability="sync",
    )
    assert "__interrupt__" in r1
    iid = r1["__interrupt__"][0].id

    r2 = await runner.resume(
        thread_id="t-pii-3",
        interrupt_id=iid,
        decision={"verb": "APPROVE", "actor": "op", "reason": "lgtm"},
        durability="sync",
    )
    # The goal in the resumed state must also be scrubbed
    assert "admin@corp.io" not in r2.get(
        "goal", ""
    ), "email PII must be redacted in resume() return"
