#!/usr/bin/env python3
"""Standalone entrypoint for the J3 trajectory shipper.

Activated by the atomic J1 flip (docs/runbooks/j1-launch-flip.md):

  1. Operator writes a new secret version to `autonomousagent-j3-shipper-config`
     with `feature_flag_enabled = true`.
  2. systemd timer (or operator manual invoke) fires this script.
  3. Script reads the config secret, constructs `TrajectoryShipper`, and
     enters its ship loop (the tail-and-ship watcher is a Phase 0a
     follow-up — this script today supports `--dry-run` and `--ship-once`).

Persistence Trap: this script does NOT invent any sanitize / GCS logic.
It is a wiring shim only. All redaction enforcement lives in
`lib.trajectory.shipper.TrajectoryShipper.ship_batch`, which the
8-variant contract at `tests/integration/test_persistence_trap.py` keeps
honest.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("trajectory_shipper")
logging.basicConfig(
    level=os.getenv("HERMES_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

_SECRET_RESOURCE = os.getenv(
    "HERMES_J3_SHIPPER_CONFIG_SECRET",
    "projects/autonomous-agent-2026/secrets/autonomousagent-j3-shipper-config/versions/latest",
)

_REQUIRED_CONFIG_KEYS = (
    "bucket_name",
    "model_armor_template_resource",
    "feature_flag_enabled",
)


def _read_config_secret() -> dict[str, Any]:
    """Read the j3-shipper-config secret from Secret Manager.

    Lazy import so the script can be unit-tested without the google-cloud
    SDK installed (the test layer mocks this function).
    """
    from google.cloud import secretmanager  # type: ignore[import-not-found]

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": _SECRET_RESOURCE})
    payload = response.payload.data.decode("utf-8")
    return json.loads(payload)


def _validate_config(config: dict[str, Any]) -> None:
    """Validate config has every required key. Fail loud on missing keys —
    silent defaults are a Persistence Trap regression vector.
    """
    missing = [k for k in _REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        print(
            f"ERROR: j3-shipper-config secret is missing required keys: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="J3 trajectory shipper standalone entrypoint")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config + construct shipper, but do not ship records",
    )
    parser.add_argument(
        "--ship-once",
        action="store_true",
        help="Read the current batch of pending records and ship them once, then exit",
    )
    args = parser.parse_args(argv)

    config = _read_config_secret()
    _validate_config(config)

    if not config["feature_flag_enabled"]:
        logger.info("j3-shipper feature_flag_enabled=false — no-op exit")
        print("j3-shipper: feature_flag_enabled=false, exiting without shipping")
        return 0

    from lib.trajectory import TrajectoryShipper

    shipper = TrajectoryShipper(
        bucket=config["bucket_name"],
        template=config["model_armor_template_resource"],
    )
    # Log construction confirmation only — config values originate from
    # Secret Manager. Even though bucket_name + template resource path are
    # operational identifiers (not credentials), CodeQL's taint analysis
    # conservatively flags any field of a secret-sourced dict in logs
    # (rule: py/clear-text-logging-sensitive-data). Operator visibility into
    # which bucket/template is active lives in docs/runbooks/j1-launch-flip.md
    # and the Secret Manager UI; the shipper itself (lib/trajectory/shipper.py)
    # emits per-batch diagnostics where they're operationally required.
    logger.info("j3-shipper constructed from secret config (feature_flag_enabled=true)")

    if args.dry_run:
        # Same rationale as the logger.info above — confirmation only, no
        # secret-dict field exposure to stdout.
        print("j3-shipper: dry-run OK — secret config loaded, shipper constructed")
        return 0

    if args.ship_once:
        # Caller-provided pending-batch source is out of scope for this entrypoint —
        # the tail-and-ship watcher (Phase 0a follow-up) feeds it. For now,
        # --ship-once without an implemented batch reader is a no-op with a clear
        # message. `shipper` is constructed above so the wiring is exercised even
        # in this placeholder mode.
        del shipper
        logger.warning(
            "j3-shipper: --ship-once invoked but tail-and-ship watcher is not yet implemented"
        )
        print("j3-shipper: --ship-once is a no-op until tail-watcher lands (Phase 0a follow-up)")
        return 0

    # Default mode (no flags): print usage and exit 0 — long-running loop is the
    # tail-watcher's responsibility, not this entrypoint's.
    del shipper
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
