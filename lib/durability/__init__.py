"""Durability plugin: failure-matrix-driven retry policy, checkpoint-resume (P1-3),
and REJECTED-inject (P1-4). P1-6 lands the real hook bodies here; P1-3 and P1-4
fill the on_session_start stubs in subsequent PRs.

All hook callbacks use the ``**kwargs`` Hermes contract (see
``hermes-agent/hermes_cli/plugins.py:1253`` — ``invoke_hook`` calls ``cb(**kwargs)``).
Hermes passes ``on_session_start`` kwargs ``session_id``, ``model``, ``platform`` — NOT
``ctx``. Previously these stubs declared a positional ``ctx`` arg and every invocation
raised ``TypeError("got an unexpected keyword argument 'session_id'")`` which was
silently swallowed at WARN level. This file now mirrors ``lib/observability/__init__.py``
which got the kwargs contract right from day one (PR #52).
"""

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from lib.durability import failure_matrix, trichotomy, escalation, checkpoint, resume

__all__ = ["register", "failure_matrix", "trichotomy", "escalation", "checkpoint", "resume"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# P1-3 per-session step accounting (used by _p1_3_checkpoint_on_tool_call)
# ---------------------------------------------------------------------------
# A simple in-memory step counter keyed by session_id. Hermes' tool dispatcher
# does not expose a step number on the ``post_tool_call`` hook surface, so we
# track our own monotonic counter per live session. Lock-protected so the
# counter is safe under any future thread-pool-based hook dispatch (today
# Hermes invokes hooks serially in the asyncio loop, but the contract is
# kwargs-only — nothing prohibits parallel dispatch in a future build).
_session_step_counter: Dict[str, int] = {}
_session_step_lock = threading.Lock()

# Capped rolling history of (tool_name, tool_call_id, duration_ms, timestamp)
# per session. Persisted into each checkpoint so a post-restart resume can
# reconstruct the last N tool calls without a database query. Capped to bound
# memory growth on long-running sessions.
_recent_tool_history: Dict[str, List[Dict[str, Any]]] = {}
_RECENT_HISTORY_MAX = 20

# Default root for checkpoint files. Lives on the bind-mounted ``/data`` volume
# so checkpoints survive ``docker compose up --force-recreate``. The actual
# ``Checkpoint`` writer enforces interval/retention via config/limits.yaml.
_CHECKPOINT_ROOT = Path("/data/checkpoints")


def register(ctx):
    # P1-6 hooks (real implementations from this PR)
    ctx.register_hook("pre_tool_call", trichotomy.before_tool_call)
    ctx.register_hook("post_tool_call", trichotomy.after_tool_call)
    # P1-3 (PR α-2): wire Checkpoint.maybe_write into the live tool-call flow.
    # MUST register AFTER trichotomy.after_tool_call so trichotomy classifies
    # the error first (and emits its OTel span) before we durably checkpoint.
    ctx.register_hook("post_tool_call", _p1_3_checkpoint_on_tool_call)

    # P1-3 + P1-4 hooks (stubs; sessions c + d fill in)
    # ORDER MATTERS: resume must run first so REJECTED-inject can read active TaskSpec
    ctx.register_hook("on_session_start", _p1_3_resume_session)  # session-c fills
    ctx.register_hook("on_session_start", _p1_4_inject_rejected)  # session-d fills


def _p1_3_checkpoint_on_tool_call(
    tool_name: Optional[str] = None,
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tool_call_id: Optional[str] = None,
    duration_ms: Optional[float] = None,
    **_: Any,
) -> None:
    """P1-3 hook: write a per-session checkpoint every N tool calls.

    Hermes ``post_tool_call`` invoke_hook kwargs (see ``hermes-agent/model_tools.py``):
    ``tool_name``, ``args``, ``result``, ``task_id``, ``session_id``,
    ``tool_call_id``, ``duration_ms``. Unknown future kwargs are absorbed by
    ``**_`` for forward-compatibility.

    Why a separate hook (not folded into ``trichotomy.after_tool_call``)?
    Separation of concerns: trichotomy classifies errors and emits a
    ``durability.classify`` span; checkpointing is orthogonal and runs on the
    success path too. Hermes' ``invoke_hook`` runs registered callbacks
    sequentially per hook name, so registering this *after* trichotomy
    guarantees the error has been classified before we snapshot state.

    Behaviour:
    - Increment a per-session step counter (lock-protected).
    - Append the current tool call to a capped rolling history.
    - Instantiate ``Checkpoint`` and call ``maybe_write(step, state)`` — the
      writer itself enforces the ``durability.checkpoint.interval_steps``
      cadence (default 5) and rolling retention.

    Fail-open: missing ``session_id`` → no-op (defensive — some internal tool
    paths in Hermes synthesize tool calls without a session). Any exception
    raised by the underlying ``Checkpoint.write`` is caught and logged at
    DEBUG so a single bad write (e.g. transient ENOSPC) cannot crash the
    agent loop — the trichotomy classifier handles F28 (disk-full) separately
    via its own classify span on the next tool failure.
    """
    if not session_id:
        return None

    with _session_step_lock:
        step = _session_step_counter.get(session_id, 0) + 1
        _session_step_counter[session_id] = step

        history = _recent_tool_history.setdefault(session_id, [])
        history.append(
            {
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "duration_ms": duration_ms,
                "timestamp": time.time(),
            }
        )
        # Cap the history in place so other readers see the bounded version.
        if len(history) > _RECENT_HISTORY_MAX:
            del history[: len(history) - _RECENT_HISTORY_MAX]
        current_history = list(history)

    try:
        # Local import keeps the unit suite hermetic and matches the pattern
        # established by the P1-4 _p1_4_inject_rejected stub above.
        from lib.durability.checkpoint import Checkpoint

        cp = Checkpoint(
            session_id=session_id,
            # task_id isn't always populated by Hermes (some tool calls
            # synthesize an empty string); fall back to a session-derived
            # value so Checkpoint always has a non-empty taskspec_id field.
            taskspec_id=task_id or f"session-{session_id}",
            root_dir=_CHECKPOINT_ROOT,
        )
        state: Dict[str, Any] = {
            "session_id": session_id,
            "task_id": task_id,
            "last_tool_name": tool_name,
            "last_tool_call_id": tool_call_id,
            "recent_tool_history": current_history,
        }
        cp.maybe_write(step=step, state=state)
    except Exception as exc:  # noqa: BLE001 — never block the agent loop
        logger.debug(
            "P1-3 checkpoint write failed for session %s step %d: %s",
            session_id,
            step,
            exc,
        )
    return None


def _p1_3_resume_session(**kwargs: Any) -> None:
    """P1-3 (session-c): on container start, scan /data/checkpoints/ for incomplete
    sessions and rehydrate the latest checkpoint per session.

    Hermes ``on_session_start`` kwargs: ``session_id``, ``model``, ``platform``
    (see ``hermes-agent/run_agent.py`` ``_invoke_hook("on_session_start", ...)``).
    Unknown future kwargs are absorbed by the ``**kwargs`` signature.

    Delegates to ``lib.durability.resume.rehydrate_latest_for_session`` which:
    - honours ``durability.checkpoint.autoresume_enabled`` in config/limits.yaml,
    - skips sessions marked DONE (via ``.done`` sentinel),
    - walks back from the highest-step file on corruption (skip_and_warn),
    - returns ``None`` when there's nothing to resume (the common case on a
      fresh box, where ``/data/checkpoints/`` does not exist).

    Hermes does NOT pass a ``ctx`` object through ``on_session_start``. The
    underlying ``rehydrate_latest_for_session`` accepts ``ctx=None`` (it's currently
    only used as a sentinel) so we pass ``None``. Session-c will swap this for a
    real ctx source once Hermes exposes one — until then ``ctx`` is unused inside
    ``resume.rehydrate_latest_for_session`` so no behavioural regression.
    """
    return resume.rehydrate_latest_for_session(ctx=None)


def _p1_4_inject_rejected(**kwargs: Any) -> None:
    """P1-4 (session-d): read active TaskSpec.intent_category, load matching unexpired
    REJECTED.md entries, inject as system message: 'Past failed approaches for this kind of
    task — DO NOT repeat:'. See ``lib.memory.rejected``.

    Hermes ``on_session_start`` kwargs: ``session_id``, ``model``, ``platform``
    (see ``hermes-agent/run_agent.py``). Unknown future kwargs are absorbed by ``**kwargs``.

    Local imports avoid a top-line import conflict with the P1-3 line that this
    session must not touch. The function never raises — any failure (no active
    spec, REJECTED.md missing, classifier down) silently no-ops so a memory
    fault can't block session start.

    Hermes' ``on_session_start`` invocation does NOT include a ``ctx`` object today
    (verified in ``hermes-agent/run_agent.py``); the TaskSpec/inject_message surface
    referenced below comes from the not-yet-stable plugin context object. Until
    Hermes exposes it on the hook surface, this stub no-ops gracefully — ``ctx``
    is resolved from ``kwargs.get('ctx')`` to remain forward-compatible once
    session-e (P1-5) firms up the contract.
    """
    # Local imports — see docstring re: avoiding top-line conflict.
    from lib.memory import rejected as _rej

    session_id = kwargs.get("session_id")
    if not session_id:
        return None

    try:
        from lib.anchors import _get_spec_store, _most_recent_draft

        store = _get_spec_store()
        spec = _most_recent_draft(store, statuses=("draft", "draft_locked", "locked"))

        if spec is None:
            return None

        # TaskSpec.intent_category is set at lock-time (P1-1). If absent,
        # classify on the fly using the cached classifier.
        category = getattr(spec, "intent_category", None) or "unknown"
        if category == "unknown" and hasattr(spec, "intent"):
            # We don't have a direct LLM handle here anymore; fallback to "unknown"
            # or could initialize a simple intent router. We'll leave it as unknown
            # if we can't classify, but in practice locked specs have it.
            pass

        # Read the per-session cap from limits.yaml; fall back to module default.
        max_inject = _rej.DEFAULT_MAX_INJECT
        try:
            import yaml  # local import; keeps the unit suite hermetic

            cfg_path = (
                __import__("pathlib").Path(__file__).resolve().parents[2] / "config" / "limits.yaml"
            )
            if cfg_path.exists():
                cfg = yaml.safe_load(cfg_path.read_text()) or {}
                max_inject = int(
                    (cfg.get("memory") or {}).get(
                        "rejected_max_inject_per_session", _rej.DEFAULT_MAX_INJECT
                    )
                )
        except Exception:  # noqa: BLE001
            pass

        entries = _rej.load_active_entries(intent_category=category, max_entries=max_inject)
        if not entries:
            return None

        body_lines = [
            "Past failed approaches for this kind of task — DO NOT repeat:",
            "",
        ]
        for e in entries:
            body_lines.append(f"- [{e.get('id', '?')}] {e.get('approach_summary', '')}")
            why = e.get("why_failed", "")
            if why:
                body_lines.append(f"  why_failed: {why}")
            alt = e.get("alternatives", "")
            if alt:
                body_lines.append(f"  alternatives: {alt}")
        message = "\n".join(body_lines)

        from lib.evaluators.orchestrator_hook import queue_judge_dispatch, PendingFeedback

        queue_judge_dispatch(
            session_id=session_id, feedback=PendingFeedback(verdict="reject", reasoning=message)
        )
        return None
    except Exception as exc:  # noqa: BLE001 — never block session start
        import logging

        logging.getLogger(__name__).warning("P1-4 REJECTED inject failed (non-fatal): %s", exc)
        return None
