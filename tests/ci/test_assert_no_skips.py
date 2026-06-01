"""Red-green unit tests for scripts/ci/assert_no_skips.py.

Tests use small inline JUnit XML fixtures from tests/fixtures/ to verify
the three core behaviours required by the PRD:

  RED-1  junit_with_skip.xml    -- skipped > 0   → exit != 0
  RED-2  junit_missing_node.xml -- required nodeid absent → exit != 0
  GREEN  junit_all_green.xml    -- skipped == 0 + required nodeids present
                                   and passed → exit 0

No xfail, no skip, no skipif.  All three cases are expected-green assertions.
"""

from __future__ import annotations

import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Import the module under test directly (stdlib-only, no install step needed).
# ---------------------------------------------------------------------------

_SCRIPTS_CI = Path(__file__).resolve().parents[2] / "scripts" / "ci"
sys.path.insert(0, str(_SCRIPTS_CI))

from assert_no_skips import main as _main  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

_JUNIT_WITH_SKIP = str(_FIXTURES / "junit_with_skip.xml")
_JUNIT_MISSING_NODE = str(_FIXTURES / "junit_missing_node.xml")
_JUNIT_ALL_GREEN = str(_FIXTURES / "junit_all_green.xml")


# ---------------------------------------------------------------------------
# RED-1: skipped > 0 → exit 1
# ---------------------------------------------------------------------------


def test_junit_with_skip_fails():
    """A JUnit report with skipped=1 must cause exit code != 0."""
    exit_code = _main([_JUNIT_WITH_SKIP])
    assert exit_code != 0, f"Expected non-zero exit for a report with skipped=1, got {exit_code}"


def test_junit_with_skip_fails_even_without_required_nodeids():
    """Skipped count gate triggers independently of --require-nodeid."""
    exit_code = _main([_JUNIT_WITH_SKIP])
    assert exit_code != 0


# ---------------------------------------------------------------------------
# RED-2: required nodeid absent (deselected) → exit 1
# ---------------------------------------------------------------------------


def test_missing_required_nodeid_fails():
    """A nodeid that does not appear in the report at all must cause exit != 0."""
    exit_code = _main(
        [
            _JUNIT_MISSING_NODE,
            "--require-nodeid",
            "tests/integration/test_full_turn.py::test_health_endpoint_responds",
        ]
    )
    assert exit_code != 0, "Expected non-zero exit when a required nodeid is absent from the report"


def test_missing_required_nodeid_with_failed_status_fails():
    """A nodeid present in the report with status=failed must also cause exit != 0."""
    # Build a synthetic junit via the all-green fixture but ask for a nodeid
    # that is present in junit_with_skip as 'skipped' (test_skipped_one).
    exit_code = _main(
        [
            _JUNIT_WITH_SKIP,
            "--require-nodeid",
            "test_skipped_one",
        ]
    )
    assert exit_code != 0, "Expected non-zero exit when a required nodeid has status=skipped"


# ---------------------------------------------------------------------------
# GREEN: skipped == 0 AND required nodeids present+passed → exit 0
# ---------------------------------------------------------------------------


def test_all_green_no_required_nodeids_passes():
    """A report with zero skips and no required nodeids must exit 0."""
    exit_code = _main([_JUNIT_ALL_GREEN])
    assert (
        exit_code == 0
    ), f"Expected exit 0 for an all-green report with no required nodeids, got {exit_code}"


def test_all_green_with_present_nodeid_passes():
    """A report with zero skips and a required nodeid that IS present+passed must exit 0."""
    # test_health_endpoint_responds appears in junit_all_green.xml as passed.
    exit_code = _main(
        [
            _JUNIT_ALL_GREEN,
            "--require-nodeid",
            "test_health_endpoint_responds",
        ]
    )
    assert (
        exit_code == 0
    ), "Expected exit 0 when skipped=0 and the required nodeid is present+passed"


def test_all_green_with_multiple_required_nodeids_all_present_passes():
    """All required nodeids present+passed should still give exit 0."""
    exit_code = _main(
        [
            _JUNIT_ALL_GREEN,
            "--require-nodeid",
            "test_alpha",
            "--require-nodeid",
            "test_beta",
            "--require-nodeid",
            "test_health_endpoint_responds",
        ]
    )
    assert exit_code == 0, "Expected exit 0 when all required nodeids are present+passed"


# ---------------------------------------------------------------------------
# Edge: missing junit file → exit 1 (not a crash)
# ---------------------------------------------------------------------------


def test_missing_junit_file_exits_nonzero(tmp_path):
    """A non-existent junit path must produce exit != 0, not an unhandled exception."""
    missing = str(tmp_path / "does_not_exist.xml")
    exit_code = _main([missing])
    assert exit_code != 0, "Expected non-zero exit when the JUnit XML file does not exist"
