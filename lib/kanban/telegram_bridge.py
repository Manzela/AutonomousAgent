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
   operator's Telegram chat via the Bot API. Also exposed for use by
   ``lib/durability/escalation.emit_escalation`` (P1-6).

3. **Status updates**: ``update_card_status(session_id, status)`` is
   called from the ``post_tool_call`` hook in ``lib/kanban/__init__.py``
   and propagates the new status to Hermes' Kanban DB, simultaneously
   driving the policy table to emit (or suppress) a Telegram alert.

4. **Slash-command surface**: ``cancel_card(id)`` is called by the
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
from pathlib import Path
from typing import Any, Optional

import httpx

from lib.kanban.notification_policy import status_transition_to_notification
from lib.scrubber import scrub_string

logger = logging.getLogger(__name__)


# Default DB path. Mirrored from ``config/limits.yaml::kanban.db_path``.
# The container runs as user `hermes` (uid 1000) with HOME=/home/hermes
# after the α-5 security-hardening PR. Hermes resolves the kanban DB
# via ``Path.home() / ".hermes" / "kanban.db"``, which is reachable
# at ``/home/hermes/.hermes/kanban.db`` (NOT a ``kanban/`` subdir —
# verified live with ``docker exec ... find /home/hermes -name kanban.db``).
# Tests don't reach this code path (they patch ``_kanban_db``), so a
# hardcoded fallback is fine for the runtime contract.
_DEFAULT_DB_PATH = "/home/hermes/.hermes/kanban.db"

# Telegram Bot API endpoint template. Used by ``send_alert``.
_TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
# HTTP timeout for the Telegram send. Short by design — the alert is
# best-effort; we don't want to stall the agent loop waiting on it.
_TELEGRAM_SEND_TIMEOUT_S = 10.0


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
    """Open a connection via Hermes' module. Path comes from env or default.

    Hermes' ``kanban_db.connect`` calls ``path.parent.mkdir(...)`` on the
    argument (verified at hermes_cli/kanban_db.py:919), so we wrap the
    string env-var into a ``pathlib.Path`` before passing it through.
    """
    db_path = Path(os.environ.get("HERMES_KANBAN_DB", _DEFAULT_DB_PATH))
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


def _telegram_chat_id() -> Optional[str]:
    """Resolve the operator's Telegram chat id.

    Resolution order:
    1. ``TELEGRAM_HOME_CHAT_ID`` env var (preferred — keeps the runtime
       knob out of yaml).
    2. The first entry of ``TELEGRAM_ALLOWED_USERS`` (already in
       ``secrets/telegram.env``), as a sensible default for single-user
       deployments.

    Returns ``None`` if neither is set, so ``send_alert`` can degrade
    gracefully (logs + no HTTP).
    """
    explicit = os.environ.get("TELEGRAM_HOME_CHAT_ID")
    if explicit:
        return explicit.strip()
    allowed = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    for token in allowed.split(","):
        token = token.strip()
        if token:
            return token
    return None


def send_alert(card_id: Any, msg: str) -> bool:
    """POST ``msg`` to the operator's Telegram chat for ``card_id``.

    Fail-open: any failure (missing token, network error, non-2xx) is
    logged but never raised — the agent loop must not crash because the
    bridge can't reach Telegram. The bot token is read from
    ``TELEGRAM_BOT_TOKEN`` (already injected via
    ``deploy/docker-compose.yml`` from ``secrets/telegram.env``).

    Returns ``True`` iff the Telegram API accepted the message (2xx). All
    other paths (missing token, missing chat id, HTTP/transport failure,
    unexpected exception) return ``False`` so callers can decide to fall
    through to a secondary channel (see ``lib/durability/github_fallback``
    for the F32 path that opens a GitHub issue when Telegram is silent).
    Existing call sites that ignored the previous ``None`` return remain
    correct — they continue to ignore the bool.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.info(
            "kanban: send_alert no-op (TELEGRAM_BOT_TOKEN unset) card=%s msg=%r",
            card_id,
            msg,
        )
        return False

    chat_id = _telegram_chat_id()
    if not chat_id:
        logger.info(
            "kanban: send_alert no-op (no chat id configured) card=%s msg=%r",
            card_id,
            msg,
        )
        return False

    url = _TELEGRAM_API_URL.format(token=token)
    # P2 #34: scrub the message body before send. Telegram retains chat
    # messages indefinitely, and the upstream caller may have interpolated
    # raw card title/description (which can contain pasted secrets) into
    # ``msg``. The scrubber is idempotent on already-scrubbed text.
    scrubbed_msg = scrub_string(msg, source="telegram_alert")
    payload = {
        "chat_id": chat_id,
        # Prefix with the card id so the operator can grep / quote-reply
        # without needing to copy from the body.
        "text": f"[card {card_id}] {scrubbed_msg}",
    }
    try:
        with httpx.Client(timeout=_TELEGRAM_SEND_TIMEOUT_S) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
        logger.info("kanban: send_alert ok card=%s", card_id)
        return True
    except httpx.HTTPStatusError as exc:
        # Log status code only — never log the request URL (contains bot token).
        logger.warning("kanban: send_alert HTTP %s card=%s", exc.response.status_code, card_id)
        return False
    except httpx.HTTPError as exc:
        # Log exception type only — request URL (with token) must not appear in logs.
        logger.warning(
            "kanban: send_alert HTTP transport error %s card=%s",
            type(exc).__name__,
            card_id,
        )
        return False
    except Exception as exc:  # noqa: BLE001 — bridge is fail-open
        logger.warning(
            "kanban: send_alert unexpected failure %s card=%s", type(exc).__name__, card_id
        )
        return False


def _row_to_dict(row: Any) -> dict:
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return {
        "id": row[0],
        "status": row[1],
        "title": row[2],
        "last_failure_error": row[3],
        "body": row[4],
        "consecutive_failures": row[5],
        "result": row[6] if len(row) > 6 else None,
    }


def _find_task_by_session_or_task_id(
    conn: Any, session_id: Optional[str], task_id: Optional[str] = None
) -> Optional[dict]:
    # Try by ID first
    for tid in (task_id, session_id):
        if not tid:
            continue
        try:
            row = conn.execute(
                "SELECT id, status, title, last_failure_error, body, consecutive_failures, result "
                "FROM tasks WHERE id = ?",
                (tid,),
            ).fetchone()
            if row:
                return _row_to_dict(row)
        except Exception as exc:  # noqa: BLE001
            logger.debug("kanban: task lookup by id failed tid=%s: %s", tid, exc)

    # Try by created_by
    for tid in (task_id, session_id):
        if not tid:
            continue
        try:
            row = conn.execute(
                "SELECT id, status, title, last_failure_error, body, consecutive_failures, result "
                "FROM tasks WHERE created_by = ? AND status != 'archived' "
                "ORDER BY created_at DESC LIMIT 1",
                (tid,),
            ).fetchone()
            if row:
                return _row_to_dict(row)
        except Exception as exc:  # noqa: BLE001
            logger.debug("kanban: task lookup by created_by failed tid=%s: %s", tid, exc)
    return None


def update_card_status(session_id: str, status: str) -> None:
    """Update the Kanban card for ``session_id`` to ``status``.

    Called from the ``post_tool_call`` hook in ``lib/kanban/__init__.py``
    after every tool call (``"running"`` on success, ``"blocked"`` on
    exception). The function is intentionally tolerant:

    - If the Hermes Kanban DB isn't importable (unit test env / submodule
      not initialised), the function returns silently — the hook must
      not crash because the bridge can't reach Hermes' DB.
    - If no card exists for the session yet, the underlying DB call may
      be a no-op or raise; we swallow either outcome.

    The status string must be one of Hermes' canonical enum values
    (``triage, todo, ready, running, blocked, done, archived``).
    """
    import time
    from types import SimpleNamespace

    db = _kanban_db()
    if db is None:
        logger.debug(
            "kanban: update_card_status no-op (DB unavailable) session=%s status=%s",
            session_id,
            status,
        )
        return

    conn = None
    try:
        conn = _open_conn(db)

        # 1. Retrieve current card state
        task_row = _find_task_by_session_or_task_id(conn, session_id)
        if not task_row:
            logger.debug("kanban: card not found for session=%s", session_id)
            return

        old_status = task_row["status"]
        card_id = task_row["id"]

        # 2. Touch heartbeat if status matches
        if old_status == status:
            conn.execute(
                "UPDATE tasks SET last_heartbeat_at = ? WHERE id = ?", (int(time.time()), card_id)
            )
            # Ensure the heartbeat update is committed
            try:
                conn.commit()
            except AttributeError:
                pass
            return

        # 3. Perform transition
        if status == "blocked" and hasattr(db, "block_task"):
            db.block_task(conn, card_id, reason="Tool execution failed")
        elif status == "done" and hasattr(db, "complete_task"):
            db.complete_task(conn, card_id, result="Task completed successfully")
        else:
            # Fallback path uses a transaction to update status & heartbeat atomically
            if hasattr(db, "write_txn"):
                txn_mgr = db.write_txn(conn)
            else:
                import contextlib

                @contextlib.contextmanager
                def _dummy_txn():
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        yield
                    except Exception:
                        conn.execute("ROLLBACK")
                        raise
                    else:
                        conn.execute("COMMIT")

                txn_mgr = _dummy_txn()

            with txn_mgr:
                conn.execute(
                    "UPDATE tasks SET status = ?, last_heartbeat_at = ? WHERE id = ?",
                    (status, int(time.time()), card_id),
                )

        # 4. Fetch updated card info and send alert
        updated_row = conn.execute(
            "SELECT id, status, title, last_failure_error, body, consecutive_failures, result "
            "FROM tasks WHERE id = ?",
            (card_id,),
        ).fetchone()

        if updated_row:
            updated_dict = _row_to_dict(updated_row)
            card = SimpleNamespace(
                id=updated_dict["id"],
                status=updated_dict["status"],
                title=updated_dict["title"],
                last_failure_error=updated_dict["last_failure_error"],
                body=updated_dict["body"],
                consecutive_failures=updated_dict["consecutive_failures"],
                result=updated_dict["result"],
            )
            msg = status_transition_to_notification(old_status, status, card)
            if msg:
                send_alert(card_id, msg)

    except Exception as exc:  # noqa: BLE001 — bridge is fail-open
        logger.debug(
            "kanban: update_card_status failed session=%s status=%s err=%s",
            session_id,
            status,
            exc,
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except AttributeError:
                pass


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
    "update_card_status",
    "cancel_card",
]
