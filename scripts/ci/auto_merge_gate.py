#!/usr/bin/env python3
"""SP-12 — Auto-merge gate: evaluate PR check results for eval-gated auto-merge.

Called from `.github/workflows/sp12-auto-merge.yml` with the PR number and
the required-status-checks list. Fetches the current check-suite results via
the GitHub API and applies `AutoMergePolicy.evaluate()`.

Exits 0 if the PR can auto-merge; exits 1 with a descriptive message otherwise.

Usage:
    python scripts/ci/auto_merge_gate.py --pr-number 278 --repo owner/name

Environment:
    GH_TOKEN  — GitHub token with `pull_requests: read` + `checks: read` scope
                 (injected by the workflow as the GITHUB_TOKEN or App token).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.core.ci_self_heal import AutoMergePolicy, CheckResult


def _gh_json(args: list[str]) -> dict | list:
    raise NotImplementedError("use _gh_api_json below")


def _gh_api_json(path: str) -> dict | list:
    result = subprocess.run(
        ["gh", "api", path],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _fetch_required_check_names(repo: str, branch: str) -> list[str]:
    """Return the names of required status checks for the branch."""
    try:
        data = _gh_api_json(f"repos/{repo}/branches/{branch}/protection")
        return [c["context"] for c in data.get("required_status_checks", {}).get("contexts", [])]
    except subprocess.CalledProcessError:
        return []


def _fetch_pr_checks(repo: str, pr_number: int) -> list[CheckResult]:
    """Fetch PR check results and convert to CheckResult objects."""
    data = _gh_api_json(f"repos/{repo}/pulls/{pr_number}")
    head_sha = data["head"]["sha"]
    base_ref = data["base"]["ref"]

    required_names = set(_fetch_required_check_names(repo, base_ref))

    check_data = _gh_api_json(f"repos/{repo}/commits/{head_sha}/check-suites")
    results: list[CheckResult] = []
    seen: set[str] = set()

    for suite in check_data.get("check_suites", []):
        suite_id = suite["id"]
        runs_data = _gh_api_json(f"repos/{repo}/check-suites/{suite_id}/check-runs")
        for run in runs_data.get("check_runs", []):
            name = run["name"]
            if name in seen:
                continue
            seen.add(name)
            results.append(
                CheckResult(
                    name=name,
                    conclusion=run.get("conclusion"),
                    required=name in required_names,
                )
            )

    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SP-12 eval-gated auto-merge gate")
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args(argv)

    if not args.repo:
        print("ERROR: --repo or GITHUB_REPOSITORY env var required", file=sys.stderr)
        return 1

    try:
        checks = _fetch_pr_checks(args.repo, args.pr_number)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: GitHub API call failed: {exc}", file=sys.stderr)
        print(exc.stderr, file=sys.stderr)
        return 1

    policy = AutoMergePolicy()
    decision = policy.evaluate(checks)

    if decision.can_merge:
        print(f"AUTO-MERGE: APPROVED — {decision.reason}")
        return 0
    else:
        print(f"AUTO-MERGE: BLOCKED — {decision.reason}")
        if decision.blocking_checks:
            print("Blocking checks:")
            for name in decision.blocking_checks:
                print(f"  ✗ {name}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
