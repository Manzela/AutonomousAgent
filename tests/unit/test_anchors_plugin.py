"""Verify the anchors plugin registers the expected hooks + commands."""

from unittest.mock import MagicMock

from lib.anchors import register


def test_register_wires_session_start_hook():
    ctx = MagicMock()
    register(ctx)
    hook_calls = [c for c in ctx.register_hook.call_args_list if c.args[0] == "on_session_start"]
    assert len(hook_calls) == 1


def test_register_wires_pre_tool_call_hook():
    ctx = MagicMock()
    register(ctx)
    hook_calls = [c for c in ctx.register_hook.call_args_list if c.args[0] == "pre_tool_call"]
    assert len(hook_calls) == 1


def test_register_wires_clarification_slash_commands():
    ctx = MagicMock()
    register(ctx)
    cmd_names = [c.kwargs.get("name") or c.args[0] for c in ctx.register_command.call_args_list]
    for cmd in ("lock", "skip", "cancel", "confirm"):
        assert cmd in cmd_names, f"Missing slash command: /{cmd}"


def test_register_wires_new_cli_command():
    ctx = MagicMock()
    register(ctx)
    cli_calls = [c for c in ctx.register_cli_command.call_args_list]
    cli_names = [c.kwargs.get("name") or c.args[0] for c in cli_calls]
    assert "new" in cli_names, "Missing CLI subcommand: hermes new"
