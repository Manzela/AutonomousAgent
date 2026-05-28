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
    """Dispatch judge panel asynchronously when toolset is evaluation-eligible."""
    try:
        from pathlib import Path
        from lib.toolset_router import ToolsetRouter

        cfg_path = Path(__file__).resolve().parents[2] / "config" / "toolsets.yaml"
        router = ToolsetRouter.from_config(cfg_path)
        if not router.is_evaluation_eligible(tool_name):
            return
    except Exception as exc:
        logger.warning("evaluators: failed to check evaluation eligibility: %s", exc)
        return

    def _runner() -> None:
        import asyncio
        from lib.evaluators import judge_panel
        from lib.evaluators.orchestrator_hook import PendingFeedback, queue_judge_dispatch
        from lib.evaluators.judge_events import record_consensus_event
        from lib.anchors import _get_spec_store, _most_recent_draft

        try:
            worker_action = {"tool": tool_name, "args": args, "result": result}
            # Create an explicit new event loop for this daemon thread so we
            # never collide with a running loop in the spawning thread.
            loop = asyncio.new_event_loop()
            try:
                consensus_result = loop.run_until_complete(judge_panel.evaluate(worker_action))
            finally:
                loop.close()

            fb = PendingFeedback(
                verdict=consensus_result.verdict,
                reasoning=consensus_result.rationale,
                axes_failed=[j.axis for j in consensus_result.judges if j.verdict == "reject"],
            )
            queue_judge_dispatch(session_id=session_id, feedback=fb)

            # Record the consensus event
            store = _get_spec_store()
            spec = _most_recent_draft(store, statuses=("locked",))
            spec_id = str(spec.spec_id) if spec else "unknown"

            # Create a one-line summary
            summary = f"{tool_name}({str(args)[:100]}) -> {str(result)[:100]}"
            record_consensus_event(
                result=consensus_result,
                session_id=session_id,
                task_spec_id=spec_id,
                worker_action_summary=summary,
            )

            if consensus_result.verdict == "reject":
                from lib.evaluators.consensus import record_rejection_for_fingerprint
                import json
                import hashlib

                fp_data = [{"tool": tool_name, "first_arg": str(args)[:80]}]
                fp = hashlib.sha256(json.dumps(fp_data, sort_keys=True).encode("utf-8")).hexdigest()

                intent_category = getattr(spec, "intent_category", "unknown") if spec else "unknown"

                record_rejection_for_fingerprint(
                    session_id=session_id,
                    approach_fingerprint=fp,
                    approach_summary=summary,
                    taskspec_id=spec_id,
                    intent_category=intent_category,
                    why_failed=consensus_result.rationale,
                    alternatives="Please reconsider your approach.",
                )
        except Exception as exc:
            logger.error("Judge panel runner failed: %s", exc)

    import threading

    t = threading.Thread(target=_runner, name=f"judge-panel-{session_id}-{task_id}", daemon=True)
    t.start()


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
