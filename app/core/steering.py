"""SP-17 SteeringEventBus — inbound channel arbitration (C15).

Normalises Telegram updates and board comments into SteeringEvents, deduplicates
on (channel, origin_id), drops agent-authored events (origin="agent"), and routes
approve/reject/answer to the open interrupt() on the matching thread_id.

C15 authority matrix (hard-coded — no runtime override):
  telegram  => lifecycle control (approve/reject/abort/prod-approval)
  board     => content steering / answers

On conflict for one interrupt_id, reject/abort beats approve (arbitrate() in
graph_state implements the REJECT > REPLAN > TIMEOUT > APPROVE precedence rule).

Idempotency ledger: SQLite, persisted across kill+resume (constructor takes an
optional db_path).  An in-memory SQLite is the default (CI/unit tests — no disk).

Thread-safety: a per-instance threading.Lock serialises all ledger operations so a
multi-threaded server (e.g. an asyncio + background-poller mix) can share one bus.

DEFERRED (named, NOT silently dropped):
  * per-super-step ON-the-loop delivery to the execute/fan-out loop (criterion 2 —
    "a comment posted while execute runs is observed at the next iteration and alters
    the trace"); requires the execute-loop steer check in graph.py;
  * cross-thread "blocked on you" legibility (SP-17 criterion 6 / U-3): enumerate
    interrupt()-paused and SP-05-parked threads from a /pending Telegram query;
  * the Telegram aiogram adapter (SP-13) and board poll/webhook adapters — they call
    bus.put(); their normalisation and transport layers land separately.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from app.core.graph_state import SteeringEvent, arbitrate

logger = logging.getLogger(__name__)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS steering_ledger (
    channel      TEXT NOT NULL,
    origin_id    TEXT NOT NULL,
    thread_id    TEXT NOT NULL,
    interrupt_id TEXT,
    kind         TEXT NOT NULL,
    verb         TEXT NOT NULL,
    payload      TEXT,
    ts           TEXT NOT NULL,
    PRIMARY KEY (channel, origin_id)
)
"""

_INSERT_SQL = """
INSERT INTO steering_ledger
    (channel, origin_id, thread_id, interrupt_id, kind, verb, payload, ts)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_LEDGER_COUNT_SQL = """
SELECT COUNT(*) FROM steering_ledger WHERE channel=? AND origin_id=?
"""

_LEDGER_EXISTS_SQL = """
SELECT 1 FROM steering_ledger WHERE channel=? AND origin_id=? LIMIT 1
"""

_SELECT_FOR_INTERRUPT_SQL = """
SELECT channel, origin_id, thread_id, interrupt_id, kind, verb, payload, ts
  FROM steering_ledger
 WHERE interrupt_id=?
"""

_SELECT_FOR_THREAD_SQL = """
SELECT channel, origin_id, thread_id, interrupt_id, kind, verb, payload, ts
  FROM steering_ledger
 WHERE thread_id=?
"""

_SELECT_ALL_SQL = """
SELECT channel, origin_id, thread_id, interrupt_id, kind, verb, payload, ts
  FROM steering_ledger
"""


def _row_to_event(row: tuple) -> SteeringEvent:
    channel, origin_id, thread_id, interrupt_id, kind, verb, payload_str, ts = row
    return SteeringEvent(
        thread_id=thread_id,
        channel=channel,
        origin_id=origin_id,
        kind=kind,  # type: ignore[arg-type]
        verb=verb,  # type: ignore[arg-type]
        interrupt_id=interrupt_id,
        payload=json.loads(payload_str) if payload_str else None,
        ts=ts,
    )


class SteeringEventBus:
    """Inbound human message bus — C15 dedup + arbitration hub (SP-17).

    Usage:
        bus = SteeringEventBus()                       # in-memory (CI)
        bus = SteeringEventBus(db_path=Path("..."))    # persisted (prod)

        new = bus.put(event)                           # True if accepted, False if deduped
        events = bus.get_for_interrupt(interrupt_id)
        verb = await bus.route_to_interrupt(runner, thread_id=tid, interrupt_id=iid)
    """

    def __init__(self, *, db_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._db_path = db_path
        connect_target = str(db_path) if db_path is not None else ":memory:"
        self._conn = sqlite3.connect(connect_target, check_same_thread=False)
        self._conn.execute(_CREATE_SQL)
        self._conn.commit()

    # ── Ingestion ─────────────────────────────────────────────────────────

    def put(self, event: SteeringEvent, *, origin: Optional[str] = None) -> bool:
        """Ingest a normalised SteeringEvent.

        Returns True if the event is new (stored in the ledger).
        Returns False when the event is:
          * a duplicate (same (channel, origin_id) already in the ledger), OR
          * agent-authored (origin="agent") — dropped at ingest per C15.
        """
        if origin == "agent":
            logger.debug(
                "steering_bus.put: dropped agent-authored event channel=%s origin_id=%s",
                event.get("channel"),
                event.get("origin_id"),
            )
            return False

        channel = event["channel"]
        origin_id = event["origin_id"]

        with self._lock:
            exists = self._conn.execute(_LEDGER_EXISTS_SQL, (channel, origin_id)).fetchone()
            if exists:
                logger.debug(
                    "steering_bus.put: deduped channel=%s origin_id=%s", channel, origin_id
                )
                return False

            payload = event.get("payload")
            self._conn.execute(
                _INSERT_SQL,
                (
                    channel,
                    origin_id,
                    event["thread_id"],
                    event.get("interrupt_id"),
                    event["kind"],
                    event["verb"],
                    json.dumps(payload) if payload is not None else None,
                    event["ts"],
                ),
            )
            self._conn.commit()
            logger.info(
                "steering_bus.put: accepted channel=%s origin_id=%s kind=%s thread=%s",
                channel,
                origin_id,
                event["kind"],
                event["thread_id"],
            )
            return True

    # ── Queries ───────────────────────────────────────────────────────────

    def get_for_interrupt(self, interrupt_id: str) -> list[SteeringEvent]:
        """Return all stored events targeting interrupt_id (for arbitration)."""
        with self._lock:
            rows = self._conn.execute(_SELECT_FOR_INTERRUPT_SQL, (interrupt_id,)).fetchall()
        return [_row_to_event(r) for r in rows]

    def get_for_thread(self, thread_id: str) -> list[SteeringEvent]:
        """Return all stored events for a thread_id (steer/abort — ON-the-loop delivery)."""
        with self._lock:
            rows = self._conn.execute(_SELECT_FOR_THREAD_SQL, (thread_id,)).fetchall()
        return [_row_to_event(r) for r in rows]

    def ledger_count(self, channel: str, origin_id: str) -> int:
        """Return how many times (channel, origin_id) appears in the ledger (0 or 1)."""
        with self._lock:
            row = self._conn.execute(_LEDGER_COUNT_SQL, (channel, origin_id)).fetchone()
        return row[0] if row else 0

    # ── Routing ───────────────────────────────────────────────────────────

    async def route_to_interrupt(
        self,
        runner: Any,  # SpineRunner — avoids circular import
        *,
        thread_id: str,
        interrupt_id: str,
    ) -> str:
        """Arbitrate accumulated events for interrupt_id and resume the runner.

        Calls graph_state.arbitrate() on every stored event whose interrupt_id matches.
        If no matching events → defaults to "APPROVE" (pass-through, fail-safe for
        an interrupt with no opposing signal).

        Returns the effective HitlVerb applied to runner.resume().
        """
        events = self.get_for_interrupt(interrupt_id)
        verb = arbitrate(events, interrupt_id) or "APPROVE"

        logger.info(
            "steering_bus.route: thread=%s interrupt=%s events=%d verb=%s",
            thread_id,
            interrupt_id,
            len(events),
            verb,
        )
        await runner.resume(
            thread_id=thread_id,
            interrupt_id=interrupt_id,
            decision={
                "verb": verb,
                "actor": "steering_bus",
                "reason": f"C15 arbitrate: {verb} ({len(events)} event(s))",
            },
        )
        return verb

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the SQLite connection. After close() the bus is unusable."""
        self._conn.close()
