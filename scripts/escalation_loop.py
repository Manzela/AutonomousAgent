#!/usr/bin/env python3
"""Periodic 24h Telegram silence watcher; runs in the escalation-watcher sidecar.

Reads thresholds from /config/limits.yaml on each iteration so hot-reloading
limits.yaml works without a sidecar restart.
"""

import sys
import time

from yaml import safe_load

from lib.durability.escalation import run_once


CFG_PATH = "/config/limits.yaml"


def main() -> int:
    while True:
        with open(CFG_PATH) as f:
            cfg = safe_load(f)
        thr = cfg["agent"]["telegram_escalation_timeout_h"]
        interval = cfg["durability"]["escalation"]["watcher_interval_s"]
        n = run_once(threshold_h=thr)
        print(f"escalated {n}", flush=True)
        time.sleep(interval)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
