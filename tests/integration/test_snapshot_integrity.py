"""Snapshot integrity: round-trip write → simulated-crash → rehydrate.

Closes risks R1 ("snapshot loop survives container restart") and R2
("write/read symmetry on checkpoint format") in
``audit/2026-05-19-resume-orchestration/audit-plan.md`` (P0-6).

These tests exercise the cross-module contract between
:mod:`lib.durability.checkpoint` (writer) and :mod:`lib.durability.resume`
(reader) without needing any of the live docker-compose stack — they
operate purely on ``tmp_path`` so the test stays in CI's
``pytest tests/integration/`` matrix and runs on every PR.

Coverage:
* Write → read round-trip preserves all schema fields.
* Atomic-write contract: readers never see a half-written ``step-N.json``.
* Stale ``.tmp`` files left by a crashed writer don't break rehydrate.
* Truncated/corrupted JSON in the most-recent step is skipped; the next
  parseable file is returned (skip_and_warn semantics).
* Retention pruning keeps the recent + sparse tiers as documented.
* SCHEMA_VERSION emitted by the writer matches what readers expect.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.durability.checkpoint import SCHEMA_VERSION, Checkpoint
from lib.durability.resume import rehydrate_for_session


def _make_checkpoint(tmp_path: Path, **kwargs) -> Checkpoint:
    defaults = dict(
        session_id="sess-test",
        taskspec_id="ts-test",
        root_dir=tmp_path,
        interval_steps=1,
        retention_count=50,
        keep_every_nth=100,
    )
    defaults.update(kwargs)
    return Checkpoint(**defaults)


# ----------------------------------------------------------------------
# R1/R2: write → rehydrate round-trip
# ----------------------------------------------------------------------


def test_write_then_rehydrate_returns_latest_state(tmp_path):
    cp = _make_checkpoint(tmp_path)
    state = {
        "step": 1,
        "tool_call_history": [{"tool": "terminal", "args": {"cmd": "ls"}}],
        "scratch": "intermediate work",
    }
    cp.write(step=1, state=state)

    rehydrated = rehydrate_for_session("sess-test", root_dir=tmp_path)
    assert rehydrated is not None
    assert rehydrated["session_id"] == "sess-test"
    assert rehydrated["taskspec_id"] == "ts-test"
    assert rehydrated["step_index"] == 1
    assert rehydrated["schema_version"] == SCHEMA_VERSION
    assert rehydrated["tool_call_history"] == state["tool_call_history"]
    assert rehydrated["scratch"] == "intermediate work"


def test_rehydrate_picks_highest_step(tmp_path):
    cp = _make_checkpoint(tmp_path)
    for step in (1, 5, 12, 27):
        cp.write(step=step, state={"step": step})

    rehydrated = rehydrate_for_session("sess-test", root_dir=tmp_path)
    assert rehydrated["step_index"] == 27


def test_rehydrate_returns_none_for_unknown_session(tmp_path):
    assert rehydrate_for_session("never-existed", root_dir=tmp_path) is None


def test_rehydrate_returns_none_for_empty_session_dir(tmp_path):
    (tmp_path / "sess-empty").mkdir()
    assert rehydrate_for_session("sess-empty", root_dir=tmp_path) is None


def test_rehydrate_respects_done_sentinel(tmp_path):
    cp = _make_checkpoint(tmp_path)
    cp.write(step=3, state={"step": 3})
    (cp.session_dir / ".done").touch()
    assert rehydrate_for_session("sess-test", root_dir=tmp_path) is None


# ----------------------------------------------------------------------
# Atomic-write contract: no half-written files visible to readers
# ----------------------------------------------------------------------


def test_no_dot_tmp_left_after_successful_write(tmp_path):
    cp = _make_checkpoint(tmp_path)
    cp.write(step=4, state={"step": 4})
    leftover_tmp = list(cp.session_dir.glob("*.tmp"))
    assert leftover_tmp == [], f"unexpected leftover tmp files: {leftover_tmp}"


def test_stale_dot_tmp_does_not_break_rehydrate(tmp_path):
    """Simulate a crash mid-write: a .tmp file exists but the final
    step-N.json does not. The reader must ignore the .tmp file."""
    cp = _make_checkpoint(tmp_path)
    cp.write(step=10, state={"step": 10, "marker": "real-write"})
    # Simulate a crashed writer leaving a partial tmp behind for step 11.
    cp.session_dir.joinpath("step-11.json.tmp").write_text("partial")

    rehydrated = rehydrate_for_session("sess-test", root_dir=tmp_path)
    assert rehydrated is not None
    assert rehydrated["step_index"] == 10
    assert rehydrated["marker"] == "real-write"


def test_rehydrate_skips_corrupted_latest_step(tmp_path):
    """If the highest-numbered step-N.json is corrupted, the reader must
    walk backward and return the next parseable checkpoint."""
    cp = _make_checkpoint(tmp_path)
    cp.write(step=1, state={"step": 1, "marker": "good-1"})
    cp.write(step=2, state={"step": 2, "marker": "good-2"})

    # Truncate step-2.json mid-record so json.loads fails.
    cp.step_path(2).write_text('{"schema_version": 1, "session_id": "sess-test", "step_in')

    rehydrated = rehydrate_for_session("sess-test", root_dir=tmp_path)
    assert rehydrated is not None
    assert rehydrated["step_index"] == 1
    assert rehydrated["marker"] == "good-1"


def test_rehydrate_returns_none_when_all_steps_corrupted(tmp_path):
    cp = _make_checkpoint(tmp_path)
    cp.write(step=1, state={"step": 1})
    cp.write(step=2, state={"step": 2})
    cp.step_path(1).write_text("not json")
    cp.step_path(2).write_text("also not json")
    assert rehydrate_for_session("sess-test", root_dir=tmp_path) is None


# ----------------------------------------------------------------------
# Retention semantics: recent tier + sparse tier
# ----------------------------------------------------------------------


def test_retention_keeps_recent_window_plus_sparse_tier(tmp_path):
    """With retention_count=5 and keep_every_nth=10, after 25 writes the
    on-disk set must be {16..25} ∪ {10, 20} = 12 files.

    Verifies the docstring example in
    ``Checkpoint._apply_retention`` (extrapolated to smaller numbers so
    the test is fast)."""
    cp = _make_checkpoint(
        tmp_path,
        retention_count=5,
        keep_every_nth=10,
    )
    for step in range(1, 26):
        cp.write(step=step, state={"step": step})

    kept = sorted(int(p.stem.split("-")[1]) for p in cp.session_dir.glob("step-*.json"))
    expected = sorted({10, 20} | set(range(21, 26)))
    assert kept == expected


def test_retention_does_not_drop_only_checkpoint(tmp_path):
    cp = _make_checkpoint(tmp_path, retention_count=1, keep_every_nth=1)
    cp.write(step=1, state={"step": 1})
    files = list(cp.session_dir.glob("step-*.json"))
    assert len(files) == 1


# ----------------------------------------------------------------------
# Schema version contract
# ----------------------------------------------------------------------


def test_writer_emits_documented_schema_version(tmp_path):
    cp = _make_checkpoint(tmp_path)
    cp.write(step=1, state={"step": 1})
    payload = json.loads(cp.step_path(1).read_text())
    assert payload["schema_version"] == SCHEMA_VERSION
    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 1


def test_payload_has_all_required_fields(tmp_path):
    cp = _make_checkpoint(tmp_path)
    cp.write(step=7, state={"step": 7})
    payload = json.loads(cp.step_path(7).read_text())
    required = {
        "schema_version",
        "session_id",
        "step_index",
        "taskspec_id",
        "timestamp",
        "tool_call_history",
    }
    assert required.issubset(payload.keys())
    assert payload["timestamp"].endswith("Z")
