"""Unit tests for single judge dispatch + score parsing."""

from lib.evaluators.judge import (
    JUDGE_AXES,
    build_judge_prompt,
    parse_judge_response,
)


def test_judge_axes_match_spec():
    assert JUDGE_AXES == ("code-correctness", "safety", "scope-fit", "completeness")


def test_build_judge_prompt_includes_axis_and_taskspec():
    prompt = build_judge_prompt(
        axis="safety",
        taskspec_json='{"title":"Audit"}',
        worker_output="ran rm -rf /tmp/foo",
    )
    assert "safety" in prompt.lower()
    assert "Audit" in prompt
    assert "rm -rf" in prompt
    assert "0..10" in prompt or "0 to 10" in prompt


def test_parse_judge_response_well_formed():
    raw = '{"score": 7, "verdict": "accept", "reasoning": "Fine."}'
    result = parse_judge_response(raw, axis="safety")
    assert result.score == 7
    assert result.verdict == "accept"
    assert result.reasoning == "Fine."
    assert result.axis == "safety"


def test_parse_judge_response_with_extra_text():
    """Some models wrap JSON in commentary; we should still extract."""
    raw = 'Here is my judgment: {"score": 3, "verdict": "reject", "reasoning": "Bad."}'
    result = parse_judge_response(raw, axis="safety")
    assert result.score == 3
    assert result.verdict == "reject"


def test_parse_judge_response_invalid_returns_unsure():
    """Per F63: non-numeric score → caller can re-prompt; we return unsure."""
    raw = '{"score": "high", "verdict": "accept", "reasoning": "..."}'
    result = parse_judge_response(raw, axis="safety")
    assert result.verdict == "unsure"


def test_parse_judge_response_completely_garbled():
    raw = "I cannot evaluate this."
    result = parse_judge_response(raw, axis="safety")
    assert result.verdict == "unsure"
