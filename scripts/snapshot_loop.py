#!/usr/bin/env python3
"""Periodic GCS snapshot watcher; runs in the snapshot-watchdog sidecar.

Reads ``snapshots.gcs_snapshot_cron`` (parsed: only the hour field is
honored — see :func:`_parse_snapshot_hour`) and
``durability.snapshot_watchdog.interval_s`` (default 1800s = 30 min)
from ``/config/limits.yaml`` on every iteration so hot-edits take
effect without restarting the sidecar.

Mirrors ``scripts/budget_watchdog_loop.py`` — same fail-open contract:
an exception from any tick is logged and the loop continues. The
underlying :func:`lib.snapshots.gcs_snapshot.run_once` already returns
on every error path; the outer try/except is the belt-and-suspenders
guard for failures we couldn't have predicted (yaml parse, schema
mismatch).
"""

from __future__ import annotations

import logging
import sys
import time

from yaml import safe_load

from lib.snapshots.gcs_snapshot import DEFAULT_SNAPSHOT_HOUR_UTC, run_once

CFG_PATH = "/config/limits.yaml"
DEFAULT_INTERVAL_S = 1800

logger = logging.getLogger("snapshot_loop")

# O-5 fix: OTel tick counter for snapshot watchdog visibility.
_watchdog_ticks: object = None
_watchdog_errors: object = None
try:
    from opentelemetry import metrics as _otel_metrics  # type: ignore

    _meter = _otel_metrics.get_meter("hermes.scripts.snapshot_loop")
    _watchdog_ticks = _meter.create_counter(
        name="watchdog.ticks",
        description="Snapshot watchdog loop iterations completed (label: loop=snapshot)",
        unit="1",
    )
    _watchdog_errors = _meter.create_counter(
        name="watchdog.errors",
        description="Snapshot watchdog loop iterations that raised an exception",
        unit="1",
    )
except Exception:  # pragma: no cover
    pass


def _parse_snapshot_hour(cron_str: str) -> int:
    """Extract the hour field from a 5-field cron string.

    We honor only the hour because the watcher already enforces "once
    per UTC day" via the today_already_uploaded check, and the loop
    poll cadence (`interval_s`) determines minute-level granularity.
    Cron strings that don't match the simple ``"M H * * *"`` shape fall
    back to :data:`DEFAULT_SNAPSHOT_HOUR_UTC` with a warning.
    """

    try:
        fields = (cron_str or "").split()
        if len(fields) != 5:
            raise ValueError(f"expected 5 fields, got {len(fields)}")
        hour = int(fields[1])
        if not 0 <= hour <= 23:
            raise ValueError(f"hour out of range: {hour}")
        return hour
    except (AttributeError, ValueError) as exc:
        logger.warning(
            "snapshot_loop: cron parse failed (%s); using default hour %d UTC",
            exc,
            DEFAULT_SNAPSHOT_HOUR_UTC,
        )
        return DEFAULT_SNAPSHOT_HOUR_UTC


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    while True:
        try:
            with open(CFG_PATH) as f:
                cfg = safe_load(f) or {}
            snap_cfg = cfg.get("snapshots") or {}
            cron_str = snap_cfg.get("gcs_snapshot_cron", "0 4 * * *")
            hour_utc = _parse_snapshot_hour(cron_str)
            interval = int(
                (cfg.get("durability") or {})
                .get("snapshot_watchdog", {})
                .get("interval_s", DEFAULT_INTERVAL_S)
            )
            # FinOps slice — include_spend_logs defaults False so existing
            # operators keep the legacy tar shape; flip via
            # snapshots.include_spend_logs in limits.yaml once the
            # spend-log retention runbook is signed off.
            include_spend = bool(snap_cfg.get("include_spend_logs", False))
            state = run_once(
                snapshot_hour_utc=hour_utc,
                include_spend_logs=include_spend,
            )
            if state.error:
                logger.warning("snapshot_loop tick error: %s", state.error)
            elif state.uploaded:
                logger.info(
                    "snapshot_loop uploaded object=%s bytes=%d spend_rows=%s",
                    state.object_name,
                    state.bytes_uploaded or 0,
                    state.spend_logs_rows if state.spend_logs_rows is not None else "n/a",
                )
            elif state.skipped:
                logger.info(
                    "snapshot_loop skipped reason=%s",
                    state.reason,
                )
            if _watchdog_ticks is not None:
                try:
                    _watchdog_ticks.add(1, {"loop": "snapshot"})  # type: ignore[union-attr]
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001 — sidecar must keep ticking
            logger.warning("snapshot_loop iteration failed: %s", exc)
            if _watchdog_errors is not None:
                try:
                    _watchdog_errors.add(1, {"loop": "snapshot"})  # type: ignore[union-attr]
                except Exception:
                    pass
            interval = DEFAULT_INTERVAL_S
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main() or 0)
