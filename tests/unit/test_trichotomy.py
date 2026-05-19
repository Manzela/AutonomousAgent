"""Unit tests for the trichotomy classifier + retry policy."""

from unittest import mock

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


# ----------------------------------------------------------------------
# MCP error classification (audit P0-7)
# ----------------------------------------------------------------------


def test_classify_github_mcp_unauthorized_to_F14():
    err = RuntimeError("github-mcp call returned 401 unauthorized")
    assert trichotomy.classify(err) == "F14"


def test_classify_mcp_session_terminated_to_F14():
    err = RuntimeError("MCP session terminated by remote")
    assert trichotomy.classify(err) == "F14"


def test_classify_mcp_session_expired_to_F14():
    err = RuntimeError("MCP error: Invalid or expired session")
    assert trichotomy.classify(err) == "F14"


def test_classify_mcp_transport_closed_to_F14():
    err = RuntimeError("MCP transport is closed")
    assert trichotomy.classify(err) == "F14"


def test_classify_mcp_connection_closed_to_F14():
    err = ConnectionError("mcp client connection closed")
    assert trichotomy.classify(err) == "F14"


def test_classify_closedresourceerror_to_F14():
    """anyio.ClosedResourceError surfaces from the MCP transport layer."""
    err = type("ClosedResourceError", (RuntimeError,), {})("resource closed")
    assert trichotomy.classify(err) == "F14"


# ----------------------------------------------------------------------
# after_tool_call dispatch wiring (audit P0-7)
# ----------------------------------------------------------------------


def test_after_tool_call_dispatches_for_fail_soft():
    """FAIL_SOFT errors (e.g. MCP unavailable) must invoke the handler so the
    side-effects (JSONL fallback, skip-tool-class state) actually fire."""
    err = RuntimeError("github-mcp session terminated")  # F14, FAIL_SOFT
    with mock.patch("lib.durability.handlers.dispatch") as mock_dispatch:
        trichotomy.after_tool_call(
            tool_name="github_search",
            result=err,
            task_id="task-1",
            session_id="sess-1",
        )
    mock_dispatch.assert_called_once()
    assert mock_dispatch.call_args.args[0] == "F14"
    assert mock_dispatch.call_args.kwargs["error"] is err
    assert mock_dispatch.call_args.kwargs["tool_name"] == "github_search"


def test_after_tool_call_dispatches_for_fail_loud():
    """FAIL_LOUD errors must dispatch so the Telegram alert + card transition fire."""
    err = RuntimeError("disk full: no space left on device")  # F28, FAIL_LOUD
    with mock.patch("lib.durability.handlers.dispatch") as mock_dispatch:
        trichotomy.after_tool_call(
            tool_name="checkpoint_write",
            result=err,
            session_id="sess-1",
        )
    mock_dispatch.assert_called_once()
    assert mock_dispatch.call_args.args[0] == "F28"


def test_after_tool_call_skips_dispatch_for_self_heal():
    """SELF_HEAL is owned by Hermes' own retry loop — dispatch from a
    fire-and-forget hook would have no consumer for the returned delay."""
    err = TimeoutError("upstream timed out after 60s")  # F2, SELF_HEAL
    with mock.patch("lib.durability.handlers.dispatch") as mock_dispatch:
        trichotomy.after_tool_call(tool_name="terminal", result=err)
    mock_dispatch.assert_not_called()


def test_after_tool_call_swallows_dispatch_exception():
    """Hook is fire-and-forget; a handler raising must NOT bubble up to Hermes."""
    err = RuntimeError("github-mcp connection closed")  # F14
    with mock.patch(
        "lib.durability.handlers.dispatch",
        side_effect=RuntimeError("handler exploded"),
    ):
        # Must return None (not re-raise).
        assert trichotomy.after_tool_call(tool_name="github_search", result=err) is None


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
