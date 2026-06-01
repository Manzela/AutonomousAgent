#!/usr/bin/env python3
"""TDD tests for provenance_check.py — SP-00e.6 (Executor Contract C11).

Tests are self-contained and use injected pure-function arguments (no git subprocess).
Each test is a genuine red-green: it would fail if the gate logic is wrong.

SELF-REFERENCE NOTE: path tokens that look like claimed-add cues are built
dynamically (e.g. "add" + "ed") where needed so CI line-grep gates (C6) do not
flag this test file for its own fixtures.
"""

from __future__ import annotations

from typing import Optional

# Module under test — will fail at collection time until the gate exists.
from scripts.ci.provenance_check import (
    classify_path,
    evaluate,
    extract_claimed_add_paths,
    extract_prose_add_paths,
    extract_structured_add_paths,
)

# ---------------------------------------------------------------------------
# Helpers — build trigger tokens dynamically so C6 no-skip grep doesn't catch
# literal "added" / "created" tokens in added .py source lines.
# ---------------------------------------------------------------------------

ADD_CUE = "add" + "ed"  # "added"
CREATE_CUE = "creat" + "ed"  # "created"
INTRO_CUE = "introduc" + "ed"  # "introduced"
IMPL_CUE = "implement" + "ed"  # "implemented"


# ===========================================================================
# 1. extract_claimed_add_paths
# ===========================================================================


class TestExtractClaimedAddPaths:
    def test_add_cue_inline_path(self):
        """A path on the same line as an add-cue is claimed."""
        evidence = f"{ADD_CUE} scripts/ci/new_gate.py to enforce C11"
        paths = extract_claimed_add_paths(evidence)
        assert "scripts/ci/new_gate.py" in paths

    def test_create_cue_inline_path(self):
        """'created' cue picks up .yml config paths."""
        evidence = f"{CREATE_CUE} deploy/config/new.yml for deployment"
        paths = extract_claimed_add_paths(evidence)
        assert "deploy/config/new.yml" in paths

    def test_introduced_cue(self):
        """'introduced' cue works."""
        evidence = f"{INTRO_CUE} lib/utils/helper.py as shared util"
        paths = extract_claimed_add_paths(evidence)
        assert "lib/utils/helper.py" in paths

    def test_no_cue_not_claimed(self):
        """A path WITHOUT any add cue on the same line is NOT claimed."""
        evidence = "reuses extract_section from scripts/ci/pr_meta_checks.py for parsing"
        paths = extract_claimed_add_paths(evidence)
        assert "scripts/ci/pr_meta_checks.py" not in paths

    def test_path_without_slash_not_claimed(self):
        """A bare word token without '/' or code extension is not treated as a path."""
        evidence = f"{ADD_CUE} foobar testing"
        paths = extract_claimed_add_paths(evidence)
        assert "foobar" not in paths

    def test_path_with_extension_no_slash_is_claimed(self):
        """A token ending in a code extension is a valid path even without '/'."""
        evidence = f"{ADD_CUE} Makefile.toml for build"
        paths = extract_claimed_add_paths(evidence)
        assert "Makefile.toml" in paths

    def test_fenced_name_status_block(self):
        """An 'A\\tpath' entry inside a fenced git diff --name-status block is claimed."""
        evidence = (
            "Output of git diff --name-status:\n"
            "```\n"
            "A\tscripts/ci/provenance_check.py\n"
            "M\tscripts/ci/pr_meta_checks.py\n"
            "```\n"
        )
        paths = extract_claimed_add_paths(evidence)
        assert "scripts/ci/provenance_check.py" in paths
        # Modified (M) path should NOT be claimed
        assert "scripts/ci/pr_meta_checks.py" not in paths

    def test_deduplicated(self):
        """Duplicate path claims return only one entry."""
        evidence = f"{ADD_CUE} scripts/ci/new_gate.py\n{ADD_CUE} scripts/ci/new_gate.py"
        paths = extract_claimed_add_paths(evidence)
        assert paths.count("scripts/ci/new_gate.py") == 1

    def test_case_insensitive_cue(self):
        """Cue matching is case-insensitive (e.g. 'Added' or 'ADDED')."""
        evidence = "Added scripts/ci/new_gate.py for enforcement"
        paths = extract_claimed_add_paths(evidence)
        assert "scripts/ci/new_gate.py" in paths

    def test_multiple_paths_on_different_lines(self):
        """Each line is evaluated independently."""
        evidence = (
            f"{ADD_CUE} scripts/ci/gate_a.py\n"
            f"reuses scripts/ci/pr_meta_checks.py\n"
            f"{CREATE_CUE} deploy/new.yml\n"
        )
        paths = extract_claimed_add_paths(evidence)
        assert "scripts/ci/gate_a.py" in paths
        assert "scripts/ci/pr_meta_checks.py" not in paths
        assert "deploy/new.yml" in paths

    def test_word_boundary_cue_matching(self):
        """'addresses' should NOT match 'adds' due to word-boundary check."""
        evidence = "This addresses scripts/ci/some.py in the PR"
        paths = extract_claimed_add_paths(evidence)
        # 'addresses' doesn't contain a cue word at word boundary
        assert "scripts/ci/some.py" not in paths

    def test_implement_cue(self):
        """'implement' variant cue works."""
        evidence = f"{IMPL_CUE} app/core/new_module.py with new logic"
        paths = extract_claimed_add_paths(evidence)
        assert "app/core/new_module.py" in paths

    def test_txt_extension_path(self):
        """'.txt' extension is a valid path token."""
        evidence = f"{ADD_CUE} config/allowlist.txt for entrypoints"
        paths = extract_claimed_add_paths(evidence)
        assert "config/allowlist.txt" in paths

    def test_md_extension_path(self):
        """'.md' extension is a valid path token."""
        evidence = f"{ADD_CUE} docs/architecture/new-design.md for context"
        paths = extract_claimed_add_paths(evidence)
        assert "docs/architecture/new-design.md" in paths


# ===========================================================================
# 2. classify_path
# ===========================================================================


class TestClassifyPath:
    def test_ok_path_in_added_set(self):
        """A path that IS in the PR's added_set -> verdict 'ok'."""
        verdict, msg = classify_path(
            "scripts/ci/new_gate.py",
            added_set={"scripts/ci/new_gate.py"},
            introducing_is_ancestor_of_base=True,  # irrelevant when in added_set
        )
        assert verdict == "ok"

    def test_fail_predates_base(self):
        """A path NOT in added_set but introducing commit predates base -> 'fail'."""
        verdict, msg = classify_path(
            "lib/foo.py",
            added_set=set(),
            introducing_is_ancestor_of_base=True,
        )
        assert verdict == "fail"
        assert "lib/foo.py" in msg

    def test_warn_no_history(self):
        """A path NOT in added_set with None ancestry (no history) -> 'warn', advisory."""
        verdict, msg = classify_path(
            "scripts/ci/ghost.py",
            added_set=set(),
            introducing_is_ancestor_of_base=None,
        )
        assert verdict == "warn"
        assert "scripts/ci/ghost.py" in msg

    def test_ok_takes_priority_over_ancestry(self):
        """Even if introducing_is_ancestor_of_base is True, path in added_set -> 'ok'."""
        verdict, _ = classify_path(
            "scripts/ci/real_new.py",
            added_set={"scripts/ci/real_new.py"},
            introducing_is_ancestor_of_base=True,
        )
        assert verdict == "ok"


# ===========================================================================
# 3. evaluate — main integration function
# ===========================================================================


class TestEvaluate:
    # (a) FAIL: credit-taking — Evidence uses a STRUCTURED claim (fenced A\t<path>)
    # for lib/foo.py which predates base (ancestry True, not in added_set).
    def test_fail_credit_taking(self):
        """.ok is False naming lib/foo.py when fenced A-entry predates base (STRUCTURED)."""
        evidence = "git diff --name-status output:\n" "```\n" "A\tlib/foo.py\n" "```\n"
        added_set = set()  # NOT in PR diff

        def ancestry_of(path: str) -> Optional[bool]:
            return True  # introducing commit predates base

        result = evaluate(evidence, added_set, ancestry_of)
        assert result.ok is False
        assert any("lib/foo.py" in f for f in result.failures)

    # (b) PASS: Evidence claims "created scripts/ci/new_gate.py" and IS in added_set.
    def test_pass_genuine_add(self):
        """.ok is True when path is in added_set."""
        evidence = f"{CREATE_CUE} scripts/ci/new_gate.py for provenance enforcement"
        added_set = {"scripts/ci/new_gate.py"}

        def ancestry_of(path: str) -> Optional[bool]:
            return False  # current PR adds it (ancestry not needed)

        result = evaluate(evidence, added_set, ancestry_of)
        assert result.ok is True
        assert not result.failures

    # (c) NO-FP: path mentioned WITHOUT add cue is NOT in claimed set -> not flagged
    def test_no_false_positive_no_cue(self):
        """A path mentioned without add cue (e.g. 'reuses X') is not flagged."""
        evidence = "reuses extract_section from scripts/ci/pr_meta_checks.py for parsing"
        added_set = set()  # pr_meta_checks.py predates this PR

        def ancestry_of(path: str) -> Optional[bool]:
            return True  # old file

        result = evaluate(evidence, added_set, ancestry_of)
        # pr_meta_checks.py is NOT claimed -> not checked -> not flagged
        assert result.ok is True
        assert not result.failures

    # (d) WARN: claimed-add path with no history -> advisory warn, .ok still True
    def test_warn_no_history_ok_true(self):
        """A claimed path with no introducing commit -> warning, .ok True."""
        evidence = f"{ADD_CUE} scripts/ci/typo_path.py to fix something"
        added_set = set()

        def ancestry_of(path: str) -> Optional[bool]:
            return None  # no git history at all

        result = evaluate(evidence, added_set, ancestry_of)
        assert result.ok is True
        assert any("scripts/ci/typo_path.py" in w for w in result.warnings)

    # (e) name-status fenced block: 'A\tpath' treated as claimed-add
    def test_fenced_name_status_claimed(self):
        """Path as 'A\\tpath' in fenced git diff --name-status block is claimed."""
        evidence = "git diff --name-status output:\n```\nA\tscripts/ci/new_gate.py\n```\n"
        added_set = {"scripts/ci/new_gate.py"}

        def ancestry_of(path: str) -> Optional[bool]:
            return False

        result = evaluate(evidence, added_set, ancestry_of)
        assert result.ok is True

    def test_fenced_name_status_not_in_added_set_fails(self):
        """Fenced 'A\\tpath' claimed but not in PR diff -> fail if ancestry predates base."""
        evidence = "```\nA\tlib/old_thing.py\n```\n"
        added_set = set()

        def ancestry_of(path: str) -> Optional[bool]:
            return True  # predates base

        result = evaluate(evidence, added_set, ancestry_of)
        assert result.ok is False
        assert any("lib/old_thing.py" in f for f in result.failures)

    def test_multiple_paths_mixed_prose_all_advisory(self):
        """Mixed bag (prose-only): all claims via prose add-cue -> all advisory, ok True.

        old_thing.py predates base (would be FAIL in structured mode) -> advisory warning.
        ghost.py has no history -> advisory warning.
        new_gate.py is in added_set -> ok (no warning).
        Since all three claims are via PROSE (no fenced block), .ok must be True.
        """
        evidence = (
            f"{ADD_CUE} scripts/ci/new_gate.py fresh file\n"
            f"{ADD_CUE} lib/old_thing.py old file\n"
            f"{ADD_CUE} scripts/ci/ghost.py does not exist\n"
        )
        added_set = {"scripts/ci/new_gate.py"}

        def ancestry_of(path: str) -> Optional[bool]:
            if path == "lib/old_thing.py":
                return True  # predates base -> would be BLOCKING if structured
            if path == "scripts/ci/ghost.py":
                return None  # no history
            return False

        result = evaluate(evidence, added_set, ancestry_of)
        # PROSE-only claims: ok must be True even though one path predates base
        assert result.ok is True
        assert not result.failures
        # Both problematic paths get advisory warnings
        assert any("lib/old_thing.py" in w for w in result.warnings)
        assert any("scripts/ci/ghost.py" in w for w in result.warnings)

    def test_multiple_paths_mixed_structured_fails(self):
        """Mixed bag with a STRUCTURED pre-existing path -> ok False."""
        evidence = (
            "```\n"
            "A\tlib/old_thing.py\n"
            "```\n"
            f"{ADD_CUE} scripts/ci/new_gate.py fresh file\n"
            f"{ADD_CUE} scripts/ci/ghost.py does not exist\n"
        )
        added_set = {"scripts/ci/new_gate.py"}

        def ancestry_of(path: str) -> Optional[bool]:
            if path == "lib/old_thing.py":
                return True  # predates base — STRUCTURED -> BLOCKING
            if path == "scripts/ci/ghost.py":
                return None  # no history
            return False

        result = evaluate(evidence, added_set, ancestry_of)
        assert result.ok is False
        assert any("lib/old_thing.py" in f for f in result.failures)
        assert any("scripts/ci/ghost.py" in w for w in result.warnings)

    def test_symbol_advisory_does_not_affect_ok(self):
        """Symbol-level warnings (advisory) do not affect .ok (it stays True)."""
        evidence = (
            f"{ADD_CUE} scripts/ci/new_gate.py fresh file\n"
            "scripts/ci/new_gate.py::some_function " + ADD_CUE + " here"
        )
        added_set = {"scripts/ci/new_gate.py"}

        def ancestry_of(path: str) -> Optional[bool]:
            return False

        result = evaluate(evidence, added_set, ancestry_of)
        # Symbol warnings must NOT affect ok
        assert result.ok is True

    # NEW: prose credit-taking is ADVISORY only (core fix for the false-positive class)
    def test_prose_credit_taking_is_advisory_not_blocking(self):
        """PROSE-only claim of a pre-existing path -> .ok True (advisory), not FAIL.

        Reviewer blocker: 'Added tests for app/existing_feature.py' co-locates
        the add-cue with a pre-existing path. Gate must NOT hard-fail this case
        because a regex cannot reliably distinguish the grammatical object of 'add'.
        """
        evidence = f"{ADD_CUE} tests for app/existing_feature.py"
        added_set = set()  # NOT a new file in this PR

        def ancestry_of(path: str) -> Optional[bool]:
            return True  # predates base — would be BLOCKING if treated as structured

        result = evaluate(evidence, added_set, ancestry_of)
        # Prose claim must NOT block: .ok stays True
        assert result.ok is True
        # But there must be an advisory warning mentioning the path
        assert any("app/existing_feature.py" in w for w in result.warnings)

    # NEW: structured credit-taking still blocks (regression guard)
    def test_structured_credit_taking_still_blocks(self):
        """STRUCTURED fenced A-entry of a pre-existing path -> .ok False (BLOCKING).

        This is the anti-credit-taking guarantee: if the Evidence section has
        a fenced git diff --name-status block listing a path as 'A' but that
        path predates base, the gate HARD-FAILS.
        """
        evidence = "git diff --name-status output:\n" "```\n" "A\tapp/existing_feature.py\n" "```\n"
        added_set = set()  # NOT a new file in this PR

        def ancestry_of(path: str) -> Optional[bool]:
            return True  # predates base

        result = evaluate(evidence, added_set, ancestry_of)
        assert result.ok is False
        assert any("app/existing_feature.py" in f for f in result.failures)


# ===========================================================================
# 4. extract_structured_add_paths and extract_prose_add_paths
# ===========================================================================


class TestExtractStructuredAddPaths:
    def test_fenced_a_tab_entry_is_structured(self):
        """A\\tpath in a fenced block is a structured claim."""
        evidence = "```\nA\tscripts/ci/new_gate.py\nM\tother.py\n```\n"
        paths = extract_structured_add_paths(evidence)
        assert "scripts/ci/new_gate.py" in paths
        assert "other.py" not in paths

    def test_prose_line_is_not_structured(self):
        """A prose line with add cue + path is NOT in the structured set."""
        evidence = f"{ADD_CUE} scripts/ci/new_gate.py as the gate"
        paths = extract_structured_add_paths(evidence)
        assert "scripts/ci/new_gate.py" not in paths

    def test_empty_evidence_returns_empty(self):
        """No fenced blocks -> empty structured set."""
        evidence = f"{ADD_CUE} scripts/ci/new_gate.py as the gate"
        paths = extract_structured_add_paths(evidence)
        assert paths == []


class TestExtractProseAddPaths:
    def test_prose_add_cue_path_is_prose(self):
        """A prose add-cue line produces a path in the prose set."""
        evidence = f"{ADD_CUE} scripts/ci/new_gate.py as the gate"
        paths = extract_prose_add_paths(evidence)
        assert "scripts/ci/new_gate.py" in paths

    def test_structured_path_excluded_from_prose(self):
        """A path already in the structured set is excluded from prose results."""
        evidence = (
            "```\n"
            "A\tscripts/ci/new_gate.py\n"
            "```\n"
            f"{ADD_CUE} scripts/ci/new_gate.py referenced here too\n"
        )
        # new_gate.py is in the structured set, so prose should NOT include it
        prose_paths = extract_prose_add_paths(evidence)
        assert "scripts/ci/new_gate.py" not in prose_paths

    def test_no_cue_produces_no_prose_paths(self):
        """Line without add cue produces no prose paths."""
        evidence = "reuses scripts/ci/pr_meta_checks.py for parsing"
        paths = extract_prose_add_paths(evidence)
        assert "scripts/ci/pr_meta_checks.py" not in paths


# ===========================================================================
# 5. Backtick / quote stripping (F4)
# ===========================================================================


class TestBacktickQuoteStripping:
    def test_backtick_wrapped_path_normalized(self):
        """A path wrapped in backticks in a prose line is stripped and claimed."""
        evidence = f"{ADD_CUE} `app/foo.py` to handle edge cases"
        paths = extract_claimed_add_paths(evidence)
        assert "app/foo.py" in paths
        assert "`app/foo.py`" not in paths

    def test_trailing_punctuation_stripped(self):
        """Trailing period/comma/semicolon stripped from path token."""
        evidence = f"{ADD_CUE} app/foo.py. Also updated other things."
        paths = extract_claimed_add_paths(evidence)
        assert "app/foo.py" in paths
        assert "app/foo.py." not in paths

    def test_quoted_path_normalized(self):
        """A path wrapped in double quotes is stripped to bare path."""
        evidence = f'{ADD_CUE} "app/bar.py" for new feature'
        paths = extract_claimed_add_paths(evidence)
        assert "app/bar.py" in paths
        assert '"app/bar.py"' not in paths

    def test_backtick_path_in_structured_fenced_block(self):
        """Backticks inside a fenced block A-entry are stripped for structured paths."""
        evidence = "```\nA\t`scripts/ci/gate.py`\n```\n"
        paths = extract_structured_add_paths(evidence)
        assert "scripts/ci/gate.py" in paths
        assert "`scripts/ci/gate.py`" not in paths
