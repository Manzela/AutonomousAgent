"""Tests for SP-00e.7 coverage-floor check in test_integrity_gate.py (C6).

The coverage-floor check is an EXTENSION of the existing test-integrity-gate:
if a PR's coverage.xml reports line_rate or branch_rate below the committed
baseline (config/coverage-baseline.json), the PR FAILS unless it carries an
approved `## Test Changes` block.

These tests exercise evaluate_coverage_floor() directly with tiny inline
fixtures (no file I/O needed in the pure-function tests), then exercise the
parse_coverage_xml() / load_coverage_baseline() helpers with tempfile fixtures.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ci"))

import test_integrity_gate as tig  # noqa: E402

# ---------------------------------------------------------------------------
# Baseline values used across tests
# ---------------------------------------------------------------------------
_BASELINE_LINE = 0.37
_BASELINE_BRANCH = 0.45
_ABOVE_LINE = 0.40  # clearly above baseline
_ABOVE_BRANCH = 0.50  # clearly above baseline
_BELOW_LINE = 0.30  # clearly below baseline
_BELOW_BRANCH = 0.35  # clearly below baseline
_AT_LINE = 0.37  # exactly at baseline
_AT_BRANCH = 0.45  # exactly at baseline

_BODY_NO_BLOCK = "## Summary\nsome change\n"
_BODY_WITH_BLOCK = "## Test Changes\nRefactored suite; coverage drop is intentional.\n"


# ---------------------------------------------------------------------------
# evaluate_coverage_floor — pure function tests
# ---------------------------------------------------------------------------


def test_above_baseline_no_block_passes():
    """Coverage above baseline never needs a block — always passes."""
    ok, reasons = tig.evaluate_coverage_floor(
        _ABOVE_LINE, _ABOVE_BRANCH, _BASELINE_LINE, _BASELINE_BRANCH, _BODY_NO_BLOCK
    )
    assert ok is True
    assert reasons == []


def test_at_baseline_no_block_passes():
    """Coverage exactly at baseline passes without a block (boundary condition)."""
    ok, reasons = tig.evaluate_coverage_floor(
        _AT_LINE, _AT_BRANCH, _BASELINE_LINE, _BASELINE_BRANCH, _BODY_NO_BLOCK
    )
    assert ok is True
    assert reasons == []


def test_below_baseline_no_block_fails():
    """Coverage below baseline WITHOUT a ## Test Changes block → gate FAIL."""
    ok, reasons = tig.evaluate_coverage_floor(
        _BELOW_LINE, _BELOW_BRANCH, _BASELINE_LINE, _BASELINE_BRANCH, _BODY_NO_BLOCK
    )
    assert ok is False
    assert len(reasons) >= 1
    # Both line and branch are below — both should be reported
    assert any("line coverage" in r for r in reasons)
    assert any("branch coverage" in r for r in reasons)
    # The C6 block requirement must be mentioned
    assert all("## Test Changes" in r for r in reasons)


def test_below_baseline_with_approved_block_passes():
    """Coverage below baseline WITH an approved ## Test Changes block → gate PASS."""
    ok, reasons = tig.evaluate_coverage_floor(
        _BELOW_LINE, _BELOW_BRANCH, _BASELINE_LINE, _BASELINE_BRANCH, _BODY_WITH_BLOCK
    )
    assert ok is True
    assert reasons == []


def test_only_line_below_no_block_fails():
    """Only line_rate drops — still fails without a block."""
    ok, reasons = tig.evaluate_coverage_floor(
        _BELOW_LINE,
        _ABOVE_BRANCH,  # branch is fine
        _BASELINE_LINE,
        _BASELINE_BRANCH,
        _BODY_NO_BLOCK,
    )
    assert ok is False
    assert any("line coverage" in r for r in reasons)
    assert not any("branch coverage" in r for r in reasons)


def test_only_branch_below_no_block_fails():
    """Only branch_rate drops — still fails without a block."""
    ok, reasons = tig.evaluate_coverage_floor(
        _ABOVE_LINE,  # line is fine
        _BELOW_BRANCH,
        _BASELINE_LINE,
        _BASELINE_BRANCH,
        _BODY_NO_BLOCK,
    )
    assert ok is False
    assert any("branch coverage" in r for r in reasons)
    assert not any("line coverage" in r for r in reasons)


def test_epsilon_tolerance_does_not_fail():
    """A drop within the epsilon window is treated as at-baseline — passes without block."""
    # epsilon is 0.005; drop by 0.004 — within tolerance
    near_line = _BASELINE_LINE - 0.004
    near_branch = _BASELINE_BRANCH - 0.004
    ok, reasons = tig.evaluate_coverage_floor(
        near_line, near_branch, _BASELINE_LINE, _BASELINE_BRANCH, _BODY_NO_BLOCK
    )
    assert ok is True, f"epsilon-within drop should pass; reasons: {reasons}"


def test_just_outside_epsilon_fails():
    """A drop just outside the epsilon window fails without a block."""
    just_outside_line = _BASELINE_LINE - 0.006  # 0.006 > epsilon(0.005)
    just_outside_branch = _BASELINE_BRANCH - 0.006
    ok, reasons = tig.evaluate_coverage_floor(
        just_outside_line,
        just_outside_branch,
        _BASELINE_LINE,
        _BASELINE_BRANCH,
        _BODY_NO_BLOCK,
    )
    assert ok is False, f"drop outside epsilon should fail; reasons: {reasons}"


# ---------------------------------------------------------------------------
# parse_coverage_xml — tempfile-based tests
# ---------------------------------------------------------------------------

_COVERAGE_XML_TEMPLATE = """\
<?xml version="1.0" ?>
<coverage version="7.0" line-rate="{lr}" branch-rate="{br}" branches-covered="10" branches-valid="20">
  <packages/>
</coverage>
"""


def _write_xml(line_rate: float, branch_rate: float) -> str:
    """Write a minimal coverage.xml to a tempfile and return the path."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False)
    f.write(_COVERAGE_XML_TEMPLATE.format(lr=line_rate, br=branch_rate))
    f.close()
    return f.name


def test_parse_coverage_xml_above():
    path = _write_xml(0.75, 0.60)
    try:
        lr, br = tig.parse_coverage_xml(path)
        assert abs(lr - 0.75) < 1e-9
        assert abs(br - 0.60) < 1e-9
    finally:
        os.unlink(path)


def test_parse_coverage_xml_at_baseline():
    path = _write_xml(_AT_LINE, _AT_BRANCH)
    try:
        lr, br = tig.parse_coverage_xml(path)
        assert abs(lr - _AT_LINE) < 1e-9
        assert abs(br - _AT_BRANCH) < 1e-9
    finally:
        os.unlink(path)


def test_parse_coverage_xml_malformed_raises():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False)
    f.write("<coverage>not valid xml at all<<")
    f.close()
    try:
        try:
            tig.parse_coverage_xml(f.name)
            assert False, "should have raised ValueError"
        except ValueError:
            pass
    finally:
        os.unlink(f.name)


# ---------------------------------------------------------------------------
# load_coverage_baseline — tempfile-based tests
# ---------------------------------------------------------------------------


def test_load_coverage_baseline_reads_correctly():
    data = {"line_rate": 0.37, "branch_rate": 0.45}
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    try:
        lr, br = tig.load_coverage_baseline(f.name)
        assert abs(lr - 0.37) < 1e-9
        assert abs(br - 0.45) < 1e-9
    finally:
        os.unlink(f.name)


def test_load_coverage_baseline_missing_key_raises():
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"line_rate": 0.37}, f)  # branch_rate missing
    f.close()
    try:
        try:
            tig.load_coverage_baseline(f.name)
            assert False, "should have raised ValueError"
        except ValueError:
            pass
    finally:
        os.unlink(f.name)


# ---------------------------------------------------------------------------
# End-to-end: verify the actual baseline file exists and is valid JSON
# ---------------------------------------------------------------------------


def test_baseline_file_exists_and_valid():
    """config/coverage-baseline.json must exist and contain line_rate + branch_rate."""
    repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
    baseline = os.path.join(repo_root, "config", "coverage-baseline.json")
    assert os.path.isfile(baseline), f"config/coverage-baseline.json not found at {baseline}"
    with open(baseline) as fh:
        data = json.load(fh)
    assert "line_rate" in data, "coverage-baseline.json must have 'line_rate'"
    assert "branch_rate" in data, "coverage-baseline.json must have 'branch_rate'"
    assert isinstance(data["line_rate"], (int, float)), "line_rate must be numeric"
    assert isinstance(data["branch_rate"], (int, float)), "branch_rate must be numeric"
    assert 0.0 <= data["line_rate"] <= 1.0, "line_rate must be in [0, 1]"
    assert 0.0 <= data["branch_rate"] <= 1.0, "branch_rate must be in [0, 1]"
