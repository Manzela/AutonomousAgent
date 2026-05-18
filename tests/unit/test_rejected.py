"""Unit tests for P1-4 REJECTED.md institutional-memory subsystem.

Storage layout: each entry is a markdown block with YAML frontmatter.
Dedup key: ``approach_fingerprint`` (locked formula at design-alignment
spec L337-339). Same-fingerprint appends bump an ``occurrence_count``
counter instead of creating a duplicate row.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from lib.memory import rejected


def _fp(tools: list[tuple[str, str]]) -> str:
    """Compute a fingerprint the same way production does, for fixture parity."""
    import hashlib

    canonical = json.dumps([{"tool": t, "first_arg": a[:80]} for t, a in tools], sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.fixture
def memory_path(tmp_path, monkeypatch):
    """Redirect rejected.MEMORY_PATH to a tmp file so tests don't write to /data."""
    target = tmp_path / "REJECTED.md"
    monkeypatch.setattr(rejected, "DEFAULT_PATH", target)
    return target


def _append(memory_path, **kw):
    """Helper: append an entry with sensible defaults."""
    rejected.append_entry(
        approach_fingerprint=kw.get("approach_fingerprint", _fp([("Read", "/etc/passwd")])),
        approach_summary=kw.get("approach_summary", "Tried to read /etc/passwd directly"),
        taskspec_id=kw.get("taskspec_id", "spec-1"),
        intent_category=kw.get("intent_category", "coding"),
        why_failed=kw.get("why_failed", "consensus reject: out of scope"),
        alternatives=kw.get("alternatives", "Use the user-config loader"),
        path=memory_path,
    )


# ---------------------------------------------------------------------------
# append_entry
# ---------------------------------------------------------------------------
def test_append_entry_creates_file_if_absent(memory_path):
    assert not memory_path.exists()
    _append(memory_path)
    assert memory_path.exists()
    body = memory_path.read_text()
    assert "approach_fingerprint:" in body
    assert "Tried to read /etc/passwd directly" in body


def test_append_entry_appends_to_existing(memory_path):
    _append(memory_path, approach_fingerprint=_fp([("Read", "a")]), approach_summary="first")
    _append(memory_path, approach_fingerprint=_fp([("Read", "b")]), approach_summary="second")
    body = memory_path.read_text()
    assert body.count("approach_fingerprint:") == 2
    assert "first" in body and "second" in body


# ---------------------------------------------------------------------------
# load_active_entries
# ---------------------------------------------------------------------------
def test_load_active_entries_filters_by_intent_category(memory_path):
    _append(memory_path, approach_fingerprint=_fp([("Read", "a")]), intent_category="coding")
    _append(memory_path, approach_fingerprint=_fp([("Read", "b")]), intent_category="audit")
    out = rejected.load_active_entries(intent_category="audit", path=memory_path)
    assert len(out) == 1
    assert out[0]["intent_category"] == "audit"


def test_load_active_entries_respects_max_cap(memory_path):
    for i in range(5):
        _append(memory_path, approach_fingerprint=_fp([("Read", f"file-{i}")]))
    out = rejected.load_active_entries(intent_category="coding", path=memory_path, max_entries=3)
    assert len(out) == 3


def test_load_active_entries_skips_expired(memory_path):
    # Write one expired entry directly to disk
    now = datetime.now(timezone.utc)
    expired = now - timedelta(days=60)
    expires_at = (expired + timedelta(days=30)).isoformat()
    block = (
        "---\n"
        "id: rej-expired\n"
        f"approach_fingerprint: {_fp([('Read', 'old')])}\n"
        "approach_summary: stale\n"
        "taskspec_id: spec-old\n"
        "intent_category: coding\n"
        "why_failed: ancient\n"
        "alternatives: try again later\n"
        "occurrence_count: 1\n"
        f"created_at: {expired.isoformat()}\n"
        f"expires_at: {expires_at}\n"
        "---\n\n"
    )
    memory_path.write_text(block)
    _append(memory_path, approach_fingerprint=_fp([("Read", "fresh")]))

    out = rejected.load_active_entries(intent_category="coding", path=memory_path)
    summaries = [e["approach_summary"] for e in out]
    assert "stale" not in summaries
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Fingerprint dedup
# ---------------------------------------------------------------------------
def test_approach_fingerprint_dedup(memory_path):
    fp = _fp([("Read", "/etc/passwd")])
    _append(memory_path, approach_fingerprint=fp, approach_summary="first try")
    _append(memory_path, approach_fingerprint=fp, approach_summary="second try (same fp)")
    body = memory_path.read_text()
    # Exactly one entry block, with occurrence_count bumped
    assert body.count("approach_fingerprint:") == 1
    assert "occurrence_count: 2" in body


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------
def test_forget_by_pattern(memory_path):
    _append(memory_path, approach_fingerprint=_fp([("Read", "a")]), approach_summary="alpha trial")
    _append(memory_path, approach_fingerprint=_fp([("Read", "b")]), approach_summary="beta trial")
    _append(memory_path, approach_fingerprint=_fp([("Read", "c")]), approach_summary="gamma run")

    removed = rejected.forget("trial", path=memory_path)
    assert removed == 2
    body = memory_path.read_text()
    assert "alpha trial" not in body
    assert "beta trial" not in body
    assert "gamma run" in body


def test_forget_by_id(memory_path):
    _append(memory_path, approach_fingerprint=_fp([("Read", "a")]), approach_summary="one")
    _append(memory_path, approach_fingerprint=_fp([("Read", "b")]), approach_summary="two")
    entries = rejected.load_active_entries(intent_category="coding", path=memory_path)
    target_id = entries[0]["id"]

    removed = rejected.forget(f"id:{target_id}", path=memory_path)
    assert removed == 1
    after = rejected.load_active_entries(intent_category="coding", path=memory_path)
    assert len(after) == 1
    assert after[0]["id"] != target_id


def test_forget_no_match_returns_zero(memory_path):
    _append(memory_path)
    assert rejected.forget("nonexistent-pattern", path=memory_path) == 0


# ---------------------------------------------------------------------------
# list_active
# ---------------------------------------------------------------------------
def test_list_active_returns_short_summaries(memory_path):
    _append(memory_path, approach_fingerprint=_fp([("Read", "a")]), approach_summary="first")
    _append(memory_path, approach_fingerprint=_fp([("Read", "b")]), approach_summary="second")
    lines = rejected.list_active(path=memory_path)
    assert len(lines) == 2
    joined = " ".join(lines)
    assert "first" in joined and "second" in joined


# ---------------------------------------------------------------------------
# compute_fingerprint helper (locked formula at design-alignment L337-339)
# ---------------------------------------------------------------------------
def test_compute_fingerprint_truncates_first_arg_to_80_chars():
    long_arg = "x" * 200
    short_arg = "x" * 80
    fp_long = rejected.compute_fingerprint([{"tool": "Read", "first_arg": long_arg}])
    fp_short = rejected.compute_fingerprint([{"tool": "Read", "first_arg": short_arg}])
    assert fp_long == fp_short


def test_compute_fingerprint_order_independent_via_sort():
    a = rejected.compute_fingerprint(
        [{"tool": "Read", "first_arg": "x"}, {"tool": "Edit", "first_arg": "y"}]
    )
    # Same data — sorted_keys means dict reordering doesn't matter; tool-call
    # ordering DOES matter (a sequence vs reverse-sequence are distinct approaches).
    b = rejected.compute_fingerprint(
        [{"first_arg": "x", "tool": "Read"}, {"first_arg": "y", "tool": "Edit"}]
    )
    assert a == b


# ---------------------------------------------------------------------------
# 3-strike tracker (consensus.record_rejection_for_fingerprint)
# ---------------------------------------------------------------------------
def test_three_strikes_appends_to_rejected_md(memory_path, monkeypatch):
    """Per design-alignment spec L333: 3 consecutive rejects for the same
    fingerprint must trigger append_entry. The first two must not."""
    from lib.evaluators import consensus

    # Isolate per-test state from any other test that touched the streak dicts.
    consensus.reset_session_strikes("sess-A")
    # Point append_entry at the tmp path via a wrapper on rejected.append_entry
    real_append = rejected.append_entry

    def tmp_append(**kw):
        return real_append(path=memory_path, **kw)

    monkeypatch.setattr(rejected, "append_entry", tmp_append)

    fp = _fp([("Read", "/etc/passwd")])
    for _ in range(2):
        consensus.record_rejection_for_fingerprint(
            session_id="sess-A",
            approach_fingerprint=fp,
            approach_summary="read system file directly",
            taskspec_id="spec-X",
            intent_category="coding",
            why_failed="out of scope",
            alternatives="ask the user for a path",
            threshold=3,
        )
    # No entry yet (streak < threshold)
    assert not memory_path.exists() or "approach_fingerprint:" not in memory_path.read_text()

    # Third reject fires the threshold.
    consensus.record_rejection_for_fingerprint(
        session_id="sess-A",
        approach_fingerprint=fp,
        approach_summary="read system file directly",
        taskspec_id="spec-X",
        intent_category="coding",
        why_failed="out of scope",
        alternatives="ask the user for a path",
        threshold=3,
    )
    body = memory_path.read_text()
    assert "approach_fingerprint:" in body
    assert "read system file directly" in body

    consensus.reset_session_strikes("sess-A")


def test_three_strikes_resets_on_new_fingerprint(memory_path, monkeypatch):
    from lib.evaluators import consensus

    consensus.reset_session_strikes("sess-B")
    real_append = rejected.append_entry

    def tmp_append(**kw):
        return real_append(path=memory_path, **kw)

    monkeypatch.setattr(rejected, "append_entry", tmp_append)

    fp1 = _fp([("Read", "a")])
    fp2 = _fp([("Read", "b")])
    # Two rejects on fp1, then a reject on fp2 — streak should reset, not fire.
    consensus.record_rejection_for_fingerprint(
        session_id="sess-B",
        approach_fingerprint=fp1,
        approach_summary="alpha",
        taskspec_id="spec-Y",
        intent_category="coding",
        why_failed="why",
        alternatives="alt",
        threshold=3,
    )
    consensus.record_rejection_for_fingerprint(
        session_id="sess-B",
        approach_fingerprint=fp1,
        approach_summary="alpha",
        taskspec_id="spec-Y",
        intent_category="coding",
        why_failed="why",
        alternatives="alt",
        threshold=3,
    )
    consensus.record_rejection_for_fingerprint(
        session_id="sess-B",
        approach_fingerprint=fp2,
        approach_summary="beta",
        taskspec_id="spec-Y",
        intent_category="coding",
        why_failed="why",
        alternatives="alt",
        threshold=3,
    )
    # fp2 only has 1 strike — no entry should exist yet.
    assert not memory_path.exists() or "approach_fingerprint:" not in memory_path.read_text()

    consensus.reset_session_strikes("sess-B")
