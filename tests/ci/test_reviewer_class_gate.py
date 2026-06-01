"""C-04 unit tests — C9 reviewer-model-class gate (reviewer_class_gate.py).

RED-GREEN matrix
================
Each test exercises a fixture PR body + commit-trailer text.  The table below
summarises PASS / FAIL / ADVISORY:

 Case                                   | expect
 --------------------------------------- | ------
 Opus reviews Sonnet (P0)               | PASS
 Opus reviews Opus (P0)                 | FAIL  (same model)
 Missing Reviewer model line (P0)       | FAIL  (closed-default)
 Gemini reviews Claude (P0)             | PASS  (different model + different vendor)
 Reviewer also co-authored (P0)         | FAIL  (mixed-authorship independence)
 Non-P0/P1 with missing reviewer        | PASS (advisory, no hard gate)
 Non-P0/P1 same model                   | PASS (advisory)
 P1 label detected from label list      | FAIL  (same model, P1 → hard gate)
 P1 from Priority: body field           | FAIL  (same model, P1 → hard gate)
 Blank / placeholder Reviewer model     | FAIL  (closed-default)
 No Co-Authored-By trailer (P0)         | FAIL  (closed-default)
 Haiku reviews Sonnet                   | PASS  (different model)
 normalise_model smoke test             | unit  (not gate call)
"""

from __future__ import annotations

import sys
import os

# Allow importing from scripts/ci without installing
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ci"))

from reviewer_class_gate import (
    evaluate,
    extract_implementer_models,
    extract_reviewer_model,
    is_p0_or_p1,
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
