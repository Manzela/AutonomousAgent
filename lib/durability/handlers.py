"""Production implementations for the failure-matrix handler dispatch layer.

Currently implemented (5 handlers):

* :func:`retry_with_backoff` — SELF_HEAL baseline (exp backoff + jitter)
* :func:`halt_alert_snapshot` — FAIL_LOUD baseline (snapshot + Telegram + BLOCKED)
* :func:`fallback_local_log` — FAIL_SOFT baseline (JSONL forensic record)
* :func:`interrupt_with_loop_feedback` — F34 (F-LOOP) — builds an injectable
  loop-break message for the orchestrator + writes forensic JSONL
* :func:`escalate_context_pressure` — F36 (F-CONTEXT) — Telegram escalation +
  orchestrator hint to force compaction, FAIL_SOFT (NOT a BLOCKED transition)

All other named handlers from ``FAILURE_MATRIX`` are auto-stubbed via
:func:`_make_stub`. Stubs delegate to the baseline that matches the F-code's
trichotomy class:

* ``SELF_HEAL`` → :func:`retry_with_backoff`
* ``FAIL_SOFT`` → :func:`fallback_local_log`
* ``FAIL_LOUD`` → :func:`halt_alert_snapshot`

Each stub emits a WARNING log identifying the unimplemented handler name so
operators can prioritize which stub to replace with real behavior.

Audit reference: ``audit/2026-05-19-resume-orchestration/audit-plan.md`` P0-5,
risk register R4 ("matrix names 16 handlers; none implemented") — closed.
F34/F36 promotions from auto-stub to production: Task #56.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from lib.durability.failure_matrix import FAILURE_MATRIX, TrichotomyClass, lookup
from lib.durability.trichotomy import backoff_delay

logger = logging.getLogger(__name__)


# Volume `hermes-data` is mounted at /data per deploy/docker-compose.yml.
# Override via HERMES_LOCAL_LOG_DIR for tests or alternative deployments.
DEFAULT_LOCAL_LOG_DIR = Path(os.environ.get("HERMES_LOCAL_LOG_DIR", "/data/local_logs"))


@dataclass
class HandlerResult:
    """What the caller should do next after a handler runs.

    Attributes
    ----------
    action:
        ``"retry"`` — operation should be retried after ``delay_ms``.
        ``"halt"`` — operation has been halted (task BLOCKED, alert sent).
        ``"continue"`` — operation should continue with degraded fidelity.
    delay_ms:
        For ``action="retry"``, how long the caller should sleep before
        re-attempting the operation.
    f_code:
        The F-code that produced this result.
    handler:
        Name of the handler that produced this result.
    message:
        Optional human-readable message (e.g. the Telegram alert body).
    """

    action: str
    delay_ms: int = 0
    f_code: Optional[str] = None
    handler: Optional[str] = None
    message: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Baseline handler implementations (3)
# ----------------------------------------------------------------------


def retry_with_backoff(
    f_code: str,
    *,
    attempt: int = 1,
    base_delay_ms: int = 500,
    max_delay_ms: int = 30000,
    jitter_range_pct: int = 25,
    **_: Any,
) -> HandlerResult:
    """Self-heal by retrying with exponential backoff + jitter.

    The delay formula matches ``docs/architecture/failure-matrix.md`` §4
    and reuses :func:`lib.durability.trichotomy.backoff_delay`. Callers are
    expected to sleep for ``delay_ms`` before re-attempting the operation.
    """
    delay = backoff_delay(
        attempt=attempt,
        base_ms=base_delay_ms,
        max_ms=max_delay_ms,
        jitter_pct=jitter_range_pct,
    )
    logger.info(
        "handlers.retry_with_backoff f_code=%s attempt=%d delay_ms=%d",
        f_code,
        attempt,
        delay,
    )
    return HandlerResult(
        action="retry",
        delay_ms=delay,
        f_code=f_code,
        handler="retry_with_backoff",
    )


def halt_alert_snapshot(
    f_code: str,
    *,
    error: Optional[BaseException] = None,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    card_id: Optional[Any] = None,
    checkpoint: Any = None,
    state: Optional[Dict[str, Any]] = None,
    **_: Any,
) -> HandlerResult:
    """Fail-loud: snapshot state, alert Telegram, transition card to BLOCKED.

    All three side effects are isolated in their own try/except so a partial
    failure of one (e.g. Telegram down) does not block the others. This is
    the same fail-open posture used throughout ``lib.kanban.telegram_bridge``.
    """
    entry = FAILURE_MATRIX.get(f_code, {})
    description = entry.get("description", "unclassified")
    err_msg = f"{type(error).__name__}: {error}" if error else "no exception attached"

    # 1. Snapshot — write checkpoint if a Checkpoint instance was passed.
    if checkpoint is not None and state is not None:
        try:
            step = int(state.get("step", 0)) if isinstance(state, dict) else 0
            checkpoint.maybe_write(step=step, state=state)
            logger.info("handlers.halt_alert_snapshot f_code=%s checkpoint written", f_code)
        except Exception as exc:  # noqa: BLE001 — fail-open by design
            logger.warning(
                "handlers.halt_alert_snapshot checkpoint write failed f_code=%s err=%s",
                f_code,
                exc,
            )

    # 2. Telegram alert.
    msg = (
        f"🚨 HALT — {f_code} ({description})\n"
        f"session={session_id or 'n/a'} task={task_id or 'n/a'}\n"
        f"error: {err_msg}"
    )
    try:
        from lib.kanban.telegram_bridge import send_alert

        send_alert(card_id, msg)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "handlers.halt_alert_snapshot Telegram failed f_code=%s err=%s msg=%r",
            f_code,
            exc,
            msg,
        )

    # 3. Transition card to BLOCKED.
    if session_id or task_id:
        try:
            from lib.kanban.telegram_bridge import update_card_status

            update_card_status(session_id=session_id, status="blocked")
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning(
                "handlers.halt_alert_snapshot card transition failed f_code=%s err=%s",
                f_code,
                exc,
            )

    return HandlerResult(
        action="halt",
        f_code=f_code,
        handler="halt_alert_snapshot",
        message=msg,
    )


def fallback_local_log(
    f_code: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    log_dir: Optional[Path] = None,
    **_: Any,
) -> HandlerResult:
    """Fail-soft: degrade to local JSONL when a remote target is unreachable.

    Writes one JSON line per failure to
    ``<log_dir>/<UTC date>/<f_code lowercased>.jsonl``. Each line carries the
    F-code, an ISO-8601 UTC timestamp, and any payload the caller attached
    (e.g. an OTel span dict that couldn't be exported).
    """
    target_dir = Path(log_dir) if log_dir else DEFAULT_LOCAL_LOG_DIR
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = target_dir / today
    out_path = out_dir / f"{f_code.lower()}.jsonl"

    record = {
        "f_code": f_code,
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "payload": payload or {},
    }

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        logger.debug(
            "handlers.fallback_local_log f_code=%s wrote %s",
            f_code,
            out_path,
        )
    except Exception as exc:  # noqa: BLE001 — last-resort, log to stderr
        logger.error(
            "handlers.fallback_local_log f_code=%s write failed err=%s record=%s",
            f_code,
            exc,
            record,
        )

    return HandlerResult(
        action="continue",
        f_code=f_code,
        handler="fallback_local_log",
    )


# ----------------------------------------------------------------------
# F34 — interrupt_with_loop_feedback (F-LOOP, FAIL_SOFT)
# ----------------------------------------------------------------------


# Public so tests + the orchestrator can build the same string deterministically
# without relying on the formatted message in the HandlerResult.
LOOP_FEEDBACK_TEMPLATE = (
    "[Loop break] You have called `{tool_name}` {repeat_count} times in a row "
    "with identical arguments. Repeated identical calls almost never produce "
    "new information. Try one of: (1) materially different arguments, "
    "(2) a different tool, (3) summarize what you have learned so far and move "
    "to the next subtask, or (4) stop and explain why the task is blocked. If "
    "the repetition is intentional (e.g. polling), state that intent explicitly "
    "in your next message so this loop guard does not fire again."
)


def interrupt_with_loop_feedback(
    f_code: str,
    *,
    session_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    repeat_count: Optional[int] = None,
    fingerprint: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    log_dir: Optional[Path] = None,
    **_: Any,
) -> HandlerResult:
    """F-LOOP handler — build an injectable loop-break message + persist forensics.

    Output contract (consumed by the orchestrator's per-turn loop):

    * ``result.action == "continue"`` (FAIL_SOFT — never halts the session)
    * ``result.message`` carries the human-/model-readable loop-break feedback
      string. The orchestrator SHOULD inject this as a synthetic system or
      user message into the next agent turn so the model has a chance to
      change behavior before the loop guard fires again.
    * ``result.extra["loop_break_feedback"]`` mirrors ``result.message`` so a
      caller that only reads ``extra`` (e.g. structured logging consumers)
      can still pick it up.
    * ``result.extra["tool_name"]`` / ``["repeat_count"]`` / ``["fingerprint"]``
      surface the detector's evidence so a dashboard can group repeat
      offenders by tool.

    Side effects (each isolated in its own try/except — fail-open):

    1. WARNING log line identifying tool + repeat count.
    2. JSONL forensic record via :func:`fallback_local_log` so the same
       on-disk trail every FAIL_SOFT handler writes is preserved. The
       payload contains the loop fingerprint + repeat count + feedback
       text — operators auditing a session can reconstruct what the model
       was told without re-running the orchestrator.

    No Telegram alert by default — F-LOOP is FAIL_SOFT and self-correcting;
    paging an operator on every loop is alert fatigue. The orchestrator can
    layer an alert on top if N loop-breaks fire in a session window.
    """
    # Provide safe defaults for the formatter so a sparse dispatch (e.g. from a
    # detector that doesn't yet pass tool_name) still produces a coherent
    # message rather than crashing on a KeyError.
    tool_display = tool_name or "<unknown tool>"
    count_display = repeat_count if repeat_count is not None else "N"

    feedback = LOOP_FEEDBACK_TEMPLATE.format(
        tool_name=tool_display,
        repeat_count=count_display,
    )

    logger.warning(
        "handlers.interrupt_with_loop_feedback f_code=%s session=%s tool=%s "
        "repeat_count=%s fingerprint=%s",
        f_code,
        session_id or "n/a",
        tool_display,
        count_display,
        fingerprint or "n/a",
    )

    # Persist a forensic record. We deliberately reuse fallback_local_log
    # rather than open a parallel file format so the on-disk schema stays
    # uniform across every FAIL_SOFT handler.
    forensic_payload = {
        "session_id": session_id,
        "tool_name": tool_name,
        "repeat_count": repeat_count,
        "fingerprint": fingerprint,
        "loop_break_feedback": feedback,
    }
    if payload:
        forensic_payload["detector_payload"] = payload
    try:
        fallback_local_log(f_code, payload=forensic_payload, log_dir=log_dir)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "handlers.interrupt_with_loop_feedback forensic log failed f_code=%s err=%s",
            f_code,
            exc,
        )

    return HandlerResult(
        action="continue",
        f_code=f_code,
        handler="interrupt_with_loop_feedback",
        message=feedback,
        extra={
            "loop_break_feedback": feedback,
            "tool_name": tool_name,
            "repeat_count": repeat_count,
            "fingerprint": fingerprint,
        },
    )


# ----------------------------------------------------------------------
# F36 — escalate_context_pressure (F-CONTEXT, FAIL_SOFT)
# ----------------------------------------------------------------------


CONTEXT_PRESSURE_TEMPLATE = (
    "[Context pressure] {ratio_pct:.1f}% of {model}'s {context_length:,}-token "
    "context window is consumed by prompt tokens ({prompt_tokens:,}). Upstream "
    "compaction is presumed ineffective (either it ran and got rolled back by "
    "the anti-thrashing guard, or it never fired). Recommended next actions: "
    "(1) summarize-and-discard older turns, (2) request `/new` from the "
    "operator, (3) hard-truncate retrieval results before the next tool call."
)


def escalate_context_pressure(
    f_code: str,
    *,
    session_id: Optional[str] = None,
    prompt_tokens: Optional[int] = None,
    context_length: Optional[int] = None,
    model: Optional[str] = None,
    card_id: Optional[Any] = None,
    payload: Optional[Dict[str, Any]] = None,
    log_dir: Optional[Path] = None,
    **_: Any,
) -> HandlerResult:
    """F-CONTEXT handler — escalate via Telegram + forensic log + orchestrator hint.

    Accepts the context measurements either as explicit kwargs or inside a
    ``payload`` dict. The observability shim that fires this F-code
    (:mod:`lib.observability` ``_record_context_usage``) currently dispatches
    via ``payload={"model": ..., "prompt_tokens": ..., "context_length": ...}``
    so both forms are first-class.

    Output contract:

    * ``result.action == "continue"`` (FAIL_SOFT — the model can keep running,
      but every subsequent turn risks provider-side hard truncation).
    * ``result.message`` carries the escalation text — suitable for both
      operator-facing Telegram and as a synthetic system message the
      orchestrator can inject into the next turn.
    * ``result.extra["context_pressure"] == True`` is a stable boolean flag
      a Hermes loop layer can check to force a compaction pass before the
      next model call.
    * ``result.extra["ratio"]`` carries the floating-point ratio so a
      dashboard / alert pipeline can threshold on it without re-parsing the
      message.

    Side effects (each isolated):

    1. WARNING log line.
    2. Telegram alert via :func:`lib.kanban.telegram_bridge.send_alert` —
       importantly NOT a halt or card-status transition (FAIL_SOFT).
    3. JSONL forensic record via :func:`fallback_local_log`.
    """
    # Pull missing kwargs from the payload dict — supports both calling
    # conventions used in production.
    payload = payload or {}
    if prompt_tokens is None:
        prompt_tokens = payload.get("prompt_tokens")
    if context_length is None:
        context_length = payload.get("context_length")
    if model is None:
        model = payload.get("model")

    # Compute the ratio defensively. The detector should only fire above the
    # warn threshold so the inputs are normally well-formed, but a stray
    # dispatch with missing/zero context_length must not crash the handler.
    try:
        pt = int(prompt_tokens) if prompt_tokens is not None else 0
        cl = int(context_length) if context_length is not None else 0
    except (TypeError, ValueError):
        pt, cl = 0, 0
    ratio = (pt / cl) if cl > 0 else 0.0

    model_display = model or "<unknown model>"
    if cl > 0 and pt > 0:
        message = CONTEXT_PRESSURE_TEMPLATE.format(
            ratio_pct=ratio * 100,
            model=model_display,
            context_length=cl,
            prompt_tokens=pt,
        )
    else:
        # Degenerate inputs — still emit a coherent message rather than
        # a half-rendered template.
        message = (
            f"[Context pressure] F-CONTEXT escalation fired for session="
            f"{session_id or 'n/a'} model={model_display} but inputs were "
            f"incomplete (prompt_tokens={prompt_tokens!r}, "
            f"context_length={context_length!r}). Investigate detector wiring."
        )

    logger.warning(
        "handlers.escalate_context_pressure f_code=%s session=%s model=%s "
        "prompt_tokens=%s context_length=%s ratio=%.3f",
        f_code,
        session_id or "n/a",
        model_display,
        prompt_tokens,
        context_length,
        ratio,
    )

    # Telegram — best-effort, fail-open. Note: no update_card_status here.
    # F-CONTEXT is FAIL_SOFT; blocking the card on every escalation would
    # collapse the agent into halt-loud territory.
    try:
        from lib.kanban.telegram_bridge import send_alert

        send_alert(card_id, message)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "handlers.escalate_context_pressure Telegram failed f_code=%s err=%s",
            f_code,
            exc,
        )

    # Forensic JSONL record.
    forensic_payload = {
        "session_id": session_id,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "context_length": context_length,
        "ratio": ratio,
        "escalation_message": message,
    }
    if payload:
        forensic_payload["detector_payload"] = payload
    try:
        fallback_local_log(f_code, payload=forensic_payload, log_dir=log_dir)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "handlers.escalate_context_pressure forensic log failed f_code=%s err=%s",
            f_code,
            exc,
        )

    return HandlerResult(
        action="continue",
        f_code=f_code,
        handler="escalate_context_pressure",
        message=message,
        extra={
            "context_pressure": True,
            "ratio": ratio,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "context_length": context_length,
            "recommended_action": "force_compaction_or_request_new_session",
        },
    )


# ----------------------------------------------------------------------
# Stub registrations for the remaining named handlers
# ----------------------------------------------------------------------


def _make_stub(handler_name: str) -> Callable[..., HandlerResult]:
    """Build a stub handler that delegates to a baseline based on F-code class.

    The returned callable logs a WARNING identifying the unimplemented
    handler name so operators can prioritize which stub to replace with real
    behavior next.
    """

    def stub(f_code: str, **kwargs: Any) -> HandlerResult:
        entry = FAILURE_MATRIX.get(f_code, {})
        cls = entry.get("class")
        logger.warning(
            "handlers.STUB %s not implemented for f_code=%s; "
            "delegating based on trichotomy class=%s",
            handler_name,
            f_code,
            cls,
        )
        if cls == TrichotomyClass.SELF_HEAL:
            return retry_with_backoff(f_code, **kwargs)
        if cls == TrichotomyClass.FAIL_SOFT:
            return fallback_local_log(f_code, **kwargs)
        return halt_alert_snapshot(f_code, **kwargs)

    stub.__name__ = f"stub_{handler_name}"
    stub.__qualname__ = stub.__name__
    return stub


# Discover all distinct handler names referenced in the matrix.
_DISTINCT_HANDLER_NAMES = {entry["handler"] for entry in FAILURE_MATRIX.values()}

HANDLER_REGISTRY: Dict[str, Callable[..., HandlerResult]] = {
    "retry_with_backoff": retry_with_backoff,
    "halt_alert_snapshot": halt_alert_snapshot,
    "fallback_local_log": fallback_local_log,
    "interrupt_with_loop_feedback": interrupt_with_loop_feedback,
    "escalate_context_pressure": escalate_context_pressure,
}

# Register stubs for everything else.
for _name in _DISTINCT_HANDLER_NAMES:
    if _name not in HANDLER_REGISTRY:
        HANDLER_REGISTRY[_name] = _make_stub(_name)


# ----------------------------------------------------------------------
# Dispatch entrypoint
# ----------------------------------------------------------------------


def dispatch(f_code: str, **kwargs: Any) -> HandlerResult:
    """Dispatch an F-code to its registered handler.

    Unknown F-codes route to F33 (the matrix's "unclassified exception"
    catch-all) which itself dispatches to ``halt_alert_snapshot``. A guard
    on the recursion depth prevents an infinite loop if F33 ever gets
    delisted from the matrix.
    """
    try:
        entry = lookup(f_code)
    except KeyError:
        logger.warning("handlers.dispatch unknown f_code=%s; routing to F33", f_code)
        if f_code == "F33":
            # Defensive — F33 should always exist; if not, halt synchronously.
            return halt_alert_snapshot("F33", **kwargs)
        return dispatch("F33", **kwargs)

    handler_name = entry["handler"]
    handler = HANDLER_REGISTRY.get(handler_name)
    if handler is None:
        logger.error(
            "handlers.dispatch no callable registered for handler=%r f_code=%s; "
            "falling back to halt_alert_snapshot",
            handler_name,
            f_code,
        )
        return halt_alert_snapshot(f_code, **kwargs)
    return handler(f_code, **kwargs)
