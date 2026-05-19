"""P1-6 escalation watcher — ``emit_escalation`` wires to the kanban bridge.

Before Phase 1.0.1 α-4 this was a ``print()`` stub; the test below pins
the contract that the 24h-stale watcher publishes through the same
Telegram bridge path as Kanban status transitions.
"""

from __future__ import annotations

from unittest.mock import patch

from lib.durability import escalation


def test_emit_escalation_calls_send_alert():
    """``emit_escalation`` must publish through ``telegram_bridge.send_alert``.

    The message must include the card id, title, and age so the operator
    can act on the alert without opening the Kanban UI.
    """
    with patch("lib.kanban.telegram_bridge.send_alert") as mock_send:
        escalation.emit_escalation(card_id=42, title="Repro flaky test", age_h=26.4)

    assert mock_send.called, "Expected send_alert to be called by emit_escalation"
    args, kwargs = mock_send.call_args
    # First positional or 'card_id' kwarg should carry the id.
    card_arg = kwargs.get("card_id", args[0] if args else None)
    msg_arg = kwargs.get("msg", args[1] if len(args) >= 2 else None)
    assert card_arg == 42, f"Expected card_id=42, got {card_arg!r}"
    assert "Repro flaky test" in str(
        msg_arg
    ), f"Expected card title in alert message, got {msg_arg!r}"
    assert "26" in str(msg_arg) or "26.4" in str(
        msg_arg
    ), f"Expected blocked-age (26.4h) in alert message, got {msg_arg!r}"


def test_emit_escalation_does_not_raise_when_bridge_fails():
    """Escalation watcher runs in a sidecar loop — bridge faults must not crash it."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated bridge fault")

    with patch("lib.kanban.telegram_bridge.send_alert", side_effect=boom):
        # Should not propagate.
        escalation.emit_escalation(card_id=1, title="x", age_h=25.0)
