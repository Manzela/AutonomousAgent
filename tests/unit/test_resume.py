"""Unit tests for P1-3 resume scanner / rehydrator.

Spec: docs/superpowers/specs/2026-05-15-phase1-design-alignment.md §P1-3.
"""

from __future__ import annotations

import json
from pathlib import Path


from lib.durability import resume


def _write_chkpt(root: Path, session: str, step: int, taskspec_id: str = "spec-abc") -> Path:
    """Pre-create a checkpoint file outside the writer to isolate resume tests."""
    d = root / session
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"step-{step}.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": session,
                "step_index": step,
                "taskspec_id": taskspec_id,
                "timestamp": "2026-05-18T00:00:00Z",
                "tool_call_history": [{"tool": "Read", "step": step}],
            }
        )
    )
    return p


def test_rehydrate_returns_latest_checkpoint(tmp_path):
    """Pre-create 3 checkpoints; rehydrate picks the highest step_index."""
    session = "sess-A"
    for n in (3, 17, 42):
        _write_chkpt(tmp_path, session, n)

    state = resume.rehydrate_for_session(session, root_dir=tmp_path)
    assert state is not None
    assert state["step_index"] == 42
    assert state["session_id"] == session


def test_rehydrate_returns_none_for_no_checkpoints(tmp_path):
    """No checkpoint files for the session → None (clean start)."""
    assert resume.rehydrate_for_session("never-existed", root_dir=tmp_path) is None


def test_rehydrate_skips_done_sessions(tmp_path):
    """Session marked DONE (via a .done sentinel file) → None even if checkpoints exist."""
    session = "sess-done"
    _write_chkpt(tmp_path, session, 5)
    # Conventional DONE sentinel; see lib/durability/resume.py DONE_SENTINEL.
    (tmp_path / session / ".done").write_text("done at 2026-05-18T01:00:00Z\n")

    assert resume.rehydrate_for_session(session, root_dir=tmp_path) is None


def test_rehydrate_resume_disabled(tmp_path):
    """autoresume_enabled=False → None regardless of available checkpoints."""
    session = "sess-disabled"
    _write_chkpt(tmp_path, session, 7)

    out = resume.rehydrate_latest_for_session(ctx=None, root_dir=tmp_path, autoresume_enabled=False)
    assert out is None


def test_rehydrate_latest_for_session_picks_most_recent_incomplete(tmp_path):
    """Multiple incomplete sessions → resume the most recently checkpointed one."""
    # sess-old: last checkpoint at step 5, timestamp older
    _write_chkpt(tmp_path, "sess-old", 5)
    # sess-new: last checkpoint at step 9, timestamp newer (default writer uses mtime)
    p_new = _write_chkpt(tmp_path, "sess-new", 9)
    # Bump mtime of the newer one to ensure ordering
    import os
    import time

    os.utime(p_new, (time.time(), time.time()))

    state = resume.rehydrate_latest_for_session(ctx=None, root_dir=tmp_path)
    assert state is not None
    assert state["session_id"] == "sess-new"
    assert state["step_index"] == 9


def test_rehydrate_handles_corrupt_checkpoint_by_skipping(tmp_path):
    """Corrupted JSON in the latest file → fall back to the next-latest (skip_and_warn)."""
    session = "sess-corrupt"
    _write_chkpt(tmp_path, session, 1)
    _write_chkpt(tmp_path, session, 2)
    # Corrupt the highest-step file
    (tmp_path / session / "step-2.json").write_text("{ not valid json")

    state = resume.rehydrate_for_session(session, root_dir=tmp_path)
    assert state is not None
    assert state["step_index"] == 1  # fell back


def test_p1_3_resume_hook_returns_none_without_checkpoints(tmp_path, monkeypatch):
    """The plugin stub-replacement keeps the contract: returns None on a fresh box.

    Required to keep tests/unit/test_durability_plugin.py::test_stub_callbacks_return_none green.
    """
    from lib.durability import _p1_3_resume_session

    # Point the resume root at an empty tmp dir so no sessions are found.
    monkeypatch.setattr(resume, "DEFAULT_ROOT", tmp_path)
    out = _p1_3_resume_session(ctx=None)
    assert out is None
