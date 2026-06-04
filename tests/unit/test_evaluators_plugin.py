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
    """When feedback is queued, pre_llm_call must inject a system message.

    Uses the canonical Hermes kwarg ``conversation_history`` (SP-O2 fix).
    """
    from lib.evaluators import _on_pre_llm_call

    sid = "test-session-pre-llm-inject"
    queue_judge_dispatch(
        session_id=sid,
        feedback=PendingFeedback(verdict="reject", reasoning="bad", axes_failed=["safety"]),
    )
    conversation_history = [{"role": "user", "content": "next turn"}]
    _on_pre_llm_call(session_id=sid, conversation_history=conversation_history)
    assert conversation_history[0]["role"] == "system"
    assert (
        "REJECTED" in conversation_history[0]["content"]
        or "rejected" in conversation_history[0]["content"].lower()
    )


def test_pre_llm_call_no_op_without_feedback():
    """No feedback → conversation_history left untouched."""
    from lib.evaluators import _on_pre_llm_call

    sid = "test-session-no-feedback-xyz"
    conversation_history = [{"role": "user", "content": "hello"}]
    original = list(conversation_history)
    _on_pre_llm_call(session_id=sid, conversation_history=conversation_history)
    assert conversation_history == original


# ---------------------------------------------------------------------------
# SP-O2 regression tests: rejected feedback reaches the LLM
# ---------------------------------------------------------------------------


def test_pre_llm_call_injects_via_conversation_history_kwarg():
    """RED->GREEN: Hermes fires pre_llm_call with conversation_history=...
    (not messages=...).  The feedback MUST appear in the outgoing list.

    Before SP-O2 the function signature was::

        def _on_pre_llm_call(session_id="", messages=None, **_)

    so the Hermes kwarg landed in **_ and was silently discarded -- rejected
    feedback never reached the LLM.  This test fails on the old code and
    passes after the fix.
    """
    from lib.evaluators import _on_pre_llm_call

    sid = "sp-o2-regression-conversation-history"
    queue_judge_dispatch(
        session_id=sid,
        feedback=PendingFeedback(
            verdict="reject",
            reasoning="dangerous approach",
            axes_failed=["safety"],
        ),
    )

    # Simulate exactly the kwarg name Hermes passes (conversation_loop.py:509).
    outgoing = [{"role": "user", "content": "please do X"}]
    _on_pre_llm_call(session_id=sid, conversation_history=outgoing)

    # The feedback system message must have been prepended.
    assert len(outgoing) == 2, (
        "Expected a system message to be injected before the user turn, "
        f"but list has {len(outgoing)} items: {outgoing}"
    )
    injected = outgoing[0]
    assert injected["role"] == "system"
    assert (
        "dangerous approach" in injected["content"]
    ), f"Expected rejection reasoning in injected content, got: {injected['content']!r}"


def test_pre_llm_call_legacy_messages_kwarg_still_works():
    """Backwards-compat: callers using the old ``messages=`` kwarg must still
    get feedback injected (e.g. existing unit tests, third-party plugins).
    """
    from lib.evaluators import _on_pre_llm_call

    sid = "sp-o2-legacy-messages-kwarg"
    queue_judge_dispatch(
        session_id=sid,
        feedback=PendingFeedback(
            verdict="reject",
            reasoning="legacy path check",
            axes_failed=["alignment"],
        ),
    )

    messages = [{"role": "user", "content": "do Y"}]
    _on_pre_llm_call(session_id=sid, messages=messages)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "legacy path check" in messages[0]["content"]


def test_pre_llm_call_conversation_history_wins_over_messages():
    """When both kwargs are present, conversation_history takes precedence
    (it is the canonical Hermes parameter).
    """
    from lib.evaluators import _on_pre_llm_call

    sid = "sp-o2-both-kwargs"
    queue_judge_dispatch(
        session_id=sid,
        feedback=PendingFeedback(
            verdict="reject",
            reasoning="priority check",
            axes_failed=["safety"],
        ),
    )

    conv = [{"role": "user", "content": "turn A"}]
    msgs = [{"role": "user", "content": "turn B"}]
    _on_pre_llm_call(session_id=sid, conversation_history=conv, messages=msgs)

    # Only conv should have been mutated.
    assert len(conv) == 2, "conversation_history should have been mutated"
    assert len(msgs) == 1, "messages (legacy) should be untouched when conversation_history present"
