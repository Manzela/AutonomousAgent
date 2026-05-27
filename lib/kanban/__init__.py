"""P1-5 Kanban → Telegram bridge plugin.

Wires two hooks into the Hermes agent loop:

- ``pre_tool_call``: on the FIRST tool call of each new session, create
  a Kanban card via ``telegram_bridge.telegram_msg_to_card``. Tracked
  in a module-level set so subsequent tool calls in the same session
  don't create duplicate cards.
- ``post_tool_call``: after every tool call, update the session's card
  status — ``"running"`` on success, ``"blocked"`` on exception. The
  bridge converts these into Telegram notifications via
  ``notification_policy.status_transition_to_notification``.

Per the audit P2-B decision (and brief): **accept Hermes' status names
verbatim** — no fork. The user-facing message phrasing can differ but
the internal status enum stays exactly as Hermes ships it
(``triage, todo, ready, running, blocked, done, archived``).

This plugin **does NOT register a ``/cancel`` slash command** — the
anchors plugin owns the ``/cancel`` command and dispatches by argument
shape (bare → draft cancel; ``<id>`` → ``cancel_card`` here).

Hook signatures match the kwargs Hermes' ``invoke_hook`` passes (see
``hermes-agent/hermes_cli/plugins.py`` + the merged ``trichotomy.py``
reference). All callbacks absorb unknown kwargs via ``**_`` and return
``None`` so the per-hook try/except in ``invoke_hook`` stays fail-open.
"""

from __future__ import annotations

import logging
import threading
from types import SimpleNamespace
from typing import Any, Dict, Optional, Set

from lib.kanban import telegram_bridge
from lib.kanban.notification_policy import status_transition_to_notification

logger = logging.getLogger(__name__)


# Per-session card creation happens at-most once per session_id. The
# threading.Lock guards the seen-set (Hermes processes turns sequentially
# per session, but the gateway can interleave sessions on the same process).
_LOCK = threading.Lock()
_SEEN_SESSIONS: Set[str] = set()


class BudgetExhaustedError(Exception):
    """Raised when the daily LiteLLM budget is exhausted (CC-6 Sentinel)."""

    pass


def _on_pre_tool_call(
    tool_name: Optional[str] = None,
    args: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    **_: Any,
) -> None:
    """Create a Kanban card on the first tool call of each new session.

    Phase 1.0.1 heuristic: 1 session = 1 card. We don't try to re-detect
    the TaskSpec-lock event from the tool-call surface — the anchors
    plugin can still call ``telegram_bridge.telegram_msg_to_card``
    directly when it transitions ``draft_locked → locked``; this hook
    is the catch-all so cards are created even for ad-hoc sessions
    that bypass the anchors lock flow (e.g. ``hermes -z "..."``).

    Returns ``None`` — card creation is a side-effect; we never block a
    tool call on it.
    """
    import os

    if os.path.exists("/data/HALT_F21"):
        raise BudgetExhaustedError(
            "Agent halted: Daily LiteLLM budget exhausted. See /data/HALT_F21"
        )

    if not session_id:
        return None

    with _LOCK:
        if session_id in _SEEN_SESSIONS:
            return None
        _SEEN_SESSIONS.add(session_id)

    try:
        # At the tool-call site we don't have access to the inbound Telegram
        # message, so synthesize a minimal stand-in: the title is a short
        # scannable marker tying the card back to the session.
        title = f"Session {session_id[:12]}: {tool_name or 'tool call'}"
        msg = SimpleNamespace(text=title, message_id=tool_call_id or session_id)
        card_id = telegram_bridge.telegram_msg_to_card(msg, user_id=str(task_id or session_id))
        logger.debug(
            "kanban: pre_tool_call created card=%s session=%s tool=%s",
            card_id,
            session_id,
            tool_name,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open per hook contract
        logger.debug("kanban: pre_tool_call card creation failed: %s", exc)
    return None


def _on_post_tool_call(
    tool_name: Optional[str] = None,
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    duration_ms: Optional[int] = None,
    **_: Any,
) -> None:
    """Update the session's card status based on tool-call outcome.

    - ``result`` is an ``Exception`` → status ``"blocked"`` (the
      notification policy maps ``running → blocked`` to the
      priority-alert ``_blocked`` renderer).
    - Otherwise → status ``"running"`` (the policy maps
      ``ready → running`` to silent / OTel-heartbeat-only, so this is a
      cheap no-op when nothing has changed but still keeps the
      ``last_heartbeat_at`` column fresh so the 24h watcher in
      ``lib/durability/escalation.py`` doesn't false-escalate).

    Defensive: if no card has been created for this session yet (e.g.
    because card creation failed in pre_tool_call), the bridge call is
    a no-op via ``update_card_status``'s own DB-availability check.
    """
    if not session_id:
        return None

    status = "blocked" if isinstance(result, Exception) else "running"
    try:
        telegram_bridge.update_card_status(session_id=session_id, status=status)
        logger.debug(
            "kanban: post_tool_call session=%s tool=%s status=%s",
            session_id,
            tool_name,
            status,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open per hook contract
        logger.debug("kanban: post_tool_call status update failed: %s", exc)
    return None


def register(ctx: Any) -> None:
    """Plugin entry point — wires the bridge hooks.

    No slash commands here. ``/cancel`` is owned by the anchors plugin
    and dispatches by argument shape to ``telegram_bridge.cancel_card``.
    """
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)


__all__ = [
    "register",
    "telegram_bridge",
    "status_transition_to_notification",
]
