"""SP-24 hermetic unit tests for the predeploy gate shell script.

All tests run via subprocess against scripts/predeploy_gate.sh using
PREDEPLOY_*_RESULT env overrides — no live GH CLI, cosign, or terraform.

Oracles:
  1.  eval-gate failure → gate exits non-zero, output cites "eval-gate".
  2.  dead-code-c4 failure → gate exits non-zero, output cites "dead-code-c4".
  3.  cosign-verify failure → gate exits non-zero, output cites "cosign-verify".
  4.  submodule-clean failure → gate exits non-zero, output cites "submodule-clean".
  5.  secrets-encrypted failure → gate exits non-zero, output cites "secrets-encrypted".
  6.  terraform-plan-clean failure → gate exits non-zero, output cites "terraform-plan-clean".
  7.  All checks pass → gate exits 0, output contains "ALL CHECKS PASSED".
  8.  Two simultaneous failures → both check names cited in output.
  9.  scripts/predeploy_gate.sh exists and is executable.
  10. scripts/predeploy_gate.sh contains 'set -euo pipefail'.
  11. docs/compliance/predeploy-controls.md exists and is non-empty.
  12. compliance map references all 6 check names.
  13. Empty-string override is treated as failure, not a silent bypass.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "predeploy_gate.sh"
COMPLIANCE_MAP = REPO_ROOT / "docs" / "compliance" / "predeploy-controls.md"

CHECK_NAMES = [
    "eval-gate",
    "dead-code-c4",
    "cosign-verify",
    "submodule-clean",
    "secrets-encrypted",
    "terraform-plan-clean",
]
ENV_VAR_NAMES = [
    "PREDEPLOY_EVAL_RESULT",
    "PREDEPLOY_C4_RESULT",
    "PREDEPLOY_COSIGN_RESULT",
    "PREDEPLOY_SUBMODULE_RESULT",
    "PREDEPLOY_SECRETS_RESULT",
    "PREDEPLOY_TERRAFORM_RESULT",
]


def _run_gate(**overrides: int) -> subprocess.CompletedProcess:
    """Run predeploy_gate.sh with all checks overridden.

    By default all checks pass (result=0). Pass keyword args to fail
    specific checks: EVAL=1 / C4=1 / COSIGN=1 / SUBMODULE=1 /
    SECRETS=1 / TERRAFORM=1.
    """
    env = {**os.environ}
    # Default all to pass.
    for var in ENV_VAR_NAMES:
        env[var] = "0"
    # Apply caller overrides.
    key_map = {
        "EVAL": "PREDEPLOY_EVAL_RESULT",
        "C4": "PREDEPLOY_C4_RESULT",
        "COSIGN": "PREDEPLOY_COSIGN_RESULT",
        "SUBMODULE": "PREDEPLOY_SUBMODULE_RESULT",
        "SECRETS": "PREDEPLOY_SECRETS_RESULT",  # pragma: allowlist secret
        "TERRAFORM": "PREDEPLOY_TERRAFORM_RESULT",
    }
    for short_key, value in overrides.items():
        env[key_map[short_key]] = str(value)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )


# ── Oracle 1-6 — per-check failure → cited, non-zero exit ─────────────────────


@pytest.mark.parametrize(
    "short_key,check_name",
    [
        ("EVAL", "eval-gate"),
        ("C4", "dead-code-c4"),
        ("COSIGN", "cosign-verify"),
        ("SUBMODULE", "submodule-clean"),
        ("SECRETS", "secrets-encrypted"),
        ("TERRAFORM", "terraform-plan-clean"),
    ],
)
def test_per_check_failure_cited(short_key, check_name):
    result = _run_gate(**{short_key: 1})
    assert result.returncode != 0, f"Expected non-zero exit when {check_name} fails"
    combined = result.stdout + result.stderr
    assert (
        check_name in combined
    ), f"Expected '{check_name}' cited in gate output when it fails.\nOutput: {combined}"


# ── Oracle 7 — all-green → exits 0, "ALL CHECKS PASSED" ──────────────────────


def test_all_green_passes():
    result = _run_gate()
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"Expected exit 0 for all-green run.\nOutput: {combined}"
    assert (
        "ALL CHECKS PASSED" in combined
    ), f"Expected 'ALL CHECKS PASSED' in output.\nOutput: {combined}"


# ── Oracle 8 — two simultaneous failures both cited ───────────────────────────


def test_two_failures_both_cited():
    result = _run_gate(EVAL=1, TERRAFORM=1)
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "eval-gate" in combined, f"'eval-gate' not cited.\nOutput: {combined}"
    assert (
        "terraform-plan-clean" in combined
    ), f"'terraform-plan-clean' not cited.\nOutput: {combined}"


# ── Oracle 9 — script exists and is executable ────────────────────────────────


def test_script_exists_and_executable():
    assert SCRIPT.exists(), f"scripts/predeploy_gate.sh not found at {SCRIPT}"
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/predeploy_gate.sh is not user-executable"


# ── Oracle 10 — script contains set -euo pipefail ────────────────────────────


def test_script_has_strict_mode():
    source = SCRIPT.read_text()
    assert (
        "set -euo pipefail" in source
    ), "scripts/predeploy_gate.sh must contain 'set -euo pipefail' (strict mode)"


# ── Oracle 11 — compliance map exists and is non-empty ───────────────────────


def test_compliance_map_exists_and_nonempty():
    assert COMPLIANCE_MAP.exists(), "docs/compliance/predeploy-controls.md not found"
    assert COMPLIANCE_MAP.stat().st_size > 0, "docs/compliance/predeploy-controls.md is empty"


# ── Oracle 12 — compliance map references all 6 check names ─────────────────


@pytest.mark.parametrize("check_name", CHECK_NAMES)
def test_compliance_map_references_check(check_name):
    content = COMPLIANCE_MAP.read_text()
    assert (
        check_name in content
    ), f"docs/compliance/predeploy-controls.md does not reference check '{check_name}'"


# ── Oracle 13 — empty-string override is a failure, not a bypass ─────────────


def test_empty_string_override_is_not_bypass():
    # Setting a PREDEPLOY_*_RESULT to empty string must NOT pass the check.
    # If it were treated as "0" (pass), an unintentionally blank CI env var
    # would silently skip real verification and approve the deploy.
    env = {**os.environ}
    for var in ENV_VAR_NAMES:
        env[var] = "0"  # default all to pass
    # Override one check with empty string — must still fail.
    env["PREDEPLOY_EVAL_RESULT"] = ""
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
    )
    combined = result.stdout + result.stderr
    assert (
        result.returncode != 0
    ), f"Empty-string override must NOT be treated as pass.\nOutput: {combined}"
    assert (
        "eval-gate" in combined
    ), f"Expected 'eval-gate' cited when override is empty string.\nOutput: {combined}"
