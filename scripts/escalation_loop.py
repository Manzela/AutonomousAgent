#!/usr/bin/env python3
"""Periodic 24h Telegram silence watcher; runs in the escalation-watcher sidecar.

Reads thresholds from /config/limits.yaml on each iteration so hot-reloading
limits.yaml works without a sidecar restart.
"""

import logging
import sys
import time

from yaml import safe_load

from lib.durability.escalation import run_once

logger = logging.getLogger(__name__)

CFG_PATH = "/config/limits.yaml"
DEFAULT_INTERVAL_S = 300

# O-5 fix: OTel tick counter for escalation watchdog visibility.
_watchdog_ticks: object = None
_watchdog_errors: object = None
try:
    from opentelemetry import metrics as _otel_metrics  # type: ignore

    _meter = _otel_metrics.get_meter("hermes.scripts.escalation_loop")
    _watchdog_ticks = _meter.create_counter(
        name="watchdog.ticks",
        description="Escalation watchdog loop iterations completed (label: loop=escalation)",
        unit="1",
    )
    _watchdog_errors = _meter.create_counter(
        name="watchdog.errors",
        description="Escalation watchdog loop iterations that raised an exception",
        unit="1",
    )
except Exception:  # pragma: no cover
    pass


def main() -> int:
    while True:
        try:
            with open(CFG_PATH) as f:
                cfg = safe_load(f) or {}
            thr = (cfg.get("agent") or {}).get("telegram_escalation_timeout_h", 24)
            interval = (
                (cfg.get("durability") or {})
                .get("escalation", {})
                .get("watcher_interval_s", DEFAULT_INTERVAL_S)
            )
            n = run_once(threshold_h=thr)
            print(f"escalated {n}", flush=True)
            if _watchdog_ticks is not None:
                try:
                    _watchdog_ticks.add(1, {"loop": "escalation"})  # type: ignore[union-attr]
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001 — sidecar must keep ticking
            logger.warning("escalation_loop: tick error (sleeping 60s): %s", exc)
            if _watchdog_errors is not None:
                try:
                    _watchdog_errors.add(1, {"loop": "escalation"})  # type: ignore[union-attr]
                except Exception:
                    pass
            interval = 60
        time.sleep(interval)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
