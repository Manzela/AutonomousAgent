"""Tests for limits.yaml schema validation."""

from __future__ import annotations

from pathlib import Path

import yaml

from lib.limits_validator import validate

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "config" / "limits.yaml"
SCHEMA = REPO_ROOT / "config" / "limits-schema.json"


def test_shipped_limits_is_valid():
    """The limits.yaml we committed must validate against its schema."""
    errors = validate(CONFIG, SCHEMA)
    assert errors == [], f"Shipped limits.yaml is invalid: {errors}"


def test_invalid_default_for_unknown_rejected(tmp_path):
    """Approval default must be ask/allow/deny — anything else is rejected."""
    bad = yaml.safe_load(CONFIG.read_text())
    bad["approval"]["default_for_unknown"] = "yolo"
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text(yaml.dump(bad))
    errors = validate(bad_path, SCHEMA)
    assert any("default_for_unknown" in e for e in errors)


def test_negative_budget_rejected(tmp_path):
    bad = yaml.safe_load(CONFIG.read_text())
    bad["budget"]["daily_usd_cap"] = -10
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text(yaml.dump(bad))
    errors = validate(bad_path, SCHEMA)
    assert any("daily_usd_cap" in e for e in errors)


def test_missing_required_section_rejected(tmp_path):
    bad = yaml.safe_load(CONFIG.read_text())
    del bad["budget"]
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text(yaml.dump(bad))
    errors = validate(bad_path, SCHEMA)
    assert any("budget" in e for e in errors)


def test_unknown_top_level_section_rejected(tmp_path):
    bad = yaml.safe_load(CONFIG.read_text())
    bad["unknown_section"] = {"foo": "bar"}
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text(yaml.dump(bad))
    errors = validate(bad_path, SCHEMA)
    assert any("Additional properties" in e or "unknown_section" in e for e in errors)
