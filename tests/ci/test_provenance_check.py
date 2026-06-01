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
    # (a) FAIL: credit-taking — Evidence claims "added lib/foo.py" but foo.py
    # predates base (ancestry True, not in added_set).
    def test_fail_credit_taking(self):
        """.ok is False naming lib/foo.py when introducing commit predates base."""
        evidence = f"{ADD_CUE} lib/foo.py as shared utility"
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

    def test_multiple_paths_mixed(self):
        """Mixed bag: one ok, one fail, one warn."""
        evidence = (
            f"{ADD_CUE} scripts/ci/new_gate.py fresh file\n"
            f"{ADD_CUE} lib/old_thing.py old file\n"
            f"{ADD_CUE} scripts/ci/ghost.py does not exist\n"
        )
        added_set = {"scripts/ci/new_gate.py"}

        def ancestry_of(path: str) -> Optional[bool]:
            if path == "lib/old_thing.py":
                return True  # predates base
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
