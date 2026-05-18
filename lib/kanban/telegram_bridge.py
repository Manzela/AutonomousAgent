"""P1-5 Telegram ↔ Hermes Kanban bridge.

Two directions of flow:

1. **Telegram → Kanban**: ``telegram_msg_to_card(msg, user_id)`` runs at
   TaskSpec lock time (driven by the P1-1 anchors plugin once the
   clarification loop closes). The inbound message becomes a Kanban
   card via Hermes' ``kanban_db.create_task``. Returns the new card id
   so the anchors plugin can attach it to session metadata.

2. **Kanban → Telegram**: every Kanban status transition is mapped to
   an optional Telegram message via
   ``notification_policy.status_transition_to_notification``. Silent
   transitions return ``None`` and the bridge sends nothing. Non-silent
   transitions return a string that ``send_alert`` posts to the
   operator's Telegram chat. Also exposed for use by
   ``lib/durability/escalation.emit_escalation`` (P1-6).

3. **Slash-command surface**: ``cancel_card(id)`` is called by the
   ``/cancel <id>`` handler in ``lib/anchors/__init__.py`` (the bare
   ``/cancel`` form stays inside the P1-1 draft-spec flow). It
   archives the card via ``kanban_db.archive_task`` and returns a
   bool so the caller can format a user-facing reply.

The Hermes Kanban DB module isn't always importable from the unit
test environment (it's a submodule + has its own dependency surface).
``_kanban_db()`` is the indirection that tests can patch.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from lib.kanban.notification_policy import status_transition_to_notification

logger = logging.getLogger(__name__)


# Default DB path. Mirrored from ``config/limits.yaml::kanban.db_path``.
# Tests don't reach this code path (they patch ``_kanban_db``), so a
# hardcoded fallback is fine for the runtime contract.
_DEFAULT_DB_PATH = "/root/.hermes/kanban/kanban.db"


def _kanban_db() -> Optional[Any]:
    """Lazy import of Hermes' ``kanban_db`` module.

    Returns ``None`` if Hermes isn't available (unit test env, submodule
    not initialised, etc.). Callers must handle the ``None`` case
    gracefully — typically by returning ``False`` / no-op.
    """
    try:
        from hermes_cli import kanban_db  # type: ignore[import-not-found]

        return kanban_db
    except ImportError:
        return None


def _open_conn(db_module: Any):
    """Open a connection via Hermes' module. Path comes from env or default."""
    db_path = os.environ.get("HERMES_KANBAN_DB", _DEFAULT_DB_PATH)
    # ``connect`` is Hermes' public entry point — it handles WAL setup +
    # migrations. The exact kwarg name has changed across pins; pass the
    # path positionally for max forward-compat (and let it raise loudly
    # if the surface ever changes again).
    try:
        return db_module.connect(db_path)
    except TypeError:
        # Some Hermes builds expect ``connect(db=path)``.
        return db_module.connect(db=db_path)


# ----------------------------------------------------------------------
# Telegram → Kanban
# ----------------------------------------------------------------------


def telegram_msg_to_card(msg: Any, user_id: str) -> Optional[str]:
    """Create a Kanban card from an inbound Telegram message.

    Returns the new task id, or ``None`` if the Hermes DB isn't
    available (test env / submodule not initialised).

    Per spec L356: **1 user message = 1 Kanban card**. The card is
    created at TaskSpec lock-time by the P1-1 anchors plugin (not on
    raw arrival), so by the time we get here ``msg.text`` is the
    locked intent — no clarification rounds left.
    """
    db = _kanban_db()
    if db is None:
        logger.debug("kanban: Hermes DB unavailable; skipping card creation")
        return None

    text = (getattr(msg, "text", "") or "").strip()
    # Telegram messages can be arbitrary length; keep the title short
    # (the Kanban /list view is one-line-per-card) and push the rest
    # to body.
    title = text[:80] if text else "(untitled Telegram message)"
    body = text if len(text) > 80 else None

    conn = _open_conn(db)
    try:
        return db.create_task(
            conn,
            title=title,
            body=body,
            created_by=str(user_id),
            triage=True,  # locked TaskSpecs land in `triage` for the
            #                dispatcher to promote on the next tick.
        )
    finally:
        # Best-effort close — some Hermes builds wrap conn in a
        # context-manager-only helper, so ignore AttributeError.
        try:
            conn.close()
        except AttributeError:
            pass


# ----------------------------------------------------------------------
# Kanban → Telegram
# ----------------------------------------------------------------------


def send_alert(card_id: Any, msg: str) -> None:
    """Send a Telegram alert for ``card_id``.

    Real implementation goes through Hermes' ``send_message`` tool /
    Telegram Bot API. In Phase 1 this is intentionally a thin wrapper
    so P1-6 (``lib/durability/escalation.py``) and this module can
    share one publish path.

    For now this logs the alert; the actual HTTP send lives in the
    gateway. The bridge can be wired into the gateway's outbound
    queue once the gateway-side hook is in place (P1-5 task 38).
    """
    logger.info("kanban: alert card=%s msg=%r", card_id, msg)


# ----------------------------------------------------------------------
# Slash-command surface
# ----------------------------------------------------------------------


def cancel_card(card_id: str) -> bool:
    """Archive a Kanban card by id. Returns True on success, False otherwise.

    Wraps Hermes' ``archive_task`` which CAS-transitions
    ``status → 'archived'`` and closes any in-flight run with
    ``outcome='reclaimed'``. Returns False if the card doesn't exist
    or is already archived.
    """
    db = _kanban_db()
    if db is None:
        logger.warning("kanban: Hermes DB unavailable; cannot cancel card %s", card_id)
        return False

    conn = _open_conn(db)
    try:
        return bool(db.archive_task(conn, card_id))
    except Exception as exc:  # noqa: BLE001 — bridge must not crash on bad input
        logger.warning("kanban: cancel_card(%s) failed: %s", card_id, exc)
        return False
    finally:
        try:
            conn.close()
        except AttributeError:
            pass


__all__ = [
    "telegram_msg_to_card",
    "status_transition_to_notification",
    "send_alert",
    "cancel_card",
]
