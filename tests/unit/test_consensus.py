"""Unit tests for 4-judge consensus + 5th-judge tiebreak."""

from lib.evaluators.consensus import decide_consensus
from lib.evaluators.judge import JudgeResult


def _judges(verdicts: list[str]) -> list[JudgeResult]:
    """Build a list of JudgeResult with given verdicts (for testing)."""
    axes = ["code-correctness", "safety", "scope-fit", "completeness"]
    return [
        JudgeResult(
            axis=axes[i],
            score=8 if v == "accept" else 2 if v == "reject" else 5,
            verdict=v,
            reasoning="",
        )
        for i, v in enumerate(verdicts)
    ]


def test_4_accept_unanimous_accept():
    result = decide_consensus(_judges(["accept"] * 4))
    assert result.verdict == "accept"
    assert result.escalated is False


def test_3_accept_1_reject_majority_accept():
    result = decide_consensus(_judges(["accept", "accept", "accept", "reject"]))
    assert result.verdict == "accept"


def test_4_reject_unanimous_reject():
    result = decide_consensus(_judges(["reject"] * 4))
    assert result.verdict == "reject"


def test_3_reject_1_accept_majority_reject():
    result = decide_consensus(_judges(["reject", "reject", "reject", "accept"]))
    assert result.verdict == "reject"


def test_2_2_split_escalates():
    """No 3-of-4 majority -> F60 -> escalate to 5th judge."""
    result = decide_consensus(_judges(["accept", "accept", "reject", "reject"]))
    assert result.escalated is True
    assert result.verdict == "needs_5th_judge"


def test_any_unsure_escalates():
    """Any 'unsure' vote -> F60 escalation."""
    result = decide_consensus(_judges(["accept", "accept", "accept", "unsure"]))
    assert result.escalated is True


def test_5th_judge_tiebreaker_accept():
    base = _judges(["accept", "accept", "reject", "reject"])
    fifth = JudgeResult(axis="tiebreaker", score=9, verdict="accept", reasoning="Tiebreaker")
    result = decide_consensus(base, fifth_judge=fifth)
    assert result.verdict == "accept"
    assert result.escalated is True


def test_5th_judge_still_unsure_fail_loud():
    base = _judges(["accept", "accept", "reject", "reject"])
    fifth = JudgeResult(axis="tiebreaker", score=5, verdict="unsure", reasoning="Still unclear")
    result = decide_consensus(base, fifth_judge=fifth)
    assert result.verdict == "fail_loud"
