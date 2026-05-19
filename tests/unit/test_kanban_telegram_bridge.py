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


# ---------------------------------------------------------------------------
# Real send_alert HTTP behaviour (Phase 1.0.1 α-4)
# ---------------------------------------------------------------------------


def test_send_alert_posts_to_telegram_api(monkeypatch):
    """When a bot token + chat id are present, send_alert POSTs to Telegram's API."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN123")
    monkeypatch.setenv("TELEGRAM_HOME_CHAT_ID", "7217166969")

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=None)
    fake_client.post = MagicMock(return_value=fake_response)

    with patch("httpx.Client", return_value=fake_client):
        telegram_bridge.send_alert("card-42", "blocked >24h")

    assert fake_client.post.called, "Expected httpx.Client.post to be invoked"
    args, kwargs = fake_client.post.call_args
    url = args[0] if args else kwargs.get("url")
    assert (
        url is not None and "api.telegram.org" in url
    ), f"Expected URL to contain api.telegram.org, got {url!r}"
    assert "TESTTOKEN123" in url, f"Expected token in URL path, got {url!r}"
    assert "sendMessage" in url, f"Expected sendMessage endpoint, got {url!r}"
    body = kwargs.get("json") or kwargs.get("data") or {}
    assert (
        str(body.get("chat_id")) == "7217166969"
    ), f"Expected chat_id=7217166969 in body, got {body!r}"
    assert "blocked" in str(body.get("text", "")), f"Expected message text in body, got {body!r}"


def test_send_alert_fail_open_on_network_error(monkeypatch):
    """Network failure must NOT raise — alerts are best-effort."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN123")
    monkeypatch.setenv("TELEGRAM_HOME_CHAT_ID", "7217166969")

    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=None)
    # Mimic a transport-layer failure.
    import httpx

    fake_client.post = MagicMock(side_effect=httpx.ConnectError("dns failure"))

    with patch("httpx.Client", return_value=fake_client):
        # Must not raise.
        telegram_bridge.send_alert("card-42", "blocked >24h")


def test_send_alert_no_op_without_token(monkeypatch):
    """Missing TELEGRAM_BOT_TOKEN: degrade gracefully — no HTTP, no exception.

    Strong contract: ``httpx.Client`` must not even be *instantiated* when
    the bot token is missing, since constructing a client allocates
    transport resources that an inert send shouldn't touch.
    """
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    client_constructor = MagicMock()
    with patch("httpx.Client", client_constructor):
        telegram_bridge.send_alert("card-42", "blocked >24h")
    assert (
        not client_constructor.called
    ), "Expected httpx.Client to never be constructed when TELEGRAM_BOT_TOKEN unset"


def test_update_card_status_no_op_when_db_unavailable():
    """update_card_status is the bridge entry the kanban plugin uses for
    success/blocked transitions. It must tolerate the Hermes DB being absent."""
    with patch.object(telegram_bridge, "_kanban_db", return_value=None):
        # Should not raise; no return contract beyond not exploding.
        telegram_bridge.update_card_status("session-X", "running")


def test_update_card_status_same_status_touches_heartbeat():
    """If the new status matches the old status, we only touch last_heartbeat_at, without sending alert."""
    fake_db = MagicMock()
    fake_conn = MagicMock()
    fake_db.connect.return_value = fake_conn

    # Return old status as "running"
    # SELECT query returns row: (id, status, title, last_failure_error, body, consecutive_failures, result)
    fake_conn.execute.return_value.fetchone.side_effect = [
        ("task-123", "running", "My Task", None, None, 0, None),  # first SELECT (retrieve current)
    ]

    with (
        patch.object(telegram_bridge, "_kanban_db", return_value=fake_db),
        patch.object(telegram_bridge, "send_alert") as mock_send_alert,
    ):
        telegram_bridge.update_card_status("session-123", "running")

    mock_send_alert.assert_not_called()
    # Check that it ran UPDATE tasks SET last_heartbeat_at = ...
    updates = [args[0] for args, _ in fake_conn.execute.call_args_list if "UPDATE" in args[0]]
    assert len(updates) == 1
    assert "last_heartbeat_at" in updates[0]
    assert "status = ?" not in updates[0]


def test_update_card_status_transition_sends_alert():
    """If the status changes and is not silent, we perform transition and send alert."""
    fake_db = MagicMock()
    fake_conn = MagicMock()
    fake_db.connect.return_value = fake_conn

    # Return old status as "running", and updated row as "blocked"
    fake_conn.execute.return_value.fetchone.side_effect = [
        ("task-123", "running", "My Task", "some error", None, 1, None),  # retrieve current
        (
            "task-123",
            "blocked",
            "My Task",
            "some error",
            None,
            1,
            None,
        ),  # retrieve updated for alert
    ]

    # Let's say block_task does NOT exist on fake_db, so it falls back to the UPDATE path.
    if hasattr(fake_db, "block_task"):
        del fake_db.block_task

    with (
        patch.object(telegram_bridge, "_kanban_db", return_value=fake_db),
        patch.object(telegram_bridge, "send_alert") as mock_send_alert,
    ):
        telegram_bridge.update_card_status("session-123", "blocked")

    # Verify transition update SQL ran
    updates = [args[0] for args, _ in fake_conn.execute.call_args_list if "UPDATE" in args[0]]
    assert any("status = ?" in u for u in updates)

    # Verify alert sent
    mock_send_alert.assert_called_once()
    args, _ = mock_send_alert.call_args
    assert args[0] == "task-123"
    assert "Blocked on: some error" in args[1]


def test_update_card_status_uses_block_task_helper():
    """If transitioning to blocked and block_task exists, use it instead of direct SQL."""
    fake_db = MagicMock()
    fake_conn = MagicMock()
    fake_db.connect.return_value = fake_conn

    # Return old status as "running", and updated row as "blocked"
    fake_conn.execute.return_value.fetchone.side_effect = [
        ("task-123", "running", "My Task", "some error", None, 1, None),
        ("task-123", "blocked", "My Task", "some error", None, 1, None),
    ]

    with (
        patch.object(telegram_bridge, "_kanban_db", return_value=fake_db),
        patch.object(telegram_bridge, "send_alert") as mock_send_alert,
    ):
        telegram_bridge.update_card_status("session-123", "blocked")

    fake_db.block_task.assert_called_once_with(
        fake_conn, "task-123", reason="Tool execution failed"
    )
    mock_send_alert.assert_called_once()
