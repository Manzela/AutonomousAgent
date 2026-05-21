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


# ---------------------------------------------------------------------------
# J11 — dual-emit GenAI semantic-convention attributes
# ---------------------------------------------------------------------------


@pytest.fixture
def _dual_emit_on(monkeypatch):
    """Turn on the J11 dual-emit shim for the duration of one test."""
    import lib.observability as obs

    monkeypatch.setattr(obs, "_DUAL_EMIT_ENABLED", True)
    yield


@pytest.fixture
def _dual_emit_off(monkeypatch):
    """Pin the J11 dual-emit shim OFF (default), even if the host env exports it."""
    import lib.observability as obs

    monkeypatch.setattr(obs, "_DUAL_EMIT_ENABLED", False)
    yield


def test_dual_emit_default_off_emits_no_gen_ai_attrs(_dual_emit_off):
    """Default behavior — Phoenix-compatible OpenInference-only attrs.

    With the flag OFF, model.call spans must contain ZERO ``gen_ai.*``
    attributes; only the existing ``llm.*`` set. This is the contract that
    preserves Phoenix UI compatibility.
    """
    sid = "sess-dual-off"

    def emit():
        _pre_llm_call(session_id=sid, user_message="hi", model="claude-opus-4-7", platform="cli")
        _post_api_request(
            session_id=sid,
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="stop",
            response_model="claude-opus-4-7",
        )
        _post_llm_call(session_id=sid, assistant_response="ack")

    spans = _capture_span(emit)
    attrs = _attrs([s for s in spans if s.name == "model.call"][0])
    gen_ai_keys = [k for k in attrs if k.startswith("gen_ai.")]
    assert gen_ai_keys == [], f"expected no gen_ai.* attrs when flag off, got {gen_ai_keys}"


def test_dual_emit_on_pre_llm_call_sets_request_attrs(_dual_emit_on):
    """``_pre_llm_call`` emits the three pre-call GenAI attrs."""
    sid = "sess-dual-pre"

    def emit():
        _pre_llm_call(session_id=sid, user_message="hi", model="claude-opus-4-7", platform="cli")
        _post_llm_call(session_id=sid, assistant_response="ack")

    spans = _capture_span(emit)
    attrs = _attrs([s for s in spans if s.name == "model.call"][0])
    assert attrs.get("gen_ai.operation.name") == "chat"
    assert attrs.get("gen_ai.request.model") == "claude-opus-4-7"
    assert attrs.get("gen_ai.system") == "cli"


def test_dual_emit_on_post_api_request_sets_usage_attrs(_dual_emit_on):
    """``_post_api_request`` adds usage + response attrs that mirror the
    accumulated OpenInference totals."""
    sid = "sess-dual-post"

    def emit():
        _pre_llm_call(session_id=sid, user_message="x", model="claude-opus-4-7", platform="cli")
        _post_api_request(
            session_id=sid,
            usage={"input_tokens": 100, "output_tokens": 20},
            finish_reason="tool_use",
            response_model="claude-opus-4-7",
        )
        _post_api_request(
            session_id=sid,
            usage={"input_tokens": 150, "output_tokens": 35},
            finish_reason="stop",
            response_model="claude-opus-4-7",
        )
        _post_llm_call(session_id=sid, assistant_response="done")

    spans = _capture_span(emit)
    attrs = _attrs([s for s in spans if s.name == "model.call"][0])
    # Running totals match the OpenInference accumulator.
    assert attrs.get("gen_ai.usage.input_tokens") == 250
    assert attrs.get("gen_ai.usage.output_tokens") == 55
    # GenAI spec defines finish_reasons as an array — emit as length-1 tuple.
    fr = attrs.get("gen_ai.response.finish_reasons")
    assert fr is not None
    assert list(fr) == ["stop"]
    assert attrs.get("gen_ai.response.model") == "claude-opus-4-7"


def test_dual_emit_on_preserves_openinference_attrs(_dual_emit_on):
    """Dual-emit MUST be additive — every existing ``llm.*`` attribute is
    still present when the GenAI shim is on."""
    sid = "sess-dual-both"

    def emit():
        _pre_llm_call(session_id=sid, user_message="x", model="claude-opus-4-7", platform="cli")
        _post_api_request(
            session_id=sid,
            usage={"input_tokens": 7, "output_tokens": 3},
            finish_reason="stop",
            response_model="claude-opus-4-7",
        )
        _post_llm_call(session_id=sid, assistant_response="ok")

    spans = _capture_span(emit)
    attrs = _attrs([s for s in spans if s.name == "model.call"][0])
    # OpenInference path still works.
    assert attrs.get("llm.model_name") == "claude-opus-4-7"
    assert attrs.get("llm.system") == "cli"
    assert attrs.get("llm.token_count.prompt") == 7
    assert attrs.get("llm.token_count.completion") == 3
    assert attrs.get("llm.finish_reason") == "stop"
    assert attrs.get("llm.response_model") == "claude-opus-4-7"
    # GenAI side present in addition.
    assert attrs.get("gen_ai.request.model") == "claude-opus-4-7"
    assert attrs.get("gen_ai.usage.input_tokens") == 7


def test_dual_emit_env_var_parsing():
    """Truthy strings flip the flag on; everything else stays off. We test
    the helper logic by simulating the env-var parse the module performs
    at import time, since reload semantics make a direct importlib test
    fragile in pytest's module-cache."""
    import lib.observability as obs

    truthy = {"1", "true", "yes", "on", "TRUE", "  yes  ", "On"}
    falsy = {"", "0", "false", "no", "off", "random"}

    for v in truthy:
        assert v.strip().lower() in obs._DUAL_EMIT_TRUTHY, f"{v!r} should be truthy"
    for v in falsy:
        assert v.strip().lower() not in obs._DUAL_EMIT_TRUTHY, f"{v!r} should be falsy"


# ---------------------------------------------------------------------------
# J9 — F-CONTEXT detector wiring (Task #55)
# ---------------------------------------------------------------------------
# Covers the wrapper-side shim that feeds ``post_api_request``'s
# prompt-token count into ``ContextUsageDetector.record_usage`` and
# dispatches F36 on threshold crossings. These tests never touch the real
# OTel SDK — they monkeypatch the module-level singletons so behavior is
# verifiable without the production exporter pipeline.


def test_get_model_context_length_known_models():
    """The registry returns the published context window for known models,
    in both bare and LiteLLM-prefixed forms."""
    from lib.observability.model_context import get_model_context_length

    assert get_model_context_length("claude-opus-4-7") == 200_000
    assert get_model_context_length("vertex_ai/claude-opus-4-7") == 200_000
    assert get_model_context_length("anthropic/claude-sonnet-4-6") == 200_000
    assert get_model_context_length("gpt-4o") == 128_000
    assert get_model_context_length("gpt-4") == 8_192
    assert get_model_context_length("vertex_ai/gemini-2.5-pro") == 1_048_576


def test_get_model_context_length_unknown_or_empty_returns_zero():
    """Unknown / empty model strings return 0 — the documented "skip
    F-CONTEXT recording" sentinel."""
    from lib.observability.model_context import get_model_context_length

    assert get_model_context_length(None) == 0
    assert get_model_context_length("") == 0
    assert get_model_context_length("not-a-real-model") == 0
    # No prefix/suffix matching by design — exact match only.
    assert get_model_context_length("claude-opus-4-7-20251022") == 0
    assert get_model_context_length("gpt-4o-2024-08-06") == 0


def test_record_context_usage_skips_when_session_id_missing(monkeypatch):
    """Empty / None session_id is a no-op (matches detector's contract:
    session_id keys the per-episode state, so a blank session would
    collide across callers)."""
    import lib.observability as obs

    fake_detector = MagicMock()
    monkeypatch.setattr(obs, "_get_context_detector", lambda: fake_detector)

    obs._record_context_usage(session_id="", prompt_tokens=100, model="claude-opus-4-7")
    obs._record_context_usage(session_id=None, prompt_tokens=100, model="claude-opus-4-7")  # type: ignore[arg-type]
    assert fake_detector.record_usage.call_count == 0


def test_record_context_usage_skips_when_prompt_tokens_zero(monkeypatch):
    """Zero / negative prompt_tokens — the caller already filtered, but be
    defensive (record_usage with prompt_tokens=0 would emit a 0.0 ratio
    that pollutes dashboards without signal)."""
    import lib.observability as obs

    fake_detector = MagicMock()
    monkeypatch.setattr(obs, "_get_context_detector", lambda: fake_detector)

    obs._record_context_usage(session_id="s1", prompt_tokens=0, model="claude-opus-4-7")
    obs._record_context_usage(session_id="s1", prompt_tokens=-5, model="claude-opus-4-7")
    assert fake_detector.record_usage.call_count == 0


def test_record_context_usage_skips_when_model_unknown(monkeypatch):
    """Unknown model -> context_length 0 -> no record_usage call. F36 can't
    fire because the detector needs a non-zero divisor; the shim short-
    circuits before calling rather than relying on detector's own guard."""
    import lib.observability as obs

    fake_detector = MagicMock()
    monkeypatch.setattr(obs, "_get_context_detector", lambda: fake_detector)

    obs._record_context_usage(session_id="s1", prompt_tokens=100, model="unknown-model-xyz")
    obs._record_context_usage(session_id="s1", prompt_tokens=100, model=None)
    assert fake_detector.record_usage.call_count == 0


def test_record_context_usage_invokes_detector_with_correct_args(monkeypatch):
    """Known model + valid prompt_tokens -> detector.record_usage called
    with session_id, prompt_tokens, and the looked-up context_length."""
    import lib.observability as obs

    fake_detector = MagicMock()
    fake_detector.record_usage.return_value = None  # below threshold
    monkeypatch.setattr(obs, "_get_context_detector", lambda: fake_detector)

    obs._record_context_usage(session_id="s1", prompt_tokens=42, model="claude-opus-4-7")

    fake_detector.record_usage.assert_called_once_with(
        session_id="s1",
        prompt_tokens=42,
        context_length=200_000,
    )


def test_record_context_usage_dispatches_f36_on_threshold(monkeypatch):
    """When detector returns 'F36', the shim dispatches via the failure-
    matrix handler with model + token + context_length payload. This
    exercises the lib.durability.handlers.dispatch indirection."""
    import lib.observability as obs

    fake_detector = MagicMock()
    fake_detector.record_usage.return_value = "F36"
    monkeypatch.setattr(obs, "_get_context_detector", lambda: fake_detector)

    fake_dispatch = MagicMock()
    # Inline import inside _record_context_usage targets
    # lib.durability.handlers; patch via sys.modules so the inline
    # ``from lib.durability.handlers import dispatch`` resolves to our mock.
    import sys
    import types

    fake_handlers = types.ModuleType("lib.durability.handlers")
    fake_handlers.dispatch = fake_dispatch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lib.durability.handlers", fake_handlers)

    obs._record_context_usage(session_id="s1", prompt_tokens=190_000, model="claude-opus-4-7")

    fake_dispatch.assert_called_once()
    args, kwargs = fake_dispatch.call_args
    assert args == ("F36",)
    assert kwargs["session_id"] == "s1"
    assert kwargs["payload"] == {
        "model": "claude-opus-4-7",
        "prompt_tokens": 190_000,
        "context_length": 200_000,
    }


def test_record_context_usage_does_not_dispatch_below_threshold(monkeypatch):
    """Detector returning None must not trigger dispatch."""
    import lib.observability as obs

    fake_detector = MagicMock()
    fake_detector.record_usage.return_value = None
    monkeypatch.setattr(obs, "_get_context_detector", lambda: fake_detector)

    import sys
    import types

    fake_handlers = types.ModuleType("lib.durability.handlers")
    fake_handlers.dispatch = MagicMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lib.durability.handlers", fake_handlers)

    obs._record_context_usage(session_id="s1", prompt_tokens=100, model="claude-opus-4-7")

    assert fake_handlers.dispatch.call_count == 0  # type: ignore[attr-defined]


def test_record_context_usage_swallows_detector_exceptions(monkeypatch):
    """A broken detector must not break the LLM tracing pipeline."""
    import lib.observability as obs

    fake_detector = MagicMock()
    fake_detector.record_usage.side_effect = RuntimeError("detector exploded")
    monkeypatch.setattr(obs, "_get_context_detector", lambda: fake_detector)

    # Should not raise.
    obs._record_context_usage(session_id="s1", prompt_tokens=100, model="claude-opus-4-7")


def test_record_context_usage_swallows_dispatch_exceptions(monkeypatch):
    """A broken dispatch must not break the LLM tracing pipeline either."""
    import lib.observability as obs

    fake_detector = MagicMock()
    fake_detector.record_usage.return_value = "F36"
    monkeypatch.setattr(obs, "_get_context_detector", lambda: fake_detector)

    import sys
    import types

    fake_handlers = types.ModuleType("lib.durability.handlers")
    fake_handlers.dispatch = MagicMock(side_effect=RuntimeError("dispatch exploded"))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lib.durability.handlers", fake_handlers)

    # Should not raise.
    obs._record_context_usage(session_id="s1", prompt_tokens=100, model="claude-opus-4-7")


def test_record_context_usage_is_noop_when_detector_init_failed(monkeypatch):
    """_get_context_detector returning None (init failure cache) must
    short-circuit cleanly."""
    import lib.observability as obs

    monkeypatch.setattr(obs, "_get_context_detector", lambda: None)
    # Should not raise; nothing to assert beyond non-failure.
    obs._record_context_usage(session_id="s1", prompt_tokens=100, model="claude-opus-4-7")


def test_get_context_detector_caches_failure(monkeypatch):
    """A failing ContextUsageDetector import is cached — the second call
    returns None without re-attempting + logging."""
    import sys

    import lib.observability as obs

    # Wipe any cached state.
    monkeypatch.setattr(obs, "_context_detector", None)
    monkeypatch.setattr(obs, "_context_detector_init_failed", False)

    # Force the inline import to fail by removing the module from sys.modules
    # AND inserting a fake that raises on attribute access.
    import types

    broken = types.ModuleType("lib.durability.runtime_detectors")

    def _raising_getattr(name):
        raise ImportError(f"simulated import failure: {name}")

    broken.__getattr__ = _raising_getattr  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "lib.durability.runtime_detectors", broken)

    first = obs._get_context_detector()
    second = obs._get_context_detector()
    assert first is None
    assert second is None
    assert obs._context_detector_init_failed is True


def test_pre_llm_call_captures_model_for_session(monkeypatch):
    """pre_llm_call writes the model into _LLM_MODEL_BY_SESSION so
    post_api_request can fall back to it when response_model is absent."""
    import lib.observability as obs

    # Wipe any leftover state from prior tests.
    obs._LLM_MODEL_BY_SESSION.clear()

    # _tracer must be present for the body of _pre_llm_call to execute the
    # state-capture block; patch with a stub that returns a MagicMock span.
    fake_tracer = MagicMock()
    fake_tracer.start_span.return_value = MagicMock()
    monkeypatch.setattr(obs, "_tracer", fake_tracer)

    obs._pre_llm_call(session_id="sess-pre-1", user_message="hi", model="claude-opus-4-7")

    assert obs._LLM_MODEL_BY_SESSION.get("sess-pre-1") == "claude-opus-4-7"


def test_post_llm_call_drains_model_side_table(monkeypatch):
    """post_llm_call must remove the entry so a long-running process
    doesn't leak per-turn model state across thousands of turns."""
    import lib.observability as obs

    fake_tracer = MagicMock()
    fake_tracer.start_span.return_value = MagicMock()
    monkeypatch.setattr(obs, "_tracer", fake_tracer)

    obs._pre_llm_call(session_id="sess-drain-1", user_message="hi", model="claude-opus-4-7")
    assert "sess-drain-1" in obs._LLM_MODEL_BY_SESSION

    obs._post_llm_call(session_id="sess-drain-1", assistant_response="ok")
    assert "sess-drain-1" not in obs._LLM_MODEL_BY_SESSION


def test_post_api_request_uses_response_model_when_provided(monkeypatch):
    """When response_model is present, it wins over the pre_llm_call
    fallback (so a model swap mid-turn — e.g. provider routing — is
    honored)."""
    import lib.observability as obs

    captured: dict = {}

    def fake_record(*, session_id, prompt_tokens, model):
        captured["session_id"] = session_id
        captured["prompt_tokens"] = prompt_tokens
        captured["model"] = model

    monkeypatch.setattr(obs, "_record_context_usage", fake_record)

    # Set a fallback model so we know which path was taken.
    obs._LLM_MODEL_BY_SESSION["sess-resp-1"] = "claude-sonnet-4-6"

    fake_tracer = MagicMock()
    fake_tracer.start_span.return_value = MagicMock()
    monkeypatch.setattr(obs, "_tracer", fake_tracer)

    obs._pre_llm_call(session_id="sess-resp-1", user_message="x", model="claude-sonnet-4-6")
    obs._post_api_request(
        session_id="sess-resp-1",
        usage={"input_tokens": 1000, "output_tokens": 5},
        response_model="claude-opus-4-7",
    )

    assert captured["model"] == "claude-opus-4-7"
    assert captured["prompt_tokens"] == 1000
    obs._LLM_MODEL_BY_SESSION.pop("sess-resp-1", None)


def test_post_api_request_falls_back_to_captured_model(monkeypatch):
    """When response_model is None / empty, the shim falls back to the
    model captured at pre_llm_call."""
    import lib.observability as obs

    captured: dict = {}

    def fake_record(*, session_id, prompt_tokens, model):
        captured["model"] = model

    monkeypatch.setattr(obs, "_record_context_usage", fake_record)

    fake_tracer = MagicMock()
    fake_tracer.start_span.return_value = MagicMock()
    monkeypatch.setattr(obs, "_tracer", fake_tracer)

    obs._pre_llm_call(session_id="sess-fb-1", user_message="x", model="claude-sonnet-4-6")
    obs._post_api_request(
        session_id="sess-fb-1",
        usage={"input_tokens": 500},
        # response_model omitted -> None
    )

    assert captured["model"] == "claude-sonnet-4-6"
    obs._LLM_MODEL_BY_SESSION.pop("sess-fb-1", None)


def test_post_api_request_skips_record_when_no_input_tokens(monkeypatch):
    """Zero input_tokens (e.g. a streaming envelope without usage) — the
    shim short-circuits before calling _record_context_usage."""
    import lib.observability as obs

    call_count = {"n": 0}

    def fake_record(**_):
        call_count["n"] += 1

    monkeypatch.setattr(obs, "_record_context_usage", fake_record)

    fake_tracer = MagicMock()
    fake_tracer.start_span.return_value = MagicMock()
    monkeypatch.setattr(obs, "_tracer", fake_tracer)

    obs._pre_llm_call(session_id="sess-zero-tok", user_message="x", model="claude-opus-4-7")
    obs._post_api_request(
        session_id="sess-zero-tok",
        usage={"input_tokens": 0, "output_tokens": 5},
    )

    assert call_count["n"] == 0
    obs._LLM_MODEL_BY_SESSION.pop("sess-zero-tok", None)
