"""Runtime detectors for F-LOOP (F34), F-STALL (F35), and F-CONTEXT (F36).

These run inside the orchestrator's per-tool-call lifecycle (LoopDetector),
a periodic watchdog (StallDetector), and the post-model-call hook
(ContextUsageDetector). Each returns the F-code string on firing so the
caller can hand it to ``lib.durability.handlers.dispatch``.

All detectors are thread-safe (per-session state guarded by a single mutex)
and idempotent on reset — callers can call ``reset(session_id)`` at the end
of a session without checking whether state exists.

Configuration (read at construction; the orchestrator wires these from
``config/limits.yaml → durability.{loop,stall,context}_detector``):
- ``LoopDetector.threshold``: consecutive identical-fingerprint count that
  fires F34. Default 5 — matches the audit-plan §J4 recommendation.
- ``StallDetector.idle_timeout_s``: wall-clock idle seconds while a task is
  in_progress before F35 fires. Default 300 (5 min).
- ``ContextUsageDetector.warn_threshold``: prompt-tokens / context_length
  ratio that fires F36. Default 0.9 — chosen as a "compaction-already-failed"
  signal; upstream Hermes' context_compressor triggers at 0.5, so a 0.9
  reading means compaction either fired and got rolled back by the
  anti-thrashing guard, or never ran.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_LOOP_THRESHOLD = 5
DEFAULT_STALL_IDLE_TIMEOUT_S = 300
# F-CONTEXT default warn threshold (prompt_tokens / context_length).
# Above this ratio compaction is presumed ineffective — see module docstring.
DEFAULT_CONTEXT_WARN_THRESHOLD = 0.9

# OTel metric instrument for context-window pressure. Name + unit defined
# by the audit-plan (J9) and the gap-analysis source:
#     audit/2026-05-20-architecture-research-gap-analysis/source.md:800
# Lazily created on first ``record_usage`` so importing this module does
# NOT require the OTel metrics SDK to be present (the unit test suite
# runs without it). The setter is module-level + guarded by a lock so
# the first record_usage call wins the race even under concurrent
# threads.
_GAUGE_NAME = "agent.memory.context_usage_pct"
_GAUGE_UNIT = "1"
_GAUGE_DESCRIPTION = (
    "Ratio of prompt_tokens to model context_length per session "
    "(0.0–1.0). Source: ContextUsageDetector.record_usage."
)
_context_usage_gauge: Any = None
_gauge_init_lock = threading.Lock()
# Cached "tried and failed" flag — if the SDK isn't importable we don't
# want to retry on every record_usage call (would spam the logger).
_gauge_init_failed = False


def _get_context_usage_gauge() -> Any:
    """Lazy initializer for the ``agent.memory.context_usage_pct`` Gauge.

    Returns the cached instrument on subsequent calls. Returns ``None``
    when the OpenTelemetry metrics SDK is unavailable — callers MUST
    null-check before calling ``.set(...)``.

    Why sync ``Gauge`` (not ``ObservableGauge``)? Values arrive event-
    driven from ``ContextUsageDetector.record_usage``, so a sync gauge
    lets us record the moment new data is available. ``ObservableGauge``
    would force a polling callback that re-reads ``self._sessions`` and
    add a collection-interval delay.

    Requires opentelemetry-api >= 1.27 (sync ``Gauge`` was added there).
    Container builds pin >= 1.27 in ``deploy/Dockerfile.hermes``.
    """
    global _context_usage_gauge, _gauge_init_failed

    if _context_usage_gauge is not None:
        return _context_usage_gauge
    if _gauge_init_failed:
        return None

    with _gauge_init_lock:
        # Re-check under the lock (someone else may have just succeeded).
        if _context_usage_gauge is not None:
            return _context_usage_gauge
        if _gauge_init_failed:
            return None

        try:
            from opentelemetry import metrics

            meter = metrics.get_meter("hermes.durability.runtime_detectors")
            _context_usage_gauge = meter.create_gauge(
                name=_GAUGE_NAME,
                unit=_GAUGE_UNIT,
                description=_GAUGE_DESCRIPTION,
            )
            return _context_usage_gauge
        except Exception as exc:  # noqa: BLE001 — defensive; never let gauge wiring break the detector
            logger.warning(
                "runtime_detectors: gauge %s init failed (%s); F36 detector "
                "still active but metric emission disabled.",
                _GAUGE_NAME,
                exc,
            )
            _gauge_init_failed = True
            return None


def _record_context_usage_gauge(*, ratio: float, session_id: str) -> None:
    """Emit one ``agent.memory.context_usage_pct`` reading to the OTel gauge.

    Wraps the gauge call so the metric emission can never break the
    detector — any exception (gauge unavailable, exporter error, attr
    coercion failure) is caught and logged at debug level. The gauge
    instrument itself is null-safely handled by :func:`_get_context_usage_gauge`.

    Attributes:
        ``session.id`` — string. Dashboards aggregate or filter by this
        key. We do NOT emit prompt_tokens or context_length as attrs to
        avoid cardinality explosion (those are span attributes; gauge
        cardinality is bounded by session count).
    """
    gauge = _get_context_usage_gauge()
    if gauge is None:
        return
    try:
        gauge.set(float(ratio), attributes={"session.id": str(session_id)})
    except Exception as exc:  # noqa: BLE001 — defensive; detector path stays alive
        logger.debug(
            "runtime_detectors: gauge %s emission failed for session=%s: %s",
            _GAUGE_NAME,
            session_id,
            exc,
        )


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


@dataclass
class _ContextState:
    last_ratio: float = 0.0
    fired: bool = False


class ContextUsageDetector:
    """F-CONTEXT (F36) detector — fires when prompt_tokens / context_length crosses warn threshold.

    Wired into the post-model-call lifecycle. The caller passes the latest
    prompt-token count (typically read from ``response.usage.prompt_tokens``)
    and the model's context window (typically read from
    ``agent.model_metadata.get_model_context_length``); the detector computes
    the ratio and returns ``"F36"`` if it crosses the warn threshold for the
    first time since the last drop below it.

    Re-arm semantics mirror StallDetector: a session fires F-CONTEXT once
    per "episode" — defined as a contiguous run of ratio >= threshold. As
    soon as a recorded usage drops back under the threshold (e.g. successful
    compaction freed up space), the detector re-arms and the next crossing
    fires again.

    Why not use this to *trigger* compaction directly? Upstream Hermes'
    ``context_compressor`` already triggers compaction at 0.5 (its own
    ``threshold_percent``). Reaching 0.9 implies compaction is failing OR
    suppressed by the anti-thrashing guard. F-CONTEXT's job is to surface
    that pathological state to the orchestrator/operator, not to call
    compaction a second time.

    OTel gauge ``agent.memory.context_usage_pct`` is emitted on every
    ``record_usage`` call (independent of whether F36 fires) so
    dashboards see continuous ratio data, not just threshold-crossings.
    See ``_get_context_usage_gauge`` above for the lazy-init rationale.
    """

    def __init__(self, warn_threshold: float = DEFAULT_CONTEXT_WARN_THRESHOLD):
        if not 0.0 < warn_threshold <= 1.0:
            raise ValueError(f"warn_threshold must be in (0.0, 1.0], got {warn_threshold}")
        self.warn_threshold = warn_threshold
        self._sessions: dict[str, _ContextState] = {}
        self._lock = threading.Lock()

    def record_usage(
        self,
        *,
        session_id: str,
        prompt_tokens: int,
        context_length: int,
    ) -> Optional[str]:
        """Record one usage reading; return ``"F36"`` if warn threshold tripped.

        Side effect: emits the ratio to the
        ``agent.memory.context_usage_pct`` OTel gauge (tagged with
        ``session.id``) on every call — even when the threshold is not
        crossed and even when the detector has already fired in this
        episode. The gauge feeds dashboards that want a continuous
        view of context pressure; F36 is the discrete escalation signal.

        When the OTel metrics SDK is unavailable the gauge degrades to
        a no-op; F36 detection still works.
        """
        if context_length <= 0:
            # Defensive — should never happen, but avoid div-by-zero gracefully.
            return None
        ratio = prompt_tokens / context_length

        # Emit the gauge unconditionally — see docstring rationale.
        # Failures inside _record_gauge are swallowed there so a broken
        # exporter can't break F36 detection.
        _record_context_usage_gauge(ratio=ratio, session_id=session_id)

        with self._lock:
            state = self._sessions.setdefault(session_id, _ContextState())
            state.last_ratio = ratio
            if ratio < self.warn_threshold:
                # Re-arm if we previously fired and have now dropped below.
                state.fired = False
                return None
            if state.fired:
                return None
            state.fired = True
            logger.warning(
                "runtime_detectors.ContextUsageDetector F36 fired session=%s "
                "ratio=%.3f threshold=%.3f prompt_tokens=%d context_length=%d",
                session_id,
                ratio,
                self.warn_threshold,
                prompt_tokens,
                context_length,
            )
            return "F36"

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def snapshot(self, session_id: str) -> tuple[float, bool]:
        """Test/diagnostic helper: returns (last_ratio, fired)."""
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return (0.0, False)
            return (state.last_ratio, state.fired)
