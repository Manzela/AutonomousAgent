"""Verify the evaluators plugin registers the expected lifecycle hooks."""

from unittest.mock import MagicMock

from lib.evaluators import register
from lib.evaluators.orchestrator_hook import PendingFeedback, queue_judge_dispatch


def _hook_names_registered(ctx: MagicMock) -> list[str]:
    return [c.args[0] for c in ctx.register_hook.call_args_list]


def test_post_tool_call_hook_registered():
    ctx = MagicMock()
    register(ctx)
    assert "post_tool_call" in _hook_names_registered(ctx)


def test_pre_llm_call_hook_registered():
    ctx = MagicMock()
    register(ctx)
    assert "pre_llm_call" in _hook_names_registered(ctx)


def test_on_session_end_hook_registered():
    ctx = MagicMock()
    register(ctx)
    assert "on_session_end" in _hook_names_registered(ctx)


def test_register_calls_register_hook_exactly_three_times():
    ctx = MagicMock()
    register(ctx)
    assert ctx.register_hook.call_count == 3


def test_pre_llm_call_drains_and_injects_feedback():
    """When feedback is queued, pre_llm_call must inject a system message."""
    from lib.evaluators import _on_pre_llm_call

    sid = "test-session-pre-llm-inject"
    queue_judge_dispatch(
        session_id=sid,
        feedback=PendingFeedback(verdict="reject", reasoning="bad", axes_failed=["safety"]),
    )
    messages = [{"role": "user", "content": "next turn"}]
    _on_pre_llm_call(session_id=sid, messages=messages)
    assert messages[0]["role"] == "system"
    assert "REJECTED" in messages[0]["content"] or "rejected" in messages[0]["content"].lower()


def test_pre_llm_call_no_op_without_feedback():
    """No feedback → messages list left untouched."""
    from lib.evaluators import _on_pre_llm_call

    sid = "test-session-no-feedback-xyz"
    messages = [{"role": "user", "content": "hello"}]
    original = list(messages)
    _on_pre_llm_call(session_id=sid, messages=messages)
    assert messages == original
