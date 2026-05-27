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
        except Exception as exc:  # noqa: BLE001 — sidecar must keep ticking
            logger.warning("escalation_loop: tick error (sleeping 60s): %s", exc)
            interval = 60
        time.sleep(interval)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
