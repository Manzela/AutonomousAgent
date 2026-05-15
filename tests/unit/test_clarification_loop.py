"""Unit tests for clarification loop state machine."""

from datetime import datetime, timedelta, timezone


from lib.anchors.clarification_loop import ClarificationState, decide_next_action


def test_locks_at_high_confidence():
    state = ClarificationState(
        questions_asked=2,
        last_user_msg_at=datetime.now(timezone.utc),
        confidence=0.9,
    )
    action = decide_next_action(state, now=datetime.now(timezone.utc))
    assert action.kind == "lock"


def test_draft_locks_when_budget_exhausted():
    state = ClarificationState(
        questions_asked=6,
        last_user_msg_at=datetime.now(timezone.utc),
        confidence=0.4,
    )
    action = decide_next_action(state, now=datetime.now(timezone.utc))
    assert action.kind == "draft_lock"
    assert "budget" in action.reason


def test_draft_locks_when_silent_for_4h():
    five_hours_ago = datetime.now(timezone.utc) - timedelta(hours=5)
    state = ClarificationState(
        questions_asked=2,
        last_user_msg_at=five_hours_ago,
        confidence=0.5,
    )
    action = decide_next_action(state, now=datetime.now(timezone.utc))
    assert action.kind == "draft_lock"
    assert "silence" in action.reason


def test_escalates_when_silent_in_draft_locked_for_24h():
    twenty_five_hours_ago = datetime.now(timezone.utc) - timedelta(hours=25)
    state = ClarificationState(
        questions_asked=6,
        last_user_msg_at=twenty_five_hours_ago,
        confidence=0.5,
        is_draft_locked=True,
    )
    action = decide_next_action(state, now=datetime.now(timezone.utc))
    assert action.kind == "escalate"


def test_continues_asking_when_under_budget_and_low_confidence():
    state = ClarificationState(
        questions_asked=3,
        last_user_msg_at=datetime.now(timezone.utc),
        confidence=0.6,
    )
    action = decide_next_action(state, now=datetime.now(timezone.utc))
    assert action.kind == "ask_next"
