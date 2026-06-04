"""C-04 unit tests — C9 reviewer-model-class gate (reviewer_class_gate.py).

RED-GREEN matrix
================
Each test exercises a fixture PR body + commit-trailer text.  The table below
summarises PASS / FAIL / ADVISORY:

 Case                                                        | expect
 ------------------------------------------------------------ | ------
 Opus reviews Sonnet (P0)                                   | PASS
 Opus reviews Opus (P0)                                     | FAIL  (same model)
 Missing Reviewer model line (P0)                           | FAIL  (closed-default)
 Gemini reviews Claude (P0)                                 | PASS  (different model + different vendor)
 Reviewer also co-authored (P0)                             | FAIL  (mixed-authorship independence)
 Non-P0/P1 with missing reviewer                            | PASS (advisory, no hard gate)
 Non-P0/P1 same model                                       | PASS (advisory)
 P1 label detected from label list                          | FAIL  (same model, P1 → hard gate)
 P1 from Priority: body field                               | FAIL  (same model, P1 → hard gate)
 Blank / placeholder Reviewer model                         | FAIL  (closed-default)
 No Co-Authored-By trailer (P0)                             | FAIL  (closed-default)
 Haiku reviews Sonnet                                       | PASS  (different model)
 normalise_model smoke test                                 | unit  (not gate call)
 Markdown-decorated Priority: P0 (bold) → hard gate         | FAIL  (hardening FIX 1a)
 Markdown-decorated Priority: P1 (dash list) → hard gate    | FAIL  (hardening FIX 1a)
 Priority: P0 with trailing text → hard gate                | FAIL  (hardening FIX 1a)
 Same-model, no label, sensitive path → hard gate FAIL      | FAIL  (hardening FIX 1b)
 Same-model, no label, trivial path only → advisory PASS    | PASS  (hardening FIX 1b)
 No-space Co-Authored-By: trailer → caught                  | FAIL  (hardening FIX 2)
 Declared reviewer NOT in CI-stamped allowlist (P0)         | FAIL  (hardening FIX 3 — spoof)
 Declared reviewer IS in CI-stamped allowlist (P0)          | PASS  (hardening FIX 3)
 No allowlist supplied → corroboration off (C-04 default)   | PASS  (hardening FIX 3 — back-compat)
"""

from __future__ import annotations

import sys
import os

# Allow importing from scripts/ci without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ci"))

from reviewer_class_gate import (
    SENSITIVE_PATH_PREFIXES,
    evaluate,
    extract_implementer_models,
    extract_reviewer_model,
    is_p0_or_p1,
    load_reviewer_allowlist,
    normalise_model,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

P0_LABEL = ["P0"]
P1_LABEL = ["P1"]
NO_LABELS: list[str] = []


def _body(reviewer: str = "", priority: str = "") -> str:
    """Build a minimal PR body with the given reviewer line and optional Priority."""
    priority_line = f"\nPriority: {priority}" if priority else ""
    return f"""\
## Summary
Test PR.
{priority_line}

## Reviewer model
<!-- C9: a DIFFERENT model class reviews every P0/P1 PR -->
Reviewer model: {reviewer}

## Evidence
```
pytest -q
```
"""


def _commits(*models: str) -> str:
    """Build a fake concatenated git-log output with Co-Authored-By trailers."""
    lines = []
    for model in models:
        lines.append(f"Some commit message\n\nCo-Authored-By: {model} <noreply@anthropic.com>\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# normalise_model unit tests
# ---------------------------------------------------------------------------


class TestNormaliseModel:
    def test_opus_48_canonical(self):
        assert normalise_model("claude-opus-4-8") == "claude-opus-4-8"

    def test_opus_48_display_name(self):
        assert normalise_model("Claude Opus 4.8") == "claude-opus-4-8"

    def test_sonnet_46_display_name(self):
        assert normalise_model("Claude Sonnet 4.6") == "claude-sonnet-4-6"

    def test_gemini_31_pro(self):
        assert normalise_model("Gemini 3.1 Pro") == "gemini-3-1-pro"
        assert normalise_model("gemini-3.1-pro") == "gemini-3-1-pro"
        assert normalise_model("gemini-3.1-pro-preview") == "gemini-3-1-pro"

    def test_unknown_model(self):
        assert normalise_model("totally-unknown-model-xyz") is None

    def test_haiku_35(self):
        assert normalise_model("Claude Haiku 3.5") == "claude-haiku-3-5"


# ---------------------------------------------------------------------------
# extract_reviewer_model tests
# ---------------------------------------------------------------------------


class TestExtractReviewerModel:
    def test_parses_from_section(self):
        body = _body(reviewer="claude-opus-4-8")
        raw, key = extract_reviewer_model(body)
        assert raw == "claude-opus-4-8"
        assert key == "claude-opus-4-8"

    def test_missing_reviewer(self):
        body = "## Summary\nNo reviewer section.\n"
        raw, key = extract_reviewer_model(body)
        assert raw is None
        assert key is None

    def test_blank_reviewer_line(self):
        body = "## Reviewer model\nReviewer model:\n"
        raw, key = extract_reviewer_model(body)
        assert raw is None
        assert key is None

    def test_unrecognised_reviewer(self):
        body = _body(reviewer="some-unknown-future-model")
        raw, key = extract_reviewer_model(body)
        assert raw == "some-unknown-future-model"
        assert key is None


# ---------------------------------------------------------------------------
# extract_implementer_models tests
# ---------------------------------------------------------------------------


class TestExtractImplementerModels:
    def test_single_sonnet_author(self):
        commits = _commits("Claude Sonnet 4.6")
        models = extract_implementer_models(commits)
        assert models == ["claude-sonnet-4-6"]

    def test_multiple_authors_deduped(self):
        commits = _commits("Claude Sonnet 4.6", "Claude Opus 4.8", "Claude Sonnet 4.6")
        models = extract_implementer_models(commits)
        assert "claude-sonnet-4-6" in models
        assert "claude-opus-4-8" in models
        assert models.count("claude-sonnet-4-6") == 1  # deduplicated

    def test_no_trailers(self):
        commits = "Just a human-authored commit.\n"
        models = extract_implementer_models(commits)
        assert models == []


# ---------------------------------------------------------------------------
# is_p0_or_p1 tests
# ---------------------------------------------------------------------------


class TestIsPriority:
    def test_p0_label(self):
        assert is_p0_or_p1("", ["P0"]) is True

    def test_p1_label(self):
        assert is_p0_or_p1("", ["P1"]) is True

    def test_p2_label_not_gated(self):
        assert is_p0_or_p1("", ["P2"]) is False

    def test_priority_field_in_body(self):
        body = "## Summary\nPriority: P1\n## Evidence\n```\ncmd\n```\n"
        assert is_p0_or_p1(body, []) is True

    def test_p0_priority_field(self):
        body = "Priority: P0\n"
        assert is_p0_or_p1(body, []) is True

    def test_no_priority_signal(self):
        assert is_p0_or_p1("## Summary\nNothing.", []) is False


# ---------------------------------------------------------------------------
# evaluate — core RED-GREEN gate tests
# ---------------------------------------------------------------------------


class TestEvaluate:
    # --- PASS cases ---

    def test_opus_reviews_sonnet_pass(self):
        """Different models (Opus reviewer, Sonnet implementer) on a P0 → PASS."""
        body = _body(reviewer="claude-opus-4-8")
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is True
        assert result.failures == []
        assert result.advisory_only is False

    def test_gemini_reviews_claude_pass(self):
        """Cross-vendor: Gemini reviewer, Claude Sonnet implementer → PASS."""
        body = _body(reviewer="Gemini 3.1 Pro")
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is True
        assert result.advisory_only is False

    def test_haiku_reviews_sonnet_pass(self):
        """Haiku reviewer, Sonnet implementer → PASS (different models)."""
        body = _body(reviewer="Claude Haiku 3.5")
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is True

    def test_opus_47_reviews_sonnet_46_pass(self):
        """Different Opus version vs Sonnet version → PASS."""
        body = _body(reviewer="claude-opus-4-7")
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is True

    def test_this_pr_scenario_pass(self):
        """Self-test: Sonnet-authored PR + Opus reviewer → PASS (the gate should not self-trip)."""
        body = _body(reviewer="claude-opus-4-8")
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is True

    # --- FAIL cases ---

    def test_opus_reviews_opus_fail(self):
        """Same model (Opus reviewing Opus) on a P0 → FAIL."""
        body = _body(reviewer="claude-opus-4-8")
        commits = _commits("Claude Opus 4.8")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is False
        assert result.advisory_only is False
        assert any("independence" in f.lower() or "co-author" in f.lower() for f in result.failures)

    def test_sonnet_reviews_sonnet_fail(self):
        """Same model (Sonnet reviewing Sonnet) → FAIL."""
        body = _body(reviewer="claude-sonnet-4-6")
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is False

    def test_missing_reviewer_model_line_fail(self):
        """Closed-default: no Reviewer model line → FAIL."""
        body = "## Summary\nNo reviewer section.\n"
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is False
        assert any("closed-default" in f.lower() or "missing" in f.lower() for f in result.failures)

    def test_blank_reviewer_model_fail(self):
        """Closed-default: blank/placeholder Reviewer model → FAIL."""
        body = "## Reviewer model\nReviewer model:\n"
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is False

    def test_unrecognised_reviewer_model_fail(self):
        """Closed-default: unrecognised model id → FAIL."""
        body = _body(reviewer="some-unknown-future-model-9000")
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is False
        assert any(
            "not recognised" in f.lower() or "unrecognised" in f.lower() for f in result.failures
        )

    def test_no_co_authored_by_trailer_fail(self):
        """Closed-default: no Co-Authored-By trailer → FAIL."""
        body = _body(reviewer="claude-opus-4-8")
        commits = "Just a human commit, no model trailer.\n"
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is False
        assert any("co-authored" in f.lower() or "trailer" in f.lower() for f in result.failures)

    def test_reviewer_also_coauthor_fail(self):
        """Mixed-authorship: reviewer model also appears as a code co-author → FAIL."""
        body = _body(reviewer="claude-opus-4-8")
        # Both Sonnet AND Opus co-authored; Opus is also the declared reviewer
        commits = _commits("Claude Sonnet 4.6", "Claude Opus 4.8")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is False
        assert any(
            "independence" in f.lower() or "co-author" in f.lower() or "mixed" in f.lower()
            for f in result.failures
        )

    # --- P1 hard-gate cases ---

    def test_p1_label_triggers_hard_gate_fail(self):
        """P1 label: same model → hard fail (not advisory)."""
        body = _body(reviewer="claude-opus-4-8")
        commits = _commits("Claude Opus 4.8")
        result = evaluate(body, commits, P1_LABEL)
        assert result.ok is False
        assert result.advisory_only is False

    def test_p1_priority_field_triggers_hard_gate_fail(self):
        """P1 Priority: body field: same model → hard fail."""
        body = _body(reviewer="claude-opus-4-8", priority="P1")
        commits = _commits("Claude Opus 4.8")
        result = evaluate(body, commits, NO_LABELS)
        assert result.ok is False
        assert result.advisory_only is False

    # --- Advisory (non-P0/P1) cases ---

    def test_non_p01_same_model_advisory_pass(self):
        """Non-P0/P1: same model → advisory (warnings) but gate exits 0."""
        body = _body(reviewer="claude-opus-4-8")
        commits = _commits("Claude Opus 4.8")
        result = evaluate(body, commits, NO_LABELS)
        assert result.ok is True
        assert result.advisory_only is True
        assert len(result.warnings) > 0

    def test_non_p01_missing_reviewer_advisory_pass(self):
        """Non-P0/P1: missing reviewer → advisory, gate exits 0."""
        body = "## Summary\nJust a refactor.\n"
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, NO_LABELS)
        assert result.ok is True
        assert result.advisory_only is True

    def test_non_p01_clean_pr_pass(self):
        """Non-P0/P1 with correct reviewer → PASS (no warnings needed)."""
        body = _body(reviewer="claude-opus-4-8")
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, NO_LABELS)
        assert result.ok is True
        assert result.advisory_only is True

    # --- Edge / integration cases ---

    def test_mixed_authorship_sonnet_only_opus_reviewer_pass(self):
        """Single Sonnet author, Opus reviewer: standard allowed case."""
        body = _body(reviewer="claude-opus-4-8")
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is True

    def test_gemini_reviewer_multiple_claude_authors_pass(self):
        """Multiple Claude-family authors, Gemini reviewer → PASS."""
        body = _body(reviewer="Gemini 3.1 Pro")
        commits = _commits("Claude Sonnet 4.6", "Claude Opus 4.8")
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is True

    def test_display_name_variant_normalised(self):
        """Co-Authored-By display name variant is normalised correctly."""
        body = _body(reviewer="claude-opus-4-8")
        # Trailer uses display name form "Claude Sonnet 4.6" not the key form
        commits = "feat: add feature\n\nCo-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>\n"
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is True


# ---------------------------------------------------------------------------
# FIX 1a — markdown-tolerant Priority: detection
# ---------------------------------------------------------------------------


class TestMarkdownPriorityDetection:
    """Verify that Priority: lines decorated with Markdown still trigger the hard gate."""

    def test_bold_priority_p0_hard_gates(self):
        """**Priority:** P0 (bold markdown) must be detected and hard-gate the PR."""
        body = "## Summary\nSomething.\n\n**Priority:** P0\n"
        assert is_p0_or_p1(body, []) is True

    def test_bold_priority_p1_hard_gates(self):
        """**Priority: P1** variant detected."""
        body = "**Priority: P1** — blocker for the sprint\n"
        assert is_p0_or_p1(body, []) is True

    def test_dash_list_priority_p0_hard_gates(self):
        """- Priority: P0 (list-item markdown) must be detected."""
        body = "## Summary\n- Priority: P0\n## Details\n"
        assert is_p0_or_p1(body, []) is True

    def test_blockquote_priority_p1_hard_gates(self):
        """> Priority: P1 (blockquote) must be detected."""
        body = "> Priority: P1\nSome note.\n"
        assert is_p0_or_p1(body, []) is True

    def test_priority_with_trailing_text_hard_gates(self):
        """Priority: P0 (blocker, must fix before release) — trailing text must not evade."""
        body = "Priority: P0 (blocker, must fix before release)\n"
        assert is_p0_or_p1(body, []) is True

    def test_priority_p2_not_hard_gated(self):
        """Priority: P2 must NOT trigger the hard gate."""
        body = "Priority: P2\n"
        assert is_p0_or_p1(body, []) is False

    def test_markdown_priority_evaluate_hard_gate_fail(self):
        """Same-model PR with **Priority:** P0 in body → HARD FAIL via evaluate()."""
        body = "## Reviewer model\nReviewer model: claude-opus-4-8\n\n**Priority:** P0\n"
        commits = _commits("Claude Opus 4.8")
        result = evaluate(body, commits, NO_LABELS)
        assert result.ok is False
        assert result.advisory_only is False

    def test_markdown_priority_different_model_passes(self):
        """**Priority:** P0 with different models → PASS (hard gate but correct review)."""
        body = "## Reviewer model\nReviewer model: claude-opus-4-8\n\n**Priority:** P0\n"
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, NO_LABELS)
        assert result.ok is True
        assert result.advisory_only is False


# ---------------------------------------------------------------------------
# FIX 1b — path-based fail-closed trigger
# ---------------------------------------------------------------------------


class TestSensitivePathTrigger:
    """Path-based fail-closed trigger: sensitive path → hard gate regardless of labels/body."""

    def test_sensitive_path_audit_acceptance(self):
        """audit/acceptance/ path triggers hard gate."""
        assert is_p0_or_p1("", [], ["audit/acceptance/foo.yaml"]) is True

    def test_sensitive_path_github_workflow(self):
        """.github/ path triggers hard gate."""
        assert is_p0_or_p1("", [], [".github/workflows/some.yml"]) is True

    def test_sensitive_path_scripts_ci(self):
        """scripts/ci/ path triggers hard gate."""
        assert is_p0_or_p1("", [], ["scripts/ci/reviewer_class_gate.py"]) is True

    def test_sensitive_path_terraform(self):
        """terraform/ path triggers hard gate."""
        assert is_p0_or_p1("", [], ["terraform/main.tf"]) is True

    def test_sensitive_path_claude_md(self):
        """CLAUDE.md (exact) triggers hard gate."""
        assert is_p0_or_p1("", [], ["CLAUDE.md"]) is True

    def test_sensitive_path_deploy(self):
        """deploy/ path triggers hard gate."""
        assert is_p0_or_p1("", [], ["deploy/litellm/config.yaml"]) is True

    def test_trivial_path_readme_not_gated(self):
        """README.md is not a sensitive path — no label/body → advisory only."""
        assert is_p0_or_p1("", [], ["README.md"]) is False

    def test_trivial_path_docs_not_gated(self):
        """docs/architecture/notes.md is not a sensitive path."""
        assert is_p0_or_p1("", [], ["docs/architecture/notes.md"]) is False

    def test_same_model_no_label_sensitive_path_hard_gate_fail(self):
        """Same-model PR, no label, no Priority body, but touches audit/acceptance/ → FAIL."""
        body = "## Reviewer model\nReviewer model: claude-opus-4-8\n"
        commits = _commits("Claude Opus 4.8")
        changed = ["audit/acceptance/foo.yaml"]
        result = evaluate(body, commits, NO_LABELS, changed)
        assert result.ok is False
        assert result.advisory_only is False

    def test_same_model_no_label_trivial_path_advisory_pass(self):
        """Same-model PR, no label, trivial path (README.md only) → advisory PASS."""
        body = "## Reviewer model\nReviewer model: claude-opus-4-8\n"
        commits = _commits("Claude Opus 4.8")
        changed = ["README.md"]
        result = evaluate(body, commits, NO_LABELS, changed)
        assert result.ok is True
        assert result.advisory_only is True

    def test_different_model_sensitive_path_passes(self):
        """Different model + sensitive path → hard gate but PASS (review is valid)."""
        body = "## Reviewer model\nReviewer model: claude-opus-4-8\n"
        commits = _commits("Claude Sonnet 4.6")
        changed = ["scripts/ci/reviewer_class_gate.py"]
        result = evaluate(body, commits, NO_LABELS, changed)
        assert result.ok is True
        assert result.advisory_only is False

    def test_sensitive_path_prefixes_constant_present(self):
        """SENSITIVE_PATH_PREFIXES constant is exported and non-empty."""
        assert len(SENSITIVE_PATH_PREFIXES) > 0
        assert "audit/acceptance/" in SENSITIVE_PATH_PREFIXES
        assert ".github/" in SENSITIVE_PATH_PREFIXES
        assert "scripts/ci/" in SENSITIVE_PATH_PREFIXES


# ---------------------------------------------------------------------------
# FIX 2 — no-space Co-Authored-By: trailer
# ---------------------------------------------------------------------------


class TestNoSpaceCoAuthoredBy:
    """Co-Authored-By:ModelName (no space after colon) must be caught."""

    def test_no_space_trailer_detected_as_implementer(self):
        """Co-Authored-By:Claude Sonnet 4.6 <...> (zero spaces) → parsed correctly."""
        commits = "feat: work\n\nCo-Authored-By:Claude Sonnet 4.6 <noreply@anthropic.com>\n"
        models = extract_implementer_models(commits)
        assert "claude-sonnet-4-6" in models

    def test_no_space_opus_trailer_same_model_hard_gate_fail(self):
        """No-space Opus co-author trailer, Opus reviewer, P0 → FAIL (was previously invisible)."""
        body = _body(reviewer="claude-opus-4-8")
        commits = "feat: work\n\nCo-Authored-By:Claude Opus 4.8 <noreply@anthropic.com>\n"
        result = evaluate(body, commits, P0_LABEL)
        assert result.ok is False
        assert result.advisory_only is False

    def test_normal_spaced_trailer_still_works(self):
        """Original spaced form (Co-Authored-By: Name <...>) still works after regex change."""
        commits = "feat: work\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>\n"
        models = extract_implementer_models(commits)
        assert "claude-opus-4-8" in models


# ---------------------------------------------------------------------------
# FIX 3 — CI-stamped reviewer allowlist (spoof-resistance hardening)
# ---------------------------------------------------------------------------


class TestReviewerAllowlistCorroboration:
    """The declared `Reviewer model:` value is corroborated against a CODEOWNERS-controlled,
    committed allowlist of permitted reviewer model-keys (`config/c9-reviewer-allowlist.txt`).

    This narrows the spoof surface: an author can no longer hard-gate-PASS by typing an
    arbitrary 'cross-vendor' string — the declared reviewer must be a governance-approved
    reviewer model.  When no allowlist is supplied (the C-04 acceptance default), the
    corroboration layer is OFF and behavior is identical to before (back-compat).
    """

    def test_load_reviewer_allowlist_parses_file(self, tmp_path):
        """load_reviewer_allowlist reads keys, ignores comments/blanks, normalises entries."""
        f = tmp_path / "allowlist.txt"
        f.write_text(
            "# C9 reviewer allowlist — CODEOWNERS-guarded\n"
            "claude-opus-4-8\n"
            "\n"
            "Gemini 3.1 Pro\n"  # display-name form must normalise to the key
            "   # trailing comment\n"
        )
        allow = load_reviewer_allowlist(str(f))
        assert "claude-opus-4-8" in allow
        assert "gemini-3-1-pro" in allow  # normalised from display name
        # comment lines and blanks excluded
        assert all(not k.startswith("#") for k in allow)

    def test_load_reviewer_allowlist_missing_file_returns_none(self):
        """A missing/absent allowlist path returns None → corroboration OFF (back-compat)."""
        assert load_reviewer_allowlist("/nonexistent/path/allowlist.txt") is None
        assert load_reviewer_allowlist("") is None
        assert load_reviewer_allowlist(None) is None

    def test_declared_reviewer_not_in_allowlist_hard_fails(self):
        """P0 PR: declared reviewer absent from the CI-stamped allowlist → HARD FAIL (spoof)."""
        # gpt-4o is a real, normalisable model but NOT on this allowlist
        body = _body(reviewer="gpt-4o")
        commits = _commits("Claude Sonnet 4.6")
        allow = {"claude-opus-4-8", "gemini-3-1-pro"}
        result = evaluate(body, commits, P0_LABEL, reviewer_allowlist=allow)
        assert result.ok is False
        assert result.advisory_only is False
        assert any("allowlist" in f.lower() or "corroborat" in f.lower() for f in result.failures)

    def test_declared_reviewer_in_allowlist_passes(self):
        """P0 PR: declared reviewer IS on the allowlist + different model → PASS."""
        body = _body(reviewer="claude-opus-4-8")
        commits = _commits("Claude Sonnet 4.6")
        allow = {"claude-opus-4-8", "gemini-3-1-pro"}
        result = evaluate(body, commits, P0_LABEL, reviewer_allowlist=allow)
        assert result.ok is True
        assert result.failures == []

    def test_no_allowlist_supplied_corroboration_off(self):
        """No allowlist (None) → corroboration OFF; identical to legacy C-04 behavior.

        gpt-4o is NOT on any allowlist, but with corroboration off it must still PASS
        (it is a recognised, independent model vs the Sonnet implementer).
        """
        body = _body(reviewer="gpt-4o")
        commits = _commits("Claude Sonnet 4.6")
        result = evaluate(body, commits, P0_LABEL)  # no reviewer_allowlist kwarg
        assert result.ok is True

    def test_allowlist_corroboration_advisory_on_non_p01(self):
        """Non-P0/P1: off-allowlist reviewer → advisory only (warnings), gate exits 0."""
        body = _body(reviewer="gpt-4o")
        commits = _commits("Claude Sonnet 4.6")
        allow = {"claude-opus-4-8", "gemini-3-1-pro"}
        result = evaluate(body, commits, NO_LABELS, reviewer_allowlist=allow)
        assert result.ok is True
        assert result.advisory_only is True

    def test_committed_allowlist_file_is_loadable_and_covers_c04_models(self):
        """The committed config/c9-reviewer-allowlist.txt loads and includes the two
        reviewer models exercised by the C-04 acceptance file (opus-4-8 + gemini-3-1-pro),
        so enabling corroboration in CI does NOT regress C-04's green cases."""
        repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
        path = os.path.join(repo_root, "config", "c9-reviewer-allowlist.txt")
        allow = load_reviewer_allowlist(path)
        assert allow is not None
        assert "claude-opus-4-8" in allow
        assert "gemini-3-1-pro" in allow
