from unittest.mock import patch, MagicMock
from lib.durability import _p1_4_inject_rejected
from lib.evaluators.orchestrator_hook import drain_pending_feedback


def test_previously_rejected_surfaces_in_next_session(tmp_path):
    # Setup mock REJECTED.md
    from lib.memory.rejected import append_entry

    # We will override the DEFAULT_PATH to use our temp path
    test_path = tmp_path / "REJECTED.md"
    append_entry(
        approach_fingerprint="test1",
        approach_summary="bad approach",
        taskspec_id="test-123",
        intent_category="unknown",
        why_failed="terrible idea",
        alternatives="try good approach",
        path=test_path,
    )

    class MockSpec:
        intent_category = "unknown"
        intent = "do something bad"
        spec_id = "test-123"

    mock_store = MagicMock()

    # Run the injection for a new session
    session_id = "sess-123"

    with (
        patch("lib.anchors._get_spec_store", return_value=mock_store),
        patch("lib.anchors._most_recent_draft", return_value=MockSpec()),
        patch("lib.memory.rejected.DEFAULT_PATH", test_path),
    ):
        _p1_4_inject_rejected(session_id=session_id)

    feedbacks = drain_pending_feedback(session_id)
    assert len(feedbacks) == 1
    assert "bad approach" in feedbacks[0].reasoning
    assert "terrible idea" in feedbacks[0].reasoning
