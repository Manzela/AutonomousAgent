"""Tests for SP-00e.2 PR-meta checks: evidence-present (C1) + test-truth (C7).

C1 (evidence): a PR must carry a non-empty `## Evidence` section with at least one
fenced command block — pasted prose alone is not evidence; the CI re-run is authoritative.
C7 (test-truth): the `## Test Truth` value must byte-match the canonical counts derived
from the CI junitxml artifact — a fabricated count (the meta-audit's "541" move) FAILS.

Pure functions under test (no network, no mocks of the thing under test).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ci"))

import pr_meta_checks as pmc  # noqa: E402

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="2" skipped="3" tests="10" time="1.2">
    <testcase classname="t" name="a"/>
  </testsuite>
</testsuites>
"""


def test_parse_junit_counts_derives_passed():
    c = pmc.parse_junit_counts(JUNIT)
    assert c["collected"] == 10
    assert c["failed"] == 2
    assert c["errors"] == 1
    assert c["skipped"] == 3
    assert c["passed"] == 4  # 10 - 2 - 1 - 3


def test_parse_junit_counts_sums_multiple_suites():
    xml = (
        "<testsuites>"
        '<testsuite tests="3" failures="1" errors="0" skipped="0"/>'
        '<testsuite tests="2" failures="0" errors="0" skipped="1"/>'
        "</testsuites>"
    )
    c = pmc.parse_junit_counts(xml)
    assert c["collected"] == 5
    assert c["failed"] == 1
    assert c["skipped"] == 1
    assert c["passed"] == 3  # 5 - 1 - 0 - 1


def test_canonical_test_truth_line_is_stable():
    line = pmc.canonical_test_truth(pmc.parse_junit_counts(JUNIT))
    assert line == "collected=10 passed=4 failed=2 skipped=3 errors=1"


def test_extract_section_returns_body():
    md = "## Summary\nhi\n\n## Evidence\n```\necho ok\n```\n\n## Related\n- x\n"
    body = pmc.extract_section(md, "Evidence")
    assert "echo ok" in body
    assert "Related" not in body  # stops at the next header


def test_extract_section_missing_returns_none():
    assert pmc.extract_section("## Summary\nx\n", "Evidence") is None


def test_extract_fenced_blocks():
    section = "intro\n```\ncmd one\n```\nmid\n```bash\ncmd two\n```\n"
    blocks = pmc.extract_fenced_blocks(section)
    assert blocks == ["cmd one", "cmd two"]


def test_check_evidence_present_fails_when_section_absent():
    ok, msg = pmc.check_evidence_present("## Summary\nno evidence here\n")
    assert ok is False
    assert "Evidence" in msg


def test_check_evidence_present_fails_when_no_fenced_block():
    ok, _ = pmc.check_evidence_present("## Evidence\njust prose, no fence\n")
    assert ok is False


def test_check_evidence_present_passes_with_fenced_command():
    ok, _ = pmc.check_evidence_present("## Evidence\n```\npytest -q\n```\n")
    assert ok is True


def test_check_test_truth_passes_on_byte_match():
    counts = pmc.parse_junit_counts(JUNIT)
    canonical = pmc.canonical_test_truth(counts)
    body = f"## Test Truth\n{canonical}\n"
    ok, _ = pmc.check_test_truth(body, counts)
    assert ok is True


def test_check_test_truth_fails_on_fabricated_count():
    """The meta-audit '541' fabrication: stated counts that don't match the artifact FAIL."""
    counts = pmc.parse_junit_counts(JUNIT)
    body = "## Test Truth\ncollected=541 passed=541 failed=0 skipped=0 errors=0\n"
    ok, msg = pmc.check_test_truth(body, counts)
    assert ok is False
    assert "541" in msg or "match" in msg.lower()


def test_check_test_truth_fails_when_section_absent():
    counts = pmc.parse_junit_counts(JUNIT)
    ok, _ = pmc.check_test_truth("## Summary\nno truth block\n", counts)
    assert ok is False
