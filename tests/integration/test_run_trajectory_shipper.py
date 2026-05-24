"""Tests for scripts/run_trajectory_shipper.py — the standalone entrypoint
that the J1 launch flip activates.

These tests exercise the wiring (config-read, feature-flag, shipper
construction) but stub out the actual Model Armor + GCS calls. The 8-variant
Persistence Trap contract at tests/integration/test_persistence_trap.py
already covers the per-record sanitize + F37 + canary-token semantics; this
file does NOT re-test those — it tests that the wiring threads them through
correctly.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_trajectory_shipper.py"


def _import_script_module():
    """Load scripts/run_trajectory_shipper.py as a module so we can call
    its functions directly without spawning a subprocess."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_trajectory_shipper", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_feature_flag_off_is_noop(capsys):
    """When feature_flag_enabled=false, the entrypoint MUST short-circuit
    without constructing TrajectoryShipper, without opening the JSONL,
    and without making any Model Armor or GCS call.
    """
    config = {
        "bucket_name": "autonomous-agent-2026-j3-trajectories",
        "model_armor_template_resource": "projects/autonomous-agent-2026/locations/us-central1/templates/j1-trajectory-shipper",
        "feature_flag_enabled": False,
    }
    mod = _import_script_module()

    with mock.patch.object(mod, "_read_config_secret", return_value=config):
        with mock.patch("lib.trajectory.TrajectoryShipper") as shipper_cls:
            exit_code = mod.main(["--dry-run"])

    assert exit_code == 0
    shipper_cls.assert_not_called()
    out = capsys.readouterr().out
    assert "feature_flag_enabled=false" in out.lower() or "disabled" in out.lower()


def test_feature_flag_on_constructs_shipper(capsys):
    """When feature_flag_enabled=true and --dry-run is passed, the entrypoint
    MUST construct TrajectoryShipper with the secret-supplied bucket +
    template arguments, then exit 0 without invoking ship_batch."""
    config = {
        "bucket_name": "autonomous-agent-2026-j3-trajectories",
        "model_armor_template_resource": "projects/autonomous-agent-2026/locations/us-central1/templates/j1-trajectory-shipper",
        "feature_flag_enabled": True,
    }
    mod = _import_script_module()

    with mock.patch.object(mod, "_read_config_secret", return_value=config):
        with mock.patch("lib.trajectory.TrajectoryShipper") as shipper_cls:
            exit_code = mod.main(["--dry-run"])

    assert exit_code == 0
    shipper_cls.assert_called_once()
    call_kwargs = shipper_cls.call_args.kwargs
    assert call_kwargs["bucket"] == config["bucket_name"]
    assert call_kwargs["template"] == config["model_armor_template_resource"]


def test_missing_required_config_keys_exits_nonzero(capsys):
    """If the secret JSON is missing any required key, the entrypoint MUST
    exit nonzero with a clear message — silently defaulting would be a
    Persistence Trap regression vector."""
    config = {
        "bucket_name": "autonomous-agent-2026-j3-trajectories",
        # missing model_armor_template_resource
        "feature_flag_enabled": True,
    }
    mod = _import_script_module()

    with mock.patch.object(mod, "_read_config_secret", return_value=config):
        with pytest.raises(SystemExit) as exc_info:
            mod.main(["--dry-run"])

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "model_armor_template_resource" in err
