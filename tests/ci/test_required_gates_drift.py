"""tests/ci/test_required_gates_drift.py — PD-18a branch-protection drift guard.

GOAL: fail (red) if any HARD / blocking gate that the audit intends to guard
`main` is MISSING from the applied branch-protection contract.

Two source-of-truth artefacts are reconciled (both parsed from local files — no
live GitHub / `gh` API call, so this unit is hermetic and deterministic):

  * config/required_gates.txt — curated list of INTENDED blocking gate check-run
    names (the audit's "what must guard main").
  * terraform/phase-0a-gcp/branch_protection.tf — the
    required_status_checks.contexts an operator actually applies.

DRIFT := an intended gate absent from the terraform contexts. That is the
high-stakes failure mode: a blocking gate the PRD/audit says must gate merges,
but which is not in the applied required-checks contract.

Red-green structure (this is the PD-18a contract):
  * test_fixture_with_missing_gate_is_detected   — RED arm: a synthetic fixture
    in which one intended gate is dropped from the .tf MUST be flagged as drift.
    This proves the detector actually catches drift (not vacuously green).
  * test_fixture_in_sync_has_no_drift            — GREEN arm: a consistent
    synthetic fixture MUST report zero drift.
  * test_live_required_gates_have_no_drift       — asserts the *real* repo files
    are consistent: every intended gate in config/required_gates.txt is present
    in branch_protection.tf required_status_checks.contexts.

Distinct from tests/ci/test_required_gates_coverage.py: that suite asserts only
against the live files (sync + name-fidelity + safety). This adds the
fixture-driven red/green proof that the drift *detector itself* works on
injected drift, independent of the current repo state — which is what makes it a
regression-proof guard rather than a snapshot.

Deferred (reported in PD-18b / blockers, not covered here):
  * the LIVE `terraform apply` reconciliation against the GitHub API, and
  * name-fidelity of intended names vs the check-run names actually EMITTED by
    workflow runs (covered for the static .yml job `name:` case by
    test_required_gates_coverage.py::test_every_gate_has_real_job).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.ci._required_gates_drift import (
    find_missing_gates,
    parse_intended_gates,
    parse_tf_contexts,
)

# ── Repo layout ─────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
GATES_FILE = REPO_ROOT / "config" / "required_gates.txt"
TF_FILE = REPO_ROOT / "terraform" / "phase-0a-gcp" / "branch_protection.tf"


# ── Synthetic fixtures (hermetic red / green proof of the detector) ──────────

_FIXTURE_GATES_TXT = textwrap.dedent(
    """\
    # synthetic required_gates.txt for the drift-detector self-test
    # comment lines and blanks must be ignored

    Lint Python
    Unit Tests
    dead-code reachability (C4)
    no-sentinel-termination (anti-drift)
    """
)

# In-sync .tf: every intended gate above is present as a context.
_FIXTURE_TF_IN_SYNC = textwrap.dedent(
    """\
    resource "github_branch_protection" "main" {
      required_status_checks {
        strict = true
        contexts = [
          "Lint Python",
          "Unit Tests",
          "dead-code reachability (C4)",
          "no-sentinel-termination (anti-drift)",
        ]
      }
    }
    """
)

# Drifted .tf: "dead-code reachability (C4)" — a HARD blocking gate — was dropped
# from the applied contexts. The detector MUST flag it.
_FIXTURE_TF_MISSING_GATE = textwrap.dedent(
    """\
    resource "github_branch_protection" "main" {
      required_status_checks {
        strict = true
        contexts = [
          "Lint Python",
          "Unit Tests",
          "no-sentinel-termination (anti-drift)",
        ]
      }
    }
    """
)


# ── Parser unit behaviour ────────────────────────────────────────────────────


def test_parse_intended_gates_ignores_comments_and_blanks() -> None:
    names = parse_intended_gates(_FIXTURE_GATES_TXT)
    assert names == [
        "Lint Python",
        "Unit Tests",
        "dead-code reachability (C4)",
        "no-sentinel-termination (anti-drift)",
    ]


def test_parse_tf_contexts_extracts_quoted_contexts() -> None:
    contexts = parse_tf_contexts(_FIXTURE_TF_IN_SYNC)
    assert contexts == [
        "Lint Python",
        "Unit Tests",
        "dead-code reachability (C4)",
        "no-sentinel-termination (anti-drift)",
    ]


def test_parse_tf_contexts_raises_when_block_absent() -> None:
    """A structural change that removes the block must surface loudly, not pass
    vacuously (empty contexts would otherwise hide real drift)."""
    with pytest.raises(ValueError, match="required_status_checks"):
        parse_tf_contexts('resource "github_branch_protection" "main" {}')


# ── RED arm: injected drift MUST be detected ─────────────────────────────────


def test_fixture_with_missing_gate_is_detected() -> None:
    """A blocking gate dropped from the .tf contexts is reported as drift.

    This is the load-bearing assertion: it proves the detector is NOT vacuously
    green — a real, hand-injected drift is caught.
    """
    intended = parse_intended_gates(_FIXTURE_GATES_TXT)
    tf_contexts = parse_tf_contexts(_FIXTURE_TF_MISSING_GATE)
    missing = find_missing_gates(intended, tf_contexts)
    assert missing == ["dead-code reachability (C4)"]


# ── GREEN arm: an in-sync fixture has no drift ───────────────────────────────


def test_fixture_in_sync_has_no_drift() -> None:
    intended = parse_intended_gates(_FIXTURE_GATES_TXT)
    tf_contexts = parse_tf_contexts(_FIXTURE_TF_IN_SYNC)
    assert find_missing_gates(intended, tf_contexts) == []


# ── Live-files consistency: the real repo must have no drift ──────────────────


def test_live_required_gates_have_no_drift() -> None:
    """Every intended gate in config/required_gates.txt must be present in
    terraform/phase-0a-gcp/branch_protection.tf required_status_checks.contexts.

    If this goes red, a HARD/blocking gate has been added to required_gates.txt
    (or renamed) without being mirrored into the terraform contexts → the applied
    branch-protection contract drifted from the audit's intent. Fix: add/rename
    the missing context in branch_protection.tf.
    """
    assert GATES_FILE.exists(), f"config/required_gates.txt not found at {GATES_FILE}"
    assert TF_FILE.exists(), f"branch_protection.tf not found at {TF_FILE}"

    intended = parse_intended_gates(GATES_FILE.read_text())
    assert intended, "config/required_gates.txt has no non-comment gate names"
    tf_contexts = parse_tf_contexts(TF_FILE.read_text())

    missing = find_missing_gates(intended, tf_contexts)
    assert not missing, (
        "DRIFT: the following HARD/blocking gates are intended in "
        "config/required_gates.txt but are MISSING from "
        "terraform/phase-0a-gcp/branch_protection.tf "
        "required_status_checks.contexts:\n"
        + textwrap.indent("\n".join(f"  - {m}" for m in missing), "  ")
        + "\n\nAdd each missing name to the contexts list in branch_protection.tf "
        "so the applied branch-protection contract matches the audit's intent."
    )
