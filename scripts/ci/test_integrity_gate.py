#!/usr/bin/env python3
"""SP-00e.4 — test-integrity-gate (Executor Contract C6, anti-reward-hacking).

ImpossibleBench (arXiv 2510.20270): coding agents cheat by DELETING or WEAKENING tests to
make a suite pass. This gate inspects `git diff base...head` (scoped to test paths) and FAILS
the PR if it:
  - deletes an existing test function (net, ignoring in-diff renames), or
  - net-removes assertions (a proxy for assertion relaxation),
UNLESS the PR body carries a non-empty `## Test Changes` block (which the C9 cross-vendor
reviewer must APPROVE — the approval itself is enforced by the adversarial-review job; this
gate enforces the block's PRESENCE so a deletion can't land silently).

A net-new test (or a deliberate change WITH the block) passes. Coverage-floor enforcement
(`coverage-baseline.json`) is a separate C6 piece deferred until SP-00c gives a CI-green
suite to baseline against.

Heuristic notes (honest): assert-removal is detected on `assert` lines (pytest style, not
unittest self.assertX); the "behavior-unchanged file" qualifier is approximated by requiring
the `## Test Changes` block whenever a net removal is seen (legit refactors document it).

Usage:
    test_integrity_gate.py --diff-file <path|-> --pr-body <path>
Exit 0 iff no unexplained test deletion/weakening.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from pr_meta_checks import extract_section  # noqa: E402  (sibling CI helper, reused)

_DEF_RE = re.compile(r"^([+-])\s*(?:async\s+)?def\s+(test_\w+)\s*\(")
_REMOVED_ASSERT_RE = re.compile(r"^-\s*assert\b")
_ADDED_ASSERT_RE = re.compile(r"^\+\s*assert\b")


def find_deleted_test_funcs(diff: str) -> list[str]:
    """Return test functions removed and NOT re-added in the same diff (net deletions)."""
    removed: set[str] = set()
    added: set[str] = set()
    for line in diff.splitlines():
        m = _DEF_RE.match(line)
        if not m:
            continue
        (removed if m.group(1) == "-" else added).add(m.group(2))
    return sorted(removed - added)


def count_net_removed_asserts(diff: str) -> int:
    """Net removed `assert` lines (removed - added), floored at 0."""
    removed = added = 0
    for line in diff.splitlines():
        if _REMOVED_ASSERT_RE.match(line):
            removed += 1
        elif _ADDED_ASSERT_RE.match(line):
            added += 1
    return max(0, removed - added)


def has_test_changes_block(pr_body: str) -> bool:
    """True iff the PR body has a non-empty `## Test Changes` block that is not just 'None'."""
    section = extract_section(pr_body, "Test Changes")
    if section is None:
        return False
    stripped = section.strip()
    return bool(stripped) and stripped.lower() != "none"


def evaluate(diff: str, pr_body: str) -> tuple[bool, list[str]]:
    """(ok, reasons). Fails if a deletion/weakening is unexplained by a `## Test Changes` block."""
    deleted = find_deleted_test_funcs(diff)
    net_removed = count_net_removed_asserts(diff)
    reasons: list[str] = []
    if deleted:
        reasons.append(f"deletes test function(s): {', '.join(deleted)}")
    if net_removed > 0:
        reasons.append(f"net-removes {net_removed} assertion(s)")
    if reasons and not has_test_changes_block(pr_body):
        return False, [r + " without an approved '## Test Changes' block (C6)" for r in reasons]
    return True, []


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path) as fh:
        return fh.read()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="test-integrity-gate (C6)")
    ap.add_argument(
        "--diff-file", required=True, help="unified diff of test paths (or - for stdin)"
    )
    ap.add_argument("--pr-body", required=True, help="file containing the PR body markdown")
    args = ap.parse_args(argv)

    ok, reasons = evaluate(_read(args.diff_file), _read(args.pr_body))
    for r in reasons:
        print(f"::error::test-integrity: PR {r}")
    if ok:
        print("== test-integrity-gate: PASS (no unexplained test deletion/weakening) ==")
        return 0
    print(f"== test-integrity-gate: FAIL ({len(reasons)} issue(s)) ==")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
