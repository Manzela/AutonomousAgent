import pytest
from unittest.mock import patch
from lib.evaluators import judge_panel
from lib.evaluators.judge import JudgeResult


@pytest.mark.asyncio
async def test_2_2_split_escalates():
    worker_action = {"tool": "Bash", "args": {"command": "echo 'maybe'"}, "result": "maybe"}

    # Mock the internal _call_judge to force a 2-2 tie on the first 4 judges,
    # and an accept on the 5th judge
    call_count = 0

    async def mock_call_judge(axis, taskspec_json, worker_output, model):
        nonlocal call_count
        call_count += 1

        # first 4 calls: 2 accept, 2 reject
        if call_count <= 2:
            return JudgeResult(axis=axis, score=8, verdict="accept", reasoning="LGTM", model=model)
        elif call_count <= 4:
            return JudgeResult(axis=axis, score=2, verdict="reject", reasoning="Nope", model=model)
        else:
            # 5th judge
            return JudgeResult(
                axis=axis, score=9, verdict="accept", reasoning="5th judge LGTM", model=model
            )

    with patch("lib.evaluators.judge_panel._call_judge", side_effect=mock_call_judge):
        result = await judge_panel.evaluate(worker_action)

        assert call_count == 5
        assert result.escalated is True
        assert result.verdict == "accept"
        assert result.fifth_judge is not None
        assert result.fifth_judge.verdict == "accept"
