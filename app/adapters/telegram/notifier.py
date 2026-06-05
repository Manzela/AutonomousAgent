"""SP-13 — TelegramNotifier: exactly-once outbound message dispatcher.

Sends lifecycle notifications to a Telegram chat, deduplicating on
(thread_id, event_key) so a kill+resume never double-posts.

The notifier is transport-agnostic: it delegates actual sending to an
AbstractTransport, allowing hermetic unit tests to inject a RecordingTransport
that records calls without touching the real Bot API.

Lifecycle event_key values (PRD §6 SP-13):
  "decompose"   — task DAG created
  "questions"   — clarify Q-round started
  "prd_signed"  — operator approved the PRD (sign_off interrupt resumed)
  "sub_agents"  — fan_out wave dispatched
  "test_results"— eval_gate verdict
  "deploy"      — ship_effect completed

For gated actions (sign_off, ship_gate) the notifier attaches an inline
keyboard so the operator can approve/reject directly from Telegram.

Keyboard callback_data values: "approve", "reject", "abort", "replan"
(normalised to SteeringEvent verbs by TelegramAdapter).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Lifecycle event keys the PRD requires exactly-once delivery for
LIFECYCLE_EVENTS = frozenset(
    {
        "decompose",
        "questions",
        "prd_signed",
        "sub_agents",
        "test_results",
        "deploy",
        # sign-off / ship gates (inline keyboard attached)
        "sign_off_gate",
        "ship_gate",
    }
)

_APPROVE_REJECT_KEYBOARD = [
    [
        {"text": "✓ Approve", "callback_data": "approve"},
        {"text": "✗ Reject", "callback_data": "reject"},
    ]
]

_ABORT_KEYBOARD = [
    [
        {"text": "⚠ Abort", "callback_data": "abort"},
        {"text": "↺ Replan", "callback_data": "replan"},
    ]
]


def _make_gate_keyboard(interrupt_id: str) -> list:
    """H2 fix: encode interrupt_id in callback_data so TelegramAdapter can verify binding."""
    return [
        [
            {"text": "✓ Approve", "callback_data": f"approve:{interrupt_id}"},
            {"text": "✗ Reject", "callback_data": f"reject:{interrupt_id}"},
        ]
    ]


# ── Transport abstraction ────────────────────────────────────────────────────


class AbstractTransport(ABC):
    """Send a Telegram message.  Implementations: real Bot API or test RecordingTransport."""

    @abstractmethod
    def send(
        self,
        *,
        chat_id: str,
        text: str,
        reply_markup: Optional[list] = None,
    ) -> None:
        """Deliver ``text`` to ``chat_id``.  Raises on unrecoverable errors."""


class RecordingTransport(AbstractTransport):
    """Test double: records send() calls instead of hitting the Bot API."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, *, chat_id: str, text: str, reply_markup: Optional[list] = None) -> None:
        self.calls.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        logger.debug("RecordingTransport.send chat_id=%s text=%r", chat_id, text[:80])


class HttpxTransport(AbstractTransport):
    """Production transport using httpx to call the Telegram Bot API."""

    def __init__(self, bot_token: str, timeout: float = 10.0) -> None:
        if not bot_token:
            raise ValueError("bot_token is required for HttpxTransport")
        self._bot_token = bot_token
        self._timeout = timeout

    def send(self, *, chat_id: str, text: str, reply_markup: Optional[list] = None) -> None:
        import httpx

        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup:
            payload["reply_markup"] = {"inline_keyboard": reply_markup}

        try:
            resp = httpx.post(url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
            logger.info("Telegram notification sent successfully to chat_id=%s", chat_id)
        except Exception as exc:
            logger.error("Failed to send Telegram notification to chat_id=%s: %s", chat_id, exc)
            raise RuntimeError(f"Telegram API call failed: {exc}") from exc


# ── Idempotency ledger ───────────────────────────────────────────────────────

_CREATE_OUTBOX = """
CREATE TABLE IF NOT EXISTS notification_outbox (
    thread_id   TEXT NOT NULL,
    event_key   TEXT NOT NULL,
    PRIMARY KEY (thread_id, event_key)
)
"""


class _OutboxLedger:
    """SQLite-backed exactly-once send ledger (persisted across kill+resume).

    C9-M1 fix: uses insert-first-then-send — the INSERT (under lock) is the single
    arbiter.  rowcount==1 means this caller owns the send; rowcount==0 means someone
    else already sent.  The transport.send() call happens AFTER the lock is released,
    so no lock is held during I/O, and no TOCTOU double-post is possible.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        target = str(db_path) if db_path is not None else ":memory:"
        self._conn = sqlite3.connect(target, check_same_thread=False)
        self._conn.execute(_CREATE_OUTBOX)
        self._conn.commit()

    def try_claim(self, thread_id: str, event_key: str) -> bool:
        """Atomically claim the send slot.  Returns True iff this caller owns the send.

        Uses INSERT OR IGNORE so the DB row is the arbiter.  Under the lock, rowcount==1
        means this thread inserted the row (owns the send); rowcount==0 means the row
        existed already (duplicate — skip).
        """
        with self._lock:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO notification_outbox (thread_id, event_key) VALUES (?, ?)",
                (thread_id, event_key),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def close(self) -> None:
        self._conn.close()


# ── Public notifier ──────────────────────────────────────────────────────────


class TelegramNotifier:
    """Send exactly-once lifecycle notifications to a Telegram chat (SP-13).

    Usage:
        notifier = TelegramNotifier(chat_id="123456", transport=RecordingTransport())
        notifier.notify(thread_id="t1", event_key="prd_signed",
                        text="PRD approved — decomposing now.")
        notifier.notify_gate(thread_id="t1", event_key="sign_off_gate",
                             text="Please approve the PRD draft:")
    """

    def __init__(
        self,
        *,
        chat_id: str,
        transport: AbstractTransport,
        db_path: Optional[Path] = None,
    ) -> None:
        self._chat_id = chat_id
        self._transport = transport
        self._ledger = _OutboxLedger(db_path=db_path)

    def notify(
        self,
        *,
        thread_id: str,
        event_key: str,
        text: str,
    ) -> bool:
        """Send an informational message (no inline keyboard).

        Returns True if the message was sent; False if already sent (deduped).
        C9-M1: try_claim() is the atomic arbiter — no TOCTOU between check and send.
        """
        if not self._ledger.try_claim(thread_id, event_key):
            logger.debug("notifier: skip duplicate thread=%s event_key=%s", thread_id, event_key)
            return False
        from lib.guardrails.sanitize import sanitize_markdown

        sanitized_text = sanitize_markdown(text)
        self._transport.send(chat_id=self._chat_id, text=sanitized_text)
        logger.info("notifier: sent thread=%s event_key=%s", thread_id, event_key)
        return True

    def notify_gate(
        self,
        *,
        thread_id: str,
        event_key: str,
        text: str,
        interrupt_id: Optional[str] = None,
        keyboard: Optional[list] = None,
    ) -> bool:
        """Send a gated notification with an inline keyboard (approve/reject/abort).

        C9-H2 fix: if ``interrupt_id`` is provided and ``keyboard`` is None, the
        keyboard buttons encode the interrupt_id as "verb:{interrupt_id}" so that
        TelegramAdapter can derive the correct gate from the tapped button (not from
        whatever the current open interrupt is at routing time).

        Defaults to the approve+reject keyboard (without interrupt binding) when
        neither ``interrupt_id`` nor ``keyboard`` are passed — acceptable for tests
        and skeleton contexts.

        Returns True if sent; False if deduped.
        C9-M1: try_claim() is the atomic arbiter — no TOCTOU between check and send.
        """
        if keyboard is None:
            keyboard = (
                _make_gate_keyboard(interrupt_id)
                if interrupt_id is not None
                else _APPROVE_REJECT_KEYBOARD
            )
        if not self._ledger.try_claim(thread_id, event_key):
            logger.debug(
                "notifier: skip duplicate gate thread=%s event_key=%s", thread_id, event_key
            )
            return False
        from lib.guardrails.sanitize import sanitize_markdown

        sanitized_text = sanitize_markdown(text)
        self._transport.send(
            chat_id=self._chat_id,
            text=sanitized_text,
            reply_markup=keyboard,
        )
        logger.info(
            "notifier: sent gate thread=%s event_key=%s iid=%s", thread_id, event_key, interrupt_id
        )
        return True

    def set_transport(self, transport: AbstractTransport) -> None:
        """Replace the active transport (e.g. swap stub for real Bot in integration tests).

        Makes AbstractTransport C4-reachable from TelegramNotifier's public API.
        """
        if not isinstance(transport, AbstractTransport):
            raise TypeError(f"transport must be AbstractTransport, got {type(transport)}")
        self._transport = transport

    @classmethod
    def make_recording(
        cls,
        chat_id: str,
        *,
        db_path: Optional[Path] = None,
    ) -> "TelegramNotifier":
        """Test factory: TelegramNotifier backed by a RecordingTransport.

        Makes RecordingTransport C4-reachable from TelegramNotifier's public API;
        also the intended entry point for unit tests that don't need a real Bot token.
        """
        return cls(chat_id=chat_id, transport=RecordingTransport(), db_path=db_path)

    def close(self) -> None:
        self._ledger.close()


# ── Convenience constants ────────────────────────────────────────────────────

APPROVE_REJECT_KEYBOARD = _APPROVE_REJECT_KEYBOARD
ABORT_KEYBOARD = _ABORT_KEYBOARD
