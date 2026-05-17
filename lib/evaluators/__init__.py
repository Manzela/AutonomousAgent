"""Multi-judge evaluator — P1-2 plugin entry point.

Hermes plugin: dispatches a 4-judge consensus panel after evaluation-eligible
tool calls (per `config/toolsets.yaml` `evaluate_after`). Each judge scores
against the locked TaskSpec on its assigned axis. Majority vote → accept /
reject / escalate to 5th judge. See `docs/superpowers/specs/2026-05-15-phase1-design-alignment.md`
§P1-2 for the design.

Hooks registered (Hermes plugin contract — `hermes-agent/AGENTS.md:477-479`):
- `post_tool_call`  → background dispatch of judge panel (gated by toolsets.evaluate_after)
- `pre_llm_call`    → drain pending feedback queue + inject as system message
- `on_session_end`  → flush undelivered feedback (logged for now; persisted via P1-3 in Task 27)
"""

from __future__ import annotations

import logging
from typing import Any

from lib.evaluators.orchestrator_hook import (
    drain_pending_feedback,
    format_feedback_message,
)

logger = logging.getLogger(__name__)


def _on_post_tool_call(
    tool_name: str = "",
    args: dict | None = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    **_: Any,
) -> None:
    """Dispatch judge panel asynchronously when toolset is evaluation-eligible.

    The eligibility check reads `config/toolsets.yaml` `evaluate_after` field
    via the existing toolset_router. Background dispatch happens in a thread
    so this hook returns immediately (Hermes contract: `post_tool_call` is
    observational and must not block the agent loop).

    Wiring to `toolset_router.is_evaluation_eligible()` is intentionally
    deferred to Task 21 (live integration) — the orchestrator-side dispatch
    plumbing is fully in place via `orchestrator_hook.queue_judge_dispatch`,
    callable from a background thread once the eligibility lookup lands.
    """
    return None


def _on_pre_llm_call(session_id: str = "", messages: list | None = None, **_: Any) -> None:
    """Drain the feedback queue and prepend judge feedback to the prompt."""
    feedback = drain_pending_feedback(session_id)
    if not feedback:
        return
    msg = format_feedback_message(feedback)
    if messages is not None and msg:
        # Inject as a system-role message at the start of the next turn so
        # the agent sees "your last action was rejected because X" before
        # generating its next response.
        messages.insert(0, {"role": "system", "content": msg})
        logger.info("Injected %d feedback item(s) for session=%s", len(feedback), session_id)


def _on_session_end(session_id: str = "", **_: Any) -> None:
    """Flush any remaining feedback to checkpoint (P1-3 will read this)."""
    remaining = drain_pending_feedback(session_id)
    if remaining:
        logger.warning(
            "Session %s ended with %d undelivered feedback items",
            session_id,
            len(remaining),
        )


def register(ctx: Any) -> None:
    """Hermes plugin entry — wire the 3 lifecycle hooks."""
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("on_session_end", _on_session_end)
