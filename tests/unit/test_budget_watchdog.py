"""Unit tests for ``lib.durability.budget_watchdog`` (audit P1-1).

Covers the F21 daily-budget enforcement watchdog — verifies (a) the SQL
shape we hand to Postgres, (b) the threshold arithmetic (alert at
``alert_at_pct``, F21 at 100%), (c) the fail-open semantics on every
failure path (no DSN, psycopg ImportError, connect failure, query
failure, malformed row), and (d) that the side effects (Telegram alert,
F21 dispatch) fire in the right places.
"""

from __future__ import annotations

from unittest import mock

import pytest

from lib.durability import budget_watchdog


# ---------------------------------------------------------------------------
# evaluate_budget — pure threshold arithmetic
# ---------------------------------------------------------------------------


def test_evaluate_budget_below_alert_threshold_no_alert():
    state = budget_watchdog.evaluate_budget(spend_usd=50.0, cap_usd=500.0, alert_at_pct=75)
    assert state.alert is None
    assert state.triggered_f21 is False
    assert state.pct == pytest.approx(10.0)


def test_evaluate_budget_at_alert_threshold_emits_alert_only():
    """At exactly the warning %, we alert but DON'T dispatch F21 yet."""
    state = budget_watchdog.evaluate_budget(spend_usd=375.0, cap_usd=500.0, alert_at_pct=75)
    assert state.alert is not None
    assert "75" in state.alert
    assert state.triggered_f21 is False


def test_evaluate_budget_above_alert_below_100_emits_alert_only():
    state = budget_watchdog.evaluate_budget(spend_usd=450.0, cap_usd=500.0, alert_at_pct=75)
    assert state.alert is not None
    assert state.triggered_f21 is False
    assert state.pct == pytest.approx(90.0)


def test_evaluate_budget_at_100_triggers_f21():
    state = budget_watchdog.evaluate_budget(spend_usd=500.0, cap_usd=500.0, alert_at_pct=75)
    assert state.triggered_f21 is True
    assert state.alert is not None
    assert "F21" in state.alert


def test_evaluate_budget_over_100_triggers_f21():
    state = budget_watchdog.evaluate_budget(spend_usd=600.0, cap_usd=500.0, alert_at_pct=75)
    assert state.triggered_f21 is True
    assert state.pct == pytest.approx(120.0)


def test_evaluate_budget_zero_cap_returns_error():
    state = budget_watchdog.evaluate_budget(spend_usd=0.0, cap_usd=0.0, alert_at_pct=75)
    assert state.error is not None
    assert state.triggered_f21 is False
    assert state.alert is None


def test_evaluate_budget_negative_cap_returns_error():
    state = budget_watchdog.evaluate_budget(spend_usd=10.0, cap_usd=-1.0, alert_at_pct=75)
    assert state.error is not None


# ---------------------------------------------------------------------------
# get_daily_spend_usd — DB layer with mocked psycopg
# ---------------------------------------------------------------------------


def _make_conn(fetch_value):
    """Build a mock psycopg connection whose cursor.fetchone() returns fetch_value."""
    conn = mock.MagicMock()
    cur = mock.MagicMock()
    cur.fetchone.return_value = fetch_value
    conn.cursor.return_value = cur
    return conn


def test_get_daily_spend_usd_returns_float(monkeypatch):
    monkeypatch.setenv("LITELLM_DB_URL", "postgresql://test")
    conn = _make_conn((123.45,))
    with mock.patch.object(budget_watchdog, "_connect", return_value=conn):
        result = budget_watchdog.get_daily_spend_usd()
    assert result == pytest.approx(123.45)
    # Verify the SQL we sent uses the UTC day boundary.
    sent_sql = conn.cursor.return_value.execute.call_args.args[0]
    assert "LiteLLM_SpendLogs" in sent_sql
    assert "SUM(spend)" in sent_sql
    assert "date_trunc('day'" in sent_sql
    assert "UTC" in sent_sql


def test_get_daily_spend_usd_handles_null_sum(monkeypatch):
    """Empty table → SUM is NULL; we should report 0.0, not None."""
    monkeypatch.setenv("LITELLM_DB_URL", "postgresql://test")
    conn = _make_conn((None,))
    with mock.patch.object(budget_watchdog, "_connect", return_value=conn):
        result = budget_watchdog.get_daily_spend_usd()
    assert result == 0.0


def test_get_daily_spend_usd_no_dsn_returns_none(monkeypatch):
    monkeypatch.delenv("LITELLM_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert budget_watchdog.get_daily_spend_usd() is None


def test_get_daily_spend_usd_psycopg_missing_returns_none(monkeypatch):
    monkeypatch.setenv("LITELLM_DB_URL", "postgresql://test")
    with mock.patch.object(budget_watchdog, "_connect", side_effect=ImportError("no psycopg")):
        assert budget_watchdog.get_daily_spend_usd() is None


def test_get_daily_spend_usd_connect_failure_returns_none(monkeypatch):
    monkeypatch.setenv("LITELLM_DB_URL", "postgresql://test")
    with mock.patch.object(budget_watchdog, "_connect", side_effect=RuntimeError("db unreachable")):
        assert budget_watchdog.get_daily_spend_usd() is None


def test_get_daily_spend_usd_query_failure_returns_none(monkeypatch):
    monkeypatch.setenv("LITELLM_DB_URL", "postgresql://test")
    conn = mock.MagicMock()
    cur = mock.MagicMock()
    cur.execute.side_effect = RuntimeError("relation does not exist")
    conn.cursor.return_value = cur
    with mock.patch.object(budget_watchdog, "_connect", return_value=conn):
        assert budget_watchdog.get_daily_spend_usd() is None


def test_get_daily_spend_usd_unexpected_row_shape_returns_none(monkeypatch):
    """Defensive: if the row[0] isn't coercible to float, return None."""
    monkeypatch.setenv("LITELLM_DB_URL", "postgresql://test")
    conn = _make_conn(("not a number",))
    with mock.patch.object(budget_watchdog, "_connect", return_value=conn):
        assert budget_watchdog.get_daily_spend_usd() is None


def test_get_daily_spend_usd_prefers_LITELLM_DB_URL(monkeypatch):
    """Both env vars set → LITELLM_DB_URL wins."""
    monkeypatch.setenv("LITELLM_DB_URL", "postgresql://litellm")
    monkeypatch.setenv("DATABASE_URL", "postgresql://other")
    conn = _make_conn((0.0,))
    captured = {}

    def fake_connect(dsn):
        captured["dsn"] = dsn
        return conn

    with mock.patch.object(budget_watchdog, "_connect", side_effect=fake_connect):
        budget_watchdog.get_daily_spend_usd()
    assert captured["dsn"] == "postgresql://litellm"


def test_get_daily_spend_usd_falls_back_to_DATABASE_URL(monkeypatch):
    monkeypatch.delenv("LITELLM_DB_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://other")
    conn = _make_conn((0.0,))
    captured = {}

    def fake_connect(dsn):
        captured["dsn"] = dsn
        return conn

    with mock.patch.object(budget_watchdog, "_connect", side_effect=fake_connect):
        budget_watchdog.get_daily_spend_usd()
    assert captured["dsn"] == "postgresql://other"


# ---------------------------------------------------------------------------
# run_once — orchestration + side-effects
# ---------------------------------------------------------------------------


def test_run_once_skips_when_cap_is_none():
    state = budget_watchdog.run_once(cap_usd=None)
    assert state.error is not None
    assert state.triggered_f21 is False


def test_run_once_skips_when_db_query_returns_none():
    """DB unreachable → state.error set, no alert / dispatch fired."""
    with (
        mock.patch.object(budget_watchdog, "get_daily_spend_usd", return_value=None),
        mock.patch.object(budget_watchdog, "_emit_alert") as mock_alert,
        mock.patch.object(budget_watchdog, "_dispatch_f21") as mock_dispatch,
    ):
        state = budget_watchdog.run_once(cap_usd=500.0)
    assert state.error is not None
    mock_alert.assert_not_called()
    mock_dispatch.assert_not_called()


def test_run_once_below_threshold_no_side_effects():
    with (
        mock.patch.object(budget_watchdog, "get_daily_spend_usd", return_value=10.0),
        mock.patch.object(budget_watchdog, "_emit_alert") as mock_alert,
        mock.patch.object(budget_watchdog, "_dispatch_f21") as mock_dispatch,
    ):
        state = budget_watchdog.run_once(cap_usd=500.0)
    assert state.pct == pytest.approx(2.0)
    mock_alert.assert_not_called()
    mock_dispatch.assert_not_called()


def test_run_once_at_warning_emits_alert_but_no_f21_dispatch():
    with (
        mock.patch.object(budget_watchdog, "get_daily_spend_usd", return_value=400.0),
        mock.patch.object(budget_watchdog, "_emit_alert") as mock_alert,
        mock.patch.object(budget_watchdog, "_dispatch_f21") as mock_dispatch,
    ):
        state = budget_watchdog.run_once(cap_usd=500.0, alert_at_pct=75)
    assert state.alert is not None
    assert state.triggered_f21 is False
    mock_alert.assert_called_once()
    mock_dispatch.assert_not_called()


def test_run_once_at_100_dispatches_f21_and_alerts():
    # _dispatch_f21 handles alerting internally via halt_alert_snapshot → send_alert.
    # _emit_alert must NOT be called on the F21 path — it would duplicate the Telegram message.
    with (
        mock.patch.object(budget_watchdog, "get_daily_spend_usd", return_value=500.0),
        mock.patch.object(budget_watchdog, "_emit_alert") as mock_alert,
        mock.patch.object(budget_watchdog, "_dispatch_f21") as mock_dispatch,
    ):
        state = budget_watchdog.run_once(cap_usd=500.0, alert_at_pct=75)
    assert state.triggered_f21 is True
    mock_dispatch.assert_called_once()
    mock_alert.assert_not_called()


def test_run_once_dispatch_happens_before_alert():
    """F21 path: _dispatch_f21 handles both halt AND alert internally.

    _emit_alert must NOT be called on the F21 path — that would
    produce a duplicate Telegram message. Ordering (halt-before-alert)
    is now enforced inside halt_alert_snapshot, not at the run_once level.
    """
    call_log = []
    with (
        mock.patch.object(budget_watchdog, "get_daily_spend_usd", return_value=500.0),
        mock.patch.object(
            budget_watchdog,
            "_emit_alert",
            side_effect=lambda *a, **k: call_log.append("alert"),
        ),
        mock.patch.object(
            budget_watchdog,
            "_dispatch_f21",
            side_effect=lambda *a, **k: call_log.append("dispatch"),
        ),
    ):
        budget_watchdog.run_once(cap_usd=500.0, alert_at_pct=75)
    assert call_log == ["dispatch"]


def test_run_once_emit_alert_failure_does_not_raise(monkeypatch):
    """Telegram raising from inside _emit_alert must not bubble up."""
    with (
        mock.patch.object(budget_watchdog, "get_daily_spend_usd", return_value=400.0),
        mock.patch(
            "lib.kanban.telegram_bridge.send_alert",
            side_effect=RuntimeError("telegram down"),
        ),
    ):
        # Should not raise.
        state = budget_watchdog.run_once(cap_usd=500.0, alert_at_pct=75)
    assert state.alert is not None  # state still reported correctly


def test_run_once_f21_dispatch_failure_does_not_raise():
    """A handler raising must not crash the sidecar — caller sees state intact."""
    with (
        mock.patch.object(budget_watchdog, "get_daily_spend_usd", return_value=500.0),
        mock.patch.object(budget_watchdog, "_emit_alert"),
        mock.patch(
            "lib.durability.handlers.dispatch",
            side_effect=RuntimeError("handler exploded"),
        ),
    ):
        state = budget_watchdog.run_once(cap_usd=500.0, alert_at_pct=75)
    assert state.triggered_f21 is True
