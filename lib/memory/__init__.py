"""P1-4 memory plugin — REJECTED.md slash commands.

Registers ``/forget`` (with `id:<id>` dispatch on argument) and
``/rejections`` slash-command handlers. The actual Telegram bridging
lives in P1-5 (session-e). For now the handlers log + return a
short string the bridge can echo back.

This plugin **intentionally does not register an ``on_session_start``
hook**: the REJECTED-inject flow lives inside
``lib.durability.__init__.py`` so its order vs. P1-3's resume hook is
controlled by call sequence (design-alignment spec L332).
"""

from __future__ import annotations

import logging
from typing import Any

from lib.memory import rejected

logger = logging.getLogger(__name__)


def _slash_forget(raw_args: str) -> str:
    """`/forget <pattern>` or `/forget id:<id>`.

    Removes matching entries from REJECTED.md. Argument shape decides
    pattern-vs-id mode (handled inside ``rejected.forget``).
    """
    arg = (raw_args or "").strip()
    if not arg:
        return "Usage: /forget <pattern>  or  /forget id:<id>"
    try:
        removed = rejected.forget(arg)
    except Exception as exc:  # noqa: BLE001 — bridge mustn't crash on bad input
        logger.warning("memory: /forget failed for %r: %s", arg, exc)
        return f"/forget failed: {exc}"
    logger.info("memory: /forget %r removed %d entries", arg, removed)
    if removed == 0:
        return f"No REJECTED entries matched {arg!r}."
    return f"Removed {removed} REJECTED entr{'y' if removed == 1 else 'ies'} matching {arg!r}."


def _slash_rejections(raw_args: str) -> str:  # noqa: ARG001 — bridge signature
    """`/rejections` — list active REJECTED entries (newest 10)."""
    try:
        lines = rejected.list_active()
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory: /rejections failed: %s", exc)
        return f"/rejections failed: {exc}"
    if not lines:
        return "No active REJECTED entries."
    return "Active REJECTED entries:\n" + "\n".join(lines)


def register(ctx: Any) -> None:
    """Plugin entry point — wires only the two slash commands.

    Per design-alignment spec L330-332, this plugin does NOT register an
    ``on_session_start`` hook. Inject ordering is owned by
    ``lib.durability.__init__.py`` so resume runs before inject.
    """
    ctx.register_command(
        "forget",
        handler=_slash_forget,
        description="Remove REJECTED.md entries by pattern or `id:<id>`.",
    )
    ctx.register_command(
        "rejections",
        handler=_slash_rejections,
        description="List active REJECTED.md entries (newest 10).",
    )


__all__ = ["register", "rejected"]
