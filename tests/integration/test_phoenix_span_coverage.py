"""Phoenix span coverage spot-test (audit P1-5).

Closes the acceptance criterion in
``audit/2026-05-19-resume-orchestration/audit-plan.md`` P1-5:

    > One integration test running a short Hermes turn, asserting both
    > LLM and TOOL spans emit with OpenInference attribute set populated.

The existing unit suite (``tests/unit/test_observability_plugin.py``)
covers each span type independently by patching ``obs._tracer`` and
invoking single hook pairs. This integration test goes one level up:

* Wires the plugin via its public ``register(ctx)`` contract so the
  Hermes hook surface is exercised as the runtime invokes it.
* Replays a realistic turn-shaped sequence:
  ``on_session_start`` → ``pre_llm_call`` → ``pre_tool_call`` →
  ``post_tool_call`` → ``post_api_request`` → ``post_llm_call``.
* Asserts both span kinds (``LLM`` model.call + ``TOOL`` tool.dispatch)
  co-emit in the same flow with their OpenInference attribute sets
  populated end-to-end.

Lives in ``tests/integration/`` so it can be lifted into the dedicated
``snapshot-integrity``-style CI job (see ``.github/workflows/ci.yml``
job ``phoenix-span-coverage``) and is grep-able from branch-protection
rules.
"""

from __future__ import annotations

import pytest

# OTel SDK is required for this test; skip cleanly when absent so host venvs
# without the optional deps don't fail the unit run. CI installs opentelemetry-sdk
# explicitly in the dedicated job step.
pytest.importorskip("opentelemetry.sdk.trace")
pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")


@pytest.fixture()
def in_memory_tracer(monkeypatch):
    """Swap ``lib.observability._tracer`` for a fresh InMemorySpanExporter-
    backed tracer and yield the exporter so tests can read finished spans.

    Restores the original tracer on teardown so test ordering is irrelevant
    and the module-level state stays clean."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    import lib.observability as obs

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(obs, "_tracer", provider.get_tracer("test.phoenix-span-coverage"))
    # Clear any module-level span/token bookkeeping that prior tests may have
    # left dangling on the same session_id.
    obs._TOOL_SPANS.clear()
    obs._LLM_SPANS.clear()
    obs._LLM_TOKEN_ACCUM.clear()
    return exporter


class _FakeHermesContext:
    """Minimal stand-in for Hermes' plugin ctx. Tracks registered hooks so the
    test can re-invoke them exactly the way ``invoke_hook`` would at runtime.

    Matches the ``ctx.register_hook(name, callback)`` API surface — see
    ``hermes-agent/hermes_cli/plugins.py`` ``invoke_hook`` for the dispatch
    contract on the consuming side."""

    def __init__(self) -> None:
        self.hooks: dict[str, callable] = {}

    def register_hook(self, name: str, callback) -> None:
        self.hooks[name] = callback

    def invoke(self, name: str, **kwargs):
        """Mirror Hermes' fire-and-forget dispatch: call cb(**kwargs); swallow
        any individual hook failure so the contract under test matches
        production semantics."""
        cb = self.hooks.get(name)
        if cb is None:
            raise AssertionError(f"hook {name!r} not registered by the plugin")
        return cb(**kwargs)


def _attrs(span) -> dict:
    """Coerce a span's MappingProxy attribute view into a plain dict."""
    return dict(span.attributes or {})


# ----------------------------------------------------------------------
# Acceptance test (P1-5)
# ----------------------------------------------------------------------


def test_short_turn_emits_both_llm_and_tool_spans_with_openinference_attrs(
    in_memory_tracer,
):
    """Run a short Hermes-shaped turn through the registered hooks and assert
    both LLM and TOOL spans land with their OpenInference attribute sets
    populated.

    Turn shape mirrors a tool-using assistant exchange:

    1. ``on_session_start`` — session bootstrap, no span emitted here
    2. ``pre_llm_call`` — opens ``model.call`` span (kind=LLM)
    3. ``pre_tool_call`` → ``post_tool_call`` — opens + closes
       ``tool.dispatch`` span (kind=TOOL)
    4. ``post_api_request`` — accumulates token counts onto the open
       ``model.call`` span
    5. ``post_llm_call`` — closes the ``model.call`` span

    Acceptance: both span kinds are present in the captured exporter, both
    carry ``openinference.span.kind`` plus the attribute sets Phoenix needs
    to render the LLM and Tool panels."""

    from lib.observability import register

    ctx = _FakeHermesContext()
    register(ctx)

    # Sanity: the plugin actually registered the hook surface this test
    # depends on. If a future refactor drops one of these, the assertion
    # fails loudly here rather than silently emitting fewer spans.
    expected_hooks = {
        "on_session_start",
        "pre_tool_call",
        "post_tool_call",
        "pre_llm_call",
        "post_llm_call",
        "post_api_request",
    }
    assert expected_hooks.issubset(
        ctx.hooks.keys()
    ), f"plugin must register {expected_hooks}, got {set(ctx.hooks.keys())}"

    sid = "sess-phoenix-coverage-1"
    history = [
        {"role": "system", "content": "you are a shell assistant"},
        {"role": "user", "content": "list the current dir"},
    ]

    # --- Replay the turn through the public hook contract ---
    ctx.invoke("on_session_start", session_id=sid, model="claude-opus-4-7", platform="cli")
    ctx.invoke(
        "pre_llm_call",
        session_id=sid,
        user_message="list the current dir",
        conversation_history=history,
        is_first_turn=True,
        model="claude-opus-4-7",
        platform="cli",
    )
    ctx.invoke(
        "pre_tool_call",
        tool_name="terminal",
        args={"command": "ls -la"},
        tool_call_id="call-llm-emitted-1",
        session_id=sid,
        task_id="task-phoenix",
    )
    ctx.invoke(
        "post_tool_call",
        tool_name="terminal",
        args={"command": "ls -la"},
        result="total 0\ndrwxr-xr-x  2 user  group   64 May 19 12:00 .",
        tool_call_id="call-llm-emitted-1",
        session_id=sid,
        task_id="task-phoenix",
        duration_ms=27,
    )
    ctx.invoke(
        "post_api_request",
        session_id=sid,
        usage={"input_tokens": 312, "output_tokens": 48},
        finish_reason="stop",
        api_duration=0.42,
        response_model="claude-opus-4-7",
    )
    ctx.invoke(
        "post_llm_call",
        session_id=sid,
        assistant_response="Here are the files in the current directory.",
        model="claude-opus-4-7",
    )

    spans = list(in_memory_tracer.get_finished_spans())
    by_name = {s.name: s for s in spans}

    assert (
        "tool.dispatch" in by_name
    ), f"tool.dispatch span missing — emitted names: {[s.name for s in spans]}"
    assert (
        "model.call" in by_name
    ), f"model.call span missing — emitted names: {[s.name for s in spans]}"

    # ---- TOOL span (Phoenix Tool panel) ----
    tool_attrs = _attrs(by_name["tool.dispatch"])
    assert tool_attrs.get("openinference.span.kind") == "TOOL"
    assert tool_attrs.get("tool.name") == "terminal"
    assert tool_attrs.get("tool.parameters") == '{"command": "ls -la"}'
    assert tool_attrs.get("input.value") == '{"command": "ls -la"}'
    assert tool_attrs.get("input.mime_type") == "application/json"
    assert "total 0" in tool_attrs.get("tool.output", "")
    assert "total 0" in tool_attrs.get("output.value", "")
    assert tool_attrs.get("duration_ms") == 27

    # ---- LLM span (Phoenix LLM panel) ----
    llm_attrs = _attrs(by_name["model.call"])
    assert llm_attrs.get("openinference.span.kind") == "LLM"
    assert llm_attrs.get("llm.model_name") == "claude-opus-4-7"
    assert llm_attrs.get("llm.system") == "cli"
    # Input messages enumerated per-OpenInference convention
    assert llm_attrs.get("llm.input_messages.0.message.role") == "system"
    assert llm_attrs.get("llm.input_messages.0.message.content") == "you are a shell assistant"
    assert llm_attrs.get("llm.input_messages.1.message.role") == "user"
    assert llm_attrs.get("llm.input_messages.1.message.content") == "list the current dir"
    # Output messages
    assert llm_attrs.get("llm.output_messages.0.message.role") == "assistant"
    assert llm_attrs.get("llm.output_messages.0.message.content") == (
        "Here are the files in the current directory."
    )
    # Token accounting from post_api_request
    assert llm_attrs.get("llm.token_count.prompt") == 312
    assert llm_attrs.get("llm.token_count.completion") == 48
    assert llm_attrs.get("llm.token_count.total") == 360
    assert llm_attrs.get("llm.finish_reason") == "stop"
    assert llm_attrs.get("llm.response_model") == "claude-opus-4-7"
    # api_duration is captured in milliseconds
    assert llm_attrs.get("llm.api_duration_ms") == 420


def test_short_turn_cleans_up_module_state(in_memory_tracer):
    """After a complete turn, the per-session bookkeeping in
    ``lib.observability`` must be empty — otherwise a long-running agent
    leaks memory across turns. Verified separately from span content so a
    future attribute-only change can't silently regress this contract."""

    from lib.observability import (
        _LLM_SPANS,
        _LLM_TOKEN_ACCUM,
        _TOOL_SPANS,
        register,
    )

    ctx = _FakeHermesContext()
    register(ctx)

    sid = "sess-phoenix-coverage-cleanup"
    ctx.invoke("pre_llm_call", session_id=sid, user_message="x", model="m")
    ctx.invoke(
        "pre_tool_call",
        tool_name="terminal",
        args={},
        tool_call_id="c1",
        session_id=sid,
    )
    ctx.invoke(
        "post_tool_call",
        tool_name="terminal",
        result="ok",
        tool_call_id="c1",
        session_id=sid,
    )
    ctx.invoke(
        "post_api_request",
        session_id=sid,
        usage={"input_tokens": 1, "output_tokens": 1},
    )
    ctx.invoke("post_llm_call", session_id=sid, assistant_response="done")

    assert sid not in _LLM_SPANS, "LLM span bookkeeping leaked after turn close"
    assert sid not in _LLM_TOKEN_ACCUM, "token accumulator leaked after turn close"
    # Tool span dict should also be empty for this turn's tool_call_id
    assert "id:c1" not in _TOOL_SPANS, "tool span bookkeeping leaked after turn close"
