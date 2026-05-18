"""P1-5 Kanban → Telegram bridge plugin.

Wires two hooks into the Hermes agent loop:

- ``pre_tool_call``: at TaskSpec lock time (signalled by the anchors
  plugin), create a Kanban card from the inbound Telegram message and
  stash the card id in session metadata so the post-call hook can
  reference it.
- ``post_tool_call``: after every tool call, inspect whether the call
  triggered a Kanban status transition. If yes, look up the policy
  and emit a Telegram notification when the policy is non-silent.

Per the audit P2-B decision (and brief): **accept Hermes' status names
verbatim** — no fork. The user-facing message phrasing can differ but
the internal status enum stays exactly as Hermes ships it
(``triage, todo, ready, running, blocked, done, archived``).

This plugin **does NOT register a ``/cancel`` slash command** — the
anchors plugin owns the ``/cancel`` command and dispatches by argument
shape (bare → draft cancel; ``<id>`` → ``cancel_card`` here).
"""

from __future__ import annotations

import logging
from typing import Any

from lib.kanban import telegram_bridge
from lib.kanban.notification_policy import status_transition_to_notification

logger = logging.getLogger(__name__)


def _on_pre_tool_call(
    tool_name: str = "",
    args: dict | None = None,
    **_: Any,
) -> dict | None:
    """At TaskSpec lock time, create a Kanban card from the inbound message.

    The anchors plugin lights up a session metadata flag when it
    transitions ``draft_locked → locked``. We check that flag here
    rather than try to re-detect the lock event from tool-call args.

    Returns ``None`` (never blocks the tool call) — card creation is
    a side-effect of the lock, not a gate.
    """
    # TODO(P1-5 follow-up): read session metadata for the lock flag and the
    # original Telegram message. For now this is wired but inert —
    # the actual card creation is driven by the anchors plugin calling
    # telegram_bridge.telegram_msg_to_card directly when it locks.
    logger.debug("kanban: pre_tool_call tool=%s", tool_name)
    return None


def _on_post_tool_call(
    tool_name: str = "",
    args: dict | None = None,
    result: Any = None,
    **_: Any,
) -> None:
    """After a tool call, check whether a Kanban status transition fired.

    Hermes records status transitions in ``task_events``; in the
    follow-up implementation this hook will tail that table for the
    session's card id and emit a Telegram message per the policy. For
    now the hook is wired but inert so the register() contract is
    fulfilled and the cross-session E2E test can stub the body.
    """
    logger.debug("kanban: post_tool_call tool=%s", tool_name)
    # TODO(P1-5 follow-up): inspect result for status-change side effects,
    # call status_transition_to_notification, send_alert on non-None.
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
