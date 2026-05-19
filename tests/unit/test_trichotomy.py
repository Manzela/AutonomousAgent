"""Unit tests for the trichotomy classifier + retry policy."""

from lib.durability import trichotomy


class FakeRateLimitError(Exception):
    pass


class FakeTimeoutError(TimeoutError):
    pass


def test_classify_rate_limit_to_F1_self_heal():
    err = FakeRateLimitError("HTTP 429 rate_limit_exceeded")
    code = trichotomy.classify(err)
    assert code == "F1"


def test_classify_timeout_to_F2_self_heal():
    err = FakeTimeoutError("upstream timed out after 60s")
    code = trichotomy.classify(err)
    assert code == "F2"


def test_classify_unknown_exception_to_F33_fail_loud():
    err = RuntimeError("something exotic")
    code = trichotomy.classify(err)
    assert code == "F33"


def test_retry_policy_exponential_backoff_within_tolerance():
    delays = [trichotomy.backoff_delay(attempt=i) for i in range(1, 4)]
    assert 250 <= delays[0] <= 750
    assert 500 <= delays[1] <= 1500
    assert 1000 <= delays[2] <= 3000


def test_retry_policy_caps_at_max_delay():
    delay = trichotomy.backoff_delay(attempt=20)
    assert delay <= 30000


def test_before_tool_call_accepts_hermes_kwargs():
    """``pre_tool_call`` is invoked as ``cb(**kwargs)`` by Hermes' PluginManager
    (see hermes-agent/hermes_cli/plugins.py:1253 invoke_hook). Our hook must
    accept the exact kwarg set Hermes passes at the pre_tool_call dispatch
    site (hermes_cli/plugins.py:1408): tool_name, args, task_id, session_id,
    tool_call_id."""
    assert (
        trichotomy.before_tool_call(
            tool_name="terminal",
            args={"command": "ls"},
            task_id="task-1",
            session_id="sess-1",
            tool_call_id="call-abc",
        )
        is None
    )


def test_after_tool_call_no_op_on_success():
    """Non-Exception results are no-ops — no F-code classification, no span."""
    assert (
        trichotomy.after_tool_call(
            tool_name="terminal",
            args={"command": "ls"},
            result="stdout text",
            task_id="task-1",
            session_id="sess-1",
            tool_call_id="call-abc",
            duration_ms=42,
        )
        is None
    )


def test_after_tool_call_classifies_exception_result():
    """Hermes passes the caught Exception as ``result`` for failed tools.
    The hook classifies it to an F-code (verified separately by classify())
    and emits a durability.classify span when OTel is available."""
    err = TimeoutError("upstream timed out after 60s")
    # Should not raise even if OTel SDK absent (ImportError path)
    assert (
        trichotomy.after_tool_call(
            tool_name="terminal",
            args={"command": "sleep"},
            result=err,
            task_id="task-1",
            session_id="sess-1",
            tool_call_id="call-def",
            duration_ms=60000,
        )
        is None
    )


def test_hooks_absorb_unknown_kwargs():
    """Forward-compat: when Hermes adds new kwargs (e.g. trace_id), our hooks
    must keep returning None without raising."""
    assert (
        trichotomy.before_tool_call(
            tool_name="terminal",
            args={},
            task_id="",
            session_id="",
            tool_call_id="",
            future_kwarg="ignored",
        )
        is None
    )
    assert (
        trichotomy.after_tool_call(
            tool_name="terminal",
            args={},
            result=None,
            task_id="",
            session_id="",
            tool_call_id="",
            duration_ms=0,
            trace_id="future-kwarg",
        )
        is None
    )
