"""Unit tests for P1-3 checkpoint writer.

Spec: docs/superpowers/specs/2026-05-15-phase1-design-alignment.md §P1-3.
Config: config/limits.yaml durability.checkpoint.*
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from lib.durability import checkpoint, trichotomy


@pytest.fixture
def session_id() -> str:
    return "test-session-c-001"


@pytest.fixture
def chkpt(tmp_path, session_id):
    """A Checkpoint instance writing into a per-test tmp dir (no shared state)."""
    return checkpoint.Checkpoint(
        session_id=session_id,
        taskspec_id="spec-abc",
        root_dir=tmp_path,
        interval_steps=5,
        retention_count=50,
        keep_every_nth=100,
    )


def _state(step: int) -> dict:
    return {"tool_call_history": [{"tool": "Read", "step": step}]}


def test_checkpoint_writes_every_n_steps(chkpt, tmp_path, session_id):
    """Interval=5 → step 1-4 write nothing; step 5 writes a file."""
    for s in range(1, 5):
        assert chkpt.maybe_write(step=s, state=_state(s)) is None

    out = chkpt.maybe_write(step=5, state=_state(5))
    assert out is not None
    assert out.exists()
    files = sorted((tmp_path / session_id).glob("step-*.json"))
    assert len(files) == 1
    assert files[0].name == "step-5.json"


def test_checkpoint_schema_includes_required_fields(chkpt, tmp_path, session_id):
    """Written file must carry session_id, step_index, taskspec_id, timestamp, schema_version."""
    path = chkpt.maybe_write(step=5, state=_state(5))
    payload = json.loads(path.read_text())
    for key in ("session_id", "step_index", "taskspec_id", "timestamp", "schema_version"):
        assert key in payload, f"missing required key: {key}"
    assert payload["schema_version"] == 1
    assert payload["session_id"] == session_id
    assert payload["step_index"] == 5
    assert payload["taskspec_id"] == "spec-abc"


def test_checkpoint_rolling_retention_caps_at_50_plus_every_100th(chkpt, tmp_path, session_id):
    """Write 250 checkpoints; retention keeps last 50 + every 100th sparse-tier.

    With interval=5 each maybe_write at step 5,10,15,... fires; we drive the
    underlying writer directly via .write() to keep the test independent of cadence.
    """
    for n in range(1, 251):
        chkpt.write(step=n, state=_state(n))

    files = sorted((tmp_path / session_id).glob("step-*.json"))
    indices = sorted(int(f.stem.split("-")[1]) for f in files)

    # Last 50: 201..250 inclusive
    assert set(range(201, 251)).issubset(indices)
    # Sparse retention: multiples of 100 (100, 200) survive
    assert 100 in indices
    assert 200 in indices
    # Nothing below 100 that isn't a multiple of 100 survives
    assert all(i >= 100 for i in indices)
    # Specifically: 1..99 are gone
    assert not any(i < 100 for i in indices)
    # Total = 50 recent (201..250) + sparse {100} = 51 (200 is already in 'recent' range only if 200>=201; it isn't, so it counts here)
    # Recent = 201..250 (50 files); sparse multiples-of-100 not in recent = {100, 200} = 2; total 52.
    assert len(files) == 52


def test_checkpoint_disk_full_classifies_to_F28(chkpt, tmp_path):
    """OSError 'No space left on device' on write → trichotomy.classify == F28."""
    real_open = open

    def fake_open(*args, **kwargs):
        # Only intercept writes to the checkpoint file; leave reads alone.
        if len(args) >= 2 and "w" in str(args[1]):
            raise OSError("No space left on device")
        return real_open(*args, **kwargs)

    with patch("lib.durability.checkpoint.open", side_effect=fake_open, create=True):
        with pytest.raises(OSError) as ei:
            chkpt.write(step=5, state=_state(5))

    assert trichotomy.classify(ei.value) == "F28"
