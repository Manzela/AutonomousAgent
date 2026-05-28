"""P1-5 declarative notification policy.

The status-transition → user-facing-message table is locked by
``docs/superpowers/specs/2026-05-15-phase1-design-alignment.md`` lines
362-370 (notification policy table). A transition that maps to ``None``
is intentionally silent (e.g. OTel heartbeats cover the noisy
``ready → running`` flip; ``→ archived`` is silent because the
user-initiated ``/cancel`` echoes elsewhere).

Hermes' status enum is the canonical vocabulary
(``triage, todo, ready, running, blocked, done, archived``). We
additionally honour the synthetic destination ``"failure"`` to mean
"any non-success terminal outcome that increments the
consecutive-failure counter" — Hermes has separate
``timed_out``/``crashed``/``failed`` outcomes that all roll into the
same ``consecutive_failures`` counter, and the bridge handler can
collapse them onto ``"failure"`` before calling this function.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple


def _started(card: Any) -> str:
    return f"Started: {getattr(card, 'title', '')}"


def _blocked(card: Any) -> str:
    # ``last_failure_error`` is what Hermes records when the worker reports a
    # blocking condition; spec L367 calls this the "reason".
    reason = (
        getattr(card, "last_failure_error", None) or getattr(card, "body", None) or "unspecified"
    )
    card_id = getattr(card, "id", "?")
    return f"Blocked on: {reason}. Use `/resume {card_id}` to unblock"


def _done(card: Any) -> str:
    title = getattr(card, "title", "")
    summary = getattr(card, "result", None) or "(no summary)"
    return f"Done: {title}\n\nResult: {summary}"


def _failure(card: Any) -> str:
    card_id = getattr(card, "id", "?")
    count = getattr(card, "consecutive_failures", 0) or 0
    err = getattr(card, "last_failure_error", None) or "unknown error"
    return f"Card {card_id} failed: {count}x — {err}"


# (from_status, to_status) → renderer
_POLICY: Dict[Tuple[str, str], Optional[Callable[[Any], str]]] = {
    ("triage", "todo"): None,  # silent
    ("todo", "ready"): _started,
    ("ready", "running"): None,  # silent (OTel heartbeat-only)
    ("running", "blocked"): _blocked,  # PRIORITY ALERT
    ("running", "done"): _done,
    ("running", "failure"): _failure,  # ALERT
}


# Special-case: any status → "archived" is silent.
_ARCHIVED_SINK = "archived"


def status_transition_to_notification(
    from_status: str,
    to_status: str,
    card: Any,
) -> Optional[str]:
    """Return the Telegram message string for a status transition, or None for silent.

    A return value of ``None`` means the bridge should NOT send anything.
    A non-empty string is sent verbatim. Unknown / unmapped transitions
    return ``None`` (defensive — silence is safer than spamming the
    user with chatter we don't have a policy for).
    """
    if to_status == _ARCHIVED_SINK:
        return None
    renderer = _POLICY.get((from_status, to_status))
    if renderer is None:
        return None
    return renderer(card)


__all__ = ["status_transition_to_notification"]
