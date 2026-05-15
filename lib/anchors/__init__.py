"""TaskSpec + clarification loop — P1-1 plugin entry point."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _on_session_start(session_id: str = "", **_: Any) -> None:
    """Load the active spec for the session if one exists.

    Resolution order: session_metadata.active_spec_id → most-recent locked
    spec for the user → no active spec (fresh slate).
    """
    # TODO(P1-1 task 6): wire to session metadata loader once limits.yaml
    # anchors.spec_storage_dir is read by the plugin
    logger.debug("anchors: on_session_start fired session=%s", session_id)


def _on_pre_tool_call(
    tool_name: str = "",
    args: dict | None = None,
    **_: Any,
) -> dict | None:
    """Drive the clarification loop on the first user-message-style tool call.

    If no active spec is locked AND the inbound message looks like a project
    intent, redirect the agent into the clarification loop instead of letting
    the tool run. Returns a block dict to short-circuit, or None to allow.
    """
    # TODO(P1-1 task 6): wire heuristic + state machine integration
    return None


def _slash_lock(raw_args: str) -> str:
    """`/lock` — force-lock the current draft spec."""
    return "TODO(P1-1 task 6): force-lock the active draft TaskSpec."


def _slash_skip(raw_args: str) -> str:
    """`/skip` — skip the current clarification question (counts toward budget)."""
    return "TODO(P1-1 task 6): mark current question as skipped."


def _slash_cancel(raw_args: str) -> str:
    """`/cancel` (no arg) — abandon the current draft spec.

    With an argument it's the P1-5 card-cancel command; the kanban plugin
    handles that case. Argument-presence dispatch happens at the bridge layer.
    """
    if raw_args.strip():
        return "TODO(P1-5): /cancel <id> handled by kanban plugin."
    return "TODO(P1-1 task 6): abandon the current draft TaskSpec."


def _slash_confirm(raw_args: str) -> str:
    """`/confirm` — accept the current draft_locked spec → locked."""
    return "TODO(P1-1 task 6): transition draft_locked → locked."


def _setup_new_cli(subparser) -> None:
    """`hermes new <intent>` — operator-side spec creation (CLI, not Telegram)."""
    subparser.add_argument("intent", help="Free-form intent string for the new TaskSpec.")


def _handle_new_cli(args) -> int:
    """Handler for `hermes new <intent>`."""
    print(f"TODO(P1-1 task 6): create draft TaskSpec for intent: {args.intent}")
    return 0


def register(ctx) -> None:
    """Plugin entry point — wires hooks + slash commands + CLI subcommand."""
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_command(
        "lock", handler=_slash_lock, description="Force-lock the active draft TaskSpec."
    )
    ctx.register_command(
        "skip", handler=_slash_skip, description="Skip the current clarification question."
    )
    ctx.register_command(
        "cancel",
        handler=_slash_cancel,
        description="Abandon the active draft (no arg) or cancel a card (with id, P1-5).",
    )
    ctx.register_command(
        "confirm", handler=_slash_confirm, description="Confirm a draft_locked TaskSpec → locked."
    )
    ctx.register_cli_command(
        name="new",
        help="Create a draft TaskSpec from an intent string (operator-side).",
        setup_fn=_setup_new_cli,
        handler_fn=_handle_new_cli,
        description="Operator-side TaskSpec creation. Telegram-side equivalent is implicit (any non-slash inbound message).",
    )
