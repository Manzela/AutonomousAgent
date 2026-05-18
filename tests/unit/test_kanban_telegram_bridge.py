"""P1-5 Telegram ↔ Kanban bridge — unit tests.

The Hermes ``kanban_db`` module is *not* on the unit-test PYTHONPATH (it
lives in a submodule and the import lazily falls back to ``None``). The
bridge is structured so the DB handle is fetched through a thin
indirection that tests can ``patch``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lib.kanban import telegram_bridge


def test_telegram_msg_to_card_creates_kanban_task():
    """An inbound Telegram message becomes a Kanban card with the message text as title/body.

    The bridge calls ``kanban_db.create_task(...)`` with the inbound text
    threaded into ``title``/``body`` and the user id into ``created_by``.
    """
    fake_db = MagicMock()
    fake_db.create_task.return_value = "task-abc123"
    fake_conn = MagicMock()
    fake_db.connect.return_value = fake_conn

    with patch.object(telegram_bridge, "_kanban_db", return_value=fake_db):
        msg = SimpleNamespace(
            text="Refactor the widget pipeline and add tests.",
            message_id=999,
        )
        task_id = telegram_bridge.telegram_msg_to_card(msg, user_id="7217166969")

    assert task_id == "task-abc123"
    assert fake_db.create_task.called
    call_kwargs = fake_db.create_task.call_args.kwargs
    # Created_by must thread the Telegram user id so multi-tenant boards stay sane.
    assert call_kwargs["created_by"] == "7217166969"
    # The message text is the title (truncated if needed) and/or body.
    assert "Refactor the widget pipeline" in (
        call_kwargs.get("title", "") + (call_kwargs.get("body") or "")
    )


def test_cancel_card_returns_true_for_existing():
    """`/cancel <id>` against a live card archives it and returns True."""
    fake_db = MagicMock()
    fake_conn = MagicMock()
    fake_db.connect.return_value = fake_conn
    fake_db.archive_task.return_value = True

    with patch.object(telegram_bridge, "_kanban_db", return_value=fake_db):
        ok = telegram_bridge.cancel_card("42")

    assert ok is True
    fake_db.archive_task.assert_called_once_with(fake_conn, "42")


def test_cancel_card_returns_false_for_unknown_id():
    """Cancelling an unknown id returns False (no exception)."""
    fake_db = MagicMock()
    fake_conn = MagicMock()
    fake_db.connect.return_value = fake_conn
    fake_db.archive_task.return_value = False

    with patch.object(telegram_bridge, "_kanban_db", return_value=fake_db):
        ok = telegram_bridge.cancel_card("does-not-exist")

    assert ok is False


def test_cancel_card_returns_false_when_kanban_db_unavailable():
    """If Hermes' Kanban DB module is not importable, cancel_card is a no-op returning False."""
    with patch.object(telegram_bridge, "_kanban_db", return_value=None):
        assert telegram_bridge.cancel_card("anything") is False


def test_status_transition_to_notification_silent_returns_none():
    """The bridge re-exports the policy function for callers."""
    card = SimpleNamespace(
        id="x",
        title="t",
        body=None,
        status="ready",
        consecutive_failures=0,
        last_failure_error=None,
        result=None,
    )
    assert telegram_bridge.status_transition_to_notification("ready", "running", card) is None


def test_send_alert_is_callable_with_string():
    """``send_alert(card_id, msg)`` must be importable and accept a string.

    We can't exercise the real Telegram send call in a unit test (it
    requires HTTPS + bot token); we only verify the public surface
    exists and tolerates the documented call shape.
    """
    # Should not raise.
    telegram_bridge.send_alert("card-1", "hello")
