"""SP-13 — TelegramAdapter + TelegramNotifier hermetic unit tests.

Coverage map (PRD §6 SP-13 acceptance criteria):
  ①  inbound dedup: same update_id twice → count==1 in ledger; second put returns False.
  ②  inbound dedup across kill+resume: new TelegramAdapter over same db_path, replay →
      still deduplicated.
  ③  callback_query "approve:{iid}" → SteeringEvent(verb=APPROVE) with encoded iid.
  ④  callback_query "reject:{iid}" → SteeringEvent(verb=REJECT) accepted.
  ⑤  callback_query "abort:{iid}"  → SteeringEvent(verb=TIMEOUT) accepted (C15: abort=TIMEOUT).
  ⑥  /approve command → APPROVE accepted (interrupt_id from caller).
  ⑦  /reject  command → REJECT  accepted.
  ⑧  /abort   command → TIMEOUT accepted.
  ⑨  unknown callback_data → dropped (returns False, ledger untouched).
  ⑩  update with non-int update_id → dropped (C9-M2).
  ⑮  different update_ids for the same thread/interrupt → both accepted.

  C9 fix tests:
  H1  sender not in operator_ids → dropped (unauthorized sender).
  H1  sender in operator_ids → accepted.
  H1  no operator_ids configured → accepted with warning (CI-only posture).
  H2  callback_data "approve:{iid}" → effective interrupt_id = iid from button (not caller-supplied).
  H2  no encoded iid in callback_data → falls back to caller-supplied interrupt_id.
  M1  concurrent notify() calls claim the send slot atomically — only one send occurs.
  M2  non-int update_id (dict) → dropped.
  M3  long text truncated to 4096 in payload.

  Notifier exactly-once:
  ⑪  notify() → sent once; second call same (thread_id, event_key) → False (deduped).
  ⑫  notify_gate() → sends with inline keyboard; second call → False (deduped).
  ⑬  exactly-once across kill+resume: new notifier over same db_path → still deduped.
  ⑭  notify lifecycle event_keys: decompose / questions / prd_signed / sub_agents /
      test_results / deploy — each sends exactly once (RecordingTransport records count==1
      per event_key).

DEFERRED (not tested here):
  * aiogram Dispatcher + webhook registration (end-to-end wiring)
  * FSM clarify dialog free-text forwarding
  * /pending cross-thread legibility (SP-17 criterion 6 / U-3)
"""

from __future__ import annotations

import threading
from pathlib import Path


from app.adapters.telegram.adapter import TelegramAdapter
from app.adapters.telegram.notifier import (
    LIFECYCLE_EVENTS,
    RecordingTransport,
    TelegramNotifier,
)
from app.core.steering import SteeringEventBus

_OPERATOR_ID = 999
_OPERATOR_IDS = frozenset({_OPERATOR_ID})


# ── helpers ───────────────────────────────────────────────────────────────────


def _callback_update(update_id: int, data: str, from_id: int = _OPERATOR_ID) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cq-{update_id}",
            "data": data,
            "from": {"id": from_id, "first_name": "Operator"},
            "message": {"message_id": 42, "chat": {"id": 123}},
        },
    }


def _message_update(update_id: int, text: str, from_id: int = _OPERATOR_ID) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": from_id, "first_name": "Operator"},
            "chat": {"id": 123},
            "text": text,
        },
    }


def _adapter(bus: SteeringEventBus | None = None, operator_ids=_OPERATOR_IDS) -> TelegramAdapter:
    return TelegramAdapter(bus or SteeringEventBus(), operator_ids=operator_ids)


# ── ① inbound dedup ───────────────────────────────────────────────────────────


def test_sp13_dedup_same_update_id():
    """Criterion ①: same update_id twice → count==1 in ledger; second put returns False."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)

    first = adapter.handle_update(
        _callback_update(1001, "approve:iid1"), thread_id="t1", interrupt_id="iid1"
    )
    second = adapter.handle_update(
        _callback_update(1001, "approve:iid1"), thread_id="t1", interrupt_id="iid1"
    )
    assert first is True
    assert second is False
    assert bus.ledger_count("telegram", "1001") == 1


def test_sp13_different_update_ids_accepted():
    """⑮: Different update_ids for the same thread/interrupt → both accepted."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)

    r1 = adapter.handle_update(
        _callback_update(2001, "approve:iid1"), thread_id="t1", interrupt_id="iid1"
    )
    r2 = adapter.handle_update(
        _callback_update(2002, "reject:iid1"), thread_id="t1", interrupt_id="iid1"
    )
    assert r1 is True
    assert r2 is True


# ── ② dedup across kill+resume ────────────────────────────────────────────────


def test_sp13_dedup_across_bus_restart(tmp_path: Path):
    """Criterion ②: new TelegramAdapter + new bus over same db_path → still deduped."""
    db = tmp_path / "steering.db"

    bus1 = SteeringEventBus(db_path=db)
    _adapter(bus1).handle_update(
        _callback_update(3001, "approve:iid1"), thread_id="t1", interrupt_id="iid1"
    )
    bus1.close()

    bus2 = SteeringEventBus(db_path=db)
    result = _adapter(bus2).handle_update(
        _callback_update(3001, "approve:iid1"), thread_id="t1", interrupt_id="iid1"
    )
    bus2.close()

    assert result is False, "replayed update_id must be deduped across kill+resume"


# ── ③–⑤ callback_query verb + interrupt binding ──────────────────────────────


def test_sp13_callback_approve_with_encoded_iid():
    """Criterion ③: callback_data='approve:{iid}' → APPROVE + interrupt_id from button (H2)."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)
    ok = adapter.handle_update(_callback_update(4001, "approve:iid-gate"), thread_id="t1")
    assert ok is True
    events = bus.get_for_interrupt("iid-gate")
    assert len(events) == 1
    assert events[0]["verb"] == "APPROVE"
    assert events[0]["kind"] == "approve"
    assert events[0]["interrupt_id"] == "iid-gate"


def test_sp13_callback_reject():
    """Criterion ④: callback_data='reject:{iid}' → REJECT accepted."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)
    ok = adapter.handle_update(_callback_update(4002, "reject:iid1"), thread_id="t1")
    assert ok is True
    events = bus.get_for_interrupt("iid1")
    assert events[0]["verb"] == "REJECT"


def test_sp13_callback_abort():
    """Criterion ⑤: callback_data='abort:{iid}' → TIMEOUT (C15: abort=TIMEOUT halting verb)."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)
    ok = adapter.handle_update(_callback_update(4003, "abort:iid1"), thread_id="t1")
    assert ok is True
    events = bus.get_for_interrupt("iid1")
    assert events[0]["verb"] == "TIMEOUT"
    assert events[0]["kind"] == "abort"


# ── ⑥–⑧ /command text message verb mapping ───────────────────────────────────


def test_sp13_command_approve():
    """Criterion ⑥: /approve text message → APPROVE accepted."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)
    ok = adapter.handle_update(
        _message_update(5001, "/approve"), thread_id="t1", interrupt_id="iid1"
    )
    assert ok is True
    events = bus.get_for_interrupt("iid1")
    assert events[0]["verb"] == "APPROVE"


def test_sp13_command_reject():
    """Criterion ⑦: /reject text message → REJECT accepted."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)
    ok = adapter.handle_update(
        _message_update(5002, "/reject"), thread_id="t1", interrupt_id="iid1"
    )
    assert ok is True
    events = bus.get_for_interrupt("iid1")
    assert events[0]["verb"] == "REJECT"


def test_sp13_command_abort():
    """Criterion ⑧: /abort text message → TIMEOUT accepted."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)
    ok = adapter.handle_update(_message_update(5003, "/abort"), thread_id="t1", interrupt_id="iid1")
    assert ok is True
    events = bus.get_for_interrupt("iid1")
    assert events[0]["verb"] == "TIMEOUT"


# ── ⑨–⑩ dropped updates ──────────────────────────────────────────────────────


def test_sp13_unknown_callback_dropped():
    """Criterion ⑨: unknown callback_data → dropped; ledger untouched."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)
    ok = adapter.handle_update(
        _callback_update(6001, "UNKNOWN_VERB:iid1"), thread_id="t1", interrupt_id="iid1"
    )
    assert ok is False
    assert bus.ledger_count("telegram", "6001") == 0


def test_sp13_non_int_update_id_dropped():
    """Criterion ⑩ / C9-M2: non-int update_id → dropped."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)

    # dict update_id
    ok1 = adapter.handle_update(
        {"update_id": {"x": 1}, "callback_query": {"data": "approve:iid1"}},
        thread_id="t1",
        interrupt_id="iid1",
    )
    # str update_id
    ok2 = adapter.handle_update(
        {"update_id": "1001", "callback_query": {"data": "approve:iid1"}},
        thread_id="t1",
        interrupt_id="iid1",
    )
    assert ok1 is False
    assert ok2 is False


# ── C9-H1 sender authorization ───────────────────────────────────────────────


def test_sp13_h1_unauthorized_sender_dropped():
    """C9-H1: sender not in operator_ids → dropped."""
    bus = SteeringEventBus()
    adapter = _adapter(bus, operator_ids=frozenset({123}))
    ok = adapter.handle_update(
        _callback_update(7001, "approve:iid1", from_id=999),  # 999 not in {123}
        thread_id="t1",
        interrupt_id="iid1",
    )
    assert ok is False
    assert bus.ledger_count("telegram", "7001") == 0


def test_sp13_h1_authorized_sender_accepted():
    """C9-H1: sender in operator_ids → accepted."""
    bus = SteeringEventBus()
    adapter = _adapter(bus, operator_ids=frozenset({999}))
    ok = adapter.handle_update(
        _callback_update(7002, "approve:iid1", from_id=999),
        thread_id="t1",
        interrupt_id="iid1",
    )
    assert ok is True


def test_sp13_h1_no_operator_ids_accepted_with_warning(caplog):
    """C9-H1: no operator_ids configured → accepted (CI-only posture) with a warning logged."""
    import logging

    bus = SteeringEventBus()
    adapter = _adapter(bus, operator_ids=frozenset())
    with caplog.at_level(logging.WARNING, logger="app.adapters.telegram.adapter"):
        ok = adapter.handle_update(
            _callback_update(7003, "approve:iid1"), thread_id="t1", interrupt_id="iid1"
        )
    assert ok is True
    assert any("operator_ids not configured" in r.message for r in caplog.records)


# ── C9-H2 callback interrupt binding ─────────────────────────────────────────


def test_sp13_h2_encoded_iid_overrides_caller_iid():
    """C9-H2: callback_data encodes interrupt_id → effective iid from button, not caller."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)
    ok = adapter.handle_update(
        _callback_update(8001, "approve:button-iid"),
        thread_id="t1",
        interrupt_id="caller-iid",  # should be overridden by button
    )
    assert ok is True
    # Event must appear under button-iid, not caller-iid
    assert len(bus.get_for_interrupt("button-iid")) == 1
    assert len(bus.get_for_interrupt("caller-iid")) == 0


def test_sp13_h2_no_encoded_iid_fallback_to_caller():
    """C9-H2: callback_data with no encoded iid → falls back to caller-supplied interrupt_id."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)
    ok = adapter.handle_update(
        _callback_update(8002, "approve"),  # no ":iid" suffix
        thread_id="t1",
        interrupt_id="caller-iid",
    )
    assert ok is True
    assert len(bus.get_for_interrupt("caller-iid")) == 1


# ── C9-M1 atomic claim prevents double-post ──────────────────────────────────


def test_sp13_m1_concurrent_notify_sends_once():
    """C9-M1: concurrent notify() calls — only one send occurs (atomic INSERT claim)."""
    transport = RecordingTransport()
    notifier = TelegramNotifier(chat_id="123", transport=transport)

    results = []
    barrier = threading.Barrier(5)

    def _send():
        barrier.wait()  # synchronise all threads to maximise contention
        r = notifier.notify(thread_id="t1", event_key="test_results", text="done")
        results.append(r)

    threads = [threading.Thread(target=_send) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 1, "exactly one caller should own the send"
    assert len(transport.calls) == 1, "transport.send() called exactly once"


# ── C9-M3 text truncation ─────────────────────────────────────────────────────


def test_sp13_m3_long_text_truncated_in_payload():
    """C9-M3: payload['text'] is truncated to 4096 chars."""
    bus = SteeringEventBus()
    adapter = _adapter(bus)
    long_text = "/approve " + "x" * 10000
    ok = adapter.handle_update(
        _message_update(9001, long_text), thread_id="t1", interrupt_id="iid1"
    )
    assert ok is True
    event = bus.get_for_interrupt("iid1")[0]
    assert len(event["payload"]["text"]) <= 4096


# ── ⑪ notifier exactly-once ───────────────────────────────────────────────────


def test_sp13_notify_exactly_once():
    """Criterion ⑪: notify() sent once; second call same (thread_id, event_key) → False."""
    transport = RecordingTransport()
    notifier = TelegramNotifier(chat_id="123", transport=transport)

    first = notifier.notify(thread_id="t1", event_key="prd_signed", text="PRD approved.")
    second = notifier.notify(thread_id="t1", event_key="prd_signed", text="PRD approved.")

    assert first is True
    assert second is False
    assert len(transport.calls) == 1
    assert transport.calls[0]["text"] == "PRD approved."


def test_sp13_notify_gate_exactly_once_with_encoded_keyboard():
    """Criterion ⑫: notify_gate() with interrupt_id sends encoded keyboard; second → False."""
    transport = RecordingTransport()
    notifier = TelegramNotifier(chat_id="123", transport=transport)

    first = notifier.notify_gate(
        thread_id="t1",
        event_key="sign_off_gate",
        text="Approve the PRD?",
        interrupt_id="iid-sign-off",
    )
    second = notifier.notify_gate(
        thread_id="t1",
        event_key="sign_off_gate",
        text="Approve the PRD?",
        interrupt_id="iid-sign-off",
    )

    assert first is True
    assert second is False
    assert len(transport.calls) == 1
    kb = transport.calls[0]["reply_markup"]
    assert kb is not None
    # H2: callback_data must encode the interrupt_id
    assert kb[0][0]["callback_data"] == "approve:iid-sign-off"
    assert kb[0][1]["callback_data"] == "reject:iid-sign-off"


# ── ⑬ notifier exactly-once across kill+resume ───────────────────────────────


def test_sp13_notifier_dedup_across_restart(tmp_path: Path):
    """Criterion ⑬: new notifier over same db_path → still deduped."""
    db = tmp_path / "outbox.db"

    t1 = RecordingTransport()
    n1 = TelegramNotifier(chat_id="123", transport=t1, db_path=db)
    n1.notify(thread_id="t1", event_key="deploy", text="Deployed!")
    n1.close()

    t2 = RecordingTransport()
    n2 = TelegramNotifier(chat_id="123", transport=t2, db_path=db)
    result = n2.notify(thread_id="t1", event_key="deploy", text="Deployed!")
    n2.close()

    assert result is False, "deploy event must NOT re-send after kill+resume"
    assert len(t2.calls) == 0


# ── ⑭ all lifecycle event_keys sent exactly once ─────────────────────────────


def test_sp13_all_lifecycle_events_sent_once():
    """Criterion ⑭: every LIFECYCLE_EVENTS key sends count==1 across all keys.

    Simulates a complete lifecycle sequence and verifies the RecordingTransport
    recorded exactly one call per event key.
    """
    transport = RecordingTransport()
    notifier = TelegramNotifier(chat_id="999", transport=transport)

    for key in sorted(LIFECYCLE_EVENTS):
        notifier.notify(thread_id="life", event_key=key, text=f"event: {key}")

    assert len(transport.calls) == len(LIFECYCLE_EVENTS)
    keys_in_calls = {c["text"].split(": ")[1] for c in transport.calls}
    assert keys_in_calls == LIFECYCLE_EVENTS

    # Replay the same sequence — all must be deduped
    for key in sorted(LIFECYCLE_EVENTS):
        duplicate = notifier.notify(thread_id="life", event_key=key, text=f"event: {key}")
        assert duplicate is False, f"event_key={key} must not be sent twice"

    # Still exactly one call per event
    assert len(transport.calls) == len(LIFECYCLE_EVENTS)
