"""Tests the register() contract + hook handlers for the observability plugin.

Mirrors the pattern of tests/unit/test_durability_plugin.py + test_anchors_plugin.py.

The OTel SDK is optional from the unit-suite's perspective — when it's not
installed (host venv often lacks ``opentelemetry-*``), ``setup_tracing()`` returns
``False`` and the hooks become cheap no-ops. The runtime container always has
the SDK (verified in deploy/Dockerfile.hermes), so the production path is fully
exercised by the live verification in the PR description.
"""

from unittest.mock import MagicMock

from lib.observability import (
    _on_session_start,
    _pre_llm_call,
    _post_llm_call,
    _pre_tool_call,
    _post_tool_call,
    _tool_span_key,
    register,
)


def _registered_hooks(ctx_mock: MagicMock) -> list[str]:
    return [call.args[0] for call in ctx_mock.register_hook.call_args_list]


def test_register_wires_all_five_hooks():
    ctx = MagicMock()
    register(ctx)
    hooks = _registered_hooks(ctx)
    assert "on_session_start" in hooks
    assert "pre_tool_call" in hooks
    assert "post_tool_call" in hooks
    assert "pre_llm_call" in hooks
    assert "post_llm_call" in hooks


def test_register_wires_exactly_five_hooks():
    """No spurious hook registrations — keep the surface tight."""
    ctx = MagicMock()
    register(ctx)
    assert ctx.register_hook.call_count == 5


def test_tool_span_key_prefers_tool_call_id():
    """When tool_call_id is present, the pre/post pair key uses it verbatim."""
    assert _tool_span_key("abc123", "terminal", "sess-1") == "id:abc123"


def test_tool_span_key_falls_back_to_session_and_tool_name():
    """Empty tool_call_id falls back to a synthetic session+tool key (Hermes
    defaults tool_call_id to "" — see model_tools.py:749)."""
    assert _tool_span_key("", "terminal", "sess-1") == "sn:sess-1:terminal"
    assert _tool_span_key(None, "terminal", "sess-1") == "sn:sess-1:terminal"


def test_tool_span_key_falls_back_to_underscores_when_missing_everything():
    assert _tool_span_key(None, None, None) == "sn:_:_"


def test_hook_callbacks_always_return_none():
    """Hermes' hook contract: return None to be observer-only. Returning a
    non-None value gets aggregated and may affect dispatch downstream."""
    assert _on_session_start(session_id="s1", model="m", platform="cli") is None
    assert _pre_tool_call(tool_name="t", tool_call_id="x", session_id="s1") is None
    assert _post_tool_call(tool_name="t", tool_call_id="x", session_id="s1") is None
    assert _pre_llm_call(session_id="s1", model="m") is None
    assert _post_llm_call(session_id="s1", model="m", assistant_response="hi") is None


def test_hook_callbacks_absorb_unknown_kwargs():
    """The handler should accept future kwargs Hermes adds (e.g. ``sender_id``)
    without raising — that's the contract for forward-compat."""
    assert (
        _on_session_start(
            session_id="s1",
            model="m",
            platform="cli",
            future_kwarg="ignored",
        )
        is None
    )
    assert (
        _pre_llm_call(
            session_id="s1",
            user_message="msg",
            conversation_history=[],
            is_first_turn=True,
            model="m",
            platform="cli",
            sender_id="u1",
        )
        is None
    )


def test_setup_tracing_idempotent_and_fail_open():
    """``setup_tracing`` returns the SAME bool from every call — the
    ``_initialized`` flag makes the second call cheap. When OTel deps are
    absent (host venv often is) the function returns ``False`` and the
    hooks fall back to no-ops; the plugin must never crash on import."""
    from lib.observability import otel_setup

    first = otel_setup.setup_tracing(service_name="hermes-agent")
    second = otel_setup.setup_tracing(service_name="hermes-agent")
    assert first == second  # idempotent (same outcome on second call)
    assert isinstance(first, bool)


def test_setup_tracing_with_opentelemetry_installed_returns_true():
    """Only runs when the optional opentelemetry SDK is available — that's
    the production path inside the hermes container."""
    try:
        import opentelemetry  # noqa: F401
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,  # noqa: F401
        )
        from opentelemetry.sdk.trace import TracerProvider  # noqa: F401
    except ImportError:
        import pytest

        pytest.skip("opentelemetry SDK not installed in host venv (production path)")

    from lib.observability import otel_setup

    # Reset to force re-init for this test only
    otel_setup._initialized = False
    assert otel_setup.setup_tracing(service_name="hermes-agent") is True
