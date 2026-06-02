"""SP-01 graph_state schema + reducer oracles.

Includes the DoD-1 ledger key-choice red-green (naive (thread_id,node_id,super_step)
COLLIDES under simulated Send fan-out; content (thread_id,__pregel_task_id,action_kind)
stays distinct) and the SP-R1 serialize-time no-callable + scrub guards.
"""

from __future__ import annotations

import pytest

from app.core import graph_state as gs


def test_merge_by_task_id_is_idempotent_lww():
    a = [{"task_id": "t1", "status": "inflight"}]
    b = [{"task_id": "t1", "status": "completed"}, {"task_id": "t2", "status": "completed"}]
    out = gs._merge_by_task_id(a, b)
    by_id = {t["task_id"]: t for t in out}
    assert by_id["t1"]["status"] == "completed"  # last-write-wins per task_id
    assert set(by_id) == {"t1", "t2"}  # no duplicate t1


def test_ledger_union_dedups_by_three_tuple_key():
    r1 = {
        "thread_id": "T",
        "pregel_task_id": "p1",
        "action_kind": "ship",
        "node_label": "ship_effect",
        "super_step_label": 5,
        "ts": "z",
    }
    r1b = dict(r1, ts="later")  # same key, different payload
    out = gs._ledger_union([r1], [r1b])
    assert len(out) == 1  # one receipt per (thread,ptid,kind)
    assert out[0]["ts"] == "z"  # first write wins (write-before-effect)


def test_naive_key_collides_content_key_distinct():
    """DoD-1 red-green ON THE KEY CHOICE: two parallel Send branches share
    (node_id, super_step) but differ in __pregel_task_id. The naive key collapses
    them (FAIL — one entry for two tasks); the content key keeps them distinct."""
    branch_a = {
        "thread_id": "T",
        "pregel_task_id": "pA",
        "action_kind": "ship",
        "node_id": "execute",
        "super_step": 4,
    }
    branch_b = {
        "thread_id": "T",
        "pregel_task_id": "pB",
        "action_kind": "ship",
        "node_id": "execute",
        "super_step": 4,
    }

    naive = {gs._naive_key(branch_a), gs._naive_key(branch_b)}
    content = {gs.ledger_key(branch_a), gs.ledger_key(branch_b)}

    assert len(naive) == 1  # COLLISION: (T, execute, 4) identical -> oracle false-positives
    assert len(content) == 2  # DISTINCT: __pregel_task_id separates the two branches


def test_merge_counts_and_cost_are_additive():
    assert gs._merge_counts({"k": 1}, {"k": 1}) == {"k": 2}
    assert gs._merge_cost({"a": 0.5}, {"a": 0.25, "b": 1.0}) == {"a": 0.75, "b": 1.0}


def test_merge_counts_handles_none_initial():
    assert gs._merge_counts(None, {"k": 1}) == {"k": 1}
    assert gs._merge_cost(None, {"a": 1.0}) == {"a": 1.0}


def test_merge_steering_dedups_on_channel_origin_id():
    e1 = {
        "channel": "telegram",
        "origin_id": "m1",
        "verb": "APPROVE",
        "interrupt_id": "i",
        "ts": "1",
    }
    dup = dict(e1, ts="2")
    e2 = {"channel": "board", "origin_id": "c9", "verb": "REJECT", "interrupt_id": "i", "ts": "3"}
    out = gs._merge_steering([e1], [dup, e2])
    keys = {(e["channel"], e["origin_id"]) for e in out}
    assert keys == {("telegram", "m1"), ("board", "c9")}  # at-most-once per (channel,origin_id)
    assert sum(1 for e in out if (e["channel"], e["origin_id"]) == ("telegram", "m1")) == 1


def test_arbitrate_reject_beats_approve_for_one_interrupt():
    events = [
        {
            "channel": "telegram",
            "origin_id": "m1",
            "verb": "APPROVE",
            "interrupt_id": "i7",
            "ts": "1",
        },
        {"channel": "board", "origin_id": "c9", "verb": "REJECT", "interrupt_id": "i7", "ts": "2"},
    ]
    assert gs.arbitrate(events, "i7") == "REJECT"  # reject beats approve (deterministic C15)
    assert gs.arbitrate([events[0]], "i7") == "APPROVE"
    assert gs.arbitrate(events, "absent") is None


def test_assert_serializable_rejects_callables():
    with pytest.raises(TypeError):
        gs.assert_serializable_state({"goal": (lambda: 1)})  # top-level callable
    with pytest.raises(TypeError):
        gs.assert_serializable_state({"tasks": [{"invoke": (lambda: 1)}]})  # nested callable
    gs.assert_serializable_state({"goal": "ok", "tasks": [{"task_id": "t1"}]})  # clean -> no raise


def test_scrub_state_redacts_secrets_in_strings():
    secret = "sk-ABCDEF0123456789abcdef0123456789abcd"  # pragma: allowlist secret
    cleaned = gs.scrub_state({"goal": f"deploy with {secret} now"})
    assert secret not in cleaned["goal"]  # the scrubber redacted the OpenAI-style token


def test_key_str_roundtrips_ledger_key():
    key = gs.ledger_key({"thread_id": "T", "pregel_task_id": "p", "action_kind": "ship"})
    assert gs._key_str(key) == "T|p|ship"
