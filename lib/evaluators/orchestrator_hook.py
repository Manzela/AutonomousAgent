"""Async judge dispatch (post_tool_call) + feedback inject (pre_llm_call).

post_tool_call is observational (Hermes contract) — judges run in a
background thread so the agent loop doesn't block on 30-90s judge panels.

When a judge panel rejects, feedback is queued per-session. The next
pre_llm_call drains the queue and prepends feedback to the prompt so
the agent sees "your last action was rejected because X" before its
next turn.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# P1 routing per design-alignment spec §P1-2
PER_AXIS_MODEL = {
    "code-correctness": "vertex_ai/claude-sonnet-4-6",
    "safety": "vertex_ai/claude-opus-4-7",
    "scope-fit": "vertex_ai/claude-sonnet-4-6",
    "completeness": "vertex_ai/gemini-3.1-pro",
}


@dataclass
class PendingFeedback:
    verdict: str  # 'accept' | 'reject' | 'fail_loud'
    reasoning: str
    axes_failed: list[str] = field(default_factory=list)


# Per-session feedback queue, guarded by lock for concurrent post_tool_call
_feedback_queue: dict[str, list[PendingFeedback]] = {}
_lock = threading.Lock()


def queue_judge_dispatch(*, session_id: str, feedback: PendingFeedback) -> None:
    """Append feedback to the session's queue. Called by the background judge runner."""
    with _lock:
        _feedback_queue.setdefault(session_id, []).append(feedback)


def drain_pending_feedback(session_id: str) -> list[PendingFeedback]:
    """Pop and return all pending feedback for a session. Called from pre_llm_call."""
    with _lock:
        return _feedback_queue.pop(session_id, [])


def format_feedback_message(items: list[PendingFeedback]) -> str:
    """Render feedback into a system message the agent will see on the next turn."""
    if not items:
        return ""
    lines = ["[evaluator] Your previous action(s) received feedback from the judge panel:"]
    for fb in items:
        if fb.verdict == "reject":
            axes = ", ".join(fb.axes_failed) if fb.axes_failed else "unspecified"
            lines.append(f"  - REJECTED ({axes}): {fb.reasoning}")
        elif fb.verdict == "fail_loud":
            lines.append(f"  - FAIL-LOUD: {fb.reasoning} (task halted)")
    lines.append("Please reconsider your approach before continuing.")
    return "\n".join(lines)
