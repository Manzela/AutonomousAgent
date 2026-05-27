from unittest.mock import patch, MagicMock
import lib.evaluators as eval_init
from lib.evaluators.orchestrator_hook import drain_pending_feedback


def test_judge_rejection_writes_row_and_durability_injects(tmp_path):
    # This integration test verifies that a judge rejection correctly writes a row
    # (via record_rejection_for_fingerprint) and then durability injects it on next session.

    session_id = "test-session-1"

    class MockConsensusResult:
        verdict = "reject"
        rationale = "because"
        judges = []

    class MockSpec:
        spec_id = "123"
        intent_category = "coding"

    mock_store = MagicMock()

    with (
        patch("lib.evaluators.judge_panel.evaluate", new_callable=MagicMock) as mock_eval,
        patch("lib.anchors._get_spec_store", return_value=mock_store),
        patch("lib.anchors._most_recent_draft", return_value=MockSpec()),
        patch("lib.evaluators.consensus._rejection_repeat_threshold", return_value=1),
        patch("lib.memory.rejected.DEFAULT_PATH", tmp_path / "REJECTED.md"),
    ):
        # Make the async evaluate mock return MockConsensusResult
        async def async_evaluate(*args, **kwargs):
            return MockConsensusResult()

        mock_eval.side_effect = async_evaluate

        # Call the post tool call hook
        eval_init._on_post_tool_call(
            tool_name="Bash",
            args={"command": "rm -rf"},
            result="denied",
            task_id="t1",
            session_id=session_id,
        )

        import time

        time.sleep(0.5)  # Wait for daemon thread

        # Verify that it wrote to REJECTED.md
        rejected_file = tmp_path / "REJECTED.md"
        assert rejected_file.exists()
        content = rejected_file.read_text()
        assert "Bash" in content
        assert "because" in content

        # Now simulate a new session start
        from lib.durability import _p1_4_inject_rejected

        new_session = "test-session-2"
        _p1_4_inject_rejected(session_id=new_session)

        feedbacks = drain_pending_feedback(new_session)
        assert len(feedbacks) == 1
        assert "because" in feedbacks[0].reasoning
