"""Failure classifier + retry policy. Consumes config/limits.yaml retries.self_heal.*.

Hook signatures match the kwargs Hermes' ``invoke_hook`` passes (see
``hermes-agent/hermes_cli/plugins.py`` ``invoke_hook`` + ``get_pre_tool_call_block_message``
+ the ``post_tool_call`` call site in ``hermes-agent/model_tools.py``). All callbacks are
defensive: unexpected kwargs are absorbed via ``**_``, and any internal failure returns
``None`` so the per-hook try/except in ``invoke_hook`` is preserved as a true fail-open path.
"""

import logging
import random
import re
from typing import Any, Dict, Optional

from lib.durability.failure_matrix import lookup, TrichotomyClass

logger = logging.getLogger(__name__)


# Pattern-based classifier — order matters: more specific patterns first.
_CLASSIFIERS = [
    (re.compile(r"rate.?limit|429|too many requests", re.I), "F1"),
    (re.compile(r"timed? out|timeout|deadline exceeded", re.I), "F2"),
    (re.compile(r"name or service not known|dns|nxdomain", re.I), "F3"),
    (re.compile(r"5\d\d|internal server error|bad gateway", re.I), "F4"),
    (re.compile(r"connection reset", re.I), "F5"),
    (re.compile(r"sandbox.*(crash|exit)", re.I), "F6"),
    (re.compile(r"chroma.*unavailable", re.I), "F7"),
    (re.compile(r"vertex.*(auth|credentials)|invalid token", re.I), "F8"),
    (re.compile(r"claim.?lock|claim contention", re.I), "F9"),
    (re.compile(r"checkpoint.*(contention|locked)", re.I), "F10"),
    (re.compile(r"max_tokens too low|thinking tokens truncated", re.I), "F11"),
    (re.compile(r"chroma.*down", re.I), "F12"),
    (re.compile(r"otel.*unreachable", re.I), "F13"),
    # F14 — Github MCP server unavailable. The MCP SDK surfaces many
    # different transport-failure shapes (session GC'd, transport closed,
    # OAuth 401, connection torn down). Mapped to F14 (FAIL_SOFT,
    # skip_tool_class) so the agent gracefully drops github-tagged tools
    # for the rest of the session instead of halting.
    #
    # Source for these substrings: hermes-agent/tools/mcp_tool.py
    # _SESSION_EXPIRED_SUBSTRINGS (line 1838) + _is_auth_error path.
    (re.compile(r"github.?mcp.*(unavailable|unauthorized|forbidden)", re.I), "F14"),
    (
        re.compile(
            r"mcp.*(session (terminated|expired|not found)|expired session|transport is closed)",
            re.I,
        ),
        "F14",
    ),
    (re.compile(r"mcp.*(connection (closed|lost|refused)|broken pipe)", re.I), "F14"),
    (re.compile(r"closedresourceerror", re.I), "F14"),
    # P1-2: supplementary F14 patterns extracted from real hermes container
    # logs (audit pass-2). The patterns above require "mcp" or "github mcp"
    # to precede the keyword, but production transport errors are emitted
    # in several other shapes:
    # - asyncio TaskGroup unwrap (MCP SDK uses anyio task groups internally)
    # - httpx 401 with the URL trailing the verb
    # - bare "Session terminated" from context7 transport reset
    (re.compile(r"unhandled errors in a taskgroup", re.I), "F14"),
    (
        re.compile(
            r"401 unauthorized.*github.?mcp|github.?mcp.*401 unauthorized",
            re.I,
        ),
        "F14",
    ),
    (re.compile(r"session terminated", re.I), "F14"),
    (re.compile(r"skill.?extractor.*fail", re.I), "F15"),
    (re.compile(r"judge.*timeout", re.I), "F16"),
    (re.compile(r"daily.*budget.*exceeded", re.I), "F21"),
    (re.compile(r"secret.?leak|REDACTED:critical", re.I), "F22"),
    (re.compile(r"sandbox.*escape", re.I), "F23"),
    (re.compile(r"consensus.*(fail|split)", re.I), "F24"),
    (re.compile(r"clarification.*max.*questions", re.I), "F25"),
    (re.compile(r"3.?strike|consecutive_rejections.*3", re.I), "F26"),
    (re.compile(r"disk full|no space left", re.I), "F28"),
    (re.compile(r"kanban.*(corrupt|migration)", re.I), "F29"),
    (re.compile(r"approval.*required.*without", re.I), "F30"),
    (re.compile(r"egress.*denied|allowlist.*violation", re.I), "F31"),
]


def classify(err: Exception) -> str:
    """Classify an exception to an F-code. Falls through to F33 (fail-loud unknown)."""
    msg = f"{type(err).__name__}: {err}"
    for pat, code in _CLASSIFIERS:
        if pat.search(msg):
            return code
    return "F33"


def trichotomy_class(err: Exception) -> TrichotomyClass:
    return lookup(classify(err))["class"]


def backoff_delay(
    attempt: int, base_ms: int = 500, max_ms: int = 30000, jitter_pct: int = 25
) -> int:
    """Exponential backoff with jitter. attempt is 1-indexed.

    Final delay is clamped to [0, max_ms] *after* applying jitter so the
    contract that ``delay <= max_ms`` holds for any attempt count.
    """
    raw = base_ms * (2 ** (attempt - 1))
    raw = min(raw, max_ms)
    jitter = raw * (jitter_pct / 100.0)
    delay = raw + random.uniform(-jitter, jitter)
    return max(0, min(int(delay), max_ms))


def before_tool_call(
    tool_name: Optional[str] = None,
    args: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    **_: Any,
) -> None:
    """Hermes ``pre_tool_call`` hook. Currently no-op; reserved.

    Signature matches Hermes' ``invoke_hook("pre_tool_call", tool_name=..., args=...,
    task_id=..., session_id=..., tool_call_id=...)`` at ``hermes-agent/hermes_cli/plugins.py:1408``
    inside ``get_pre_tool_call_block_message``. Unknown future kwargs are absorbed by ``**_``.
    """
    return None


def after_tool_call(
    tool_name: Optional[str] = None,
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
    **_: Any,
) -> None:
    """Hermes ``post_tool_call`` hook. Classifies errors, emits span, dispatches handler.

    Signature matches Hermes' ``invoke_hook("post_tool_call", tool_name=..., args=...,
    result=..., task_id=..., session_id=..., tool_call_id=..., duration_ms=...)`` at
    ``hermes-agent/model_tools.py`` (see the ``post_tool_call`` dispatch site).

    Hermes passes the tool's return value (or the caught exception) as ``result``. We
    only act when ``result`` is an ``Exception`` instance — success paths are no-ops.

    Behavior on error:

    1. Classify via :func:`classify` → F-code.
    2. Emit a ``durability.classify`` OTel span (so Phoenix shows the F-code next to
       ``tool.dispatch``).
    3. **Dispatch the matrix handler** for ``FAIL_SOFT`` / ``FAIL_LOUD`` classes
       (visible side-effects: alerts, JSONL fallback, card→BLOCKED). ``SELF_HEAL``
       is intentionally skipped here — Hermes' own per-tool retry loop owns that
       path, and dispatching ``retry_with_backoff`` from a fire-and-forget hook
       has no consumer for the returned delay. Closes audit P0-7.
    """
    if not isinstance(result, Exception):
        return None

    code = classify(result)
    try:
        cls = lookup(code)["class"]
    except Exception as exc:  # noqa: BLE001
        logger.debug("trichotomy lookup failed for code=%s: %s", code, exc)
        return None

    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("hermes.durability")
        with tracer.start_as_current_span("durability.classify") as span:
            span.set_attribute("f_code", code)
            span.set_attribute("trichotomy_class", cls.value)
            if tool_name:
                span.set_attribute("tool.name", str(tool_name))
            if session_id:
                span.set_attribute("session.id", str(session_id))
            if tool_call_id:
                span.set_attribute("tool_call.id", str(tool_call_id))
            span.set_attribute("error.type", type(result).__name__)
    except ImportError:
        # OTel SDK absent — F-code still classified above; downstream consumers
        # that don't need a span (e.g. unit tests) just won't see one.
        pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("durability.classify span emit failed: %s", exc)

    if cls in (TrichotomyClass.FAIL_SOFT, TrichotomyClass.FAIL_LOUD):
        try:
            from lib.durability.handlers import dispatch

            dispatch(
                code,
                error=result,
                tool_name=tool_name,
                task_id=task_id,
                session_id=session_id,
                tool_call_id=tool_call_id,
                payload={"tool_name": tool_name, "error_type": type(result).__name__},
            )
        except Exception as exc:  # noqa: BLE001 — hook must never throw upstream
            logger.warning(
                "trichotomy.after_tool_call dispatch failed f_code=%s err=%s",
                code,
                exc,
            )
    return None
