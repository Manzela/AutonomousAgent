"""Tests the register() contract + hook handlers for the observability plugin.

Mirrors the pattern of tests/unit/test_durability_plugin.py + test_anchors_plugin.py.

The OTel SDK is optional from the unit-suite's perspective — when it's not
installed (host venv often lacks ``opentelemetry-*``), ``setup_tracing()`` returns
``False`` and the hooks become cheap no-ops. The runtime container always has
the SDK (verified in deploy/Dockerfile.hermes), so the production path is fully
exercised by the live verification in the PR description.
"""

import pytest
from unittest.mock import MagicMock

from lib.observability import (
    _LLM_SPANS,
    _LLM_TOKEN_ACCUM,
    _message_role_content,
    _on_session_start,
    _post_api_request,
    _post_llm_call,
    _post_tool_call,
    _pre_llm_call,
    _pre_tool_call,
    _safe_json,
    _safe_str,
    _tool_span_key,
    register,
)


def _registered_hooks(ctx_mock: MagicMock) -> list[str]:
    return [call.args[0] for call in ctx_mock.register_hook.call_args_list]


def test_register_wires_all_six_hooks():
    ctx = MagicMock()
    register(ctx)
    hooks = _registered_hooks(ctx)
    assert "on_session_start" in hooks
    assert "pre_tool_call" in hooks
    assert "post_tool_call" in hooks
    assert "pre_llm_call" in hooks
    assert "post_llm_call" in hooks
    assert "post_api_request" in hooks


def test_register_wires_exactly_six_hooks():
    """No spurious hook registrations — keep the surface tight.

    post_api_request was added in #53 for OpenInference token-count enrichment.
    """
    ctx = MagicMock()
    register(ctx)
    assert ctx.register_hook.call_count == 6


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
    assert _post_api_request(session_id="s1", usage={"input_tokens": 1}) is None


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
    assert (
        _post_api_request(
            session_id="s1",
            usage={"input_tokens": 1, "output_tokens": 2},
            finish_reason="stop",
            api_duration=1.5,
            response_model="claude-opus-4-7",
            future_kwarg="ignored",
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
        pytest.skip("opentelemetry SDK not installed in host venv (production path)")

    from lib.observability import otel_setup

    # Reset to force re-init for this test only
    otel_setup._initialized = False
    assert otel_setup.setup_tracing(service_name="hermes-agent") is True


# ---------------------------------------------------------------------------
# OpenInference helpers (#53)
# ---------------------------------------------------------------------------


def test_safe_json_serializes_dicts_and_lists():
    assert _safe_json({"a": 1}) == '{"a": 1}'
    assert _safe_json([1, "x", None]) == '[1, "x", null]'


def test_safe_json_handles_non_serializable_via_default_str():
    """Anything ``json.dumps`` can't handle natively falls back to ``str()``."""

    class Weird:
        def __str__(self) -> str:
            return "weird-obj"

    out = _safe_json({"k": Weird()})
    assert "weird-obj" in out


def test_safe_json_truncates_large_payloads_with_sentinel():
    huge = {"k": "x" * 50_000}
    out = _safe_json(huge, max_len=200)
    assert len(out) == 200
    assert out.endswith('"[truncated]"')


def test_safe_str_handles_none_and_truncates():
    assert _safe_str(None) == ""
    assert _safe_str("abc") == "abc"
    out = _safe_str("y" * 1000, max_len=50)
    assert len(out) == 50
    assert out.endswith("...[truncated]")


def test_message_role_content_extracts_from_dict():
    role, content = _message_role_content({"role": "user", "content": "hi"})
    assert role == "user"
    assert content == "hi"


def test_message_role_content_extracts_from_pydantic_like_obj():
    class Msg:
        role = "assistant"
        content = "response"

    role, content = _message_role_content(Msg())
    assert role == "assistant"
    assert content == "response"


def test_message_role_content_collapses_multimodal_text_parts():
    """OpenAI/Anthropic-style multimodal content list of text parts is
    flattened to a newline-joined string so Phoenix gets readable text."""
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "part one"},
            {"type": "text", "text": "part two"},
        ],
    }
    role, content = _message_role_content(msg)
    assert role == "user"
    assert content == "part one\npart two"


def test_message_role_content_handles_missing_fields():
    assert _message_role_content({}) == (None, None)
    assert _message_role_content({"role": "x"}) == ("x", None)


# ---------------------------------------------------------------------------
# OpenInference attribute emission (#53) — uses real OTel SDK when available
# ---------------------------------------------------------------------------


def _require_otel():
    """Skip when OTel SDK isn't installed (host venv path)."""
    try:
        import opentelemetry  # noqa: F401
        from opentelemetry.sdk.trace import TracerProvider  # noqa: F401
        from opentelemetry.sdk.trace.export import (  # noqa: F401
            SimpleSpanProcessor,
        )
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: F401
            InMemorySpanExporter,
        )
    except ImportError:
        pytest.skip("opentelemetry SDK not installed (production path only)")


def _capture_span(span_factory):
    """Return the finished span object emitted by ``span_factory()``.

    Patches ``_tracer`` in the module to a fresh ``InMemorySpanExporter``-
    backed provider, runs the factory, then returns the single recorded
    span. Restores the original tracer afterwards.
    """
    _require_otel()
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    import lib.observability as obs

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    original = obs._tracer
    obs._tracer = provider.get_tracer("test")
    try:
        span_factory()
    finally:
        obs._tracer = original

    spans = exporter.get_finished_spans()
    return spans


def _attrs(span) -> dict:
    """Convenience: span.attributes is a MappingProxy — coerce to dict."""
    return dict(span.attributes or {})


def test_model_call_span_has_openinference_llm_attrs():
    """model.call span carries openinference.span.kind=LLM + input.value
    + per-message role/content attributes so Phoenix renders prompt text."""

    sid = "sess-llm-1"
    history = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "hi"},
    ]

    def emit():
        _pre_llm_call(
            session_id=sid,
            user_message="hi",
            conversation_history=history,
            is_first_turn=True,
            model="claude-opus-4-7",
            platform="telegram",
        )
        _post_llm_call(
            session_id=sid,
            assistant_response="hello back",
            model="claude-opus-4-7",
        )

    spans = _capture_span(emit)
    model_calls = [s for s in spans if s.name == "model.call"]
    assert len(model_calls) == 1
    attrs = _attrs(model_calls[0])
    assert attrs.get("openinference.span.kind") == "LLM"
    assert attrs.get("llm.model_name") == "claude-opus-4-7"
    assert attrs.get("llm.system") == "telegram"
    assert "input.value" in attrs and "system" in attrs["input.value"]
    assert attrs.get("input.mime_type") == "application/json"
    assert attrs.get("llm.input_messages.0.message.role") == "system"
    assert attrs.get("llm.input_messages.0.message.content") == "be helpful"
    assert attrs.get("llm.input_messages.1.message.role") == "user"
    assert attrs.get("llm.input_messages.1.message.content") == "hi"
    assert attrs.get("output.value") == "hello back"
    assert attrs.get("output.mime_type") == "text/plain"
    assert attrs.get("llm.output_messages.0.message.role") == "assistant"
    assert attrs.get("llm.output_messages.0.message.content") == "hello back"


def test_model_call_falls_back_to_user_message_when_no_history():
    sid = "sess-llm-2"

    def emit():
        _pre_llm_call(
            session_id=sid,
            user_message="solo message",
            conversation_history=None,
            model="m",
        )
        _post_llm_call(session_id=sid, assistant_response="ack")

    spans = _capture_span(emit)
    attrs = _attrs([s for s in spans if s.name == "model.call"][0])
    assert attrs.get("input.value") == "solo message"
    assert attrs.get("input.mime_type") == "text/plain"
    assert attrs.get("llm.input_messages.0.message.role") == "user"
    assert attrs.get("llm.input_messages.0.message.content") == "solo message"


def test_post_api_request_accumulates_tokens_across_calls():
    """A turn with multiple API calls (tool-loop) should sum tokens onto
    the in-flight model.call span and show the running total on the
    final span."""
    sid = "sess-tokens"

    def emit():
        _pre_llm_call(session_id=sid, user_message="x", model="m")
        # Two API calls in the same turn (tool-call loop)
        _post_api_request(
            session_id=sid,
            usage={"input_tokens": 100, "output_tokens": 20},
            finish_reason="tool_use",
            api_duration=0.5,
            response_model="claude-opus-4-7",
        )
        _post_api_request(
            session_id=sid,
            usage={"input_tokens": 150, "output_tokens": 35},
            finish_reason="stop",
            api_duration=0.8,
            response_model="claude-opus-4-7",
        )
        _post_llm_call(session_id=sid, assistant_response="done")

    spans = _capture_span(emit)
    attrs = _attrs([s for s in spans if s.name == "model.call"][0])
    assert attrs.get("llm.token_count.prompt") == 250
    assert attrs.get("llm.token_count.completion") == 55
    assert attrs.get("llm.token_count.total") == 305
    # Most recent finish_reason wins.
    assert attrs.get("llm.finish_reason") == "stop"
    assert attrs.get("llm.response_model") == "claude-opus-4-7"
    # 0.8s -> 800ms (last call wins, no accumulation for duration)
    assert attrs.get("llm.api_duration_ms") == 800


def test_post_api_request_accepts_openai_style_keys():
    """OpenAI uses ``prompt_tokens`` / ``completion_tokens`` instead of
    Anthropic's ``input_tokens`` / ``output_tokens``."""
    sid = "sess-openai-keys"

    def emit():
        _pre_llm_call(session_id=sid, user_message="x", model="m")
        _post_api_request(
            session_id=sid,
            usage={"prompt_tokens": 12, "completion_tokens": 7},
        )
        _post_llm_call(session_id=sid, assistant_response="ok")

    spans = _capture_span(emit)
    attrs = _attrs([s for s in spans if s.name == "model.call"][0])
    assert attrs.get("llm.token_count.prompt") == 12
    assert attrs.get("llm.token_count.completion") == 7
    assert attrs.get("llm.token_count.total") == 19


def test_post_api_request_is_noop_without_active_model_call_span():
    """If post_api_request fires before pre_llm_call (unusual but defensible
    in tests / unusual orderings), it should not crash and should not
    create a phantom span."""
    sid = "sess-orphan-api"

    def emit():
        _post_api_request(
            session_id=sid,
            usage={"input_tokens": 5, "output_tokens": 5},
        )

    spans = _capture_span(emit)
    assert spans == ()  # nothing emitted


def test_tool_dispatch_span_has_openinference_tool_attrs():
    """tool.dispatch span carries openinference.span.kind=TOOL +
    tool.parameters/tool.output so Phoenix renders Tool panels."""
    sid = "sess-tool-1"

    def emit():
        _pre_tool_call(
            tool_name="terminal",
            args={"command": "ls -la"},
            tool_call_id="call-1",
            session_id=sid,
        )
        _post_tool_call(
            tool_name="terminal",
            args={"command": "ls -la"},
            result="total 0",
            tool_call_id="call-1",
            session_id=sid,
            duration_ms=42,
        )

    spans = _capture_span(emit)
    tool_dispatch = [s for s in spans if s.name == "tool.dispatch"]
    assert len(tool_dispatch) == 1
    attrs = _attrs(tool_dispatch[0])
    assert attrs.get("openinference.span.kind") == "TOOL"
    assert attrs.get("tool.name") == "terminal"
    assert attrs.get("tool.parameters") == '{"command": "ls -la"}'
    assert attrs.get("input.value") == '{"command": "ls -la"}'
    assert attrs.get("input.mime_type") == "application/json"
    assert attrs.get("tool.output") == "total 0"
    assert attrs.get("output.value") == "total 0"
    assert attrs.get("duration_ms") == 42


def test_tool_dispatch_omits_output_on_exception_result():
    """When a tool raises, we set error attributes instead of leaking the
    exception object into tool.output."""
    sid = "sess-tool-err"

    def emit():
        _pre_tool_call(
            tool_name="terminal",
            args={"command": "x"},
            tool_call_id="call-e",
            session_id=sid,
        )
        _post_tool_call(
            tool_name="terminal",
            result=ValueError("nope"),
            tool_call_id="call-e",
            session_id=sid,
        )

    spans = _capture_span(emit)
    attrs = _attrs([s for s in spans if s.name == "tool.dispatch"][0])
    assert attrs.get("error") is True
    assert attrs.get("error.type") == "ValueError"
    assert "tool.output" not in attrs
    assert "output.value" not in attrs


def test_token_accumulator_clears_between_turns():
    """A second turn for the same session_id starts at zero, not the
    accumulator value left over from the previous turn."""
    sid = "sess-multi-turn"

    def emit():
        # Turn 1
        _pre_llm_call(session_id=sid, user_message="t1", model="m")
        _post_api_request(session_id=sid, usage={"input_tokens": 100, "output_tokens": 50})
        _post_llm_call(session_id=sid, assistant_response="r1")
        # Turn 2 (same session) — accumulator must reset
        _pre_llm_call(session_id=sid, user_message="t2", model="m")
        _post_api_request(session_id=sid, usage={"input_tokens": 10, "output_tokens": 5})
        _post_llm_call(session_id=sid, assistant_response="r2")

    spans = _capture_span(emit)
    model_calls = [s for s in spans if s.name == "model.call"]
    assert len(model_calls) == 2
    # Spans come back in finish order
    attrs_t1 = _attrs(model_calls[0])
    attrs_t2 = _attrs(model_calls[1])
    assert attrs_t1.get("llm.token_count.prompt") == 100
    assert attrs_t1.get("llm.token_count.completion") == 50
    assert attrs_t2.get("llm.token_count.prompt") == 10
    assert attrs_t2.get("llm.token_count.completion") == 5
    # Module-level dict should also be cleaned up post-turn
    assert sid not in _LLM_SPANS
    assert sid not in _LLM_TOKEN_ACCUM
