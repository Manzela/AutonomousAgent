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

Hook signatures match the kwargs Hermes' ``invoke_hook`` passes (see
``hermes-agent/hermes_cli/plugins.py`` ``VALID_HOOKS`` + the call sites
in ``run_agent.py`` / ``model_tools.py``). All callbacks are defensive:
unexpected kwargs are absorbed via ``**_``, and any internal failure
returns ``None`` so the per-hook try/except in ``invoke_hook`` is
preserved as a true fail-open path.
"""

from __future__ import annotations

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


def register(ctx: Any) -> None:
    """Hermes plugin entry point.

    Wires the five hooks unconditionally — when ``setup_tracing`` failed
    (no OTel SDK) the hooks become cheap no-ops because ``_tracer`` is
    ``None`` and each handler short-circuits.
    """
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_tool_call", _pre_tool_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_llm_call", _post_llm_call)
    logger.info(
        "observability: %d hooks registered, tracing_ok=%s",
        5,
        _TRACING_OK,
    )


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
    """
    if _tracer is None:
        return None
    try:
        span = _tracer.start_span("tool.dispatch")
        if tool_name:
            span.set_attribute("tool.name", str(tool_name))
        if task_id:
            span.set_attribute("task.id", str(task_id))
        if session_id:
            span.set_attribute("session.id", str(session_id))
        if tool_call_id:
            span.set_attribute("tool_call.id", str(tool_call_id))
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
    """
    if _tracer is None or not session_id:
        return None
    try:
        span = _tracer.start_span("model.call")
        span.set_attribute("session.id", str(session_id))
        if model:
            span.set_attribute("model", str(model))
        if platform:
            span.set_attribute("platform", str(platform))
        if is_first_turn is not None:
            span.set_attribute("is_first_turn", bool(is_first_turn))
        with _LOCK:
            _LLM_SPANS[session_id] = (span, None)
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
        if entry is None:
            return None
        span, _ = entry
        if assistant_response is not None:
            try:
                span.set_attribute("response.length", len(str(assistant_response)))
            except Exception:  # noqa: BLE001
                pass
        span.end()
    except Exception as exc:  # noqa: BLE001
        logger.debug("model.call end failed: %s", exc)
    return None
