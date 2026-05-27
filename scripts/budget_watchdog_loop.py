#!/usr/bin/env python3
"""Periodic LiteLLM daily-budget watcher; runs in the budget-watchdog sidecar.

Reads ``budget.daily_usd_cap`` and ``budget.alert_at_pct`` from
``/config/limits.yaml`` on each iteration so hot-reloading limits.yaml
takes effect without a sidecar restart. Poll interval comes from
``durability.budget_watchdog.interval_s`` (default 300s = 5 min).

Mirrors ``scripts/escalation_loop.py`` — same fail-open contract: an
exception from any tick is logged and the loop continues.
"""

from __future__ import annotations

import logging
import sys
import time

from yaml import safe_load

from lib.durability.budget_watchdog import run_once

CFG_PATH = "/config/limits.yaml"
DEFAULT_INTERVAL_S = 30  # CC-1: ≤30s required; 5-min default allowed multi-cap burns

logger = logging.getLogger("budget_watchdog_loop")

# O-5 fix: OTel metric counter for watchdog tick visibility.
# A wedged watchdog loop is invisible until the email-only alert fires — the
# counter lets SLO dashboards detect "no ticks in last N minutes" without
# requiring log scraping.  Instruments degrade gracefully when OTel SDK is
# absent (counter stays None).
_watchdog_ticks: object = None
_watchdog_errors: object = None
try:
    from opentelemetry import metrics as _otel_metrics  # type: ignore

    _meter = _otel_metrics.get_meter("hermes.scripts.budget_watchdog")
    _watchdog_ticks = _meter.create_counter(
        name="watchdog.ticks",
        description="Budget watchdog loop iterations completed (label: loop=budget)",
        unit="1",
    )
    _watchdog_errors = _meter.create_counter(
        name="watchdog.errors",
        description="Budget watchdog loop iterations that raised an exception",
        unit="1",
    )
except Exception:  # pragma: no cover — OTel optional in scripts
    pass


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    while True:
        try:
            with open(CFG_PATH) as f:
                cfg = safe_load(f) or {}
            budget_cfg = cfg.get("budget") or {}
            cap = budget_cfg.get("daily_usd_cap")
            alert_at = int(budget_cfg.get("alert_at_pct", 75))
            interval = int(
                (cfg.get("durability") or {})
                .get("budget_watchdog", {})
                .get("interval_s", DEFAULT_INTERVAL_S)
            )
            state = run_once(cap_usd=cap, alert_at_pct=alert_at)
            if state.error:
                logger.warning("budget_watchdog tick skipped: %s", state.error)
            else:
                logger.info(
                    "budget_watchdog spend=$%.2f cap=$%.2f pct=%.1f%% f21=%s",
                    state.spend_usd or 0.0,
                    state.cap_usd or 0.0,
                    state.pct or 0.0,
                    state.triggered_f21,
                )
            if _watchdog_ticks is not None:
                try:
                    _watchdog_ticks.add(1, {"loop": "budget"})  # type: ignore[union-attr]
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001 — sidecar must keep ticking
            logger.warning("budget_watchdog loop iteration failed: %s", exc)
            if _watchdog_errors is not None:
                try:
                    _watchdog_errors.add(1, {"loop": "budget"})  # type: ignore[union-attr]
                except Exception:
                    pass
            interval = DEFAULT_INTERVAL_S
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
