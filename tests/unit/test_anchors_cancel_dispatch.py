"""P1-5: /cancel slash command dispatch by argument.

`/cancel` (no arg) → draft-spec cancel (existing P1-1 stub, unchanged)
`/cancel <id>` → ``lib.kanban.telegram_bridge.cancel_card(id)``
"""

from __future__ import annotations

from unittest.mock import patch

from lib.anchors import _slash_cancel


def test_cancel_without_arg_calls_draft_cancel():
    """Bare `/cancel` preserves the existing P1-1 draft-cancel behaviour.

    The P1-1 stub returns a TODO string; we only check that the kanban
    bridge is NOT consulted in this branch.
    """
    with patch("lib.kanban.telegram_bridge.cancel_card") as mock_cancel:
        out = _slash_cancel("")
        assert mock_cancel.call_count == 0
        # Whatever the P1-1 branch returns, it must be a string the bridge can echo.
        assert isinstance(out, str)
        assert len(out) > 0


def test_cancel_without_arg_with_whitespace_still_draft_cancel():
    """`/cancel   ` (only whitespace) is still the bare form — not a card id."""
    with patch("lib.kanban.telegram_bridge.cancel_card") as mock_cancel:
        out = _slash_cancel("   ")
        assert mock_cancel.call_count == 0
        assert isinstance(out, str)


def test_cancel_with_id_calls_kanban_cancel_card():
    """`/cancel 42` → ``cancel_card("42")`` and formats the bool result."""
    with patch("lib.kanban.telegram_bridge.cancel_card", return_value=True) as mock_cancel:
        out = _slash_cancel("42")
        mock_cancel.assert_called_once_with("42")
        assert isinstance(out, str)
        # Some indication of success in the user-facing reply.
        assert "42" in out


def test_cancel_with_unknown_id_reports_failure():
    """`/cancel does-not-exist` → bridge returns False; reply must be a failure message."""
    with patch("lib.kanban.telegram_bridge.cancel_card", return_value=False) as mock_cancel:
        out = _slash_cancel("does-not-exist")
        mock_cancel.assert_called_once_with("does-not-exist")
        assert isinstance(out, str)
        # Indicates the card wasn't found / not cancellable.
        assert "does-not-exist" in out
