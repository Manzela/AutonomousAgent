"""P1-5 notification policy table — declarative status-transition → Telegram message.

The policy is locked by ``docs/superpowers/specs/2026-05-15-phase1-design-alignment.md``
lines 362-370. One test per row + a defensive "unknown transition returns None".
"""

from __future__ import annotations

from types import SimpleNamespace

from lib.kanban.notification_policy import status_transition_to_notification


def _card(**overrides):
    """Tiny stand-in for the Hermes Task dataclass.

    The policy function only reads a handful of attributes, so a
    ``SimpleNamespace`` is plenty for unit-test purposes.
    """
    defaults = dict(
        id="card-001",
        title="Refactor the widget pipeline",
        body=None,
        status="todo",
        consecutive_failures=0,
        last_failure_error=None,
        result=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_triage_to_todo_is_silent():
    """`triage` → `todo` is a silent administrative move."""
    assert status_transition_to_notification("triage", "todo", _card()) is None


def test_todo_to_ready_announces_started():
    """`todo` → `ready` produces "Started: <title>"."""
    card = _card(title="Refactor the widget pipeline")
    msg = status_transition_to_notification("todo", "ready", card)
    assert msg is not None
    assert "Started" in msg
    assert "Refactor the widget pipeline" in msg


def test_ready_to_running_is_silent():
    """`ready` → `running` is silent (OTel heartbeat covers it)."""
    assert status_transition_to_notification("ready", "running", _card()) is None


def test_running_to_blocked_priority_alert():
    """`running` → `blocked` is a PRIORITY ALERT with /resume hint."""
    card = _card(id="42", last_failure_error="missing API key")
    msg = status_transition_to_notification("running", "blocked", card)
    assert msg is not None
    assert "Blocked on" in msg
    # The /resume slash command must be advertised so the user knows the recovery path.
    assert "/resume" in msg
    assert "42" in msg


def test_running_to_done_announces_result():
    """`running` → `done` produces "Done: <title>\\n\\nResult: <summary>"."""
    card = _card(title="Refactor the widget pipeline", result="3 files changed")
    msg = status_transition_to_notification("running", "done", card)
    assert msg is not None
    assert "Done" in msg
    assert "Refactor the widget pipeline" in msg
    assert "3 files changed" in msg


def test_running_to_failure_alert_includes_consecutive_count():
    """`running` → failure is an ALERT with consecutive_failures + last_failure_error.

    The spec uses the synthetic 'failure' status name to mean any non-success
    terminal outcome that increments the consecutive-failure counter (Hermes
    has crashed/timed_out/failed all rolling into the same counter).
    """
    card = _card(id="77", consecutive_failures=2, last_failure_error="ETIMEDOUT")
    msg = status_transition_to_notification("running", "failure", card)
    assert msg is not None
    assert "77" in msg
    assert "2" in msg  # consecutive_failures count
    assert "ETIMEDOUT" in msg


def test_any_to_archived_is_silent():
    """Any status → `archived` is silent (user-initiated /cancel echoes elsewhere)."""
    for from_status in ("triage", "todo", "ready", "running", "blocked", "done"):
        assert status_transition_to_notification(from_status, "archived", _card()) is None


def test_unknown_transition_returns_none():
    """Defensive: an unknown/unmapped transition is silent, not a crash."""
    assert status_transition_to_notification("triage", "running", _card()) is None
    assert status_transition_to_notification("blocked", "done", _card()) is None
