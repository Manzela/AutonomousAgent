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


def test_disallowed_license_in_proprietary_confidential_prose():
    # A comment line carrying BOTH "proprietary" AND "confidential" is the
    # canonical dual-keyword license notice form and MUST be flagged (Finding 2).
    # Requiring both words avoids false-positives on explanatory comments that
    # use only one of the words (e.g. "proprietary SPDX identifiers").
    text = "# This software is Proprietary and confidential.\nimport os\n"
    result = ldg.disallowed_license_in(text)
    assert result is not None, "Proprietary+Confidential prose notice must be caught"


def test_disallowed_license_in_proprietary_confidential_standalone():
    # Bare '# Proprietary and Confidential' header (no SPDX) is flagged.
    text = "# Proprietary and Confidential\nimport os\n"
    result = ldg.disallowed_license_in(text)
    assert result is not None, "Standalone Proprietary+Confidential notice must be caught"


def test_disallowed_license_in_proprietary_alone_not_flagged():
    # A comment that mentions only 'proprietary' (without 'confidential') is NOT
    # flagged — it is likely an explanatory code comment, not a license notice.
    text = "# Copyleft / proprietary SPDX identifiers are handled here.\nimport os\n"
    result = ldg.disallowed_license_in(text)
    assert result is None, "Single-word 'proprietary' in explanatory comment must not be flagged"


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


def test_evaluate_unknown_dep_not_typosquat_is_hard_fail():
    """An added dep not in the allowlist is a HARD failure even if it is not a typosquat.

    Adding a dep to the allowlist is a reviewed act; the gate enforces that the author
    must explicitly add it to _BUILTIN_ALLOWLIST before the PR can merge.
    """
    ok, reasons = ldg.evaluate(
        added_dep_specs=["my-new-lib>=1.0"],
        added_file_texts={},
        allowlist=frozenset(),  # not in allowlist
        popular=frozenset({"requests"}),  # far from requests — not a typosquat
    )
    # Must be a hard failure: allowlist check is now blocking, not advisory.
    assert ok is False
    hard = [r for r in reasons if r.startswith("HARD:")]
    assert any("allowlist" in r.lower() for r in hard), f"Expected allowlist hard-fail in {hard}"


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


# ---------------------------------------------------------------------------
# Fix-specific regression tests (added 2026-05-31)
# ---------------------------------------------------------------------------


def test_gate_passes_on_its_own_source():
    """disallowed_license_in must return None for license_dep_gate.py itself.

    The gate defines GPL/AGPL/LGPL as string patterns in its source; bare keyword
    scanning would false-flag its own file. This is the CI red case that Fix 1 solves.
    """
    import os

    gate_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "ci", "license_dep_gate.py"
    )
    with open(gate_path) as fh:
        content = fh.read()
    result = ldg.disallowed_license_in(content)
    assert result is None, (
        f"disallowed_license_in flagged the gate's own source: {result!r}. "
        "The gate must pass on itself (Fix 1 regression)."
    )


def test_is_pinned_dep_accepts_gt():
    """'pkg > 1.0' carries a version constraint and must be treated as pinned (Fix 3)."""
    assert ldg.is_pinned_dep("pkg > 1.0") is True


def test_is_pinned_dep_accepts_lt():
    """'pkg < 2.0' carries a version constraint and must be treated as pinned (Fix 3)."""
    assert ldg.is_pinned_dep("pkg < 2.0") is True


def test_find_added_deps_pep621_inline_array():
    """PEP-621 deps under [project] with `dependencies = [` must be detected (Fix 2).

    Real pyproject.toml structure:
        [project]
        dependencies = [
          "newdep>=1.0",
        ]
    A dep added inside this array must be returned by find_added_deps.
    """
    diff = (
        "--- a/pyproject.toml\n"
        "+++ b/pyproject.toml\n"
        " [project]\n"
        ' name = "myapp"\n'
        " dependencies = [\n"
        '+  "newdep>=1.0",\n'
        " ]\n"
    )
    specs = ldg.find_added_deps(diff)
    assert any("newdep" in s for s in specs), f"Expected newdep in {specs}"


def test_evaluate_unknown_non_typosquat_dep_hard_fails_with_allowlist_message():
    """An allowlist miss that is NOT a typosquat must produce a HARD failure (Fix 4).

    The error message must guide the author to add the dep to _BUILTIN_ALLOWLIST.
    """
    ok, reasons = ldg.evaluate(
        added_dep_specs=["totally-new-pkg>=2.0"],
        added_file_texts={},
        allowlist=frozenset(),
        popular=frozenset({"requests", "flask"}),
    )
    assert ok is False, "Unknown non-typosquat dep must be a hard failure (Fix 4)"
    hard = [r for r in reasons if r.startswith("HARD:")]
    assert any(
        "allowlist" in r.lower() for r in hard
    ), f"Hard reason must mention 'allowlist' but got: {hard}"


# ---------------------------------------------------------------------------
# Cross-vendor review findings (2026-05-31)
# ---------------------------------------------------------------------------


def test_find_added_deps_inline_single_line_array_bare_dep_is_caught():
    """FINDING 1 (CRITICAL): `dependencies = ["bare-dep"]` on a single added line
    must NOT bypass the gate.  Previously the opener-line `continue` swallowed the
    inline content, so this dep was silently ignored.
    """
    diff = '--- a/pyproject.toml\n+++ b/pyproject.toml\n [project]\n+dependencies = ["bare-dep"]\n'
    specs = ldg.find_added_deps(diff)
    assert any(
        "bare-dep" in s for s in specs
    ), f"Inline single-line array bypass: expected bare-dep in {specs}"
    # The unpinned dep must be caught by the full evaluate path too
    ok, reasons = ldg.evaluate(
        added_dep_specs=specs,
        added_file_texts={},
        allowlist=frozenset(),
        popular=frozenset({"requests"}),
    )
    assert ok is False, "Bare dep from inline single-line array must fail evaluate()"


def test_find_added_deps_two_deps_on_one_added_line():
    """FINDING 1 (CRITICAL): `"pkg1==1.0", "pkg2"` on a single `+` line must
    produce TWO separate specifiers, not one garbled concatenation.
    pkg2 (unpinned and unknown) must be caught; pkg1 must not be the cause.
    """
    diff = (
        "--- a/pyproject.toml\n"
        "+++ b/pyproject.toml\n"
        " [project]\n"
        " dependencies = [\n"
        '+  "pkg1==1.0", "pkg2",\n'
        " ]\n"
    )
    specs = ldg.find_added_deps(diff)
    assert any("pkg1" in s for s in specs), f"pkg1 missing from {specs}"
    assert any("pkg2" in s for s in specs), f"pkg2 missing from {specs}"
    # pkg1 is pinned; pkg2 is not — evaluate must flag pkg2 (not pkg1) as unpinned
    pkg2_specs = [s for s in specs if s.startswith("pkg2")]
    assert pkg2_specs, "pkg2 spec must be present separately"
    assert not ldg.is_pinned_dep(pkg2_specs[0]), "pkg2 (bare) must be detected as unpinned"
    pkg1_specs = [s for s in specs if s.startswith("pkg1")]
    assert pkg1_specs and ldg.is_pinned_dep(pkg1_specs[0]), "pkg1==1.0 must be detected as pinned"


def test_disallowed_license_in_gate_passes_on_own_source_strengthened():
    """FINDING 2 (own-source constraint): after adding proprietary+confidential
    prose detection, the gate must still return None for its own source file.
    Both the original SPDX-based and the new prose patterns are exercised.
    """
    import os

    gate_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "ci", "license_dep_gate.py"
    )
    with open(gate_path) as fh:
        content = fh.read()
    result = ldg.disallowed_license_in(content)
    assert result is None, (
        f"disallowed_license_in must return None for the gate's own source; got {result!r}. "
        "The proprietary+confidential pattern must not match explanatory code comments."
    )


def test_is_pinned_dep_rejects_not_equal_only():
    """NIT: `!=` as the sole operator is an exclusion, not a pin.
    `pkg!=1.5` still allows any other version — it is NOT pinned.
    """
    assert ldg.is_pinned_dep("pkg!=1.5") is False, "!=only must be treated as unpinned"


def test_is_pinned_dep_accepts_not_equal_combined_with_gte():
    """NIT: `>=1,!=1.5` carries a bounding operator — it IS pinned."""
    assert ldg.is_pinned_dep("pkg>=1,!=1.5") is True, ">=1,!=1.5 must be treated as pinned"
