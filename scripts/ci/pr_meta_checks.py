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

Bot exemption: automated dependency-bump PRs authored by a known bot login (see
BOT_AUTHORS) are exempt from the C1 evidence + C7 test-truth requirements.  Their
green build/test/trivy CI IS the evidence.  Pass PR_AUTHOR=<login> via env var (set
from `github.event.pull_request.user.login` in the workflow).

Pure functions (parse/extract/compare) are unit-tested directly; the CLI wires them to files.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

# Automated dependency-bump bots whose PRs are exempt from C1/C7.
# Keep this list narrow: only genuine automated bots, not human/agent authors.
BOT_AUTHORS: frozenset[str] = frozenset(
    {
        "dependabot[bot]",
        "github-actions[bot]",
    }
)

_HEADER_RE = re.compile(r"^\s*##\s+(.*\S)\s*$")
_FENCE_RE = re.compile(r"^\s*```")


def parse_junit_counts(xml_text: str) -> dict[str, int]:
    """Sum every <testsuite> in a junitxml; derive passed = tests - failures - errors - skipped.

    Raises ValueError (not a raw ParseError/crash) on malformed XML or non-integer count
    attributes, so the CI gate fails cleanly with a legible message.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"malformed junitxml: {e}") from e
    suites = list(root.iter("testsuite"))
    tests = failures = errors = skipped = 0
    for s in suites:
        try:
            tests += int(s.get("tests", 0) or 0)
            failures += int(s.get("failures", 0) or 0)
            errors += int(s.get("errors", 0) or 0)
            skipped += int(s.get("skipped", 0) or 0)
        except (TypeError, ValueError) as e:
            raise ValueError(f"non-integer junitxml count attribute: {e}") from e
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
    """Return the body of a `## <header>` section (up to the next `## ` header), or None.

    Code-fence-aware: a `## ...` line INSIDE a ``` fenced block is section content, not a
    boundary (so an Evidence block containing shell comments like `## note` does not truncate).
    """
    collecting = False
    in_fence = False
    body: list[str] = []
    target = header.strip().lower()
    for line in md.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            if collecting:
                body.append(line)
            continue
        m = _HEADER_RE.match(line)
        if m and not in_fence:
            if collecting:
                break  # next section starts
            htext = m.group(1).strip().lower()
            # Match the exact header OR a §4-annotated one ("## Evidence (C1)"), but not a
            # different header that merely shares a prefix ("## Evidenced").
            if htext == target or htext.startswith(target + " "):
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


def is_bot_pr(author: str) -> bool:
    """Return True when the PR author is a known automated dependency-bump bot."""
    return author.strip() in BOT_AUTHORS


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
    ap.add_argument(
        "--pr-author",
        default=os.environ.get("PR_AUTHOR", ""),
        help="PR author login (also read from PR_AUTHOR env var). "
        "Automated dependency-bot PRs (see BOT_AUTHORS) are exempt from C1/C7.",
    )
    args = ap.parse_args(argv)

    try:
        with open(args.junitxml) as fh:
            counts = parse_junit_counts(fh.read())
    except (OSError, ValueError) as e:
        print(f"::error::pr-meta-checks: cannot read/parse junitxml: {e}")
        return 1

    if args.emit_test_truth:
        print(canonical_test_truth(counts))
        return 0

    # Bot dependency PRs: green CI (build/tests/trivy) IS the evidence.
    # Skip C1/C7 prose checks and exit 0 so the required check stays GREEN.
    if is_bot_pr(args.pr_author):
        print(
            f"[SKIP] bot dependency PR (author={args.pr_author!r}) — "
            "evidence requirement waived; CI gates are the evidence"
        )
        print("== pr-meta-checks: PASS (bot-exempt) ==")
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
