"""Hermes snapshot subsystem.

GCS snapshot executor (P1-1b) for disaster-recovery; tars
``/home/hermes/.hermes/`` and uploads daily to a configured GCS bucket.
The cron is documented in ``config/limits.yaml`` as
``snapshots.gcs_snapshot_cron``; the loop entrypoint
(``scripts/snapshot_loop.py``) polls every
``durability.snapshot_watchdog.interval_s`` seconds and skips when
either today's snapshot is already present or the current UTC hour is
before the configured snapshot hour.
"""
