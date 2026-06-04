from __future__ import annotations


from lib.durability import _p1_3_checkpoint_on_tool_call


def test_no_step_n_json_written_by_hook(tmp_path, monkeypatch):
    """Integration test asserting that _p1_3_checkpoint_on_tool_call writes NO step-N.json files (Issue #232)."""
    # Override checkpoint root to point to tmp_path
    monkeypatch.setattr("lib.durability._CHECKPOINT_ROOT", tmp_path)

    # Trigger tool calls
    for i in range(1, 20):
        _p1_3_checkpoint_on_tool_call(
            session_id="test-session-single-store",
            tool_name=f"tool-{i}",
            args={"cmd": f"echo {i}"},
            result="success",
            task_id="task-1",
            tool_call_id=f"call-{i}",
            duration_ms=5.0,
        )

    # Check that no step-N.json files exist in the checkpoint directory
    checkpoint_dir = tmp_path / "test-session-single-store"
    assert not checkpoint_dir.exists() or not list(
        checkpoint_dir.glob("step-*.json")
    ), "Split-brain hazard: step-N.json files were written by the legacy write-hook."
