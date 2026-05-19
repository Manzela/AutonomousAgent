"""F21 daily budget cap watchdog.

Polls the LiteLLM ``LiteLLM_SpendLogs`` Postgres table directly (the
``/spend/total`` endpoint returns 404 because the LiteLLM extension that
exposes it isn't loaded — pass-2 audit finding R5). Sums today's
``spend`` values, compares against ``budget.daily_usd_cap`` from
``config/limits.yaml``, and:

* At ``>= alert_at_pct`` (default 75) of the cap: emits a Telegram
  warning so the operator can throttle or pause work.
* At ``>= 100`` % of the cap: dispatches the failure-matrix F21 handler
  (``halt_alert_snapshot``) which fans out alert + snapshot + Kanban
  card transition.

Both side effects are best-effort — the loop itself is fail-open so a
DB outage or psycopg ImportError downgrades the watchdog to a no-op
WARNING rather than a crashed sidecar.

Schema source: LiteLLM Prisma model ``LiteLLM_SpendLogs`` (camelCase
columns; matches what ``GET /global/spend/report`` aggregates over).
Query uses ``startTime >= date_trunc('day', NOW() AT TIME ZONE 'UTC')``
so daily reset happens on the same UTC boundary regardless of the
sidecar host's local timezone.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Sum-spend SQL. Uses UTC day so daily caps align with the LiteLLM
# spend/report endpoint and don't shift across timezones.
_DAILY_SPEND_SQL = (
    'SELECT COALESCE(SUM(spend), 0.0)::float FROM "LiteLLM_SpendLogs" '
    "WHERE \"startTime\" >= date_trunc('day', NOW() AT TIME ZONE 'UTC')"
)


@dataclass(frozen=True)
class BudgetState:
    """Result of one watchdog tick.

    ``alert`` is a human-readable string the watcher posts to Telegram
    at the warning threshold. ``triggered_f21`` is True iff the F21
    handler was actually dispatched. ``error`` is set when the tick was
    skipped (DB unreachable, psycopg missing, malformed cap, etc.) and
    is the WARNING-log reason. Either ``error`` is set OR
    ``spend_usd`` / ``pct`` are populated; never both.
    """

    spend_usd: Optional[float] = None
    cap_usd: Optional[float] = None
    pct: Optional[float] = None
    alert: Optional[str] = None
    triggered_f21: bool = False
    error: Optional[str] = None


def _connect(conn_str: str) -> Any:
    """Lazy psycopg import + connect. Patched by tests.

    Kept as a thin indirection so unit tests can stub the entire DB
    layer without importing psycopg.
    """
    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(conn_str)


def get_daily_spend_usd(conn_str: Optional[str] = None) -> Optional[float]:
    """Return today's total LiteLLM spend in USD, or None on any failure.

    ``conn_str`` defaults to ``LITELLM_DB_URL`` then ``DATABASE_URL``
    (the litellm-db.env file mounted into the sidecar exposes both via
    env_file). Returns None — never raises — so the caller can treat
    DB outages as a soft skip rather than a watchdog crash.
    """
    conn_str = conn_str or os.environ.get("LITELLM_DB_URL") or os.environ.get("DATABASE_URL")
    if not conn_str:
        logger.warning(
            "budget_watchdog: no DB connection string (LITELLM_DB_URL / DATABASE_URL unset)"
        )
        return None
    try:
        conn = _connect(conn_str)
    except ImportError:
        logger.warning("budget_watchdog: psycopg not installed; cannot poll spend_logs")
        return None
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("budget_watchdog: connect failed: %s", exc)
        return None
    try:
        cur = conn.cursor()
        cur.execute(_DAILY_SPEND_SQL)
        row = cur.fetchone()
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("budget_watchdog: query failed: %s", exc)
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return None
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    if not row or row[0] is None:
        return 0.0
    try:
        return float(row[0])
    except (TypeError, ValueError):
        logger.warning("budget_watchdog: unexpected spend value: %r", row[0])
        return None


def evaluate_budget(spend_usd: float, cap_usd: float, alert_at_pct: int = 75) -> BudgetState:
    """Pure function — turns a spend reading into a BudgetState.

    Split out from ``run_once`` so unit tests can pin the threshold
    arithmetic without needing to mock the DB layer.
    """
    if cap_usd <= 0:
        return BudgetState(
            spend_usd=spend_usd,
            cap_usd=cap_usd,
            error=f"invalid cap_usd={cap_usd!r}",
        )
    pct = (spend_usd / cap_usd) * 100.0
    alert: Optional[str] = None
    triggered_f21 = False
    if pct >= 100.0:
        triggered_f21 = True
        alert = (
            f"🛑 Daily LiteLLM budget exceeded: ${spend_usd:.2f} / ${cap_usd:.2f} "
            f"({pct:.0f}%). F21 dispatched — agent halted."
        )
    elif pct >= float(alert_at_pct):
        alert = (
            f"⚠️ Daily LiteLLM budget at {pct:.0f}% "
            f"(${spend_usd:.2f} / ${cap_usd:.2f}). Threshold = {alert_at_pct}%."
        )
    return BudgetState(
        spend_usd=spend_usd,
        cap_usd=cap_usd,
        pct=pct,
        alert=alert,
        triggered_f21=triggered_f21,
    )


def _emit_alert(msg: str) -> None:
    """Post the warning-threshold alert via the Telegram bridge.

    Same fail-open semantics as the escalation watcher — if Telegram is
    silent, log and continue. The 100% path uses the failure-matrix
    handler (``halt_alert_snapshot``) which already has the GitHub
    fallback path wired (P1-4); this lighter warning path stays
    Telegram-only to avoid issue spam at every tick we're above 75%.
    """
    try:
        from lib.kanban.telegram_bridge import send_alert

        send_alert("budget", msg)
    except Exception as exc:  # noqa: BLE001 — sidecar must keep ticking
        logger.warning("budget_watchdog: send_alert raised: %s", exc)


def _dispatch_f21(spend_usd: float, cap_usd: float, msg: str) -> None:
    """Dispatch the failure-matrix F21 handler.

    Constructs a synthetic ``RuntimeError`` so the handler's standard
    payload-handling path applies (it stringifies ``error`` for the
    snapshot + alert). Any exception from the dispatch is swallowed so
    the sidecar loop keeps ticking.
    """
    try:
        from lib.durability.handlers import dispatch

        dispatch(
            "F21",
            error=RuntimeError(msg),
            tool_name="budget_watchdog",
            payload={
                "spend_usd": spend_usd,
                "cap_usd": cap_usd,
                "source": "budget_watchdog",
            },
        )
    except Exception as exc:  # noqa: BLE001 — sidecar must keep ticking
        logger.warning("budget_watchdog: F21 dispatch raised: %s", exc)


def run_once(
    cap_usd: Optional[float] = None,
    alert_at_pct: int = 75,
    conn_str: Optional[str] = None,
) -> BudgetState:
    """One watchdog tick. Returns the BudgetState for the caller to log.

    Workflow:

    1. Poll today's spend from Postgres (returns None → fail-open skip).
    2. Evaluate against the cap (pure function).
    3. Side-effects: emit warning at ``alert_at_pct``, dispatch F21 at
       100 %. The dispatch happens FIRST so the operator gets the halt
       signal before the warning churn.

    The sidecar reads ``cap_usd`` + ``alert_at_pct`` from
    ``config/limits.yaml`` on each iteration so live config edits take
    effect without restarting the sidecar.
    """
    if cap_usd is None or cap_usd <= 0:
        return BudgetState(error=f"invalid cap_usd={cap_usd!r}")

    spend = get_daily_spend_usd(conn_str=conn_str)
    if spend is None:
        return BudgetState(cap_usd=cap_usd, error="spend query failed (see warnings)")

    state = evaluate_budget(spend, cap_usd, alert_at_pct=alert_at_pct)
    if state.triggered_f21 and state.alert:
        _dispatch_f21(state.spend_usd or 0.0, state.cap_usd or 0.0, state.alert)
    if state.alert:
        _emit_alert(state.alert)
    return state


__all__ = [
    "BudgetState",
    "evaluate_budget",
    "get_daily_spend_usd",
    "run_once",
]
