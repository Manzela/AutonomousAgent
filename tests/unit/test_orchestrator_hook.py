"""Unit tests for the post_tool_call → judge → pre_llm_call inject flow."""

from lib.evaluators.orchestrator_hook import (
    PER_AXIS_MODEL,
    PendingFeedback,
    drain_pending_feedback,
    queue_judge_dispatch,
)


def test_per_axis_model_routing():
    """P1 routing: Gemini 3.5 Flash + Gemini 3.1 Pro Preview."""
    assert PER_AXIS_MODEL["code-correctness"] == "vertex_ai/gemini-3-5-flash"
    assert PER_AXIS_MODEL["safety"] == "vertex_ai/gemini-3-1-pro-preview"
    assert PER_AXIS_MODEL["scope-fit"] == "vertex_ai/gemini-3-5-flash"
    assert PER_AXIS_MODEL["completeness"] == "vertex_ai/gemini-3-1-pro-preview"


def test_drain_returns_empty_for_unknown_session():
    out = drain_pending_feedback("nonexistent-session-xyz-task18")
    assert out == []


def test_queue_then_drain():
    fb = PendingFeedback(verdict="reject", reasoning="bad", axes_failed=["safety"])
    queue_judge_dispatch(session_id="sess-task18-1", feedback=fb)
    drained = drain_pending_feedback("sess-task18-1")
    assert len(drained) == 1
    assert drained[0].verdict == "reject"
    # Drain twice yields empty
    assert drain_pending_feedback("sess-task18-1") == []
