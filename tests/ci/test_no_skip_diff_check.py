"""Red-green unit tests for scripts/ci/no_skip_diff_check.py (OD-5).

The C6 "No new skip/skipif/xfail/manual markers" gate
(.github/workflows/no-skip-on-remediation.yml) historically matched only the
MARKER forms:

    pytest.mark.skip / pytest.mark.skipif / pytest.mark.xfail
    @manual / pytest.mark.manual

OD-5: it MISSED the bare RUNTIME skip call `pytest.skip(...)`, which short-
circuits a test at runtime just like a marker does. These tests pin that the
detector now flags a newly-added bare ``pytest.skip(`` call while leaving
clean added lines (and unchanged/removed lines) alone.

No xfail, no skip, no skipif in this file — all assertions are expected-green.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_CI = Path(__file__).resolve().parents[2] / "scripts" / "ci"
sys.path.insert(0, str(_SCRIPTS_CI))

from no_skip_diff_check import find_forbidden_added_lines  # noqa: E402

# A "+" prefix marks an ADDED line in a unified diff. A bare runtime skip call
# is built by concatenation so this very test file does not self-trip the gate.
_BARE_SKIP_CALL = "pytest." + "skip(" + '"needs a live GPU")'
_MARKER_SKIPIF = "@pytest." + "mark." + "skipif(True, reason='x')"


# ---------------------------------------------------------------------------
# RED (pre-fix): a newly-added bare ``pytest.skip(`` call must be flagged.
# ---------------------------------------------------------------------------


def test_bare_runtime_skip_is_flagged():
    diff = "+    " + _BARE_SKIP_CALL + "\n"
    hits = find_forbidden_added_lines(diff)
    assert len(hits) == 1, f"bare runtime skip call should be flagged once, got {hits!r}"
    assert "skip(" in hits[0]


def test_bare_runtime_skip_with_pytest_alias_in_context():
    # Realistic snippet: an added guard line inside a test body.
    diff = (
        "@@ -1,3 +1,4 @@\n"
        " def test_thing():\n"
        "+    if not has_gpu():\n"
        "+        " + _BARE_SKIP_CALL + "\n"
        "     assert True\n"
    )
    hits = find_forbidden_added_lines(diff)
    assert len(hits) == 1, f"exactly the skip line should be flagged, got {hits!r}"


# ---------------------------------------------------------------------------
# Regression: the existing marker forms must STILL be flagged.
# ---------------------------------------------------------------------------


def test_marker_skipif_still_flagged():
    diff = "+" + _MARKER_SKIPIF + "\n"
    hits = find_forbidden_added_lines(diff)
    assert len(hits) == 1, f"marker skipif must remain flagged, got {hits!r}"


def test_manual_marker_still_flagged():
    diff = "+" + "@" + "manual\n"
    hits = find_forbidden_added_lines(diff)
    assert len(hits) == 1, f"@manual must remain flagged, got {hits!r}"


# ---------------------------------------------------------------------------
# GREEN: clean added lines, removed lines, and context lines are NOT flagged.
# ---------------------------------------------------------------------------


def test_clean_added_line_not_flagged():
    diff = "+    assert response.status_code == 200\n"
    hits = find_forbidden_added_lines(diff)
    assert hits == [], f"a clean added line must not be flagged, got {hits!r}"


def test_removed_skip_line_not_flagged():
    # A REMOVED bare skip (leading '-') is a GOOD change — deleting a skip.
    diff = "-    " + _BARE_SKIP_CALL + "\n"
    hits = find_forbidden_added_lines(diff)
    assert hits == [], f"a removed skip line must not be flagged, got {hits!r}"


def test_context_skip_line_not_flagged():
    # A context line (leading space, unchanged) must not be flagged.
    diff = "     " + _BARE_SKIP_CALL + "\n"
    hits = find_forbidden_added_lines(diff)
    assert hits == [], f"an unchanged context skip line must not be flagged, got {hits!r}"


def test_diff_header_plusplus_not_flagged():
    # The unified-diff file header line '+++ b/file.py' starts with '++' and
    # must be ignored (it is not a content addition).
    diff = "+++ b/tests/integration/test_thing.py\n"
    hits = find_forbidden_added_lines(diff)
    assert hits == [], f"the +++ file header must not be flagged, got {hits!r}"
