"""Baseline implementations for the failure-matrix handler dispatch layer.

Implements the three highest-frequency handlers (``retry_with_backoff``,
``halt_alert_snapshot``, ``fallback_local_log``) and registers stubs for
the remaining named handlers so every entry in ``failure_matrix.FAILURE_MATRIX``
dispatches to a callable.

Stubs delegate to a sane default based on the F-code's trichotomy class:

* ``SELF_HEAL`` → :func:`retry_with_backoff`
* ``FAIL_SOFT`` → :func:`fallback_local_log`
* ``FAIL_LOUD`` → :func:`halt_alert_snapshot`

Each stub emits a WARNING log identifying the unimplemented handler name so
operators can prioritize which stub to replace with real behavior.

Audit reference: ``audit/2026-05-19-resume-orchestration/audit-plan.md`` P0-5,
risk register R4 ("matrix names 16 handlers; none implemented").
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
