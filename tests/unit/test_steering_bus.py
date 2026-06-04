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

C9 fix tests (adversarial review 2026-06-04 — Gemini/Opus):
  H1  unknown verb dropped at ingest — never enters ledger; route_to_interrupt safe.
  H2  origin parameter is required (structural enforcement, not optional kwarg).
  A4  route_to_interrupt filters by thread_id (cross-thread contamination impossible).

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
    first = bus.put(event, origin="human")
    second = bus.put(event, origin="human")
    assert first is True
    assert second is False
    assert bus.ledger_count("telegram", "m1") == 1


def test_sp17_dedup_different_channel_accepted():
    """Different (channel, origin_id) combinations are stored independently."""
    bus = SteeringEventBus()
    assert bus.put(_ev(channel="telegram", origin_id="m1"), origin="human") is True
    assert bus.put(_ev(channel="board", origin_id="m1"), origin="human") is True
    assert bus.put(_ev(channel="telegram", origin_id="m2"), origin="human") is True
    assert bus.ledger_count("telegram", "m1") == 1
    assert bus.ledger_count("board", "m1") == 1


# ── ② persisted ledger across bus restart ─────────────────────────────────


def test_sp17_persisted_ledger_survives_restart(tmp_path: Path):
    """Criterion ②: a new SteeringEventBus instance over the same db_path still deduplicates."""
    db = tmp_path / "steering.db"

    bus1 = SteeringEventBus(db_path=db)
    bus1.put(_ev(), origin="human")
    bus1.close()

    bus2 = SteeringEventBus(db_path=db)
    duplicate = bus2.put(_ev(), origin="human")
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
    """Explicit origin='human' → accepted normally."""
    bus = SteeringEventBus()
    assert bus.put(_ev(), origin="human") is True
    assert bus.put(_ev(origin_id="m2"), origin="human") is True


# ── ④ reject beats approve (C15 arbitration) ──────────────────────────────


@pytest.mark.asyncio
async def test_sp17_reject_beats_approve():
    """Criterion ④: board REJECT + telegram APPROVE on the same interrupt_id → REJECT wins."""
    bus = SteeringEventBus()
    runner = _mock_runner()

    bus.put(
        _ev(channel="telegram", origin_id="tg-1", verb="APPROVE", kind="approve"), origin="human"
    )
    bus.put(_ev(channel="board", origin_id="board-1", verb="REJECT", kind="reject"), origin="human")

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
        _ev(channel="telegram", origin_id="tg-1", verb="APPROVE", kind="approve", interrupt_id=iid),
        origin="human",
    )
    bus.put(
        _ev(channel="telegram", origin_id="tg-1", verb="APPROVE", kind="approve", interrupt_id=iid),
        origin="human",
    )  # dup
    bus.put(
        _ev(channel="board", origin_id="board-1", verb="REJECT", kind="reject", interrupt_id=iid),
        origin="human",
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
            channel="telegram",
            origin_id="tg-abort",
            verb="TIMEOUT",
            kind="abort",
            interrupt_id=iid,
        ),
        origin="human",
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
    bus.put(_ev(thread_id="t1", channel="telegram", origin_id="m1"), origin="human")
    bus.put(_ev(thread_id="t2", channel="telegram", origin_id="m2"), origin="human")
    bus.put(_ev(thread_id="t1", channel="board", origin_id="m3"), origin="human")

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
    bus.put(_ev(origin_id="m1", interrupt_id=iid_a, verb="APPROVE"), origin="human")
    bus.put(_ev(origin_id="m2", interrupt_id=iid_b, verb="REJECT"), origin="human")
    bus.put(_ev(channel="board", origin_id="m3", interrupt_id=iid_a, verb="REJECT"), origin="human")

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
    assert (
        bus.put(
            _ev(channel="telegram", origin_id="t1", verb="APPROVE", kind="approve"), origin="human"
        )
        is True
    )
    assert (
        bus.put(_ev(channel="board", origin_id="b1", verb="REJECT", kind="reject"), origin="human")
        is True
    )

    # dedup check
    assert (
        bus.put(
            _ev(channel="telegram", origin_id="t1", verb="APPROVE", kind="approve"), origin="human"
        )
        is False
    )
    assert bus.ledger_count("telegram", "t1") == 1

    # agent-authored drop
    assert bus.put(_ev(channel="board", origin_id="agent-1"), origin="agent") is False

    # route → REJECT wins
    verb = await bus.route_to_interrupt(runner, thread_id="t1", interrupt_id="iid1")
    assert verb == "REJECT"


# ── C9 fix tests ─────────────────────────────────────────────────────────


def test_sp17_h1_unknown_verb_dropped_at_ingest():
    """C9-H1: an event with an unknown/malformed verb is dropped at put() time.
    This prevents a future KeyError in arbitrate() from poisoning route_to_interrupt.
    """
    bus = SteeringEventBus()
    bad = bus.put(_ev(verb="MAYBE"), origin="human")
    assert bad is False, "unknown verb must be dropped (never enters ledger)"
    assert bus.ledger_count("telegram", "m1") == 0


@pytest.mark.asyncio
async def test_sp17_h1_route_safe_after_known_and_bad_verbs():
    """C9-H1: route_to_interrupt does not raise even if a bad-verb event was attempted."""
    bus = SteeringEventBus()
    runner = _mock_runner()
    iid = "iid-safe"

    # A bad verb is dropped at ingest; the valid APPROVE gets through.
    bus.put(_ev(verb="GARBAGE", kind="approve", interrupt_id=iid), origin="human")
    bus.put(_ev(verb="APPROVE", kind="approve", interrupt_id=iid), origin="human")

    # Should route to APPROVE (the only valid event), not crash.
    verb = await bus.route_to_interrupt(runner, thread_id="t1", interrupt_id=iid)
    assert verb == "APPROVE"


def test_sp17_h2_origin_is_required():
    """C9-H2: put() requires the origin kwarg — callers cannot accidentally omit it.
    Missing origin → TypeError (structural enforcement, not silent acceptance).
    """
    bus = SteeringEventBus()
    with pytest.raises(TypeError):
        bus.put(_ev())  # type: ignore[call-arg]  # missing required kwarg


@pytest.mark.asyncio
async def test_sp17_a4_route_filters_by_thread_id():
    """C9-A4: route_to_interrupt only applies events that match thread_id.
    A REJECT stored for thread tA must NOT route to thread tB (cross-thread safety).
    """
    bus = SteeringEventBus()
    runner = _mock_runner()
    iid = "iid-shared"

    # Store a REJECT for thread tA on this interrupt_id.
    bus.put(
        _ev(thread_id="tA", verb="REJECT", kind="reject", interrupt_id=iid),
        origin="human",
    )
    # Route the same interrupt_id but for thread tB — tA's REJECT must NOT apply.
    verb = await bus.route_to_interrupt(runner, thread_id="tB", interrupt_id=iid)
    assert verb == "APPROVE", "tA's REJECT must not bleed into tB's routing"


def test_sp17_self_approval_rejection(monkeypatch):
    """Secure Steering Event Bus: Reject any board steering events where the
    comment author ID matches the agent's account ID.
    """
    monkeypatch.setenv("PLANE_BOT_ACCOUNT_ID", "bot-agent-id")
    bus = SteeringEventBus()

    bot_event = _ev(channel="board", origin_id="m1")
    bot_event["payload"] = {"author_id": "bot-agent-id", "text": "APPROVE"}

    assert bus.put(bot_event, origin="human") is False

    human_event = _ev(channel="board", origin_id="m2")
    human_event["payload"] = {"author_id": "human-operator-id", "text": "APPROVE"}

    assert bus.put(human_event, origin="human") is True
