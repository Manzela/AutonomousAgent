"""Tests for multi-turn conversation and correction-handling logic.

These tests exercise the clarification driver over multiple turns, checking
that:
  1. Confidence rises only when tracked ambiguities are resolved.
  2. Remaining unresolved ambiguities continue to generate questions in subsequent rounds.
  3. Topic switches or corrections (changing answers or changing the goal mid-conversation)
     are handled gracefully and the confidence/questions update accordingly.
"""

from __future__ import annotations

import pytest
from app.adapters.inmemory.spec_drafter import InMemorySpecDrafter
from lib.anchors.clarification_driver import run_clarification_round


@pytest.fixture
def drafter() -> InMemorySpecDrafter:
    return InMemorySpecDrafter()


def test_multi_turn_resolution_flow(drafter: InMemorySpecDrafter):
    """Verify that multiple ambiguities require multiple turns of questions/answers."""
    intent = "Create a database storage service. AMBIG:retention_window AMBIG:db_type"

    # Turn 1: Initial draft run, no answers yet.
    outcome_t1 = run_clarification_round(
        intent,
        drafter=drafter,
        questions_asked=0,
        round_index=0,
        answers={},
    )
    assert outcome_t1.action.kind == "ask_next"
    assert len(outcome_t1.questions) == 2
    assert outcome_t1.confidence == pytest.approx(0.6)  # 1.0 - 0.2 * 2 unresolved

    # Turn 2: Operator answers ONE question (retention_window).
    answers = {"retention_window": "30 days"}
    outcome_t2 = run_clarification_round(
        intent,
        drafter=drafter,
        questions_asked=2,
        round_index=1,
        answers=answers,
    )
    assert outcome_t2.action.kind == "ask_next"
    # Only the unresolved ambiguity generates a question
    assert len(outcome_t2.questions) == 1
    assert outcome_t2.questions[0].references_token == "db_type"
    assert outcome_t2.confidence == pytest.approx(0.8)  # 1.0 - 0.2 * 1 unresolved

    # Turn 3: Operator answers the second question (db_type).
    answers["db_type"] = "postgres"
    outcome_t3 = run_clarification_round(
        intent,
        drafter=drafter,
        questions_asked=3,
        round_index=2,
        answers=answers,
    )
    # All ambiguities resolved -> should lock.
    assert outcome_t3.action.kind == "lock"
    assert len(outcome_t3.questions) == 0
    assert outcome_t3.confidence == pytest.approx(1.0)


def test_topic_switch_or_correction_mid_turn(drafter: InMemorySpecDrafter):
    """Verify that correction-handling (e.g. changing an answer or adding a new ambiguity

    mid-conversation) propagates correctly to confidence and questions.
    """
    intent = "Create a storage system. AMBIG:retention_window AMBIG:db_type"

    # Turn 1: Operator answers retention_window, db_type remains open.
    answers = {"retention_window": "30 days"}
    outcome_t1 = run_clarification_round(
        intent,
        drafter=drafter,
        questions_asked=2,
        round_index=1,
        answers=answers,
    )
    assert len(outcome_t1.questions) == 1
    assert outcome_t1.questions[0].references_token == "db_type"
    assert outcome_t1.confidence == pytest.approx(0.8)

    # Turn 2: Topic Switch / Correction.
    # The operator changes their mind about the goal and edits the intent to add a new requirement:
    # "AMBIG:backup_frequency" and changes their answer for database type to MySQL.
    corrected_intent = intent + " AMBIG:backup_frequency"
    answers["db_type"] = (
        "mysql"  # Operator now answers db_type too, but backup_frequency is new and open
    )

    outcome_t2 = run_clarification_round(
        corrected_intent,
        drafter=drafter,
        questions_asked=3,
        round_index=2,
        answers=answers,
    )

    # Now retention_window and db_type are resolved, but backup_frequency is open.
    assert len(outcome_t2.questions) == 1
    assert outcome_t2.questions[0].references_token == "backup_frequency"
    assert outcome_t2.confidence == pytest.approx(0.8)  # 1.0 - 0.2 * 1 unresolved

    # Turn 3: Resolve backup_frequency.
    answers["backup_frequency"] = "daily"
    outcome_t3 = run_clarification_round(
        corrected_intent,
        drafter=drafter,
        questions_asked=4,
        round_index=3,
        answers=answers,
    )
    assert outcome_t3.action.kind == "lock"
    assert len(outcome_t3.questions) == 0
    assert outcome_t3.confidence == pytest.approx(1.0)
