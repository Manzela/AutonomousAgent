"""Tests the register() contract for the durability plugin.

Mirrors tests/unit/test_anchors_plugin.py + test_evaluators_plugin.py pattern.
"""

from unittest.mock import MagicMock
from lib.durability import register


def test_register_wires_pre_tool_call_hook():
    ctx = MagicMock()
    register(ctx)
    hook_names = [call.args[0] for call in ctx.register_hook.call_args_list]
    assert "pre_tool_call" in hook_names


def test_register_wires_post_tool_call_hook():
    ctx = MagicMock()
    register(ctx)
    hook_names = [call.args[0] for call in ctx.register_hook.call_args_list]
    assert "post_tool_call" in hook_names


def test_register_wires_on_session_start_in_correct_order():
    """P1-3 resume hook MUST register BEFORE P1-4 inject hook per spec L332."""
    ctx = MagicMock()
    register(ctx)
    session_start_calls = [
        call for call in ctx.register_hook.call_args_list if call.args[0] == "on_session_start"
    ]
    assert len(session_start_calls) == 2
    callback_names = [call.args[1].__name__ for call in session_start_calls]
    assert callback_names == [
        "_p1_3_resume_session",
        "_p1_4_inject_rejected",
    ], "Resume hook MUST register before REJECTED-inject hook per design-alignment spec L332"


def test_stub_callbacks_return_none():
    from lib.durability import _p1_3_resume_session, _p1_4_inject_rejected

    ctx = MagicMock()
    assert _p1_3_resume_session(ctx) is None
    assert _p1_4_inject_rejected(ctx) is None
