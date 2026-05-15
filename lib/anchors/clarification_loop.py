"""Clarification loop state machine — drives TaskSpec from draft to locked.

Hybrid circuit-breaker per spec §P1-1: locks when ANY of confidence threshold
met, question budget exhausted, or user silence threshold exceeded. Escalates
to Telegram if a draft_locked spec stays silent past the escalation threshold.

All thresholds are module-level constants (production reads them from
limits.yaml.anchors.*).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

# Defaults; production reads these from limits.yaml.anchors.*
MAX_CLARIFICATION_QUESTIONS = 6
LOCK_CONFIDENCE_THRESHOLD = 0.85
DRAFT_SILENCE_LOCK_H = 4
DRAFT_LOCKED_SILENCE_ESCALATE_H = 24


ActionKind = Literal["ask_next", "lock", "draft_lock", "escalate", "noop"]


@dataclass
class ClarificationState:
    questions_asked: int
    last_user_msg_at: datetime
    confidence: float
    is_draft_locked: bool = False


@dataclass
class Action:
    kind: ActionKind
    reason: str = ""


def decide_next_action(state: ClarificationState, *, now: datetime) -> Action:
    """Decide what the clarification loop should do next.

    Order matters: escalation first (highest priority), then high-confidence
    lock (overrides draft_locked), then noop-if-already-draft_locked, then
    budget/silence draft_lock triggers, then ask_next fallback.
    """
    # Clock-skew clamp: silence can't be negative (would break silence checks
    # in mocked-time tests or under NTP drift)
    silence_h = max(0.0, (now - state.last_user_msg_at).total_seconds() / 3600)

    # Escalation: draft_locked + 24h silence (Fail-Loud per F-matrix)
    if state.is_draft_locked and silence_h > DRAFT_LOCKED_SILENCE_ESCALATE_H:
        return Action("escalate", f"silent for {silence_h:.1f}h in draft_locked state")

    # Lock at high confidence (overrides draft_locked status)
    if state.confidence >= LOCK_CONFIDENCE_THRESHOLD:
        return Action("lock", f"confidence {state.confidence:.2f} >= {LOCK_CONFIDENCE_THRESHOLD}")

    # Already draft_locked and not escalating → noop (waiting on user /confirm)
    if state.is_draft_locked:
        return Action("noop", "already draft_locked; waiting for user /confirm")

    # Budget exhausted → draft_lock (only fires for non-draft_locked states)
    if state.questions_asked >= MAX_CLARIFICATION_QUESTIONS:
        return Action("draft_lock", f"question budget exhausted ({state.questions_asked} asked)")

    # Silence > 4h while drafting → draft_lock
    if silence_h > DRAFT_SILENCE_LOCK_H:
        return Action("draft_lock", f"user silence for {silence_h:.1f}h")

    # Otherwise keep asking
    return Action("ask_next")
