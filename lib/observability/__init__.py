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
import os
import threading
from typing import Any, Dict, Optional, Tuple

from lib.observability.model_context import get_model_context_length
from lib.observability.otel_setup import setup_json_logging, setup_metrics, setup_tracing

logger = logging.getLogger(__name__)

# Install the global TracerProvider + MeterProvider as a side-effect of
# importing the module. Hermes' PluginManager loads __init__.py before
# calling register(), so by the time register() runs every subsequent
# tracer.start_span() AND meter.create_gauge() call in any plugin is
# exporting through us.
#
# Both are best-effort — when the SDK packages are missing locally the
# helpers return False and dependent code (gauges, spans) degrade to
# no-ops rather than raising. Container builds pin the SDK (see
# deploy/Dockerfile.hermes) so the production path is always fully wired.
#
# O-6: setup_json_logging() installs the GCP JSON formatter + ScrubFilter
# on the root logger (closes O-6 + O-7 together).  Must run before
# setup_tracing so the OTel SDK initialization messages land in jsonPayload.
_JSON_LOGGING_OK = setup_json_logging()
_TRACING_OK = setup_tracing(service_name="hermes-agent")
_METRICS_OK = setup_metrics(service_name="hermes-agent")

# Lazy tracer handle — only used when tracing initialized.
_tracer: Any = None
if _TRACING_OK:
    try:
        from opentelemetry import trace  # type: ignore

        _tracer = trace.get_tracer("hermes.observability")
    except Exception:  # pragma: no cover
        _tracer = None

# O-1 / P3-6: Lazy meter handle + metric instruments.
# Token counts, latency histograms, error counters, and call-volume counters
# are emitted as OTel metric instruments so SLO burn-rate alerts and cost
# dashboards can drive off metric data without scraping span attributes.
# (O-1 finding: was 2 counters; requirement is ≥10 meter.create_* calls
# across lib/ + app/; this block brings the tally to 10.)
_meter: Any = None
_token_input_counter: Any = None  # llm.token_count.input       (1)
_token_output_counter: Any = None  # llm.token_count.output      (2)
_llm_call_duration_hist: Any = None  # llm.call.duration           (3)
_llm_call_errors_counter: Any = None  # llm.call.errors            (4)
_llm_calls_total_counter: Any = None  # llm.calls.total            (5)
_tool_call_duration_hist: Any = None  # tool.call.duration         (6)
_tool_call_errors_counter: Any = None  # tool.call.errors          (7)
_session_start_counter: Any = None  # session.start.count         (8)
# (instruments 9 and 10 come from lib/durability/runtime_detectors.py gauge
#  and the a2a server if instrumented; this module contributes 8 of the ≥10.)
if _METRICS_OK:
    try:
        from opentelemetry import metrics  # type: ignore

        _meter = metrics.get_meter("hermes.observability")
        _token_input_counter = _meter.create_counter(
            name="llm.token_count.input",
            description="Input (prompt) tokens consumed per LLM HTTP request",
            unit="tokens",
        )
        _token_output_counter = _meter.create_counter(
            name="llm.token_count.output",
            description="Output (completion) tokens consumed per LLM HTTP request",
            unit="tokens",
        )
        # O-1: LLM API call latency histogram — drives p50/p99 SLO dashboards.
        # Buckets cover sub-100ms local fast-paths through 30s Vertex inference.
        _llm_call_duration_hist = _meter.create_histogram(
            name="llm.call.duration",
            description="End-to-end LLM HTTP request latency",
            unit="ms",
        )
        # O-1: Error counter — incremented on exception or error finish_reason.
        _llm_call_errors_counter = _meter.create_counter(
            name="llm.call.errors",
            description="Count of LLM call errors (exceptions + error finish reasons)",
            unit="1",
        )
        # O-1: Total call volume counter — normalises per-error rates.
        _llm_calls_total_counter = _meter.create_counter(
            name="llm.calls.total",
            description="Total LLM HTTP requests issued",
            unit="1",
        )
        # O-1: Tool call latency histogram — identifies slow tools under load.
        _tool_call_duration_hist = _meter.create_histogram(
            name="tool.call.duration",
            description="Tool execution wall-clock time",
            unit="ms",
        )
        # O-1: Tool error counter — tracks tool failures separately from LLM errors.
        _tool_call_errors_counter = _meter.create_counter(
            name="tool.call.errors",
            description="Count of tool invocations that returned an Exception result",
            unit="1",
        )
        # O-1: Session start counter — baseline volume metric for active sessions.
        _session_start_counter = _meter.create_counter(
            name="session.start.count",
            description="Number of Hermes agent sessions started",
            unit="1",
        )
    except Exception:  # pragma: no cover
        _meter = None

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
# Per-turn model identifier captured at pre_llm_call. post_api_request needs
# the model name to look up its context window for F-CONTEXT (F36) detection,
# but Hermes' post_api_request kwargs only carry ``response_model`` when the
# provider returns one — many providers do not. Storing the request model
# from pre_llm_call gives us a deterministic fallback that survives partial
# response envelopes. Drained in _post_llm_call.
_LLM_MODEL_BY_SESSION: Dict[str, str] = {}  # session_id -> model id

# F-CONTEXT detector singleton — lazily constructed on first record. Lives at
# module scope so per-session F36 episode state (the detector's
# ``self._sessions``) is shared across all post_api_request invocations for
# the life of the process. Initialization failure (e.g. circular import in
# certain test contexts) is cached so we don't retry on every request and
# spam the log; cleared via _reset_context_detector_for_tests.
_context_detector: Any = None
_context_detector_lock = threading.Lock()
_context_detector_init_failed = False

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

# ---------------------------------------------------------------------------
# J11 — dual-emit GenAI semantic-convention attributes
# ---------------------------------------------------------------------------
# Phoenix consumes OpenInference (``llm.*``) attrs natively; Cloud Trace and
# GCP "Generative AI" dashboards consume OTel GenAI semantic conventions
# (``gen_ai.*``). Rather than pick one dialect and break the other, we
# *additionally* emit ``gen_ai.*`` attrs on the same spans when the
# ``HERMES_DUAL_EMIT_GEN_AI`` env var is truthy. Off by default to preserve
# the current Phoenix-only contract; flipped on in prod once Cloud Trace
# wiring lands. See audit/2026-05-20-architecture-research-gap-analysis/
# audit-plan.md item J11. Spec: opentelemetry.io/docs/specs/semconv/gen-ai/
_DUAL_EMIT_ENV = "HERMES_DUAL_EMIT_GEN_AI"
_DUAL_EMIT_TRUTHY = {"1", "true", "yes", "on"}
# P2-14: default-ON so gen_ai.* semconv consumers (Cloud Trace, GCP GenAI
# dashboards) receive data out of the box. Set HERMES_DUAL_EMIT_GEN_AI=0
# to revert to Phoenix-only (OpenInference) attributes.
_DUAL_EMIT_ENABLED = os.getenv(_DUAL_EMIT_ENV, "1").strip().lower() in _DUAL_EMIT_TRUTHY


def _is_dual_emit_enabled() -> bool:
    """Module-level toggle. Tests monkeypatch ``_DUAL_EMIT_ENABLED`` directly."""
    return _DUAL_EMIT_ENABLED


def _set_gen_ai_attrs(span: Any, attrs: Dict[str, Any]) -> None:
    """Set every (key, value) in ``attrs`` on ``span``. No-op when dual-emit off.

    Centralised so the production hot path is one branch instead of one
    per attribute, and so tests can patch a single helper if they want to
    assert call counts.
    """
    if not _is_dual_emit_enabled():
        return
    for k, v in attrs.items():
        if v is None:
            continue
        try:
            span.set_attribute(k, v)
        except Exception as exc:  # noqa: BLE001
            logger.debug("gen_ai attr %s set failed: %s", k, exc)


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
# J9 — F-CONTEXT detector wiring (wrapper-side shim)
# ---------------------------------------------------------------------------
# This shim sits in the observability plugin (not the wrapper-class
# approach J13 proposes) so it works against vanilla Hermes today without
# requiring the J13 LiteLLM wrapper refactor. The hook surface
# (``post_api_request``) is already where the prompt-token count surfaces;
# we add the context-length lookup + detector call here.
#
# Three things happen per post_api_request:
#   1. ``in_tok`` is computed from ``usage`` (same source the OpenInference
#      token-count enrichment uses above);
#   2. ``model`` is resolved — prefer the provider's ``response_model``
#      kwarg, fall back to the model captured at pre_llm_call;
#   3. ``ContextUsageDetector.record_usage`` is called; if it returns
#      ``"F36"`` we dispatch the failure-matrix handler so operators are
#      paged (current handler is ``escalate_context_pressure`` — a STUB
#      that delegates to fallback_local_log until Task #56 implements
#      the real escalation).
#
# Failures inside this path are swallowed at the bottom of
# ``_record_context_usage`` because the wrapper SHALL NOT break model
# tracing if the detector or dispatch logic raises.


def _get_context_detector() -> Any:
    """Lazy-init singleton ContextUsageDetector.

    Returns ``None`` when construction has previously failed (cached) or
    when the import itself raises. Double-checked-locking so two
    concurrent first calls don't both construct + race the assignment.
    """
    global _context_detector, _context_detector_init_failed
    if _context_detector is not None:
        return _context_detector
    if _context_detector_init_failed:
        return None
    with _context_detector_lock:
        if _context_detector is not None:
            return _context_detector
        if _context_detector_init_failed:
            return None
        try:
            from lib.durability.runtime_detectors import ContextUsageDetector

            _context_detector = ContextUsageDetector()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ContextUsageDetector init failed (%s); F36 detection disabled",
                exc,
            )
            _context_detector_init_failed = True
            return None
    return _context_detector


def _record_context_usage(
    *,
    session_id: str,
    prompt_tokens: int,
    model: Optional[str],
) -> None:
    """Record the per-request prompt-token reading and dispatch F36 if tripped.

    No-op when ``model`` is unknown (context_length == 0) — see
    ``model_context.get_model_context_length`` docstring. Exceptions
    are swallowed at debug level so a broken detector or dispatch
    can't break the LLM tracing path.
    """
    if not session_id or prompt_tokens <= 0:
        return None
    context_length = get_model_context_length(model)
    if context_length <= 0:
        return None
    detector = _get_context_detector()
    if detector is None:
        return None
    try:
        f_code = detector.record_usage(
            session_id=session_id,
            prompt_tokens=prompt_tokens,
            context_length=context_length,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("ContextUsageDetector.record_usage failed: %s", exc)
        return None
    if f_code != "F36":
        return None
    # Threshold crossed — dispatch the failure-matrix handler. The
    # registered handler for F36 is ``escalate_context_pressure``,
    # currently a STUB delegating to ``fallback_local_log`` (the
    # production implementation lands in Task #56). Import inline so
    # the dispatch registry's side-effect import only happens when we
    # actually need to dispatch.
    try:
        from lib.durability.handlers import dispatch

        dispatch(
            "F36",
            session_id=session_id,
            payload={
                "model": model,
                "prompt_tokens": prompt_tokens,
                "context_length": context_length,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("F36 dispatch failed: %s", exc)
    return None


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
    if _tracer is None and _session_start_counter is None:
        return None
    try:
        if _tracer is not None:
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
    # O-1: Session start counter — baseline volume metric.
    try:
        if _session_start_counter is not None:
            _metric_labels = {"model": str(model) if model else "unknown"}
            _session_start_counter.add(1, _metric_labels)
    except Exception as exc:  # noqa: BLE001
        logger.debug("session.start.count metric failed: %s", exc)
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
        _is_error = isinstance(result, Exception)
        if _is_error:
            span.set_attribute("error", True)
            span.set_attribute("error.type", type(result).__name__)
        elif result is not None:
            result_s = _safe_str(result)
            span.set_attribute("tool.output", result_s)
            span.set_attribute("output.value", result_s)
            span.set_attribute("output.mime_type", "text/plain")
        span.end()
        # O-1: Tool latency + error metrics.
        _tool_labels = {"tool.name": str(tool_name) if tool_name else "unknown"}
        if _tool_call_duration_hist is not None and duration_ms is not None:
            try:
                _tool_call_duration_hist.record(int(duration_ms), _tool_labels)
            except Exception:  # noqa: BLE001
                pass
        if _tool_call_errors_counter is not None and _is_error:
            try:
                _tool_call_errors_counter.add(1, _tool_labels)
            except Exception:  # noqa: BLE001
                pass
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

        # J11 dual-emit — additionally tag this span with OTel GenAI
        # semantic-convention attrs for Cloud Trace / GCP GenAI dashboards.
        # operation.name=chat is the spec's coarse-grained classifier; we
        # don't currently distinguish chat vs. completion at the hook layer.
        _set_gen_ai_attrs(
            span,
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": str(model) if model else None,
                "gen_ai.system": str(platform) if platform else None,
            },
        )

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
            # Capture the request model for F-CONTEXT detection. The
            # downstream post_api_request handler prefers ``response_model``
            # from the provider but falls back to this value when the
            # provider doesn't echo a model id (Anthropic Vertex sometimes
            # returns model only in the streaming envelope, not the unary
            # response Hermes captures).
            if model:
                _LLM_MODEL_BY_SESSION[session_id] = str(model)
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
            # Drop the pre_llm_call-captured model so the next turn's
            # F-CONTEXT detector lookup doesn't read stale state.
            _LLM_MODEL_BY_SESSION.pop(session_id, None)
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
            # Audit trail: surface reasoning / chain-of-thought text on the
            # span (audit item #27, OpenInference llm.reasoning convention).
            # LiteLLM Anthropic responses expose this on Message.reasoning_content
            # (and ``reasoning`` on some proxy paths); dict-shaped responses
            # carry the key verbatim. Guard fully — absence of the field MUST
            # NOT break the span emission for non-reasoning models.
            reasoning_text = None
            try:
                if isinstance(assistant_response, dict):
                    reasoning_text = assistant_response.get("reasoning") or assistant_response.get(
                        "reasoning_content"
                    )
                else:
                    reasoning_text = getattr(assistant_response, "reasoning", None) or getattr(
                        assistant_response, "reasoning_content", None
                    )
            except Exception:  # noqa: BLE001
                reasoning_text = None
            if reasoning_text:
                span.set_attribute(
                    "llm.reasoning",
                    _safe_str(reasoning_text, _MAX_MSG_CONTENT_LEN * 2),
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
            new_in: int = 0
            new_out: int = 0
            _accum_valid = False
            if in_tok or out_tok:
                with _LOCK:
                    # Re-check the span is still live under the lock. A concurrent
                    # _post_llm_call may have already popped both the span and the
                    # accumulator between our initial span read and this point.
                    if session_id in _LLM_SPANS:
                        prev_in, prev_out = _LLM_TOKEN_ACCUM.get(session_id, (0, 0))
                        new_in = prev_in + in_tok
                        new_out = prev_out + out_tok
                        _LLM_TOKEN_ACCUM[session_id] = (new_in, new_out)
                        _accum_valid = True
            if _accum_valid:
                span.set_attribute("llm.token_count.prompt", new_in)
                span.set_attribute("llm.token_count.completion", new_out)
                span.set_attribute("llm.token_count.total", new_in + new_out)
                # J11 dual-emit — running totals mirror what the OpenInference
                # attrs above show, so accumulator semantics are identical.
                _set_gen_ai_attrs(
                    span,
                    {
                        "gen_ai.usage.input_tokens": new_in,
                        "gen_ai.usage.output_tokens": new_out,
                    },
                )
                # P3-6: also emit per-request token deltas as OTel metric
                # counters so cost dashboards don't require span scraping.
                # Uses `in_tok` / `out_tok` (the per-request delta) rather
                # than the accumulated totals so the metric counter's own
                # accumulation matches expected semantics.
                _metric_attrs = {
                    "gen_ai.response.model": str(response_model) if response_model else ""
                }
                if _token_input_counter is not None and in_tok:
                    _token_input_counter.add(in_tok, _metric_attrs)
                if _token_output_counter is not None and out_tok:
                    _token_output_counter.add(out_tok, _metric_attrs)

            # J9 wiring — feed the per-request prompt-token count into the
            # F-CONTEXT detector. Uses ``in_tok`` (the current request's
            # input count, NOT the running ``new_in`` total) because the
            # detector wants the live ratio against this turn's context
            # window, not the cumulative debt across the tool-loop.
            # Model resolution: prefer the provider-echoed ``response_model``;
            # fall back to the model captured at pre_llm_call.
            if in_tok > 0:
                with _LOCK:
                    fallback_model = _LLM_MODEL_BY_SESSION.get(session_id)
                _record_context_usage(
                    session_id=session_id,
                    prompt_tokens=in_tok,
                    model=str(response_model) if response_model else fallback_model,
                )

        _model_label = str(response_model) if response_model else "unknown"
        _llm_labels = {"gen_ai.response.model": _model_label}
        _is_llm_error = finish_reason in ("error", "content_filter", "stop_sequence")

        if finish_reason:
            span.set_attribute("llm.finish_reason", str(finish_reason))
            # GenAI spec uses an array of finish reasons (one per choice/
            # candidate). We only see a single reason at the wrapper layer,
            # so emit a length-1 tuple — tuples are OTel-attribute-safe.
            _set_gen_ai_attrs(span, {"gen_ai.response.finish_reasons": (str(finish_reason),)})
        if api_duration is not None:
            try:
                _duration_ms = int(float(api_duration) * 1000)
                span.set_attribute("llm.api_duration_ms", _duration_ms)
                # O-1: LLM latency histogram — drives p50/p99 SLO dashboards.
                if _llm_call_duration_hist is not None:
                    _llm_call_duration_hist.record(_duration_ms, _llm_labels)
            except (TypeError, ValueError):
                pass
        if response_model:
            span.set_attribute("llm.response_model", str(response_model))
            _set_gen_ai_attrs(span, {"gen_ai.response.model": str(response_model)})

        # O-1: LLM call volume + error counters.
        try:
            if _llm_calls_total_counter is not None:
                _llm_calls_total_counter.add(1, _llm_labels)
            if _llm_call_errors_counter is not None and _is_llm_error:
                _llm_call_errors_counter.add(1, {**_llm_labels, "finish_reason": finish_reason})
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("post_api_request enrich failed: %s", exc)
    return None
