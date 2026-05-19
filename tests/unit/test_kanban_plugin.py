"""P1-5 Kanban plugin entry — ``register(ctx)`` wiring + hook-body contract."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import lib.kanban as kanban_pkg
from lib.kanban import (
    _on_post_tool_call,
    _on_pre_tool_call,
    register,
)


def _hook_names(ctx: MagicMock) -> list[str]:
    return [c.args[0] for c in ctx.register_hook.call_args_list]


def test_register_wires_pre_and_post_tool_call_hooks():
    """The plugin must wire pre_tool_call (create card at TaskSpec lock)
    AND post_tool_call (status update → Telegram notification)."""
    ctx = MagicMock()
    register(ctx)
    hooks = _hook_names(ctx)
    assert "pre_tool_call" in hooks, f"Expected pre_tool_call hook, got: {hooks}"
    assert "post_tool_call" in hooks, f"Expected post_tool_call hook, got: {hooks}"


def test_register_does_not_register_anchors_slash_commands():
    """The kanban plugin must not steal /cancel — that lives in anchors plugin.

    The anchors plugin dispatches by argument shape to the kanban bridge.
    """
    ctx = MagicMock()
    register(ctx)
    cmd_names = []
    for call in ctx.register_command.call_args_list:
        if call.args:
            cmd_names.append(call.args[0])
        elif "name" in call.kwargs:
            cmd_names.append(call.kwargs["name"])
    assert "cancel" not in cmd_names


# ---------------------------------------------------------------------------
# Hook body contract (Phase 1.0.1 α-4)
# ---------------------------------------------------------------------------


def _reset_session_state() -> None:
    """Clear the module-level session-tracking cache between tests.

    The ``_on_pre_tool_call`` hook tracks first-tool-call-per-session in a
    module-level set so each new session triggers exactly one
    ``telegram_msg_to_card`` call. Tests that share interpreter state must
    reset this between runs.
    """
    # Tolerate the attribute not yet existing in older versions of the
    # module so the helper is forward-compatible with refactors.
    cache = getattr(kanban_pkg, "_SEEN_SESSIONS", None)
    if cache is not None:
        cache.clear()


def test_on_pre_tool_call_creates_card_on_new_session():
    """First tool call of a new session creates exactly one Kanban card."""
    _reset_session_state()
    with patch.object(kanban_pkg.telegram_bridge, "telegram_msg_to_card") as mock_create:
        mock_create.return_value = "card-new-1"
        _on_pre_tool_call(
            tool_name="shell.exec",
            args={"cmd": "ls"},
            task_id="task-1",
            session_id="session-NEW",
            tool_call_id="tc-1",
        )
    assert (
        mock_create.call_count == 1
    ), f"Expected exactly one card creation on first call, got {mock_create.call_count}"


def test_on_pre_tool_call_no_duplicate_card_per_session():
    """Subsequent tool calls with the same session_id MUST NOT create extra cards."""
    _reset_session_state()
    with patch.object(kanban_pkg.telegram_bridge, "telegram_msg_to_card") as mock_create:
        mock_create.return_value = "card-dedup-1"
        for tc in ("tc-1", "tc-2", "tc-3"):
            _on_pre_tool_call(
                tool_name="shell.exec",
                args={"cmd": "ls"},
                task_id="task-1",
                session_id="session-DEDUP",
                tool_call_id=tc,
            )
    assert (
        mock_create.call_count == 1
    ), f"Expected one card across 3 calls with same session_id, got {mock_create.call_count}"


def test_on_post_tool_call_marks_blocked_on_exception():
    """When result is an Exception, the card status update sends 'blocked'."""
    _reset_session_state()
    with patch.object(kanban_pkg.telegram_bridge, "update_card_status") as mock_update:
        _on_post_tool_call(
            tool_name="shell.exec",
            args={"cmd": "ls"},
            result=RuntimeError("worker crashed"),
            task_id="task-7",
            session_id="session-BOOM",
            tool_call_id="tc-1",
            duration_ms=42,
        )
    assert mock_update.called, "Expected update_card_status to be invoked on exception result"
    # Status arg must be the literal "blocked" string per notification policy.
    args, kwargs = mock_update.call_args
    status_arg = kwargs.get("status") or (args[1] if len(args) >= 2 else None)
    assert status_arg == "blocked", f"Expected status='blocked', got {status_arg!r}"


def test_on_post_tool_call_marks_running_on_success():
    """When result is a non-exception success, the card status update sends 'running'."""
    _reset_session_state()
    with patch.object(kanban_pkg.telegram_bridge, "update_card_status") as mock_update:
        _on_post_tool_call(
            tool_name="shell.exec",
            args={"cmd": "ls"},
            result="ok",
            task_id="task-7",
            session_id="session-OK",
            tool_call_id="tc-1",
            duration_ms=42,
        )
    assert mock_update.called, "Expected update_card_status to be invoked on success result"
    args, kwargs = mock_update.call_args
    status_arg = kwargs.get("status") or (args[1] if len(args) >= 2 else None)
    assert status_arg == "running", f"Expected status='running', got {status_arg!r}"


def test_hook_absorbs_unknown_kwargs():
    """Forward-compat: both hooks must absorb future kwargs without raising.

    Hermes' ``invoke_hook`` already wraps each hook in try/except, but the
    hooks themselves should be tolerant so future kwargs don't blow up.
    """
    _reset_session_state()
    # Should not raise.
    _on_pre_tool_call(
        tool_name="shell.exec",
        args={},
        session_id="session-FUT",
        future_kwarg_added_in_2027="surprise",
    )
    _on_post_tool_call(
        tool_name="shell.exec",
        args={},
        result="ok",
        session_id="session-FUT",
        future_kwarg_added_in_2027="surprise",
    )
