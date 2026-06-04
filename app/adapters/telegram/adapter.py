"""SP-13 — TelegramAdapter: aiogram 3 inbound normaliser (C15).

Converts Telegram updates (callback_query + text commands) into SteeringEvents
and ingests them via SteeringEventBus.  Telegram update_id is the origin_id so
the ledger deduplicates replays across kill+resume.

Authority (C15): Telegram is the AUTHORITATIVE lifecycle channel — only this
adapter may produce APPROVE/REJECT/ABORT verbs from Telegram input.

C9-H1 fix (sender authorization): handle_update() checks the sender's from_id
against an operator_ids allowlist.  If operator_ids is non-empty and from_id is
not in the set, the event is silently dropped.  Callers MUST configure this
allowlist before a live Bot token is wired; an empty set is intentional (CI-only
posture) but logs a warning on every accepted event.

C9-H2 fix (callback interrupt binding): keyboard buttons encode the interrupt_id
as "verb:interrupt_id" in callback_data (e.g. "approve:iid-1").  The adapter
extracts and uses the encoded interrupt_id from the tapped button, making the
approval binding to the gate it was issued for.  Text /commands still use the
caller-supplied interrupt_id (acceptable when H1 restricts callers to operators).

C9-M2 fix: update_id must be a plain int; dict/list/str values are rejected.
C9-M3 fix: payload["text"] is truncated at 4096 chars (Telegram's own limit).

The adapter is bot-agnostic: it does not hold a Bot token or run a dispatcher
itself.  The caller (FastAPI lifespan or the integration test harness) constructs
a TelegramAdapter(bus, operator_ids={...}) and calls handle_update(raw_update_dict,
thread_id=..., interrupt_id=...) for each incoming webhook payload.

Deferred (not in this slice):
  * aiogram Dispatcher + webhook registration (end-to-end wiring)
  * FSM clarify dialog (the clarify loop already runs via interrupt(); the dialog
    surface is the operator replying with free-text answers forwarded here)
  * /pending cross-thread legibility (SP-17 criterion 6 / U-3)
"""

from __future__ import annotations

import logging
from typing import Any, cast

from app.core.graph_state import SteeringEvent
from app.core.steering import SteeringEventBus

logger = logging.getLogger(__name__)

# Map Telegram command text → (kind, verb)
_COMMAND_MAP: dict[str, tuple[str, str]] = {
    "/approve": ("approve", "APPROVE"),
    "/reject": ("reject", "REJECT"),
    "/abort": ("abort", "TIMEOUT"),
    "/replan": ("replan", "REPLAN"),
}

# Map inline keyboard callback verb token → (kind, verb)
# Callback data format: "verb" or "verb:interrupt_id" (H2 fix)
_CALLBACK_VERB_MAP: dict[str, tuple[str, str]] = {
    "approve": ("approve", "APPROVE"),
    "reject": ("reject", "REJECT"),
    "abort": ("abort", "TIMEOUT"),
    "replan": ("replan", "REPLAN"),
}

_CHANNEL = "telegram"
_TEXT_MAX = 4096  # Telegram's own limit; enforced here for forged webhook payloads (M3)


class TelegramAdapter:
    """Normalise Telegram updates to SteeringEvents and ingest via bus (C15).

    Usage:
        adapter = TelegramAdapter(bus, operator_ids={123456})
        accepted = adapter.handle_update(raw_update_dict, thread_id="t1", interrupt_id="iid1")

    ``operator_ids``: set of Telegram user IDs that are authorised to control the lifecycle.
    Pass an empty set only in CI/skeleton contexts (a WARNING is logged on every accepted event).
    """

    def __init__(
        self,
        bus: SteeringEventBus,
        *,
        operator_ids: frozenset[int] | None = None,
    ) -> None:
        self._bus = bus
        # H1: authorised sender allowlist.  frozenset() == no restriction (warn-only).
        self._operator_ids: frozenset[int] = operator_ids or frozenset()

    def handle_update(
        self,
        raw: dict[str, Any],
        *,
        thread_id: str,
        interrupt_id: str | None = None,
    ) -> bool:
        """Ingest a Telegram webhook payload.

        Normalises the update to a SteeringEvent and passes it to bus.put().
        Returns True if the event was new (accepted); False if deduped or dropped.

        ``raw`` is the JSON-decoded Telegram Update object (as a plain dict).
        ``thread_id`` is the spine thread this update targets.
        ``interrupt_id`` scopes the event to the active interrupt gate; for
        callback_query updates, the interrupt_id encoded in callback_data takes
        precedence (H2 fix).
        """
        # M2: update_id must be a plain integer (not dict/list/str)
        update_id = raw.get("update_id")
        if not isinstance(update_id, int):
            logger.warning(
                "telegram_adapter: ignoring update with non-int update_id: %r (%s)",
                update_id,
                type(update_id).__name__,
            )
            return False

        origin_id = str(update_id)

        kind, verb, payload, encoded_iid = self._parse_update(raw)
        if kind is None:
            logger.debug("telegram_adapter: ignoring unhandled update update_id=%s", origin_id)
            return False

        # H2: for callback_query, use the interrupt_id encoded in the button data
        effective_iid = encoded_iid if encoded_iid is not None else interrupt_id

        # H1: sender authorization
        from_id = (payload or {}).get("from_id")
        if self._operator_ids and from_id not in self._operator_ids:
            logger.warning(
                "telegram_adapter: dropped update from unauthorized sender "
                "update_id=%s from_id=%s (not in operator_ids)",
                origin_id,
                from_id,
            )
            return False
        if not self._operator_ids:
            logger.warning(
                "telegram_adapter: operator_ids not configured — "
                "accepting update_id=%s without sender check (CI-only posture)",
                origin_id,
            )

        import datetime

        ts = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()

        event: SteeringEvent = cast(
            SteeringEvent,
            {
                "thread_id": thread_id,
                "channel": _CHANNEL,
                "origin_id": origin_id,
                "kind": kind,
                "verb": verb,
                "interrupt_id": effective_iid,
                "payload": payload,
                "ts": ts,
            },
        )
        accepted = self._bus.put(event, origin="human")
        logger.info(
            "telegram_adapter: update_id=%s kind=%s verb=%s thread=%s iid=%s accepted=%s",
            origin_id,
            kind,
            verb,
            thread_id,
            effective_iid,
            accepted,
        )
        return accepted

    # ── Parsing ───────────────────────────────────────────────────────────

    def _parse_update(
        self, raw: dict[str, Any]
    ) -> tuple[str | None, str | None, dict | None, str | None]:
        """Extract (kind, verb, payload, encoded_interrupt_id) from a raw Telegram Update dict.

        Returns (None, None, None, None) if the update type is not a lifecycle command.
        The 4th element is the interrupt_id encoded in callback_data ("verb:iid"), or None
        for text commands (where the caller-supplied interrupt_id is used instead — H2).
        """
        if cq := raw.get("callback_query"):
            return self._parse_callback_query(cq)

        if msg := raw.get("message"):
            kind, verb, payload = self._parse_message(msg)
            return kind, verb, payload, None

        return None, None, None, None

    def _parse_callback_query(
        self, cq: dict[str, Any]
    ) -> tuple[str | None, str | None, dict | None, str | None]:
        # H2: callback_data format is "verb" or "verb:interrupt_id"
        data = (cq.get("data") or "").strip()
        parts = data.split(":", 1)
        verb_token = parts[0].lower()
        encoded_iid = parts[1] if len(parts) > 1 and parts[1] else None

        entry = _CALLBACK_VERB_MAP.get(verb_token)
        if entry is None:
            return None, None, None, None
        kind, verb = entry
        payload: dict[str, Any] = {
            "callback_query_id": cq.get("id"),
            "from_id": (cq.get("from") or {}).get("id"),
            "message_id": (cq.get("message") or {}).get("message_id"),
        }
        return kind, verb, payload, encoded_iid

    def _parse_message(self, msg: dict[str, Any]) -> tuple[str | None, str | None, dict | None]:
        text = (msg.get("text") or "").strip()
        cmd_token = text.split()[0].split("@")[0].lower() if text.startswith("/") else None
        if cmd_token is None:
            return None, None, None
        entry = _COMMAND_MAP.get(cmd_token)
        if entry is None:
            return None, None, None
        kind, verb = entry
        payload: dict[str, Any] = {
            "from_id": (msg.get("from") or {}).get("id"),
            "message_id": msg.get("message_id"),
            "text": text[:_TEXT_MAX],  # M3: truncate to Telegram's own limit
        }
        return kind, verb, payload
