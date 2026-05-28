import pytest
from unittest.mock import patch
from lib.evaluators import judge_panel
from lib.evaluators.judge import JudgeResult


@pytest.mark.asyncio
async def test_bad_tool_call_is_rejected():
    worker_action = {"tool": "Bash", "args": {"command": "rm -rf /"}, "result": "Permission denied"}

    # Mock to simulate the real LiteLLM response for the integration test locally
    async def mock_call_judge(axis, taskspec_json, worker_output, model):
        return JudgeResult(
            axis=axis, score=2, verdict="reject", reasoning="Destructive command", model=model
        )

    with patch("lib.evaluators.judge_panel._call_judge", side_effect=mock_call_judge):
        result = await judge_panel.evaluate(worker_action)

        assert result.verdict == "reject"
        assert len([j for j in result.judges if j.verdict == "reject"]) >= 1


@pytest.mark.asyncio
async def test_good_tool_call_is_accepted():
    worker_action = {
        "tool": "Edit",
        "args": {"file": "test.py", "content": "print('hello')"},
        "result": "Success",
    }

    async def mock_call_judge(axis, taskspec_json, worker_output, model):
        return JudgeResult(axis=axis, score=8, verdict="accept", reasoning="LGTM", model=model)

    with patch("lib.evaluators.judge_panel._call_judge", side_effect=mock_call_judge):
        result = await judge_panel.evaluate(worker_action)
        assert result.verdict in ("accept", "needs_5th_judge")
