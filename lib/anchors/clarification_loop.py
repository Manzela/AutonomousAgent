"""Clarification loop state machine — drives TaskSpec from draft to locked.

Hybrid circuit-breaker: locks when ANY of confidence >= 0.85, question
budget exhausted (=6), or user silent > 4h. Escalates to Telegram if
draft_locked spec is silent for > 24h.
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

    Order matters: escalation is checked first (highest priority), then lock
    triggers, then continue-asking.
    """
    silence_h = (now - state.last_user_msg_at).total_seconds() / 3600

    # Escalation: draft_locked + 24h silence (Fail-Loud per F-matrix)
    if state.is_draft_locked and silence_h > DRAFT_LOCKED_SILENCE_ESCALATE_H:
        return Action("escalate", f"silent for {silence_h:.1f}h in draft_locked state")

    # Lock at high confidence
    if state.confidence >= LOCK_CONFIDENCE_THRESHOLD:
        return Action("lock", f"confidence {state.confidence:.2f} >= {LOCK_CONFIDENCE_THRESHOLD}")

    # Budget exhausted → draft_lock
    if state.questions_asked >= MAX_CLARIFICATION_QUESTIONS:
        return Action("draft_lock", f"question budget exhausted ({state.questions_asked} asked)")

    # Silence > 4h while drafting → draft_lock
    if not state.is_draft_locked and silence_h > DRAFT_SILENCE_LOCK_H:
        return Action("draft_lock", f"user silence for {silence_h:.1f}h")

    # Otherwise keep asking
    return Action("ask_next")
