"""Observability plugin.

Two responsibilities, both fixing the Phase 1 acceptance step 4 gap:

1. **Initialize the OTel SDK** once at register time (see ``otel_setup``).
   Without this, every ``trace.get_tracer().start_span(...)`` in the rest
   of our code (e.g. ``lib/durability/trichotomy.py``) silently no-ops
   against ``ProxyTracerProvider``.

2. **Emit Hermes app-level spans** that the runbook expects to see in
   Phoenix:

   - ``turn.start`` on ``on_session_start``
   - ``tool.dispatch`` wrapping ``pre_tool_call`` -> ``post_tool_call``
   - ``model.call`` wrapping ``pre_llm_call`` -> ``post_llm_call``

   Spans carry **OpenInference** semantic-convention attributes
   (openinference-spec/spec/semantic_conventions.md) so Phoenix's LLM
   Tracing UI surfaces prompt / completion text, per-message role+content,
   and token counts. Token data arrives via ``post_api_request`` (Hermes
   doesn't pass usage through ``post_llm_call``) and is accumulated onto
   the in-flight ``model.call`` span keyed by ``session_id``.

Hook signatures match the kwargs Hermes' ``invoke_hook`` passes (see
``hermes-agent/hermes_cli/plugins.py`` ``VALID_HOOKS`` + the call sites
in ``run_agent.py`` / ``model_tools.py``). All callbacks are defensive:
unexpected kwargs are absorbed via ``**_``, and any internal failure
returns ``None`` so the per-hook try/except in ``invoke_hook`` is
preserved as a true fail-open path.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Optional, Tuple

from lib.observability.otel_setup import setup_tracing

logger = logging.getLogger(__name__)

# Install the global TracerProvider as a side-effect of importing the
# module. Hermes' PluginManager loads __init__.py before calling
# register(), so by the time register() runs every subsequent
# tracer.start_span() in any plugin is exporting through us.
_TRACING_OK = setup_tracing(service_name="hermes-agent")

# Lazy tracer handle — only used when tracing initialized.
_tracer: Any = None
if _TRACING_OK:
    try:
        from opentelemetry import trace  # type: ignore

        _tracer = trace.get_tracer("hermes.observability")
    except Exception:  # pragma: no cover
        _tracer = None

# Active-span registry. Tool-call spans use the ``tool_call_id`` key
# (passed verbatim through both pre/post hook kwargs). LLM-call spans use
# the ``session_id`` because Hermes does not pass an llm_call_id through
# the hook surface — a session only has one in-flight LLM call at a time
# so session_id is unambiguous.
_LOCK = threading.Lock()
_TOOL_SPANS: Dict[str, Any] = {}
_LLM_SPANS: Dict[str, Tuple[Any, Any]] = {}  # session_id -> (span, ctx_manager)
# Per-turn token accumulator: a turn often makes multiple API calls in the
# tool-calling loop and each emits its own post_api_request. We sum across
# them so the model.call span shows the full turn cost.
_LLM_TOKEN_ACCUM: Dict[str, Tuple[int, int]] = {}  # session_id -> (in_tokens, out_tokens)

# Attribute size cap — OTel spec allows arbitrary string lengths but exporters
# (Phoenix, Tempo, etc.) commonly truncate at 32k. 10k is safe and keeps span
# payloads small enough that batch export isn't dominated by a single span.
_MAX_ATTR_LEN = 10_000
# Per-message content cap. 20 messages × 2k chars = 40k worst-case across
# input_messages attrs, which is still well within batch limits.
_MAX_MSG_CONTENT_LEN = 2_000
# Cap on number of input messages emitted per span — a long history shouldn't
# explode attribute count. The full history is still serialized as input.value.
_MAX_INPUT_MESSAGES = 20

_OI_KIND = "openinference.span.kind"


def register(ctx: Any) -> None:
    """Hermes plugin entry point.

    Wires the six hooks unconditionally — when ``setup_tracing`` failed
    (no OTel SDK) the hooks become cheap no-ops because ``_tracer`` is
    ``None`` and each handler short-circuits.
    """
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_llm_call", _post_llm_call)
    # post_api_request fires once per LLM HTTP request (potentially N per turn
    # when the model triggers tool calls). We attach token counts onto the
    # in-flight model.call span; see _post_api_request docstring.
    ctx.register_hook("post_api_request", _post_api_request)
    logger.info(
        "observability: %d hooks registered, tracing_ok=%s",
        6,
        _TRACING_OK,
    )


# ---------------------------------------------------------------------------
# Helpers — safe encoding for OTel attribute values
# ---------------------------------------------------------------------------


def _safe_json(obj: Any, max_len: int = _MAX_ATTR_LEN) -> str:
    """JSON-encode ``obj`` with ``default=str`` for non-serializable types.

    Returns a truncated string with a sentinel suffix when over ``max_len``
    so downstream consumers can detect truncation.
    """
    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:
        s = str(obj)
    if len(s) > max_len:
        return s[: max_len - 16] + '..."[truncated]"'
    return s


def _safe_str(obj: Any, max_len: int = _MAX_ATTR_LEN) -> str:
    """Stringify ``obj`` and truncate to ``max_len``."""
    s = str(obj) if obj is not None else ""
    if len(s) > max_len:
        return s[: max_len - 14] + "...[truncated]"
    return s


def _message_role_content(msg: Any) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort extraction of ``(role, content)`` from a message-like obj.

    Handles dict-shaped messages (the OpenAI/Anthropic wire format) and
    Pydantic-style objects (e.g. LiteLLM's ``Message`` model). Multimodal
    content (a list of text/image parts) is collapsed to a newline-joined
    text-only string; this is sufficient for the Phoenix UI which renders
    plain text by default.
    """
    if isinstance(msg, dict):
        role = msg.get("role")
        content = msg.get("content")
    else:
        role = getattr(msg, "role", None)
        content = getattr(msg, "content", None)

    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
                elif "text" in part:
                    parts.append(str(part["text"]))
            elif isinstance(part, str):
                parts.append(part)
        content = "\n".join(parts) if parts else None

    role_s = str(role) if role is not None else None
    content_s = str(content) if content is not None else None
    return role_s, content_s


# ---------------------------------------------------------------------------
# Hook callbacks
# ---------------------------------------------------------------------------


def _on_session_start(
    session_id: Optional[str] = None,
    model: Optional[str] = None,
    platform: Optional[str] = None,
    **_: Any,
) -> None:
    """Emit a point-in-time span marking session start.

    Started + ended inline since Hermes does not expose a matching
    ``on_session_open`` / ``on_session_close`` pair we could span across.
    """
    if _tracer is None:
        return None
    try:
        span = _tracer.start_span("turn.start")
        if session_id:
            span.set_attribute("session.id", str(session_id))
        if model:
            span.set_attribute("model", str(model))
        if platform:
            span.set_attribute("platform", str(platform))
        span.end()
    except Exception as exc:  # noqa: BLE001
        logger.debug("turn.start span failed: %s", exc)
    return None


def _tool_span_key(
    tool_call_id: Optional[str],
    tool_name: Optional[str],
    session_id: Optional[str],
) -> str:
    """Choose a stable key for pairing pre/post tool_call spans.

    Hermes' ``model_tools.py`` populates ``tool_call_id`` from the
    upstream LLM response when available, but defaults to ``""`` (empty
    string) when the caller hasn't supplied one (e.g. internal tools).
    Empty string is unreliable as a dict key, so we synthesize a key
    from ``session_id|tool_name`` as a fallback. Pre/post pair on the
    same combination because Hermes awaits each tool call before issuing
    the next, so even synthetic keys round-trip cleanly within a turn.
    """
    if tool_call_id:
        return f"id:{tool_call_id}"
    sid = session_id or "_"
    tn = tool_name or "_"
    return f"sn:{sid}:{tn}"


def _pre_tool_call(
    tool_name: Optional[str] = None,
    args: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    **_: Any,
) -> None:
    """Open a ``tool.dispatch`` span. Keyed via ``_tool_span_key``.

    Hermes always invokes ``pre_tool_call`` exactly once before each
    function dispatch (model_tools.py:740) and the matching
    ``post_tool_call`` exactly once after (model_tools.py:793), so we
    always have a pair.

    Sets OpenInference TOOL attributes (``openinference.span.kind=TOOL``,
    ``tool.name``, ``tool.parameters``, ``input.value``) so Phoenix's
    Tool span panel can render the call.
    """
    if _tracer is None:
        return None
    try:
        span = _tracer.start_span("tool.dispatch")
        span.set_attribute(_OI_KIND, "TOOL")
        if tool_name:
            span.set_attribute("tool.name", str(tool_name))
        if task_id:
            span.set_attribute("task.id", str(task_id))
        if session_id:
            span.set_attribute("session.id", str(session_id))
        if tool_call_id:
            span.set_attribute("tool_call.id", str(tool_call_id))
        if args is not None:
            args_json = _safe_json(args)
            span.set_attribute("tool.parameters", args_json)
            span.set_attribute("input.value", args_json)
            span.set_attribute("input.mime_type", "application/json")
        key = _tool_span_key(tool_call_id, tool_name, session_id)
        with _LOCK:
            _TOOL_SPANS[key] = span
    except Exception as exc:  # noqa: BLE001
        logger.debug("tool.dispatch start failed: %s", exc)
    return None


def _post_tool_call(
    tool_name: Optional[str] = None,
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
    **_: Any,
) -> None:
    if _tracer is None:
        return None
    try:
        key = _tool_span_key(tool_call_id, tool_name, session_id)
        with _LOCK:
            span = _TOOL_SPANS.pop(key, None)
        if span is None:
            # Orphaned post — emit a synthetic short-lived span so the
            # event isn't lost.
            span = _tracer.start_span("tool.dispatch")
            span.set_attribute(_OI_KIND, "TOOL")
            if tool_name:
                span.set_attribute("tool.name", str(tool_name))
            span.set_attribute("orphaned", True)
        if duration_ms is not None:
            try:
                span.set_attribute("duration_ms", int(duration_ms))
            except Exception:  # noqa: BLE001
                pass
        if isinstance(result, Exception):
            span.set_attribute("error", True)
            span.set_attribute("error.type", type(result).__name__)
        elif result is not None:
            result_s = _safe_str(result)
            span.set_attribute("tool.output", result_s)
            span.set_attribute("output.value", result_s)
            span.set_attribute("output.mime_type", "text/plain")
        span.end()
    except Exception as exc:  # noqa: BLE001
        logger.debug("tool.dispatch end failed: %s", exc)
    return None


def _pre_llm_call(
    session_id: Optional[str] = None,
    user_message: Any = None,
    conversation_history: Any = None,
    is_first_turn: Optional[bool] = None,
    model: Optional[str] = None,
    platform: Optional[str] = None,
    sender_id: Optional[str] = None,
    **_: Any,
) -> None:
    """Open a ``model.call`` span keyed by ``session_id``.

    A session has at most one outstanding LLM call at any moment (Hermes
    awaits the response synchronously inside its agent loop), so
    ``session_id`` is a sound unique key for the pre/post pair.

    Sets OpenInference LLM attributes (``openinference.span.kind=LLM``,
    ``llm.model_name``, ``llm.system``, ``input.value``,
    ``llm.input_messages.{N}.message.{role,content}``) so Phoenix's LLM
    span panel surfaces the prompt and per-message structure.
    """
    if _tracer is None or not session_id:
        return None
    try:
        span = _tracer.start_span("model.call")
        span.set_attribute(_OI_KIND, "LLM")
        span.set_attribute("session.id", str(session_id))
        if model:
            span.set_attribute("model", str(model))
            span.set_attribute("llm.model_name", str(model))
        if platform:
            span.set_attribute("platform", str(platform))
            span.set_attribute("llm.system", str(platform))
        if is_first_turn is not None:
            span.set_attribute("is_first_turn", bool(is_first_turn))

        # Input messages — full conversation_history if available, else
        # fall back to the single user_message string.
        if isinstance(conversation_history, (list, tuple)) and conversation_history:
            span.set_attribute("input.value", _safe_json(conversation_history))
            span.set_attribute("input.mime_type", "application/json")
            for idx, msg in enumerate(conversation_history[:_MAX_INPUT_MESSAGES]):
                role, content = _message_role_content(msg)
                if role:
                    span.set_attribute(f"llm.input_messages.{idx}.message.role", role)
                if content is not None:
                    span.set_attribute(
                        f"llm.input_messages.{idx}.message.content",
                        _safe_str(content, _MAX_MSG_CONTENT_LEN),
                    )
        elif user_message is not None:
            um = _safe_str(user_message)
            span.set_attribute("input.value", um)
            span.set_attribute("input.mime_type", "text/plain")
            span.set_attribute("llm.input_messages.0.message.role", "user")
            span.set_attribute(
                "llm.input_messages.0.message.content",
                _safe_str(user_message, _MAX_MSG_CONTENT_LEN),
            )

        with _LOCK:
            _LLM_SPANS[session_id] = (span, None)
            # Reset the token accumulator for the new turn so prior-turn
            # totals don't leak into this span.
            _LLM_TOKEN_ACCUM[session_id] = (0, 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("model.call start failed: %s", exc)
    return None


def _post_llm_call(
    session_id: Optional[str] = None,
    user_message: Any = None,
    assistant_response: Any = None,
    conversation_history: Any = None,
    model: Optional[str] = None,
    platform: Optional[str] = None,
    **_: Any,
) -> None:
    if _tracer is None or not session_id:
        return None
    try:
        with _LOCK:
            entry = _LLM_SPANS.pop(session_id, None)
            # Drop accumulator state now that the turn is closing.
            _LLM_TOKEN_ACCUM.pop(session_id, None)
        if entry is None:
            return None
        span, _ = entry
        if assistant_response is not None:
            response_s = _safe_str(assistant_response)
            try:
                span.set_attribute("response.length", len(str(assistant_response)))
            except Exception:  # noqa: BLE001
                pass
            span.set_attribute("output.value", response_s)
            span.set_attribute("output.mime_type", "text/plain")
            span.set_attribute("llm.output_messages.0.message.role", "assistant")
            span.set_attribute(
                "llm.output_messages.0.message.content",
                _safe_str(assistant_response, _MAX_MSG_CONTENT_LEN * 2),
            )
        span.end()
    except Exception as exc:  # noqa: BLE001
        logger.debug("model.call end failed: %s", exc)
    return None


def _post_api_request(
    session_id: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
    finish_reason: Optional[str] = None,
    api_duration: Optional[float] = None,
    response_model: Optional[str] = None,
    **_: Any,
) -> None:
    """Enrich the in-flight ``model.call`` span with token + finish_reason data.

    Hermes fires ``post_api_request`` once per HTTP call to the model
    provider (run_agent.py:14533). A single turn may make N calls when
    the model triggers tool calls in a loop — we accumulate input/output
    tokens across all of them and re-set the span attributes each time,
    so the final ``model.call`` span shows the full turn cost.

    No-op when no active ``model.call`` span exists for the session
    (e.g. test fixtures that fire post_api_request without a matching
    pre_llm_call).
    """
    if _tracer is None or not session_id:
        return None
    try:
        with _LOCK:
            entry = _LLM_SPANS.get(session_id)
        if entry is None:
            return None
        span, _ = entry

        if isinstance(usage, dict):
            in_tok_raw = usage.get("input_tokens", usage.get("prompt_tokens", 0))
            out_tok_raw = usage.get("output_tokens", usage.get("completion_tokens", 0))
            try:
                in_tok = int(in_tok_raw or 0)
                out_tok = int(out_tok_raw or 0)
            except (TypeError, ValueError):
                in_tok = 0
                out_tok = 0
            if in_tok or out_tok:
                with _LOCK:
                    prev_in, prev_out = _LLM_TOKEN_ACCUM.get(session_id, (0, 0))
                    new_in = prev_in + in_tok
                    new_out = prev_out + out_tok
                    _LLM_TOKEN_ACCUM[session_id] = (new_in, new_out)
                span.set_attribute("llm.token_count.prompt", new_in)
                span.set_attribute("llm.token_count.completion", new_out)
                span.set_attribute("llm.token_count.total", new_in + new_out)

        if finish_reason:
            span.set_attribute("llm.finish_reason", str(finish_reason))
        if api_duration is not None:
            try:
                span.set_attribute("llm.api_duration_ms", int(float(api_duration) * 1000))
            except (TypeError, ValueError):
                pass
        if response_model:
            span.set_attribute("llm.response_model", str(response_model))
    except Exception as exc:  # noqa: BLE001
        logger.debug("post_api_request enrich failed: %s", exc)
    return None
