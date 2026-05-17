"""Unit tests for clarification loop state machine."""

from datetime import datetime, timedelta, timezone


from lib.anchors.clarification_loop import (
    DRAFT_SILENCE_LOCK_H,
    LOCK_CONFIDENCE_THRESHOLD,
    MAX_CLARIFICATION_QUESTIONS,
    ClarificationState,
    decide_next_action,
)


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


def test_already_draft_locked_low_confidence_returns_noop():
    """Already draft_locked + low confidence + within budget + not silent → noop, not re-draft_lock."""
    state = ClarificationState(
        questions_asked=3,
        last_user_msg_at=datetime.now(timezone.utc),
        confidence=0.5,
        is_draft_locked=True,
    )
    action = decide_next_action(state, now=datetime.now(timezone.utc))
    assert action.kind == "noop"


def test_clock_skew_negative_silence_does_not_break():
    """Smoke: future last_user_msg_at must NOT raise or return an unexpected kind.

    KNOWN LIMITATION (red-green verified 2026-05-15): this test is a
    documentation-only smoke test, not a true regression test for the
    `silence_h = max(0.0, ...)` clamp in decide_next_action. With future
    timestamps, the underlying delta is negative; both silence comparisons
    (`> 4` and `> 24`) return False either way, so removing the clamp does
    NOT change the returned Action.kind — only the (cosmetic) `silence_h`
    value interpolated into the reason string. To make this a true
    red-green test, decide_next_action would need to expose silence_h via
    a helper, OR the clamp would need a code path where its absence
    actually changes Action.kind. Until then, this test asserts the smoke
    path (no exception, sensible kind) and the clamp itself is defensive
    (good practice; avoids negative numbers in log messages).
    """
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    state = ClarificationState(
        questions_asked=3,
        last_user_msg_at=future,  # in the future relative to `now`
        confidence=0.5,
    )
    action = decide_next_action(state, now=datetime.now(timezone.utc))
    # Either with or without the clamp, this returns ask_next (silence_h is
    # negative, both silence checks fail, lock/escalate not triggered).
    assert action.kind == "ask_next"


def test_boundary_confidence_exactly_threshold_locks():
    """Confidence == 0.85 should lock (>= comparison)."""
    state = ClarificationState(
        questions_asked=2,
        last_user_msg_at=datetime.now(timezone.utc),
        confidence=LOCK_CONFIDENCE_THRESHOLD,  # exactly 0.85
    )
    action = decide_next_action(state, now=datetime.now(timezone.utc))
    assert action.kind == "lock"


def test_boundary_silence_exactly_threshold_does_not_lock():
    """Silence == exactly 4.0h should NOT trigger draft_lock (> comparison, not >=)."""
    # Pin both timestamps to a fixed `now` so the delta is exactly 4h (not 4h + microseconds).
    now = datetime.now(timezone.utc)
    exactly_threshold = now - timedelta(hours=DRAFT_SILENCE_LOCK_H)
    state = ClarificationState(
        questions_asked=2,
        last_user_msg_at=exactly_threshold,
        confidence=0.5,
    )
    action = decide_next_action(state, now=now)
    # Boundary at exactly 4h: silence_h == 4.0, comparison is `> 4` → False → ask_next
    assert action.kind == "ask_next"


def test_boundary_questions_exactly_budget_draft_locks():
    """questions_asked == 6 should trigger draft_lock (>= comparison)."""
    state = ClarificationState(
        questions_asked=MAX_CLARIFICATION_QUESTIONS,  # exactly 6
        last_user_msg_at=datetime.now(timezone.utc),
        confidence=0.5,
    )
    action = decide_next_action(state, now=datetime.now(timezone.utc))
    assert action.kind == "draft_lock"
