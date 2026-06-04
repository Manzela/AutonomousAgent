"""SP-12 hermetic unit tests for CI self-heal policy.

No GitHub API calls, no network, no credentials required.

Oracles:
  1.  eval gate absent → BLOCKED (never lint-only auto-merge).
  2.  eval gate present + passing + all required passing → can_merge True.
  3.  eval gate passing but a required check failing → BLOCKED (required check rule).
  4.  eval gate failing → BLOCKED (eval gate rule).
  5.  eval gate RED + all other required GREEN → BLOCKED (negative PRD path).
  6.  no required checks at all but eval gate GREEN → can_merge True.
  7.  eval gate identified by "sp-06" fragment (case-insensitive).
  8.  eval gate identified by "conformance" fragment (case-insensitive).
  9.  eval gate identified by "blocking prd" fragment.
  10. SelfHealEvent.from_dispatch_payload parses valid payload.
  11. SelfHealEvent: missing required field raises ValueError.
  12. SelfHealEvent: non-int run_id raises ValueError.
  13. SelfHealEvent: short sha raises ValueError.
  14. SelfHealEvent: non-list failed_jobs raises ValueError.
  15. SelfHealEvent: pr_number=None is allowed (push run, no PR).
  16. SelfHealEvent.fix_branch_name includes run_id + short sha.
"""

from __future__ import annotations

import pytest

from app.core.ci_self_heal import AutoMergePolicy, CheckResult, SelfHealEvent


# ── Helpers ───────────────────────────────────────────────────────────────────


def _check(name: str, *, conclusion: str = "success", required: bool = True) -> CheckResult:
    return CheckResult(name=name, conclusion=conclusion, required=required)


def _eval_gate(*, conclusion: str = "success") -> CheckResult:
    return CheckResult(
        name="Blocking PRD-conformance eval gate", conclusion=conclusion, required=True
    )


POLICY = AutoMergePolicy()


# ── Oracle 1 — eval gate absent → BLOCKED ─────────────────────────────────────


def test_eval_gate_absent_blocks_merge():
    checks = [
        _check("Lint Python"),
        _check("Unit Tests"),
    ]
    decision = POLICY.evaluate(checks)
    assert decision.can_merge is False
    assert "eval gate" in decision.reason.lower()


# ── Oracle 2 — all required pass + eval gate pass → can_merge True ───────────


def test_all_pass_including_eval_gate_can_merge():
    checks = [
        _check("Lint Python"),
        _check("Unit Tests"),
        _eval_gate(),
    ]
    decision = POLICY.evaluate(checks)
    assert decision.can_merge is True
    assert decision.blocking_checks == []


# ── Oracle 3 — required check failing → BLOCKED ───────────────────────────────


def test_required_check_failing_blocks():
    checks = [
        _check("Unit Tests", conclusion="failure"),
        _eval_gate(),
    ]
    decision = POLICY.evaluate(checks)
    assert decision.can_merge is False
    assert "Unit Tests" in decision.blocking_checks


# ── Oracle 4 — eval gate failing → BLOCKED ────────────────────────────────────


def test_eval_gate_failing_blocks():
    checks = [
        _check("Lint Python"),
        _check("Unit Tests"),
        _eval_gate(conclusion="failure"),
    ]
    decision = POLICY.evaluate(checks)
    assert decision.can_merge is False
    assert any(
        "eval gate" in name.lower() or "conformance" in name.lower()
        for name in decision.blocking_checks
    )


# ── Oracle 5 — PRD negative path: lint+unit GREEN, eval RED → BLOCKED ────────


def test_lint_unit_green_eval_red_blocks():
    checks = [
        _check("Lint Python", conclusion="success"),
        _check("Unit Tests", conclusion="success"),
        _check("Blocking PRD-conformance eval gate", conclusion="failure", required=True),
    ]
    decision = POLICY.evaluate(checks)
    assert decision.can_merge is False, "lint+unit GREEN eval RED must be BLOCKED"
    assert "Blocking PRD-conformance eval gate" in decision.blocking_checks


# ── Oracle 6 — no required checks but eval gate GREEN → can_merge True ───────


def test_no_required_checks_but_eval_gate_passes():
    checks = [
        CheckResult(name="advisory lint", conclusion="failure", required=False),
        CheckResult(name="eval-gate", conclusion="success", required=False),
    ]
    decision = POLICY.evaluate(checks)
    assert decision.can_merge is True


# ── Oracle 7 — "sp-06" fragment ───────────────────────────────────────────────


def test_eval_gate_identified_by_sp06_fragment():
    checks = [
        _check("Lint Python"),
        CheckResult(name="SP-06 scope gate", conclusion="success", required=True),
    ]
    decision = POLICY.evaluate(checks)
    assert decision.can_merge is True


# ── Oracle 8 — "conformance" fragment ────────────────────────────────────────


def test_eval_gate_identified_by_conformance_fragment():
    checks = [
        CheckResult(name="PRD-conformance-gate", conclusion="success", required=True),
    ]
    decision = POLICY.evaluate(checks)
    assert decision.can_merge is True


# ── Oracle 9 — "blocking prd" fragment ───────────────────────────────────────


def test_eval_gate_identified_by_blocking_prd_fragment():
    checks = [
        CheckResult(name="Blocking PRD checks", conclusion="success", required=True),
    ]
    decision = POLICY.evaluate(checks)
    assert decision.can_merge is True


# ── Oracle 10 — SelfHealEvent.from_dispatch_payload valid ────────────────────


def test_self_heal_event_from_valid_payload():
    payload = {
        "workflow_name": "CI",
        "run_id": 12345678,
        "sha": "abc1234defgh5678",
        "ref": "refs/heads/feat/sp-12",
        "repository": "Manzela/AutonomousAgent",
        "failed_jobs": ["Unit Tests", "Lint Python"],
        "pr_number": 42,
    }
    evt = SelfHealEvent.from_dispatch_payload(payload)
    assert evt.workflow_name == "CI"
    assert evt.run_id == 12345678
    assert evt.sha == "abc1234defgh5678"
    assert evt.failed_jobs == ["Unit Tests", "Lint Python"]
    assert evt.pr_number == 42


# ── Oracle 11 — missing required field raises ValueError ─────────────────────


def test_self_heal_event_missing_field_raises():
    payload = {
        "run_id": 1,
        "sha": "abcdef1234567890",  # pragma: allowlist secret
        "ref": "refs/heads/main",
        "repository": "Manzela/AutonomousAgent",
        # missing workflow_name
    }
    with pytest.raises(ValueError, match="workflow_name"):
        SelfHealEvent.from_dispatch_payload(payload)


# ── Oracle 12 — non-int run_id raises ValueError ─────────────────────────────


def test_self_heal_event_bad_run_id_raises():
    payload = {
        "workflow_name": "CI",
        "run_id": "not-an-int",
        "sha": "abcdef1234567890",  # pragma: allowlist secret
        "ref": "refs/heads/main",
        "repository": "Manzela/AutonomousAgent",
    }
    with pytest.raises(ValueError, match="run_id"):
        SelfHealEvent.from_dispatch_payload(payload)


# ── Oracle 13 — short sha raises ValueError ──────────────────────────────────


def test_self_heal_event_short_sha_raises():
    payload = {
        "workflow_name": "CI",
        "run_id": 1,
        "sha": "abc",
        "ref": "refs/heads/main",
        "repository": "Manzela/AutonomousAgent",
    }
    with pytest.raises(ValueError, match="sha"):
        SelfHealEvent.from_dispatch_payload(payload)


# ── Oracle 14 — non-list failed_jobs raises ValueError ───────────────────────


def test_self_heal_event_bad_failed_jobs_raises():
    payload = {
        "workflow_name": "CI",
        "run_id": 1,
        "sha": "abcdef1234567890",  # pragma: allowlist secret
        "ref": "refs/heads/main",
        "repository": "Manzela/AutonomousAgent",
        "failed_jobs": "not-a-list",
    }
    with pytest.raises(ValueError, match="failed_jobs"):
        SelfHealEvent.from_dispatch_payload(payload)


# ── Oracle 15 — pr_number=None allowed ───────────────────────────────────────


def test_self_heal_event_no_pr_number():
    payload = {
        "workflow_name": "CI",
        "run_id": 99,
        "sha": "deadbeef12345678",  # pragma: allowlist secret
        "ref": "refs/heads/main",
        "repository": "Manzela/AutonomousAgent",
    }
    evt = SelfHealEvent.from_dispatch_payload(payload)
    assert evt.pr_number is None


# ── Oracle 16 — fix_branch_name includes run_id + short sha ─────────────────


def test_self_heal_event_fix_branch_name():
    payload = {
        "workflow_name": "CI",
        "run_id": 12345678,
        "sha": "deadbeef1234abcd",  # pragma: allowlist secret
        "ref": "refs/heads/feat/sp-12",
        "repository": "Manzela/AutonomousAgent",
    }
    evt = SelfHealEvent.from_dispatch_payload(payload)
    branch = evt.fix_branch_name()
    assert "12345678" in branch
    assert "deadbeef" in branch
    assert branch.startswith("fix/self-heal-")
