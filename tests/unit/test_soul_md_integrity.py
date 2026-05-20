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

import pytest
import yaml

from lib.limits_validator import validate

REPO_ROOT = Path(__file__).resolve().parents[2]
SOUL_MD = REPO_ROOT / "config" / "hermes" / "SOUL.md"
LIMITS_YAML = REPO_ROOT / "config" / "limits.yaml"
LIMITS_SCHEMA = REPO_ROOT / "config" / "limits-schema.json"


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


# ---------------------------------------------------------------------------
# Schema-pattern regression coverage.
#
# `test_pinned_sha256_is_well_formed_hex` (above) validates the *value*
# in our shipped config. The cases below pin down the *schema* itself:
# they drive bad values through `lib.limits_validator.validate` and
# assert each is rejected with a `soul_md_sha256`-shaped error.
#
# If a future schema edit loosens the pattern (e.g. drops the `^...$`
# anchors, or accepts uppercase hex) any of the cases below will start
# passing schema validation and the test will fail. That's the point.
# ---------------------------------------------------------------------------

# (description, bad-pin-value, expected-substring-in-error)
_INVALID_SHA256_CASES = [
    pytest.param(
        "A" * 64,
        id="uppercase_hex",
    ),
    pytest.param(
        "a" * 63,
        id="too_short_63",
    ),
    pytest.param(
        "a" * 65,
        id="too_long_65",
    ),
    pytest.param(
        "0x" + "a" * 62,
        id="0x_prefixed",
    ),
    pytest.param(
        "z" * 64,
        id="non_hex_chars",
    ),
    pytest.param(
        " " + "a" * 64,
        id="leading_whitespace_breaks_anchor",
    ),
    pytest.param(
        "a" * 64 + " ",
        id="trailing_whitespace_breaks_anchor",
    ),
    pytest.param(
        "aBcD" + "0" * 60,
        id="mixed_case_hex",
    ),
]


@pytest.mark.parametrize("bad_pin", _INVALID_SHA256_CASES)
def test_schema_rejects_invalid_sha256(tmp_path, bad_pin):
    """The `^[0-9a-f]{64}$` pattern must reject malformed pins.

    Drives each invalid value through the real limits-schema via
    `lib.limits_validator.validate`. The assertion looks for an error
    whose path mentions `soul_md_sha256` — the line/section that
    failed — which proves the pattern fired (rather than some other
    schema rule).
    """
    cfg = yaml.safe_load(LIMITS_YAML.read_text())
    cfg["integrity"]["soul_md_sha256"] = bad_pin
    bad_path = tmp_path / "limits-bad.yaml"
    bad_path.write_text(yaml.dump(cfg))

    errors = validate(bad_path, LIMITS_SCHEMA)
    assert errors, f"schema accepted invalid sha256 pin: {bad_pin!r}"
    # The validator emits errors keyed by `<path>: <message>`; the path
    # for this pattern violation is `integrity/soul_md_sha256`.
    assert any(
        "soul_md_sha256" in e for e in errors
    ), f"expected a soul_md_sha256 error for {bad_pin!r}, got: {errors}"


def test_schema_accepts_valid_lowercase_64_hex(tmp_path):
    """Positive control: a well-formed pin must still validate.

    Without this, a regression that broke every pattern would also make
    `test_schema_rejects_invalid_sha256` pass vacuously.
    """
    cfg = yaml.safe_load(LIMITS_YAML.read_text())
    # 64 lowercase hex chars, distinct from the real pin to make it
    # obvious this is a fixture, not the shipped value.
    cfg["integrity"]["soul_md_sha256"] = "0" * 63 + "f"
    good_path = tmp_path / "limits-good.yaml"
    good_path.write_text(yaml.dump(cfg))

    errors = validate(good_path, LIMITS_SCHEMA)
    soul_errors = [e for e in errors if "soul_md_sha256" in e]
    assert soul_errors == [], f"valid sha256 pin rejected by schema: {soul_errors}"
