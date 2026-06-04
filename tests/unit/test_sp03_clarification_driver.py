"""SP-03 acceptance oracles — clarification driver + spec-drafter (PRD §6 SP-03).

These are the red-green oracles for SP-03. They run against the DETERMINISTIC
in-memory drafter (app/adapters/inmemory/spec_drafter.py) — NO live Vertex / LLM
calls (hermetic CI). The drafter is rule-based on PLANTED TOKENS:

  - "AMBIG:<token>"        a planted ambiguity → a clarifying question that
                           references <token>, tagged with a category.
  - "AMBIG:<token>@<cat>"  pin the question category (functional / data_contracts
                           / edge_error / non_functional / scope_boundary).
  - "MINOR:<token>"        a BELOW-threshold ambiguity → auto-resolved into an
                           assumptions[] entry, NOT a question.
  - "FALSE:<token>"        a planted false premise (nonexistent API/flag) →
                           a kind=clarification challenge citing <token>.
  - "DEPRECATED:<token>"   a real-but-deprecated/suboptimal choice → exactly one
                           kind=override item with a recommended alternative.
  - "STDDOMAIN:<domain>"   a known-standard domain → ≥1 cited applied_standards entry.

A goal with NONE of these tokens is "fully specified": ZERO questions, ZERO
challenges, ZERO overrides (the false-positive control, C2/C12).

PRD acceptance (audit/2026-05-29-prd-gap-autonomous-sdlc/PRD §6 SP-03):
  * planted-ambiguity → question references the planted token (not a fixed string)
  * fully-specified → ZERO questions
  * confidence unchanged on a non-answer; rises only when a tracked ambiguity resolves
  * (1.1) known-standard domain → ≥1 cited applied_standards; generic → may be zero
  * (1.2) under-specified in ≥2 categories → ≥1 question tagged to EACH; ≤5/round
  * (1.3) below-threshold → ZERO questions + exactly one assumptions[] entry
  * (2.4 + C18) false premise → kind=clarification citing the token; deprecated →
    exactly one kind=override; all-true control → zero challenges, zero overrides
"""

from __future__ import annotations

import pytest

from app.adapters.inmemory.spec_drafter import InMemorySpecDrafter
from app.core.spec_drafter import (
    MAX_QUESTIONS_PER_ROUND,
    AbstractSpecDrafter,
    DraftResult,
)


@pytest.fixture
def drafter() -> InMemorySpecDrafter:
    return InMemorySpecDrafter()


# ---------------------------------------------------------------------------
# ABC integrity (CLAUDE.md builder-agent rule: do NOT collapse the ABC)
# ---------------------------------------------------------------------------


def test_inmemory_drafter_is_abstract_subclass():
    assert issubclass(InMemorySpecDrafter, AbstractSpecDrafter)


def test_abc_cannot_be_instantiated():
    with pytest.raises(TypeError):
        AbstractSpecDrafter()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Core oracle 1: planted ambiguity → question references the planted token
# ---------------------------------------------------------------------------


def test_planted_ambiguity_yields_question_referencing_token(drafter: InMemorySpecDrafter):
    result = drafter.draft("Build a service. AMBIG:retention_window")
    tokens = {q.references_token for q in result.questions}
    assert "retention_window" in tokens, "question must reference the planted token"
    # NOT a fixed string: the token must appear in the question text itself.
    matching = [q for q in result.questions if q.references_token == "retention_window"]
    assert matching and "retention_window" in matching[0].text


def test_distinct_planted_tokens_yield_distinct_questions(drafter: InMemorySpecDrafter):
    # The question text must track the token, not be a fixed template string.
    r1 = drafter.draft("Goal. AMBIG:alpha_token")
    r2 = drafter.draft("Goal. AMBIG:beta_token")
    t1 = next(q.text for q in r1.questions if q.references_token == "alpha_token")
    t2 = next(q.text for q in r2.questions if q.references_token == "beta_token")
    assert t1 != t2


# ---------------------------------------------------------------------------
# Core oracle 2: fully-specified goal → ZERO questions (false-positive control)
# ---------------------------------------------------------------------------


def test_fully_specified_goal_yields_zero_questions(drafter: InMemorySpecDrafter):
    result = drafter.draft(
        "Add a /healthz endpoint returning 200 with body 'ok'; "
        "no auth; deploy to staging; success = curl 200."
    )
    assert result.questions == []


def test_fully_specified_goal_yields_zero_challenges_and_overrides(drafter: InMemorySpecDrafter):
    # All-true control (PRD 2.4 false-positive control): no challenges, no overrides.
    result = drafter.draft("Add a /healthz endpoint returning 200 with body 'ok'.")
    assert result.challenges == []
    assert result.overrides == []


# ---------------------------------------------------------------------------
# Core oracle 3: confidence — unchanged on non-answer; rises on resolution
# ---------------------------------------------------------------------------


def test_confidence_unchanged_on_non_answer(drafter: InMemorySpecDrafter):
    intent = "Goal. AMBIG:retention_window"
    base = drafter.draft(intent)
    # An irrelevant / non-answer (wrong key) must NOT move confidence.
    nonanswer = drafter.draft(intent, answers={"unrelated_key": "whatever"})
    assert nonanswer.confidence == pytest.approx(base.confidence)


def test_confidence_rises_only_when_tracked_ambiguity_resolved(drafter: InMemorySpecDrafter):
    intent = "Goal. AMBIG:retention_window"
    base = drafter.draft(intent)
    resolved = drafter.draft(intent, answers={"retention_window": "30 days"})
    assert resolved.confidence > base.confidence


# ---------------------------------------------------------------------------
# (1.1) applied_standards — known-standard domain cited; generic may be zero
# ---------------------------------------------------------------------------


def test_known_standard_domain_yields_cited_applied_standard(drafter: InMemorySpecDrafter):
    result = drafter.draft("Build a login form. STDDOMAIN:auth")
    assert len(result.applied_standards) >= 1
    std = result.applied_standards[0]
    assert std.principle and std.source and std.why
    assert std.status == "proposed"  # overridable DEFAULT (R5), not a lock


def test_generic_goal_may_yield_zero_applied_standards(drafter: InMemorySpecDrafter):
    # Green control: a generic goal with no STDDOMAIN token → zero standards.
    result = drafter.draft("Rename a local variable in a helper function.")
    assert result.applied_standards == []


# ---------------------------------------------------------------------------
# (1.2) coverage — ≥2 under-specified categories → ≥1 question tagged to EACH
# ---------------------------------------------------------------------------


def test_two_undersized_categories_each_get_a_tagged_question(drafter: InMemorySpecDrafter):
    result = drafter.draft(
        "Build an importer. AMBIG:csv_schema@data_contracts AMBIG:bad_row@edge_error"
    )
    cats = result.covered_categories
    assert "data_contracts" in cats
    assert "edge_error" in cats
    # red arm: a missed category would drop tag-count below the under-spec count.
    assert len(cats) >= 2


def test_questions_capped_at_five_per_round(drafter: InMemorySpecDrafter):
    # Six planted ambiguities → the drafter MUST cap at ≤5 (PRD §6 SP-03 (b)).
    intent = " ".join(f"AMBIG:tok{i}@functional" for i in range(6))
    result = drafter.draft("Goal. " + intent)
    assert len(result.questions) <= MAX_QUESTIONS_PER_ROUND


def test_draftresult_rejects_more_than_five_questions():
    # The cap is structural at the type boundary, not a soft preference.
    from app.core.spec_drafter import ClarifyingQuestion

    qs = [
        ClarifyingQuestion(text=f"q{i}", category="functional", references_token=f"t{i}")
        for i in range(6)
    ]
    with pytest.raises(ValueError):
        DraftResult(questions=qs)


# ---------------------------------------------------------------------------
# (1.3) anti-over-clarification — below-threshold → assumptions entry, not a Q
# ---------------------------------------------------------------------------


def test_below_threshold_ambiguity_logs_assumption_not_question(drafter: InMemorySpecDrafter):
    result = drafter.draft("Build a CLI tool. MINOR:log_format")
    # ZERO questions for the minor ambiguity ...
    minor_qs = [q for q in result.questions if q.references_token == "log_format"]
    assert minor_qs == []
    # ... and exactly ONE assumptions entry referencing the resolved token.
    minor_assumptions = [a for a in result.assumptions if a.resolved_token == "log_format"]
    assert len(minor_assumptions) == 1
    assert minor_assumptions[0].chosen_interpretation


def test_threshold_crossing_ambiguity_asks_a_question(drafter: InMemorySpecDrafter):
    # Red-green pair to the above: a real (above-threshold) ambiguity → a question.
    result = drafter.draft("Build a CLI tool. AMBIG:output_path")
    asked = [q for q in result.questions if q.references_token == "output_path"]
    assert len(asked) == 1
    # And it is NOT logged as an auto-resolved assumption.
    assert all(a.resolved_token != "output_path" for a in result.assumptions)


# ---------------------------------------------------------------------------
# (2.4 + C18) anti-sycophancy — false premise (clarification) + deprecated (override)
# ---------------------------------------------------------------------------


def test_false_premise_yields_clarification_citing_token(drafter: InMemorySpecDrafter):
    result = drafter.draft("Use the FALSE:os.fastopen API to speed up reads.")
    clar = [a for a in result.ambiguities if a.kind == "clarification"]
    assert len(clar) >= 1
    assert any(a.references_token == "os.fastopen" for a in clar)
    # NOT silently encoded into the TaskSpec acceptance/scope.
    blob = " ".join(result.acceptance_criteria + result.in_scope + result.out_of_scope)
    assert "os.fastopen" not in blob


def test_deprecated_choice_yields_exactly_one_override(drafter: InMemorySpecDrafter):
    result = drafter.draft("Hash passwords with DEPRECATED:md5.")
    overrides = result.overrides
    assert len(overrides) == 1
    ov = overrides[0]
    assert ov.references_token == "md5"
    assert ov.recommended_alternative  # SOTA-grounded alternative is present
    assert ov.rationale
    # cite is optional + ALWAYS flagged unverified (C18), never gated on reachability.
    assert ov.cite_unverified is True


def test_all_true_control_no_challenges_no_overrides(drafter: InMemorySpecDrafter):
    # The all-true fully-specified CONTROL: zero challenges, zero overrides.
    result = drafter.draft(
        "Hash passwords with argon2id; store the salt; success = round-trip verify."
    )
    assert result.challenges == []
    assert result.overrides == []


# ---------------------------------------------------------------------------
# Driver wiring — decide_next_action + the drafter
# ---------------------------------------------------------------------------


def test_driver_asks_next_when_ambiguity_open(drafter: InMemorySpecDrafter):
    from lib.anchors.clarification_driver import run_clarification_round

    outcome = run_clarification_round(
        "Goal. AMBIG:retention_window", drafter=drafter, questions_asked=0
    )
    # An open ambiguity with low confidence → the loop keeps asking.
    assert outcome.action.kind == "ask_next"
    assert outcome.questions  # at least one question surfaced
    assert any(q.references_token == "retention_window" for q in outcome.questions)


def test_driver_locks_when_fully_specified(drafter: InMemorySpecDrafter):
    from lib.anchors.clarification_driver import run_clarification_round

    outcome = run_clarification_round(
        "Add a /healthz endpoint returning 200 with body 'ok'; no auth; success = curl 200.",
        drafter=drafter,
        questions_asked=0,
    )
    # Fully specified → high confidence → decide_next_action says lock.
    assert outcome.action.kind == "lock"
    assert outcome.questions == []


def test_driver_tags_intent_as_untrusted(drafter: InMemorySpecDrafter):
    # C16: the operator intent enters as UNTRUSTED external text.
    from lib.anchors.clarification_driver import run_clarification_round

    outcome = run_clarification_round("Goal. AMBIG:x", drafter=drafter, questions_asked=0)
    assert outcome.intent_trust == "untrusted"
