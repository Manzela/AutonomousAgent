"""SP-12 — Autonomous CI/CD self-heal policy.

Core data types and policy logic for:
  - AutoMergePolicy: decides if a PR can auto-merge given a set of check results.
    Rule: ALL required checks must pass. The SP-06 eval gate (identified by name
    fragment) is REQUIRED even when not in the static required-checks list — a PR
    that passes lint/unit but fails the eval gate is BLOCKED.
  - SelfHealEvent: structured representation of a repository_dispatch `ci-failure`
    payload. Parsed from the workflow's client_payload JSON.
  - AutoMergeDecision: the verdict (can_merge + reason + list of blocking checks).

All types are pure-Python dataclasses (no I/O) to keep this unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CheckResult:
    """A single CI check result on a PR.

    Args:
        name: Human-readable check name (e.g. "Unit Tests", "eval-gate").
        conclusion: Reported conclusion — "success" | "failure" | "cancelled"
            | "skipped" | "neutral" | "action_required" | "timed_out" | "stale"
            | None (still running).
        required: Whether this check is in the branch-protection required list.
    """

    name: str
    conclusion: Optional[str]
    required: bool

    @property
    def passing(self) -> bool:
        return self.conclusion in ("success", "neutral", "skipped")


@dataclass(frozen=True)
class AutoMergeDecision:
    can_merge: bool
    reason: str
    blocking_checks: list[str] = field(default_factory=list)


class AutoMergePolicy:
    """Eval-gated auto-merge policy (SP-12 / PRD §6).

    The policy implements two invariants:
      1. Every required check must be passing.
      2. The SP-06 eval gate must be present AND passing — a PR that is
         lint+unit GREEN but eval RED is BLOCKED (the negative path the PRD
         specifically requires).

    The eval gate is identified by any name that contains one of the
    EVAL_GATE_FRAGMENTS; the match is case-insensitive.
    """

    EVAL_GATE_FRAGMENTS: tuple[str, ...] = (
        "eval-gate",
        "eval gate",
        "sp-06",
        "blocking prd",
        "conformance",
    )

    def _is_eval_gate(self, name: str) -> bool:
        lower = name.lower()
        return any(frag in lower for frag in self.EVAL_GATE_FRAGMENTS)

    def evaluate(self, checks: list[CheckResult]) -> AutoMergeDecision:
        blocking: list[str] = []

        # 1. All required checks must be passing.
        for c in checks:
            if c.required and not c.passing:
                blocking.append(c.name)

        # 2. Eval gate must be present and passing.
        eval_gates = [c for c in checks if self._is_eval_gate(c.name)]
        if not eval_gates:
            return AutoMergeDecision(
                can_merge=False,
                reason=(
                    "SP-06 eval gate not present in check results — "
                    "auto-merge requires explicit eval gate pass (never lint-only)"
                ),
                blocking_checks=blocking,
            )

        eval_failing = [c for c in eval_gates if not c.passing]
        for c in eval_failing:
            if c.name not in blocking:
                blocking.append(c.name)

        if blocking:
            return AutoMergeDecision(
                can_merge=False,
                reason=f"{len(blocking)} blocking check(s) did not pass",
                blocking_checks=blocking,
            )

        return AutoMergeDecision(
            can_merge=True,
            reason="all required checks pass including the SP-06 eval gate",
            blocking_checks=[],
        )


@dataclass(frozen=True)
class SelfHealEvent:
    """Structured payload of a `repository_dispatch` `ci-failure` event.

    The workflow emits this when CI fails on a PR so the self-heal agent
    can pick up the failure and open a fix PR.

    Args:
        workflow_name: Name of the failed CI workflow (e.g. "CI").
        run_id: GitHub Actions run ID.
        sha: Head commit SHA of the failing run.
        ref: Branch ref (e.g. "refs/heads/feat/sp-12").
        pr_number: Originating PR number, if available (None for push-to-branch runs).
        failed_jobs: Names of jobs that reported failure.
        repository: full repository name "owner/repo".
    """

    workflow_name: str
    run_id: int
    sha: str
    ref: str
    failed_jobs: list[str]
    repository: str
    pr_number: Optional[int] = None

    @classmethod
    def from_dispatch_payload(cls, payload: dict) -> "SelfHealEvent":
        """Parse from the client_payload dict of a repository_dispatch event.

        Raises:
            ValueError: if required fields are absent or have wrong types.
        """
        required = ("workflow_name", "run_id", "sha", "ref", "repository")
        for key in required:
            if key not in payload:
                raise ValueError(f"SelfHealEvent: missing required field '{key}'")

        run_id = payload["run_id"]
        if not isinstance(run_id, int) or run_id <= 0:
            raise ValueError(f"SelfHealEvent: run_id must be a positive int, got {run_id!r}")

        sha = str(payload["sha"])
        if len(sha) < 7:
            raise ValueError(f"SelfHealEvent: sha too short: {sha!r}")

        raw_jobs = payload.get("failed_jobs", [])
        if not isinstance(raw_jobs, list):
            raise ValueError("SelfHealEvent: failed_jobs must be a list")
        failed_jobs = list(raw_jobs)

        pr_number = payload.get("pr_number")
        if pr_number is not None and not isinstance(pr_number, int):
            raise ValueError(f"SelfHealEvent: pr_number must be int or None, got {pr_number!r}")

        return cls(
            workflow_name=str(payload["workflow_name"]),
            run_id=run_id,
            sha=sha,
            ref=str(payload["ref"]),
            failed_jobs=failed_jobs,
            repository=str(payload["repository"]),
            pr_number=pr_number,
        )

    def fix_branch_name(self) -> str:
        """Return the branch name the self-heal agent should create for the fix."""
        short_sha = self.sha[:8]
        return f"fix/self-heal-{self.run_id}-{short_sha}"
