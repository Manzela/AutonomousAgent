"""Tests the register() contract for the durability plugin.

Mirrors tests/unit/test_anchors_plugin.py + test_evaluators_plugin.py pattern.

Hook callbacks use the ``**kwargs`` Hermes contract (see
``hermes-agent/hermes_cli/plugins.py:1253`` — ``invoke_hook`` calls ``cb(**kwargs)``).
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


def test_stub_callbacks_return_none_with_hermes_kwargs():
    """The on_session_start stubs must accept Hermes' real kwargs (session_id, model,
    platform — verified at hermes-agent/run_agent.py invoke_hook call site) and
    return None. Pre-fix they declared positional ``ctx`` and TypeError'd on every
    Hermes invocation; this test guards against that regression."""
    from lib.durability import _p1_3_resume_session, _p1_4_inject_rejected

    assert _p1_3_resume_session(session_id="s1", model="claude-opus-4-7", platform="cli") is None
    assert _p1_4_inject_rejected(session_id="s1", model="claude-opus-4-7", platform="cli") is None


def test_stub_callbacks_absorb_unknown_kwargs():
    """Forward-compat: when Hermes adds new kwargs to on_session_start (e.g. sender_id,
    trace_id), our stubs must keep returning None without raising — that's the
    contract observability got right from day one and the bug this PR fixes."""
    from lib.durability import _p1_3_resume_session, _p1_4_inject_rejected

    assert (
        _p1_3_resume_session(
            session_id="s1",
            model="m",
            platform="cli",
            future_kwarg="should-be-ignored",
        )
        is None
    )
    assert (
        _p1_4_inject_rejected(
            session_id="s1",
            model="m",
            platform="cli",
            sender_id="u1",
        )
        is None
    )


def test_hooks_dont_typeerror_under_hermes_kwargs_invocation():
    """Regression test for the keystone Phase 1.0.1 bug.

    Simulates ``PluginManager.invoke_hook(name, **kwargs)`` exactly as
    ``hermes-agent/hermes_cli/plugins.py:1253`` does it — calls each of our four
    registered hooks with the precise kwargs Hermes passes at each call site,
    and asserts no exception escapes the hook body.

    Pre-fix: every invocation raised ``TypeError("got an unexpected keyword
    argument 'tool_name'")`` because our signatures declared positional
    ``(ctx, tool_call)``. Hermes' ``invoke_hook`` swallowed each TypeError at
    WARN level — silently making the entire durability plugin inert at runtime
    even though unit tests passed (because they called the functions directly
    with positional args, never via the Hermes kwargs path).

    Kwargs sourced from:
    - pre_tool_call: hermes_cli/plugins.py:1408 (get_pre_tool_call_block_message)
    - post_tool_call: model_tools.py (post_tool_call invoke_hook site)
    - on_session_start: run_agent.py (_invoke_hook("on_session_start", ...))
    """
    from lib.durability import (
        _p1_3_resume_session,
        _p1_4_inject_rejected,
        trichotomy,
    )

    # pre_tool_call — exactly as model_tools.py -> get_pre_tool_call_block_message
    # constructs the call via invoke_hook(name, **kwargs)
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

    # post_tool_call success path — result is the tool's return value (any type)
    assert (
        trichotomy.after_tool_call(
            tool_name="terminal",
            args={"command": "ls"},
            result="stdout: a\nb\nc\n",
            task_id="task-1",
            session_id="sess-1",
            tool_call_id="call-abc",
            duration_ms=42,
        )
        is None
    )

    # post_tool_call error path — Hermes passes the caught exception as ``result``;
    # trichotomy classifies it to an F-code + emits durability.classify span
    err = TimeoutError("upstream timed out after 60s")
    assert (
        trichotomy.after_tool_call(
            tool_name="terminal",
            args={"command": "sleep 100"},
            result=err,
            task_id="task-1",
            session_id="sess-1",
            tool_call_id="call-def",
            duration_ms=60000,
        )
        is None
    )

    # on_session_start — both registered callbacks
    assert (
        _p1_3_resume_session(session_id="sess-1", model="claude-opus-4-7", platform="cli") is None
    )
    assert (
        _p1_4_inject_rejected(session_id="sess-1", model="claude-opus-4-7", platform="cli") is None
    )
