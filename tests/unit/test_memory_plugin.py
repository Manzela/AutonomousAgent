"""Verify the memory plugin registers the three slash-command handlers.

P1-4 spec L322-326: ``/forget <pattern>``, ``/forget id:<id>`` (single
``/forget`` handler dispatches by argument shape), ``/rejections``.

The plugin does NOT register an ``on_session_start`` hook — the inject
flow lives inside ``lib.durability.__init__.py`` so its order relative
to P1-3's resume is controlled by call sequence (spec L332).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from lib.memory import register


def _cmd_names(ctx: MagicMock) -> list[str]:
    """Pull registered command names regardless of arg-vs-kwarg style."""
    out = []
    for call in ctx.register_command.call_args_list:
        if call.args:
            out.append(call.args[0])
        elif "name" in call.kwargs:
            out.append(call.kwargs["name"])
    return out


def test_register_wires_three_slash_commands():
    ctx = MagicMock()
    register(ctx)
    names = _cmd_names(ctx)
    assert "forget" in names
    assert "rejections" in names


def test_register_does_not_register_on_session_start():
    """P1-4 register() must NOT add its own on_session_start; that lives in
    lib/durability/__init__.py so order vs P1-3 is deterministic."""
    ctx = MagicMock()
    register(ctx)
    hook_names = [c.args[0] for c in ctx.register_hook.call_args_list]
    assert "on_session_start" not in hook_names


def test_slash_forget_pattern_returns_string():
    from lib.memory import _slash_forget

    out = _slash_forget("some pattern")
    assert isinstance(out, str)


def test_slash_forget_by_id_returns_string():
    from lib.memory import _slash_forget

    out = _slash_forget("id:rej-abc123")
    assert isinstance(out, str)


def test_slash_rejections_returns_string():
    from lib.memory import _slash_rejections

    out = _slash_rejections("")
    assert isinstance(out, str)
