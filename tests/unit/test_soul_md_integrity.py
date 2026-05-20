"""Integrity assertion for the Hermes SOUL.md persona file.

config/hermes/SOUL.md is mounted read-only into the hermes container at
/home/hermes/.hermes/SOUL.md (see deploy/docker-compose.yml). It defines
the agent's honesty/calibration persona ("Verify before claiming success",
"Acknowledge uncertainty"). A silent edit to this file changes agent
behavior in production.

This test mirrors the .github/workflows/ci.yml `soul-md-integrity` job:
both compute the file's sha256 at HEAD and compare it against the pinned
value stored under `integrity.soul_md_sha256` in config/limits.yaml.

If you intentionally changed SOUL.md, update the pin in config/limits.yaml
in the same commit.  If you didn't, revert the edit — something tampered
with the persona.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SOUL_MD = REPO_ROOT / "config" / "hermes" / "SOUL.md"
LIMITS_YAML = REPO_ROOT / "config" / "limits.yaml"


def _pinned_sha256() -> str:
    """Read the pinned sha256 from config/limits.yaml.

    Raises KeyError with a helpful message if the `integrity.soul_md_sha256`
    field is missing — that's the operator's signal that the pin was removed
    or never installed.
    """
    cfg = yaml.safe_load(LIMITS_YAML.read_text())
    try:
        return cfg["integrity"]["soul_md_sha256"]
    except KeyError as exc:
        raise KeyError(
            "config/limits.yaml is missing `integrity.soul_md_sha256`. "
            "The SOUL.md integrity pin was removed or never installed; "
            "restore it (see audit P2 #36)."
        ) from exc


def _current_sha256() -> str:
    """Compute the live sha256 of config/hermes/SOUL.md."""
    return hashlib.sha256(SOUL_MD.read_bytes()).hexdigest()


def test_soul_md_file_exists():
    """Sanity: the persona file must exist; the pin is meaningless otherwise."""
    assert SOUL_MD.is_file(), f"Expected {SOUL_MD} to exist"


def test_soul_md_sha256_matches_pin():
    """The live SHA-256 of SOUL.md must match the value pinned in limits.yaml."""
    expected = _pinned_sha256()
    actual = _current_sha256()
    assert actual == expected, (
        f"SOUL.md sha256 drift detected!\n"
        f"  expected (pinned in config/limits.yaml): {expected}\n"
        f"  actual   (config/hermes/SOUL.md HEAD):   {actual}\n\n"
        "If you intentionally changed SOUL.md, update "
        "`integrity.soul_md_sha256` in config/limits.yaml in the same commit. "
        "Otherwise, revert your change to SOUL.md."
    )


def test_pinned_sha256_is_well_formed_hex():
    """Defensive: catch shape errors in the pin (e.g. shell-quoted, truncated)."""
    pin = _pinned_sha256()
    assert isinstance(pin, str), f"pin must be a string, got {type(pin).__name__}"
    assert len(pin) == 64, f"sha256 hex must be 64 chars, got {len(pin)}"
    int(pin, 16)  # raises ValueError if not pure hex
