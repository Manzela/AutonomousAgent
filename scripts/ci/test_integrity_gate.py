#!/usr/bin/env python3
"""SP-00e.4 / SP-00e.7 — test-integrity-gate (Executor Contract C6, anti-reward-hacking).

ImpossibleBench (arXiv 2510.20270): coding agents cheat by DELETING or WEAKENING tests to
make a suite pass. This gate inspects `git diff base...head` (scoped to test paths) and FAILS
the PR if it:
  - deletes an existing test function (net, ignoring in-diff renames), or
  - net-removes assertions (a proxy for assertion relaxation),
UNLESS the PR body carries a non-empty `## Test Changes` block (which the C9 cross-vendor
reviewer must APPROVE — the approval itself is enforced by the adversarial-review job; this
gate enforces the block's PRESENCE so a deletion can't land silently).

SP-00e.7 extends this gate with a coverage-floor check: if a PR's coverage.xml reports a
line_rate or branch_rate below the committed baseline (config/coverage-baseline.json), the
PR FAILS unless it carries an approved `## Test Changes` block. Pass --coverage-xml and
--coverage-baseline to enable this check.

A net-new test (or a deliberate change WITH the block) passes.

Heuristic notes (honest): assert-removal is detected on `assert` lines (pytest style, not
unittest self.assertX); the "behavior-unchanged file" qualifier is approximated by requiring
the `## Test Changes` block whenever a net removal is seen (legit refactors document it).

Usage:
    test_integrity_gate.py --diff-file <path|-> --pr-body <path>
                           [--coverage-xml <path>] [--coverage-baseline <path>]
Exit 0 iff no unexplained test deletion/weakening and (if --coverage-xml given) no coverage drop.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
from pr_meta_checks import extract_section  # noqa: E402  (sibling CI helper, reused)

# Float jitter epsilon: a PR whose coverage falls within this tolerance of the
# baseline is treated as at-or-above (avoids spurious failures from rounding).
_COVERAGE_EPSILON = 0.005

_DEL_DEF_RE = re.compile(r"^-\s*(?:async\s+)?def\s+(test_\w+)\s*\(")
_ADD_DEF_RE = re.compile(r"^\+\s*(?:async\s+)?def\s+(test_\w+)\s*\(")
_DEL_CLASS_RE = re.compile(r"^-\s*class\s+(Test\w+)\b")
_ADD_CLASS_RE = re.compile(r"^\+\s*class\s+(Test\w+)\b")
_REMOVED_ASSERT_RE = re.compile(r"^-\s*assert\b")
_REMOVED_PARAMETRIZE_RE = re.compile(r"^-.*parametrize\s*\(")


def find_deleted_tests(diff: str) -> list[str]:
    """Test functions/classes removed and NOT re-added in the same diff (net deletions).
    `\\s*` after the sign handles indented test methods inside a removed class."""
    removed: set[str] = set()
    added: set[str] = set()
    for line in diff.splitlines():
        for rm_re, add_re in ((_DEL_DEF_RE, _ADD_DEF_RE), (_DEL_CLASS_RE, _ADD_CLASS_RE)):
            mr = rm_re.match(line)
            if mr:
                removed.add(mr.group(1))
            ma = add_re.match(line)
            if ma:
                added.add(ma.group(1))
    return sorted(removed - added)


def count_removed_asserts(diff: str) -> int:
    """Count REMOVED `assert` lines. ANY removal counts (a modification removes the old line),
    so weakening by EDIT (e.g. `== 200` -> `>= 200`) is caught, not just net deletions —
    the C9-found bypass. A net-new test removes nothing, so it stays at 0."""
    return sum(1 for line in diff.splitlines() if _REMOVED_ASSERT_RE.match(line))


def count_removed_parametrize(diff: str) -> int:
    """Count REMOVED `parametrize(...)` lines — dropping a param case (a common way to 'fix' a
    failing case) removes/modifies the decorator line (C9-found bypass)."""
    return sum(1 for line in diff.splitlines() if _REMOVED_PARAMETRIZE_RE.match(line))


def has_test_changes_block(pr_body: str) -> bool:
    """True iff the PR body has a non-empty `## Test Changes` block that is not just 'None'."""
    section = extract_section(pr_body, "Test Changes")
    if section is None:
        return False
    stripped = section.strip()
    return bool(stripped) and stripped.lower() != "none"


def parse_coverage_xml(xml_path: str) -> tuple[float, float]:
    """Parse a coverage.xml (Cobertura format) and return (line_rate, branch_rate).

    Both are floats in [0, 1]. Uses stdlib xml.etree only (no third-party deps).
    Raises ValueError on malformed XML or missing attributes.
    """
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as exc:
        raise ValueError(f"malformed coverage.xml at {xml_path!r}: {exc}") from exc
    root = tree.getroot()
    try:
        line_rate = float(root.attrib["line-rate"])
        branch_rate = float(root.attrib["branch-rate"])
    except KeyError as exc:
        raise ValueError(f"coverage.xml missing required attribute {exc} at {xml_path!r}") from exc
    return line_rate, branch_rate


def load_coverage_baseline(baseline_path: str) -> tuple[float, float]:
    """Load config/coverage-baseline.json and return (line_rate, branch_rate).

    Raises ValueError on missing/malformed JSON or missing keys.
    """
    try:
        with open(baseline_path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read coverage baseline at {baseline_path!r}: {exc}") from exc
    try:
        line_rate = float(data["line_rate"])
        branch_rate = float(data["branch_rate"])
    except KeyError as exc:
        raise ValueError(f"coverage baseline missing key {exc} in {baseline_path!r}") from exc
    return line_rate, branch_rate


def evaluate_coverage_floor(
    line_rate: float,
    branch_rate: float,
    baseline_line_rate: float,
    baseline_branch_rate: float,
    pr_body: str,
) -> tuple[bool, list[str]]:
    """Check coverage rates against the baseline floor.

    Returns (ok, reasons). Fails if EITHER rate drops more than _COVERAGE_EPSILON
    below baseline AND the PR body has no approved `## Test Changes` block (C6).
    The block is the escape hatch for deliberate, reviewed coverage changes.
    """
    reasons: list[str] = []
    if line_rate < baseline_line_rate - _COVERAGE_EPSILON:
        reasons.append(
            f"line coverage {line_rate:.4f} dropped below baseline {baseline_line_rate:.4f}"
        )
    if branch_rate < baseline_branch_rate - _COVERAGE_EPSILON:
        reasons.append(
            f"branch coverage {branch_rate:.4f} dropped below baseline {baseline_branch_rate:.4f}"
        )
    if reasons and not has_test_changes_block(pr_body):
        return False, [r + " without an approved '## Test Changes' block (C6)" for r in reasons]
    return True, []


def evaluate(diff: str, pr_body: str) -> tuple[bool, list[str]]:
    """(ok, reasons). Fails if a deletion/weakening is unexplained by a `## Test Changes` block.

    Conservative by design: ANY removed assert / parametrize line (not just net deletions)
    triggers the block requirement, so edit-weakening and case-dropping are caught. The block
    is the escape hatch for deliberate, reviewer-approved test changes (C6)."""
    deleted = find_deleted_tests(diff)
    removed_asserts = count_removed_asserts(diff)
    removed_param = count_removed_parametrize(diff)
    reasons: list[str] = []
    if deleted:
        reasons.append(f"deletes test function/class(es): {', '.join(deleted)}")
    if removed_asserts:
        reasons.append(f"removes/weakens {removed_asserts} assertion line(s)")
    if removed_param:
        reasons.append(f"removes/modifies {removed_param} parametrize line(s)")
    if reasons and not has_test_changes_block(pr_body):
        return False, [r + " without an approved '## Test Changes' block (C6)" for r in reasons]
    return True, []


def _read(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path) as fh:
        return fh.read()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="test-integrity-gate (C6 / SP-00e.7)")
    ap.add_argument(
        "--diff-file", required=True, help="unified diff of test paths (or - for stdin)"
    )
    ap.add_argument("--pr-body", required=True, help="file containing the PR body markdown")
    ap.add_argument(
        "--coverage-xml",
        default=None,
        help="path to coverage.xml produced by pytest --cov (enables coverage-floor check)",
    )
    ap.add_argument(
        "--coverage-baseline",
        default="config/coverage-baseline.json",
        help="path to coverage-baseline.json (default: config/coverage-baseline.json)",
    )
    args = ap.parse_args(argv)

    pr_body = _read(args.pr_body)

    # --- existing deletion/weakening check ---
    ok, reasons = evaluate(_read(args.diff_file), pr_body)
    for r in reasons:
        print(f"::error::test-integrity: PR {r}")

    # --- coverage-floor check (SP-00e.7, additive) ---
    cov_ok = True
    if args.coverage_xml:
        try:
            line_rate, branch_rate = parse_coverage_xml(args.coverage_xml)
            baseline_line, baseline_branch = load_coverage_baseline(args.coverage_baseline)
        except ValueError as exc:
            print(f"::error::test-integrity: coverage-floor setup failed: {exc}")
            return 1
        cov_ok, cov_reasons = evaluate_coverage_floor(
            line_rate, branch_rate, baseline_line, baseline_branch, pr_body
        )
        for r in cov_reasons:
            print(f"::error::test-integrity: PR {r}")
        if cov_ok:
            print(
                f"== coverage-floor: PASS "
                f"(line={line_rate:.4f}≥{baseline_line:.4f}, "
                f"branch={branch_rate:.4f}≥{baseline_branch:.4f}) =="
            )
        else:
            print(
                f"== coverage-floor: FAIL "
                f"(line={line_rate:.4f} vs baseline {baseline_line:.4f}, "
                f"branch={branch_rate:.4f} vs baseline {baseline_branch:.4f}) =="
            )

    all_ok = ok and cov_ok
    if all_ok:
        print("== test-integrity-gate: PASS (no unexplained test deletion/weakening) ==")
        return 0
    total = len(reasons) + (0 if cov_ok else 1)
    print(f"== test-integrity-gate: FAIL ({total} issue(s)) ==")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
