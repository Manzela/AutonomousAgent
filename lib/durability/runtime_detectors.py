"""Runtime detectors for F-LOOP (F34) and F-STALL (F35).

These run inside the orchestrator's per-tool-call lifecycle (LoopDetector)
and a periodic watchdog (StallDetector). Each returns the F-code string on
firing so the caller can hand it to ``lib.durability.handlers.dispatch``.

Both detectors are thread-safe (per-session state guarded by a single mutex)
and idempotent on reset — callers can call ``reset(session_id)`` at the end
of a session without checking whether state exists.

Configuration (read at construction; the orchestrator wires these from
``config/limits.yaml → durability.loop_detector`` / ``durability.stall_detector``):
- ``LoopDetector.threshold``: consecutive identical-fingerprint count that
  fires F34. Default 5 — matches the audit-plan §J4 recommendation.
- ``StallDetector.idle_timeout_s``: wall-clock idle seconds while a task is
  in_progress before F35 fires. Default 300 (5 min).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_LOOP_THRESHOLD = 5
DEFAULT_STALL_IDLE_TIMEOUT_S = 300


def _fingerprint(tool_name: str, args: dict | None) -> str:
    """Stable sha256 of (tool_name, canonical-JSON args).

    Args are canonicalized (sort_keys + ensure_ascii) so semantically-equal
    payloads with different key ordering hash the same.
    """
    canonical = json.dumps(args or {}, sort_keys=True, ensure_ascii=True, default=str)
    h = hashlib.sha256()
    h.update(tool_name.encode("utf-8"))
    h.update(b"\x00")
    h.update(canonical.encode("utf-8"))
    return h.hexdigest()


@dataclass
class _LoopState:
    last_fingerprint: Optional[str] = None
    consecutive: int = 0


class LoopDetector:
    """F-LOOP (F34) detector — fires on N consecutive identical-fingerprint calls.

    Usage (called from a ``post_tool_call`` hook):
        f_code = detector.record_tool_call(
            session_id=ctx.session_id,
            tool_name=tool_name,
            args=args,
        )
        if f_code:
            dispatch(f_code, session_id=ctx.session_id, ...)

    The counter is **reset on detection** so the orchestrator gets one
    F-LOOP signal per loop episode rather than one per repeated call after
    the threshold — letting the feedback inject actually take effect before
    the next signal fires.
    """

    def __init__(self, threshold: int = DEFAULT_LOOP_THRESHOLD):
        if threshold < 2:
            raise ValueError(f"loop threshold must be >= 2, got {threshold}")
        self.threshold = threshold
        self._sessions: dict[str, _LoopState] = {}
        self._lock = threading.Lock()

    def record_tool_call(
        self, *, session_id: str, tool_name: str, args: dict | None = None
    ) -> Optional[str]:
        """Record one tool call; return ``"F34"`` if loop threshold tripped."""
        fp = _fingerprint(tool_name, args)
        with self._lock:
            state = self._sessions.setdefault(session_id, _LoopState())
            if state.last_fingerprint == fp:
                state.consecutive += 1
            else:
                state.last_fingerprint = fp
                state.consecutive = 1
            if state.consecutive >= self.threshold:
                logger.info(
                    "runtime_detectors.LoopDetector F34 fired session=%s tool=%s "
                    "consecutive=%d threshold=%d",
                    session_id,
                    tool_name,
                    state.consecutive,
                    self.threshold,
                )
                # Reset so the next firing requires a fresh threshold-run.
                state.consecutive = 0
                state.last_fingerprint = None
                return "F34"
        return None

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def snapshot(self, session_id: str) -> tuple[Optional[str], int]:
        """Test/diagnostic helper: returns (last_fingerprint, consecutive)."""
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return (None, 0)
            return (state.last_fingerprint, state.consecutive)


@dataclass
class _StallState:
    last_activity_s: float
    task_in_progress: bool = True


class StallDetector:
    """F-STALL (F35) detector — fires when a session has been idle past timeout.

    The orchestrator calls ``record_activity`` on every tool call (or any
    other signal of life) and ``check`` periodically from a watchdog. While
    ``task_in_progress`` is True and now - last_activity exceeds the timeout,
    ``check`` returns ``"F35"``.

    Set ``task_in_progress=False`` (via ``set_task_state``) when the agent
    has explicitly halted/completed — we don't want to fire F-STALL on
    legitimately idle sessions waiting for the next user prompt.
    """

    def __init__(
        self,
        idle_timeout_s: int = DEFAULT_STALL_IDLE_TIMEOUT_S,
        *,
        clock=time.monotonic,
    ):
        if idle_timeout_s < 1:
            raise ValueError(f"idle_timeout_s must be >= 1, got {idle_timeout_s}")
        self.idle_timeout_s = idle_timeout_s
        self._clock = clock
        self._sessions: dict[str, _StallState] = {}
        self._lock = threading.Lock()
        self._fired: set[str] = set()

    def record_activity(self, *, session_id: str) -> None:
        """Mark the session as live as of now()."""
        with self._lock:
            existing = self._sessions.get(session_id)
            in_progress = existing.task_in_progress if existing else True
            self._sessions[session_id] = _StallState(
                last_activity_s=self._clock(),
                task_in_progress=in_progress,
            )
            self._fired.discard(session_id)

    def set_task_state(self, *, session_id: str, in_progress: bool) -> None:
        """Toggle whether F-STALL should fire for this session."""
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                self._sessions[session_id] = _StallState(
                    last_activity_s=self._clock(), task_in_progress=in_progress
                )
            else:
                state.task_in_progress = in_progress
            if not in_progress:
                # If the agent halted, don't re-fire on subsequent checks.
                self._fired.discard(session_id)

    def check(self, *, session_id: str) -> Optional[str]:
        """Return ``"F35"`` if the session has been idle past timeout, else None.

        Fires at most once per idle episode — a subsequent ``record_activity``
        re-arms the detector.
        """
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or not state.task_in_progress:
                return None
            if session_id in self._fired:
                return None
            elapsed = self._clock() - state.last_activity_s
            if elapsed > self.idle_timeout_s:
                logger.warning(
                    "runtime_detectors.StallDetector F35 fired session=%s "
                    "elapsed_s=%.1f timeout_s=%d",
                    session_id,
                    elapsed,
                    self.idle_timeout_s,
                )
                self._fired.add(session_id)
                return "F35"
        return None

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._fired.discard(session_id)
