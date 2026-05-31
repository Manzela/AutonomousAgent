"""TDD unit tests for SP-00h license + dep allowlist gate.

These import the pure functions from scripts/ci/license_dep_gate.py.
All tests must pass with ZERO mocking of the functions-under-test:
  - find_added_deps        : parses pyproject.toml diff text
  - is_pinned_dep          : checks version specifier presence
  - is_typosquat           : Levenshtein proximity to popular pkg list
  - disallowed_license_in  : SPDX / copyleft header scanner
  - evaluate               : aggregates all checks -> (ok, reasons)

TDD red state: run pytest with this file BEFORE scripts/ci/license_dep_gate.py
exists -> ImportError/ModuleNotFoundError -> all tests FAIL (red).
TDD green state: after the script is written -> all tests PASS (green).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ci"))

import license_dep_gate as ldg  # noqa: E402


# ---------------------------------------------------------------------------
# find_added_deps
# ---------------------------------------------------------------------------


def test_find_added_deps_returns_pinned_specifier():
    diff = (
        '--- a/pyproject.toml\n+++ b/pyproject.toml\n [project.dependencies]\n+  "httpx>=0.27",\n'
    )
    specs = ldg.find_added_deps(diff)
    assert any("httpx" in s for s in specs), f"expected httpx in {specs}"


def test_find_added_deps_returns_multiple():
    diff = (
        "--- a/pyproject.toml\n"
        "+++ b/pyproject.toml\n"
        " [project.dependencies]\n"
        '+  "httpx>=0.27",\n'
        '+  "pydantic>=2.6",\n'
    )
    specs = ldg.find_added_deps(diff)
    names = [s.split(">=")[0].split("==")[0].strip() for s in specs]
    assert "httpx" in names
    assert "pydantic" in names


def test_find_added_deps_ignores_removed_lines():
    diff = (
        "--- a/pyproject.toml\n"
        "+++ b/pyproject.toml\n"
        " [project.dependencies]\n"
        '-  "oldpkg>=1.0",\n'
        '+  "newpkg>=2.0",\n'
    )
    specs = ldg.find_added_deps(diff)
    assert not any("oldpkg" in s for s in specs)
    assert any("newpkg" in s for s in specs)


def test_find_added_deps_ignores_non_dep_sections():
    diff = '--- a/pyproject.toml\n+++ b/pyproject.toml\n [tool.ruff]\n+  "sometool",\n'
    specs = ldg.find_added_deps(diff)
    assert specs == [], f"expected empty, got {specs}"


def test_find_added_deps_handles_optional_deps_section():
    diff = (
        "--- a/pyproject.toml\n"
        "+++ b/pyproject.toml\n"
        " [project.optional-dependencies]\n"
        '+  "somelib>=1.0",\n'
    )
    specs = ldg.find_added_deps(diff)
    assert any("somelib" in s for s in specs)


def test_find_added_deps_bare_name_no_version():
    """A bare dep name (no version) is still returned — is_pinned_dep will catch it."""
    diff = ' [project.dependencies]\n+  "requests",\n'
    specs = ldg.find_added_deps(diff)
    assert any("requests" in s for s in specs)


# ---------------------------------------------------------------------------
# is_pinned_dep
# ---------------------------------------------------------------------------


def test_is_pinned_dep_accepts_gte():
    assert ldg.is_pinned_dep("httpx>=0.27") is True


def test_is_pinned_dep_accepts_exact():
    assert ldg.is_pinned_dep("httpx==0.27.0") is True


def test_is_pinned_dep_accepts_compatible():
    assert ldg.is_pinned_dep("pydantic~=2.6") is True


def test_is_pinned_dep_accepts_url_at():
    assert ldg.is_pinned_dep("mylib @ git+https://github.com/org/mylib.git") is True


def test_is_pinned_dep_rejects_bare_name():
    assert ldg.is_pinned_dep("requests") is False


def test_is_pinned_dep_rejects_extras_only():
    assert ldg.is_pinned_dep("requests[security]") is False


def test_is_pinned_dep_rejects_with_comment_but_no_version():
    # A dep line that had its version in a comment but not in the spec itself.
    assert ldg.is_pinned_dep("requests  # >=2.28") is False


# ---------------------------------------------------------------------------
# is_typosquat
# ---------------------------------------------------------------------------

_SMALL_POPULAR = frozenset({"requests", "numpy", "flask", "django"})
_SMALL_ALLOWLIST = frozenset({"requests", "numpy"})


def test_is_typosquat_one_edit_away_not_in_allowlist():
    # 'requuests' is 1 edit away from 'requests' and NOT in allowlist
    assert ldg.is_typosquat("requuests", frozenset(), _SMALL_POPULAR) is True


def test_is_typosquat_two_edits_away_not_in_allowlist():
    # 'numpyy' is 1 edit from 'numpy'
    assert ldg.is_typosquat("numpyy", frozenset(), _SMALL_POPULAR) is True


def test_is_typosquat_returns_false_if_in_allowlist():
    # 'requuests' would normally be a typosquat candidate but is in the allowlist
    assert ldg.is_typosquat("requuests", frozenset({"requuests"}), _SMALL_POPULAR) is False


def test_is_typosquat_exact_match_in_popular_is_not_typosquat():
    # 'requests' IS in the popular list — it is a legitimate package
    assert ldg.is_typosquat("requests", frozenset(), _SMALL_POPULAR) is False


def test_is_typosquat_far_name_is_not_typosquat():
    # 'completely-different-lib' is 3+ edits from anything in the small popular list
    assert ldg.is_typosquat("completely-different-lib", frozenset(), _SMALL_POPULAR) is False


def test_is_typosquat_normalises_underscore_hyphen():
    # 'numpy_1' vs 'numpy' — normalisation should not confuse the distance calc
    # 'numpy-' would be 1 edit from 'numpy' and NOT in allowlist
    assert ldg.is_typosquat("numpy-", frozenset(), _SMALL_POPULAR) is True


def test_is_typosquat_builtin_allowlist_by_default():
    # 'pyyaml' is in the builtin allowlist — should NOT be flagged
    assert ldg.is_typosquat("pyyaml") is False


# ---------------------------------------------------------------------------
# disallowed_license_in
# ---------------------------------------------------------------------------


def test_disallowed_license_in_clean_mit_spdx():
    text = "# SPDX-License-Identifier: MIT\nimport os\n"
    assert ldg.disallowed_license_in(text) is None


def test_disallowed_license_in_gpl_spdx():
    text = "# SPDX-License-Identifier: GPL-3.0-only\nimport os\n"
    result = ldg.disallowed_license_in(text)
    assert result is not None
    assert "GPL" in result


def test_disallowed_license_in_agpl_spdx():
    text = "# SPDX-License-Identifier: AGPL-3.0-or-later\nimport os\n"
    result = ldg.disallowed_license_in(text)
    assert result is not None


def test_disallowed_license_in_all_rights_reserved_prose():
    text = "# Copyright 2025 Example Corp. All Rights Reserved.\nimport os\n"
    result = ldg.disallowed_license_in(text)
    assert result is not None
    assert "rights reserved" in result.lower()


def test_disallowed_license_in_no_license_no_spdx_is_clean():
    """A file with no license header at all is CLEAN (absence != disallowed)."""
    text = "import os\ndef foo(): pass\n"
    assert ldg.disallowed_license_in(text) is None


def test_disallowed_license_in_apache_is_clean():
    text = "# SPDX-License-Identifier: Apache-2.0\nimport os\n"
    assert ldg.disallowed_license_in(text) is None


def test_disallowed_license_in_proprietary_prose():
    text = "# This software is Proprietary and confidential.\nimport os\n"
    result = ldg.disallowed_license_in(text)
    assert result is not None


def test_disallowed_license_in_lgpl_spdx():
    text = "# SPDX-License-Identifier: LGPL-2.1-only\n"
    result = ldg.disallowed_license_in(text)
    assert result is not None


def test_disallowed_license_in_only_scans_first_50_lines():
    """A GPL marker buried after line 50 should NOT be caught."""
    header = "\n" * 55  # 55 blank lines
    text = header + "# GPL is buried after 50 lines\n"
    assert ldg.disallowed_license_in(text) is None


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------


def test_evaluate_clean_input_returns_ok():
    ok, reasons = ldg.evaluate(
        added_dep_specs=["httpx>=0.27"],
        added_file_texts={"app/foo.py": "import os\n"},
        allowlist=frozenset({"httpx"}),
        popular=frozenset({"requests"}),
    )
    # httpx is in allowlist and pinned, file is clean
    assert ok is True
    hard = [r for r in reasons if r.startswith("HARD:")]
    assert hard == []


def test_evaluate_unpinned_dep_is_hard_fail():
    ok, reasons = ldg.evaluate(
        added_dep_specs=["requests"],  # bare name, no version
        added_file_texts={},
        allowlist=frozenset({"requests"}),
        popular=frozenset({"requests"}),
    )
    assert ok is False
    assert any("unpinned" in r for r in reasons)


def test_evaluate_typosquat_dep_is_hard_fail():
    ok, reasons = ldg.evaluate(
        added_dep_specs=["requuests>=2.28"],
        added_file_texts={},
        allowlist=frozenset(),
        popular=frozenset({"requests"}),
    )
    assert ok is False
    assert any("typosquat" in r for r in reasons)


def test_evaluate_disallowed_license_in_added_file_is_hard_fail():
    ok, reasons = ldg.evaluate(
        added_dep_specs=[],
        added_file_texts={"app/bad.py": "# SPDX-License-Identifier: GPL-3.0-only\n"},
        allowlist=frozenset(),
        popular=frozenset(),
    )
    assert ok is False
    assert any("license" in r.lower() for r in reasons)


def test_evaluate_unknown_dep_not_typosquat_is_advisory_not_hard():
    """An added dep that is not in the allowlist but not typosquatting is advisory only."""
    ok, reasons = ldg.evaluate(
        added_dep_specs=["my-new-lib>=1.0"],
        added_file_texts={},
        allowlist=frozenset(),  # not in allowlist
        popular=frozenset({"requests"}),  # far from requests
    )
    # Should pass (no hard violation) but emit an advisory
    assert ok is True
    assert any("ADVISORY" in r for r in reasons)


def test_evaluate_multiple_hard_violations_all_reported():
    ok, reasons = ldg.evaluate(
        added_dep_specs=["bare-dep", "requuests>=1.0"],
        added_file_texts={"app/copyleft.py": "# SPDX-License-Identifier: AGPL-3.0-only\n"},
        allowlist=frozenset(),
        popular=frozenset({"requests"}),
    )
    assert ok is False
    hard = [r for r in reasons if r.startswith("HARD:")]
    assert len(hard) >= 2  # at least unpinned + AGPL (or typosquat + AGPL)


def test_evaluate_empty_inputs_ok():
    ok, reasons = ldg.evaluate([], {})
    assert ok is True
    assert reasons == []
