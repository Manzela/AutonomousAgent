"""SP-17 — SteeringEventBus hermetic unit tests.

Coverage map (PRD §6 SP-17 acceptance criteria):
  ① same event twice (same channel/origin_id) → ledger_count==1 (dedup).
  ② new bus instance over the same db_path → still deduplicated (persisted ledger).
  ③ origin="agent" event → dropped at ingest (put returns False, ledger untouched).
  ④ /reject + board "approve" on one interrupt_id → route_to_interrupt resolves to
     REJECT (reject beats approve — C15 precedence rule).
  ⑤ kind=abort / verb=TIMEOUT → route_to_interrupt resolves to a halting verb.
  ⑥ no events for an interrupt_id → route_to_interrupt defaults to APPROVE (pass-through).
  ⑦ events from multiple threads are returned scoped to each thread_id.
  ⑧ SteeringEventBus with no db_path (in-memory) — full happy-path smoke.

DEFERRED (not tested here):
  * per-super-step ON-the-loop delivery (criterion 2 — steer mid-execute);
  * cross-thread "blocked on you" legibility (criterion 6 / U-3);
  * the Telegram/board channel adapters (SP-13).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.graph_state import SteeringEvent
from app.core.steering import SteeringEventBus


# ── helpers ───────────────────────────────────────────────────────────────


def _ev(
    *,
    thread_id: str = "t1",
    channel: str = "telegram",
    origin_id: str = "m1",
    kind: str = "approve",
    verb: str = "APPROVE",
    interrupt_id: str | None = "iid1",
    payload: dict | None = None,
    ts: str = "2026-06-04T00:00:00+00:00",
) -> SteeringEvent:
    return cast(
        SteeringEvent,
        {
            "thread_id": thread_id,
            "channel": channel,
            "origin_id": origin_id,
            "kind": kind,
            "verb": verb,
            "interrupt_id": interrupt_id,
            "payload": payload,
            "ts": ts,
        },
    )


def _mock_runner(resume_side_effect: Any = None) -> Any:
    runner = AsyncMock()
    if resume_side_effect is not None:
        runner.resume.side_effect = resume_side_effect
    else:
        runner.resume.return_value = {}
    return runner


# ── ① dedup: same event twice ─────────────────────────────────────────────


def test_sp17_dedup_same_event_twice():
    """Criterion ①: second put with same (channel, origin_id) is deduplicated."""
    bus = SteeringEventBus()
    event = _ev()
    first = bus.put(event)
    second = bus.put(event)
    assert first is True
    assert second is False
    assert bus.ledger_count("telegram", "m1") == 1


def test_sp17_dedup_different_channel_accepted():
    """Different (channel, origin_id) combinations are stored independently."""
    bus = SteeringEventBus()
    assert bus.put(_ev(channel="telegram", origin_id="m1")) is True
    assert bus.put(_ev(channel="board", origin_id="m1")) is True  # different channel
    assert bus.put(_ev(channel="telegram", origin_id="m2")) is True  # different origin_id
    assert bus.ledger_count("telegram", "m1") == 1
    assert bus.ledger_count("board", "m1") == 1


# ── ② persisted ledger across bus restart ─────────────────────────────────


def test_sp17_persisted_ledger_survives_restart(tmp_path: Path):
    """Criterion ②: a new SteeringEventBus instance over the same db_path still deduplicates."""
    db = tmp_path / "steering.db"

    bus1 = SteeringEventBus(db_path=db)
    bus1.put(_ev())
    bus1.close()

    bus2 = SteeringEventBus(db_path=db)
    duplicate = bus2.put(_ev())
    bus2.close()

    assert duplicate is False, "event must be deduped even after bus restart (persisted ledger)"


# ── ③ agent-authored events are dropped ───────────────────────────────────


def test_sp17_agent_origin_dropped():
    """Criterion ③: origin='agent' events are dropped at ingest; ledger stays empty."""
    bus = SteeringEventBus()
    result = bus.put(_ev(channel="board", origin_id="agent-comment-1"), origin="agent")
    assert result is False
    assert bus.ledger_count("board", "agent-comment-1") == 0


def test_sp17_human_origin_accepted():
    """Explicit origin=None (or absent) → accepted normally."""
    bus = SteeringEventBus()
    assert bus.put(_ev(), origin=None) is True
    assert bus.put(_ev(origin_id="m2")) is True


# ── ④ reject beats approve (C15 arbitration) ──────────────────────────────


@pytest.mark.asyncio
async def test_sp17_reject_beats_approve():
    """Criterion ④: board REJECT + telegram APPROVE on the same interrupt_id → REJECT wins."""
    bus = SteeringEventBus()
    runner = _mock_runner()

    bus.put(_ev(channel="telegram", origin_id="tg-1", verb="APPROVE", kind="approve"))
    bus.put(_ev(channel="board", origin_id="board-1", verb="REJECT", kind="reject"))

    verb = await bus.route_to_interrupt(runner, thread_id="t1", interrupt_id="iid1")
    assert verb == "REJECT"
    runner.resume.assert_called_once()
    call_kwargs = runner.resume.call_args.kwargs
    assert call_kwargs["decision"]["verb"] == "REJECT"
    assert call_kwargs["interrupt_id"] == "iid1"
    assert call_kwargs["thread_id"] == "t1"


@pytest.mark.asyncio
async def test_sp17_dedup_then_reject_wins():
    """Duplicate telegram APPROVE (deduped) + board REJECT → REJECT wins."""
    bus = SteeringEventBus()
    runner = _mock_runner()
    iid = "iid-x"

    bus.put(
        _ev(channel="telegram", origin_id="tg-1", verb="APPROVE", kind="approve", interrupt_id=iid)
    )
    bus.put(
        _ev(channel="telegram", origin_id="tg-1", verb="APPROVE", kind="approve", interrupt_id=iid)
    )  # dup
    bus.put(
        _ev(channel="board", origin_id="board-1", verb="REJECT", kind="reject", interrupt_id=iid)
    )

    assert bus.ledger_count("telegram", "tg-1") == 1  # deduped
    verb = await bus.route_to_interrupt(runner, thread_id="t1", interrupt_id=iid)
    assert verb == "REJECT"


# ── ⑤ abort / timeout routes as halting verb ──────────────────────────────


@pytest.mark.asyncio
async def test_sp17_abort_routes_as_timeout():
    """Criterion ⑤: an abort event (verb=TIMEOUT) halts via route_to_interrupt."""
    bus = SteeringEventBus()
    runner = _mock_runner()
    iid = "iid-abort"

    bus.put(
        _ev(
            channel="telegram", origin_id="tg-abort", verb="TIMEOUT", kind="abort", interrupt_id=iid
        )
    )

    verb = await bus.route_to_interrupt(runner, thread_id="t1", interrupt_id=iid)
    assert verb in {"TIMEOUT", "REJECT"}  # both are halting verbs


# ── ⑥ no events → APPROVE pass-through ───────────────────────────────────


@pytest.mark.asyncio
async def test_sp17_no_events_defaults_to_approve():
    """Criterion ⑥: route_to_interrupt with no events uses APPROVE (fail-safe pass-through)."""
    bus = SteeringEventBus()
    runner = _mock_runner()

    verb = await bus.route_to_interrupt(runner, thread_id="t1", interrupt_id="iid-empty")
    assert verb == "APPROVE"
    runner.resume.assert_called_once()
    assert runner.resume.call_args.kwargs["decision"]["verb"] == "APPROVE"


# ── ⑦ thread-scoped queries ───────────────────────────────────────────────


def test_sp17_get_for_thread_scoped():
    """Criterion ⑦: get_for_thread returns only events for that thread_id."""
    bus = SteeringEventBus()
    bus.put(_ev(thread_id="t1", channel="telegram", origin_id="m1"))
    bus.put(_ev(thread_id="t2", channel="telegram", origin_id="m2"))
    bus.put(_ev(thread_id="t1", channel="board", origin_id="m3"))

    t1_events = bus.get_for_thread("t1")
    t2_events = bus.get_for_thread("t2")

    assert len(t1_events) == 2
    assert all(e["thread_id"] == "t1" for e in t1_events)
    assert len(t2_events) == 1
    assert t2_events[0]["thread_id"] == "t2"


def test_sp17_get_for_interrupt_scoped():
    """get_for_interrupt returns events for that interrupt_id only."""
    bus = SteeringEventBus()
    iid_a = "iid-a"
    iid_b = "iid-b"
    bus.put(_ev(origin_id="m1", interrupt_id=iid_a, verb="APPROVE"))
    bus.put(_ev(origin_id="m2", interrupt_id=iid_b, verb="REJECT"))
    bus.put(_ev(channel="board", origin_id="m3", interrupt_id=iid_a, verb="REJECT"))

    events_a = bus.get_for_interrupt(iid_a)
    events_b = bus.get_for_interrupt(iid_b)

    assert len(events_a) == 2
    assert all(e["interrupt_id"] == iid_a for e in events_a)
    assert len(events_b) == 1


# ── ⑧ in-memory smoke test ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sp17_in_memory_smoke():
    """Criterion ⑧: SteeringEventBus() (no db_path) — full happy-path smoke."""
    bus = SteeringEventBus()
    runner = _mock_runner()

    # one approve, one reject — reject wins
    assert bus.put(_ev(channel="telegram", origin_id="t1", verb="APPROVE", kind="approve")) is True
    assert bus.put(_ev(channel="board", origin_id="b1", verb="REJECT", kind="reject")) is True

    # dedup check
    assert bus.put(_ev(channel="telegram", origin_id="t1", verb="APPROVE", kind="approve")) is False
    assert bus.ledger_count("telegram", "t1") == 1

    # agent-authored drop
    assert bus.put(_ev(channel="board", origin_id="agent-1"), origin="agent") is False

    # route → REJECT wins
    verb = await bus.route_to_interrupt(runner, thread_id="t1", interrupt_id="iid1")
    assert verb == "REJECT"
