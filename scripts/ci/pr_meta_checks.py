#!/usr/bin/env python3
"""SP-00e.2 — PR-meta enforcement: evidence-present (C1) + test-truth (C7).

These run in CI against the PR body + the pytest `--junitxml` artifact the job itself
produces, so the AUTHORITATIVE numbers come from CI, never executor prose:

  - C1 evidence-present: the PR's `## Evidence` section must exist and contain >= 1 fenced
    command block. (The deeper C1 "re-run each fenced command and diff vs the artifact" is
    layered on by the workflow, which re-runs them; this module gates presence + extracts
    the commands.)
  - C7 test-truth: the PR's `## Test Truth` value must byte-contain the canonical counts
    line derived from the junitxml — a fabricated count (the meta-audit's "541" move) FAILS.

Pure functions (parse/extract/compare) are unit-tested directly; the CLI wires them to files.
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET

_HEADER_RE = re.compile(r"^\s*##\s+(.*\S)\s*$")
_FENCE_RE = re.compile(r"^\s*```")


def parse_junit_counts(xml_text: str) -> dict[str, int]:
    """Sum every <testsuite> in a junitxml; derive passed = tests - failures - errors - skipped."""
    root = ET.fromstring(xml_text)
    suites = list(root.iter("testsuite"))
    tests = failures = errors = skipped = 0
    for s in suites:
        tests += int(s.get("tests", 0) or 0)
        failures += int(s.get("failures", 0) or 0)
        errors += int(s.get("errors", 0) or 0)
        skipped += int(s.get("skipped", 0) or 0)
    passed = tests - failures - errors - skipped
    return {
        "collected": tests,
        "passed": passed,
        "failed": failures,
        "skipped": skipped,
        "errors": errors,
    }


def canonical_test_truth(counts: dict[str, int]) -> str:
    return (
        f"collected={counts['collected']} passed={counts['passed']} "
        f"failed={counts['failed']} skipped={counts['skipped']} errors={counts['errors']}"
    )


def extract_section(md: str, header: str) -> str | None:
    """Return the body of a `## <header>` section (up to the next `## ` header), or None."""
    collecting = False
    body: list[str] = []
    for line in md.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            if collecting:
                break  # next section starts
            if m.group(1).strip().lower() == header.strip().lower():
                collecting = True
            continue
        if collecting:
            body.append(line)
    return "\n".join(body) if collecting else None


def extract_fenced_blocks(section: str) -> list[str]:
    """Return the trimmed contents of each ``` fenced block in a section."""
    blocks: list[str] = []
    cur: list[str] | None = None
    for line in section.splitlines():
        if _FENCE_RE.match(line):
            if cur is None:
                cur = []
            else:
                blocks.append("\n".join(cur).strip())
                cur = None
        elif cur is not None:
            cur.append(line)
    return [b for b in blocks if b]


def check_evidence_present(pr_body: str) -> tuple[bool, str]:
    section = extract_section(pr_body, "Evidence")
    if section is None:
        return (
            False,
            "missing `## Evidence` section (C1: evidence = a re-runnable command, not prose)",
        )
    blocks = extract_fenced_blocks(section)
    if not blocks:
        return (
            False,
            "`## Evidence` has no fenced command block (C1: paste the re-run command, fenced)",
        )
    return True, f"evidence present ({len(blocks)} fenced block(s))"


def check_test_truth(pr_body: str, counts: dict[str, int]) -> tuple[bool, str]:
    section = extract_section(pr_body, "Test Truth")
    canonical = canonical_test_truth(counts)
    if section is None:
        return False, f"missing `## Test Truth` section (C7: must byte-match '{canonical}')"
    if canonical in section:
        return True, f"test-truth matches the junitxml artifact: {canonical}"
    return False, (
        f"`## Test Truth` does NOT match the junitxml artifact. Expected to contain: '{canonical}'"
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="PR-meta checks: evidence-present (C1) + test-truth (C7)"
    )
    ap.add_argument("--pr-body", required=True, help="file containing the PR body markdown")
    ap.add_argument("--junitxml", required=True, help="pytest --junitxml artifact")
    ap.add_argument(
        "--emit-test-truth",
        action="store_true",
        help="print the canonical test-truth line (for the CI bot comment) and exit 0",
    )
    ap.add_argument(
        "--check",
        choices=["evidence", "test-truth", "all"],
        default="all",
        help="which checks to HARD-gate (default all). The workflow hard-gates 'evidence' and "
        "emits test-truth informationally until SP-00c gives a CI-green full suite to byte-match.",
    )
    args = ap.parse_args(argv)

    with open(args.junitxml) as fh:
        counts = parse_junit_counts(fh.read())

    if args.emit_test_truth:
        print(canonical_test_truth(counts))
        return 0

    with open(args.pr_body) as fh:
        body = fh.read()

    selected = {
        "evidence-present (C1)": check_evidence_present(body),
        "test-truth (C7)": check_test_truth(body, counts),
    }
    if args.check == "evidence":
        selected.pop("test-truth (C7)")
    elif args.check == "test-truth":
        selected.pop("evidence-present (C1)")

    ok = True
    for label, (passed, msg) in selected.items():
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {label}: {msg}")
        if not passed:
            print(f"::error::{label}: {msg}")
        ok &= passed
    print(f"== pr-meta-checks: {'PASS' if ok else 'FAIL'} ==")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
