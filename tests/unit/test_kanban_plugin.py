"""P1-5 Kanban plugin entry — ``register(ctx)`` wiring contract."""

from __future__ import annotations

from unittest.mock import MagicMock

from lib.kanban import register


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
