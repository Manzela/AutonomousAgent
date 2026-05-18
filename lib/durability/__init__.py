"""Durability plugin: failure-matrix-driven retry policy, checkpoint-resume (P1-3),
and REJECTED-inject (P1-4). P1-6 lands the real hook bodies here; P1-3 and P1-4
fill the on_session_start stubs in subsequent PRs."""

from lib.durability import failure_matrix, trichotomy, escalation

__all__ = ["register", "failure_matrix", "trichotomy", "escalation"]


def register(ctx):
    # P1-6 hooks (real implementations from this PR)
    ctx.register_hook("pre_tool_call", trichotomy.before_tool_call)
    ctx.register_hook("post_tool_call", trichotomy.after_tool_call)

    # P1-3 + P1-4 hooks (stubs; sessions c + d fill in)
    # ORDER MATTERS: resume must run first so REJECTED-inject can read active TaskSpec
    ctx.register_hook("on_session_start", _p1_3_resume_session)  # session-c fills
    ctx.register_hook("on_session_start", _p1_4_inject_rejected)  # session-d fills


def _p1_3_resume_session(ctx):
    """TODO(P1-3 session-c): on container start, scan /data/checkpoints/ for incomplete
    sessions and rehydrate the latest checkpoint per session. See lib/durability/checkpoint.py."""
    return None


def _p1_4_inject_rejected(ctx):
    """TODO(P1-4 session-d): read active TaskSpec.intent_category, load matching unexpired
    REJECTED.md entries, inject as system message: 'Past failed approaches for this kind of
    task — DO NOT repeat:'. See lib/memory/rejected.py."""
    return None
