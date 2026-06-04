"""SP-R4 oracles — checkpoint + judge_events retention TTL contract.

Hermetic: no network/docker. Checkpoint tests use LangGraph's MemorySaver.

These oracles prove the SP-R4 retention contract:

  R1   gc_checkpoints() deletes all checkpoints for an expired thread
  R2   gc_checkpoints() returns accurate count (N threads deleted)
  R3   gc_checkpoints() is FAIL-OPEN: exception on one thread, others proceed
  R4   find_threads_older_than() identifies threads past the cutoff
  R5   find_threads_older_than() excludes threads newer than the cutoff
  R6   find_threads_older_than() excludes threads with no checkpoints
  R6b  find_threads_older_than() excludes threads with missing/unparseable ts (B3 fix)
  R7   judge_events rotate() deletes events older than cutoff_ts
  R8   judge_events rotate() keeps events newer than cutoff_ts
  R9   judge_events rotate() returns accurate (kept, deleted) counts
  R10  judge_events rotate() handles empty file (returns (0, 0))
  R11  judge_events rotate() is a no-op for missing file
  R12  scrub assertion: rotate() with scrub_fn removes SP-R1 credential patterns from
       retained NEW events (defense-in-depth pass proving the combined TTL+scrub contract)
  R13  judge_events rotate() is atomic — no .tmp file left on disk after success
  R14  real-format timestamp: Z-suffix with microseconds (B1 fix — real producer format)
  R15  microsecond/Z boundary: kept-after-cutoff event is NOT deleted (B2 fix)
  R16  multi-checkpoint thread: thread with old+new checkpoints straddling cutoff is NOT
       expired (C3 fix — newest checkpoint wins)
  R17  malformed JSONL line is kept, not silently dropped (C4 fix)

RED/GREEN design:
  R1:  if adelete_thread() were a no-op, alist() still returns entries — FAIL
  R3:  if gc_checkpoints() raised instead of continuing, count would be 0 — FAIL
  R4:  if cutoff comparison were inverted, expired thread is excluded — FAIL
  R7:  if rotate() kept events past cutoff, deleted count would be 0 — FAIL
  R12: if scrub_fn were not applied to retained events, credential survives — FAIL
  R15: if lexicographic comparison used, event 0.5s after cutoff would be deleted — FAIL
  R16: if oldest (not newest) checkpoint were used, the thread would wrongly be expired — FAIL
"""

from __future__ import annotations

import json
import re

import pytest
from langgraph.checkpoint.memory import MemorySaver
from unittest.mock import AsyncMock

from app.core.checkpoint_retention import find_threads_older_than, gc_checkpoints
from lib.evaluators.judge_events_retention import rotate

# ── constants ─────────────────────────────────────────────────────────────────

# B1 fix: use real producer timestamp formats
# judge_events.py:48 -> '2026-01-01T00:00:00.000001Z' (Z-suffix, microseconds)
# LangGraph checkpoint 'ts' -> '2026-01-01T00:00:00.000001+00:00' (+00:00, microseconds)
_TS_OLD_Z = "2026-01-01T00:00:00.000001Z"
_TS_NEW_Z = "2026-12-31T23:59:59.999999Z"
_TS_OLD_PLUS = "2026-01-01T00:00:00.000001+00:00"
_TS_NEW_PLUS = "2026-12-31T23:59:59.999999+00:00"
_CUTOFF_Z = "2026-06-01T00:00:00.000000Z"  # midpoint
_CUTOFF_PLUS = "2026-06-01T00:00:00.000000+00:00"

# B2 fix: a timestamp 0.5s AFTER a midnight cutoff
# Lexicographic: '...00.500000Z' < '...00Z' because '.' (46) < 'Z' (90) — wrong!
# instant-based: '...00.500000Z' > '...00Z' — correct (0.5s after midnight kept)
_TS_JUST_AFTER_CUTOFF = "2026-06-01T00:00:00.500000Z"

# SP-R1 credential patterns
_CRED_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
]


def _has_credential(text: str) -> bool:
    return any(p.search(text) for p in _CRED_PATTERNS)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_checkpoint(ts: str, goal: str = "test-goal") -> dict:
    return {
        "v": 1,
        "id": "1",
        "ts": ts,
        "channel_values": {"goal": goal},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }


_METADATA = {"source": "input", "step": 0, "writes": {}, "parents": {}}


async def _put(saver: MemorySaver, thread_id: str, ts: str, checkpoint_id: str = "1") -> None:
    config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": "",
            "checkpoint_id": checkpoint_id,
        }
    }
    await saver.aput(config, _make_checkpoint(ts), dict(_METADATA), {})


def _je_event(ts: str, summary: str) -> str:
    return json.dumps(
        {"timestamp_utc": ts, "schema_version": 1, "worker_action_summary": summary},
        separators=(",", ":"),
    )


# ── R1: gc_checkpoints deletes all checkpoints for an expired thread ──────────


@pytest.mark.asyncio
async def test_gc_checkpoints_deletes_thread():
    """R1: after gc_checkpoints([tid]), alist() for that thread returns nothing."""
    saver = MemorySaver()
    tid = "spr4-r1"
    await _put(saver, tid, _TS_OLD_PLUS)

    found = False
    async for _ in saver.alist({"configurable": {"thread_id": tid}}):
        found = True
        break
    assert found, "setup: checkpoint must exist before GC"

    count = await gc_checkpoints(saver, [tid])
    assert count == 1

    entries = []
    async for t in saver.alist({"configurable": {"thread_id": tid}}):
        entries.append(t)
    assert entries == [], "checkpoint must be deleted after gc_checkpoints"


# ── R2: gc_checkpoints returns accurate count ─────────────────────────────────


@pytest.mark.asyncio
async def test_gc_checkpoints_returns_count():
    """R2: gc_checkpoints returns the exact number of threads successfully deleted."""
    saver = MemorySaver()
    for i in range(3):
        await _put(saver, f"spr4-r2-{i}", _TS_OLD_PLUS)

    count = await gc_checkpoints(saver, ["spr4-r2-0", "spr4-r2-1", "spr4-r2-2"])
    assert count == 3


# ── R3: gc_checkpoints is FAIL-OPEN on per-thread exception ──────────────────


@pytest.mark.asyncio
async def test_gc_checkpoints_failopen():
    """R3: if adelete_thread raises on one thread, gc continues and deletes others."""
    good_tid = "spr4-r3-good"
    call_log: list[str] = []
    mock_saver = AsyncMock()

    async def _mock_delete(tid: str) -> None:
        call_log.append(tid)
        if tid == "spr4-r3-bad":
            raise RuntimeError("simulated delete failure")

    mock_saver.adelete_thread = _mock_delete

    # bad first, then good — proves continuation even when the first fails
    count = await gc_checkpoints(mock_saver, ["spr4-r3-bad", good_tid])
    assert count == 1, "only the successful thread should be counted"
    assert good_tid in call_log, "good thread must still be attempted after bad-thread exception"


# ── R4: find_threads_older_than identifies expired threads ────────────────────


@pytest.mark.asyncio
async def test_find_threads_older_than_identifies_expired():
    """R4: a thread whose newest checkpoint.ts < cutoff is returned as expired."""
    saver = MemorySaver()
    await _put(saver, "spr4-r4-old", _TS_OLD_PLUS)

    expired = await find_threads_older_than(
        saver, cutoff_ts=_CUTOFF_PLUS, candidate_thread_ids=["spr4-r4-old"]
    )
    assert expired == ["spr4-r4-old"]


# ── R5: find_threads_older_than excludes newer threads ───────────────────────


@pytest.mark.asyncio
async def test_find_threads_older_than_excludes_newer():
    """R5: a thread whose newest checkpoint.ts >= cutoff is NOT returned."""
    saver = MemorySaver()
    await _put(saver, "spr4-r5-new", _TS_NEW_PLUS)

    expired = await find_threads_older_than(
        saver, cutoff_ts=_CUTOFF_PLUS, candidate_thread_ids=["spr4-r5-new"]
    )
    assert expired == []


# ── R6: find_threads_older_than excludes threads with no checkpoints ──────────


@pytest.mark.asyncio
async def test_find_threads_older_than_excludes_empty():
    """R6: a thread ID with no checkpoints is NOT returned (nothing to delete)."""
    saver = MemorySaver()
    expired = await find_threads_older_than(
        saver, cutoff_ts=_CUTOFF_PLUS, candidate_thread_ids=["spr4-r6-nonexistent"]
    )
    assert expired == []


# ── R6b: B3 fix — missing/unparseable ts → thread excluded (not expired) ─────


@pytest.mark.asyncio
async def test_find_threads_older_than_excludes_missing_ts():
    """R6b (B3 fix): a checkpoint with missing/empty 'ts' is NOT classified expired.

    The safe posture for uncertain age is KEEP, not delete. If ts is absent or
    unparseable, exclude the thread from the expired set rather than deleting it.
    """
    saver = MemorySaver()
    # Manually write a checkpoint with no 'ts' field
    tid = "spr4-r6b-no-ts"
    config = {"configurable": {"thread_id": tid, "checkpoint_ns": "", "checkpoint_id": "1"}}
    checkpoint_no_ts = {
        "v": 1,
        "id": "1",
        # 'ts' field deliberately absent
        "channel_values": {"goal": "test"},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }
    await saver.aput(config, checkpoint_no_ts, dict(_METADATA), {})

    expired = await find_threads_older_than(
        saver, cutoff_ts=_CUTOFF_PLUS, candidate_thread_ids=[tid]
    )
    assert expired == [], "thread with missing 'ts' must NOT be classified as expired"


# ── R7: rotate deletes events older than cutoff ───────────────────────────────


def test_rotate_deletes_old_events(tmp_path):
    """R7: events with timestamp_utc < cutoff_ts are deleted."""
    path = tmp_path / "judge-events.jsonl"
    path.write_text(_je_event(_TS_OLD_Z, "old-summary") + "\n", encoding="utf-8")

    kept, deleted = rotate(path, cutoff_ts=_CUTOFF_Z)
    assert deleted == 1, "old event must be deleted"
    assert kept == 0
    assert path.read_text().strip() == ""


# ── R8: rotate keeps events newer than cutoff ────────────────────────────────


def test_rotate_keeps_new_events(tmp_path):
    """R8: events with timestamp_utc >= cutoff_ts are retained."""
    path = tmp_path / "judge-events.jsonl"
    path.write_text(_je_event(_TS_NEW_Z, "new-summary") + "\n", encoding="utf-8")

    kept, deleted = rotate(path, cutoff_ts=_CUTOFF_Z)
    assert kept == 1, "new event must be kept"
    assert deleted == 0
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1


# ── R9: rotate returns accurate (kept, deleted) counts ───────────────────────


def test_rotate_counts_accurately(tmp_path):
    """R9: rotate returns (kept, deleted) where kept+deleted = total events."""
    path = tmp_path / "judge-events.jsonl"
    lines = [
        _je_event(_TS_OLD_Z, "old-1"),
        _je_event(_TS_OLD_Z, "old-2"),
        _je_event(_TS_NEW_Z, "new-1"),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    kept, deleted = rotate(path, cutoff_ts=_CUTOFF_Z)
    assert kept == 1
    assert deleted == 2


# ── R10: rotate handles empty file ───────────────────────────────────────────


def test_rotate_empty_file(tmp_path):
    """R10: rotate on an empty file returns (0, 0) and leaves the file empty."""
    path = tmp_path / "judge-events.jsonl"
    path.write_text("", encoding="utf-8")

    kept, deleted = rotate(path, cutoff_ts=_CUTOFF_Z)
    assert (kept, deleted) == (0, 0)


# ── R11: rotate is a no-op for missing file ───────────────────────────────────


def test_rotate_missing_file(tmp_path):
    """R11: if the file does not exist, rotate returns (0, 0) without raising."""
    path = tmp_path / "nonexistent.jsonl"
    assert not path.exists()

    kept, deleted = rotate(path, cutoff_ts=_CUTOFF_Z)
    assert (kept, deleted) == (0, 0)


# ── R12: scrub assertion — rotate() with scrub_fn removes credentials ─────────


def test_rotate_scrub_fn_removes_credentials_from_retained_events(tmp_path):
    """R12 (C1 fix — scrub assertion): rotate() with scrub_fn=scrub_string removes
    SP-R1 credential patterns from NEW (retained) events, not just old deleted ones.

    Plants a credential in a NEW (kept-by-TTL) event summary, passes the SP-R1
    scrubber as scrub_fn, and asserts the credential is absent from the retained
    file. This proves the defense-in-depth scrub: even if SP-R1 write-time scrub
    was missed, the rotation pass catches it.

    RED: comment out the scrub_fn= kwarg → _has_credential(retained_text) is True → FAIL.
    """
    from lib.scrubber import scrub_string

    fake_key = "sk-fakekey1234567890abcdef"
    lines = [
        _je_event(_TS_NEW_Z, f"ran tool; output: {fake_key}"),  # NEW: kept by TTL, has cred
    ]
    path = tmp_path / "judge-events.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    kept, deleted = rotate(path, cutoff_ts=_CUTOFF_Z, scrub_fn=scrub_string)
    assert kept == 1
    assert deleted == 0

    retained_text = path.read_text(encoding="utf-8")
    assert not _has_credential(retained_text), (
        f"SP-R4 scrub assertion: scrub_fn must remove credential patterns from retained events; "
        f"found in: {retained_text!r}"
    )


# ── R13: rotate is atomic — no .tmp left behind ──────────────────────────────


def test_rotate_is_atomic(tmp_path):
    """R13: after a successful rotate, no .jsonl.tmp file is left on disk."""
    path = tmp_path / "judge-events.jsonl"
    path.write_text(_je_event(_TS_OLD_Z, "old") + "\n", encoding="utf-8")

    rotate(path, cutoff_ts=_CUTOFF_Z)

    tmp = path.with_suffix(".jsonl.tmp")
    assert not tmp.exists(), ".tmp file must be cleaned up after successful rotate"


# ── R14: real-format Z-suffix with microseconds (B1 fix) ─────────────────────


def test_rotate_real_producer_format_z_microseconds(tmp_path):
    """R14 (B1 fix): real judge_events producer format — 'Z' suffix + microseconds.

    judge_events.py:48 produces '2026-06-03T21:24:19.657164Z'.
    This test proves rotate() handles that exact format correctly.
    """
    # Real-producer format: Z suffix, microseconds, right at the cutoff boundary
    ts_just_before = "2026-05-31T23:59:59.999999Z"
    ts_at_cutoff = "2026-06-01T00:00:00.000000Z"  # equal to cutoff → kept (strict <)
    ts_just_after = "2026-06-01T00:00:00.000001Z"  # 1µs after cutoff → kept

    path = tmp_path / "judge-events.jsonl"
    lines = [
        _je_event(ts_just_before, "before-cutoff"),  # deleted (< cutoff)
        _je_event(ts_at_cutoff, "at-cutoff"),  # kept (== cutoff, strict < is False)
        _je_event(ts_just_after, "after-cutoff"),  # kept (> cutoff)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    kept, deleted = rotate(path, cutoff_ts=_CUTOFF_Z)
    assert kept == 2, "events at and after the cutoff must both be kept (strict < comparison)"
    assert deleted == 1

    retained = path.read_text(encoding="utf-8")
    assert "after-cutoff" in retained
    assert "at-cutoff" in retained


# ── R15: B2 fix — microsecond/Z boundary does NOT over-delete ────────────────


def test_rotate_microsecond_z_boundary_keeps_event(tmp_path):
    """R15 (B2 fix): an event 0.5s AFTER a whole-second cutoff is KEPT, not deleted.

    Lexicographic comparison: '2026-06-01T00:00:00.500000Z' < '2026-06-01T00:00:00Z'
    is True (ord('.') < ord('Z')) — WRONG, deletes a retained event.
    Instant-based comparison: the datetime is 0.5s after midnight — correctly KEPT.
    """
    path = tmp_path / "judge-events.jsonl"
    path.write_text(
        _je_event(_TS_JUST_AFTER_CUTOFF, "half-second-after-cutoff") + "\n",
        encoding="utf-8",
    )

    # Cutoff at exactly midnight (no microseconds) — the classic B2 trigger
    cutoff = "2026-06-01T00:00:00Z"
    kept, deleted = rotate(path, cutoff_ts=cutoff)
    assert kept == 1, (
        f"event at {_TS_JUST_AFTER_CUTOFF} is 0.5s AFTER cutoff {cutoff} — must be kept "
        f"(lexicographic compare would delete it: B2 regression)"
    )
    assert deleted == 0


# ── R16: C3 fix — multi-checkpoint thread: newest checkpoint wins ─────────────


@pytest.mark.asyncio
async def test_find_threads_older_than_uses_newest_checkpoint():
    """R16 (C3 fix): a thread with both an old and a new checkpoint straddles the
    cutoff and must NOT be returned as expired (the newest checkpoint is NEW).

    RED: if find_threads_older_than() used the oldest checkpoint instead of the
    newest, this thread would be returned and wrongly GC'd — FAIL.
    """
    saver = MemorySaver()
    tid = "spr4-r16-straddle"
    # Write old checkpoint first, then a newer one
    await _put(saver, tid, _TS_OLD_PLUS, checkpoint_id="1")
    await _put(saver, tid, _TS_NEW_PLUS, checkpoint_id="2")

    expired = await find_threads_older_than(
        saver, cutoff_ts=_CUTOFF_PLUS, candidate_thread_ids=[tid]
    )
    assert (
        expired == []
    ), "thread with a NEW checkpoint must not be expired even if it also has an old one"


# ── R17: C4 fix — malformed JSONL line is kept, not dropped ──────────────────


def test_rotate_malformed_line_is_kept(tmp_path):
    """R17 (C4 fix): a malformed JSON line is logged at WARNING and KEPT.

    This is the fail-open posture for data integrity: prefer retaining an
    ambiguous record over silently dropping it.
    """
    path = tmp_path / "judge-events.jsonl"
    lines = [
        "this is not json {{{",  # malformed — must be kept
        _je_event(_TS_OLD_Z, "old-valid"),  # valid old event — must be deleted
        _je_event(_TS_NEW_Z, "new-valid"),  # valid new event — must be kept
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    kept, deleted = rotate(path, cutoff_ts=_CUTOFF_Z)
    assert kept == 2, "both the malformed line and the new event must be kept"
    assert deleted == 1, "only the valid old event must be deleted"

    retained = path.read_text(encoding="utf-8")
    assert "this is not json" in retained, "malformed line must survive rotation"
