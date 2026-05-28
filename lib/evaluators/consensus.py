"""4-judge consensus + 5th-judge tiebreak per spec §P1-2.

Rules:
- 3+ accept (>=75%) → accept
- 3+ reject (>=75%) → reject
- otherwise → escalate to 5th judge (Opus 4.7); if still tied → Fail-Loud

Also exposes ``record_rejection_for_fingerprint`` — the 3-strike tracker
that calls ``lib.memory.rejected.append_entry(...)`` on the Nth consecutive
reject for the same ``approach_fingerprint``. Threshold comes from
``config/limits.yaml evaluators.rejection_repeat_threshold`` (default 3),
per design-alignment spec L333.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Literal, Optional

from lib.evaluators.judge import JudgeResult

logger = logging.getLogger(__name__)

# Per-(session_id, fingerprint) consecutive-reject counter. A different
# fingerprint resets the streak for the session.
_STRIKE_LOCK = threading.Lock()
_STRIKE_COUNT: dict[tuple[str, str], int] = {}
_LAST_FP_PER_SESSION: dict[str, str] = {}
DEFAULT_REJECTION_REPEAT_THRESHOLD = 3

ConsensusVerdict = Literal["accept", "reject", "needs_5th_judge", "fail_loud"]


@dataclass
class ConsensusResult:
    verdict: ConsensusVerdict
    accept_count: int
    reject_count: int
    unsure_count: int
    escalated: bool
    rationale: str
    judges: list[JudgeResult]
    fifth_judge: Optional[JudgeResult] = None


def _tally(judges: list[JudgeResult]) -> tuple[int, int, int]:
    a = sum(1 for j in judges if j.verdict == "accept")
    r = sum(1 for j in judges if j.verdict == "reject")
    u = sum(1 for j in judges if j.verdict == "unsure")
    return a, r, u


def decide_consensus(
    judges: list[JudgeResult],
    *,
    fifth_judge: Optional[JudgeResult] = None,
    accept_threshold: Optional[float] = None,
    reject_threshold: Optional[float] = None,
) -> ConsensusResult:
    """Apply consensus rule to the judge panel.

    Thresholds default to config/limits.yaml evaluators.consensus.accept_threshold /
    reject_threshold (currently 0.75 each). Callers may override by passing explicit
    float values.

    If `fifth_judge` is None and the panel doesn't reach a majority
    OR contains any 'unsure', returns verdict='needs_5th_judge' (caller
    must dispatch the 5th judge and call again with fifth_judge set).

    With fifth_judge set, returns final verdict (or fail_loud if still tied).
    """
    if accept_threshold is None or reject_threshold is None:
        cfg_a, cfg_r = _consensus_thresholds()
        if accept_threshold is None:
            accept_threshold = cfg_a
        if reject_threshold is None:
            reject_threshold = cfg_r
    n = len(judges)
    if n != 4:
        raise ValueError(f"4-judge consensus expects 4 judges, got {n}")

    a, r, u = _tally(judges)
    accept_pct = a / n
    reject_pct = r / n

    if u == 0:
        if accept_pct >= accept_threshold:
            return ConsensusResult(
                verdict="accept",
                accept_count=a,
                reject_count=r,
                unsure_count=u,
                escalated=False,
                rationale=f"{a}/{n} accept >= {accept_threshold:.0%}",
                judges=judges,
            )
        if reject_pct >= reject_threshold:
            return ConsensusResult(
                verdict="reject",
                accept_count=a,
                reject_count=r,
                unsure_count=u,
                escalated=False,
                rationale=f"{r}/{n} reject >= {reject_threshold:.0%}",
                judges=judges,
            )

    if fifth_judge is None:
        return ConsensusResult(
            verdict="needs_5th_judge",
            accept_count=a,
            reject_count=r,
            unsure_count=u,
            escalated=True,
            rationale=f"No-quorum (a={a},r={r},u={u}); F24 -> escalate to 5th judge",
            judges=judges,
        )

    if fifth_judge.verdict == "accept":
        return ConsensusResult(
            verdict="accept",
            accept_count=a + 1,
            reject_count=r,
            unsure_count=u,
            escalated=True,
            rationale="5th judge broke tie: accept",
            judges=judges,
            fifth_judge=fifth_judge,
        )
    if fifth_judge.verdict == "reject":
        return ConsensusResult(
            verdict="reject",
            accept_count=a,
            reject_count=r + 1,
            unsure_count=u,
            escalated=True,
            rationale="5th judge broke tie: reject",
            judges=judges,
            fifth_judge=fifth_judge,
        )

    return ConsensusResult(
        verdict="fail_loud",
        accept_count=a,
        reject_count=r,
        unsure_count=u + 1,
        escalated=True,
        rationale="5th judge unsure; F24 -> Fail-Loud",
        judges=judges,
        fifth_judge=fifth_judge,
    )


def _consensus_thresholds() -> tuple[float, float]:
    """Read accept/reject thresholds from config/limits.yaml evaluators.consensus.*.

    Returns (accept_threshold, reject_threshold). Falls back to (0.75, 0.75) on
    any read failure so consensus is never blocked by a config read error.
    """
    try:
        import yaml
        from pathlib import Path

        cfg_path = Path(__file__).resolve().parents[2] / "config" / "limits.yaml"
        if not cfg_path.exists():
            return 0.75, 0.75
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        ev_consensus = (cfg.get("evaluators") or {}).get("consensus") or {}
        return (
            float(ev_consensus.get("accept_threshold", 0.75)),
            float(ev_consensus.get("reject_threshold", 0.75)),
        )
    except Exception:  # noqa: BLE001 — config faults must not break consensus
        return 0.75, 0.75


def _rejection_repeat_threshold() -> int:
    """Read ``evaluators.rejection_repeat_threshold`` from config/limits.yaml.

    Falls back to ``DEFAULT_REJECTION_REPEAT_THRESHOLD`` on any read failure.
    Local import keeps unit-test import cost down.
    """
    try:
        import yaml
        from pathlib import Path

        cfg_path = Path(__file__).resolve().parents[2] / "config" / "limits.yaml"
        if not cfg_path.exists():
            return DEFAULT_REJECTION_REPEAT_THRESHOLD
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        return int(
            (cfg.get("evaluators") or {}).get(
                "rejection_repeat_threshold", DEFAULT_REJECTION_REPEAT_THRESHOLD
            )
        )
    except Exception:  # noqa: BLE001 — config faults must not break consensus
        return DEFAULT_REJECTION_REPEAT_THRESHOLD


def record_rejection_for_fingerprint(
    *,
    session_id: str,
    approach_fingerprint: str,
    approach_summary: str,
    taskspec_id: str,
    intent_category: str,
    why_failed: str,
    alternatives: str,
    threshold: int | None = None,
) -> int:
    """Increment the 3-strike counter; on threshold, append to REJECTED.md.

    Per design-alignment spec L333: when ``consecutive_rejections >= threshold``
    for the same approach fingerprint inside a single session, call
    ``lib.memory.rejected.append_entry``. A different fingerprint for the
    same session resets the streak (a new approach is a fresh attempt).

    Returns the current streak count after this call. The fingerprint dedup
    inside ``rejected.append_entry`` keeps the file clean if the threshold
    re-fires for the same fingerprint (counter bumps on the entry).

    Local import of ``lib.memory.rejected`` deliberately avoids a hard
    top-line dependency from evaluators → memory.
    """
    fp = approach_fingerprint
    n = threshold if threshold is not None else _rejection_repeat_threshold()
    with _STRIKE_LOCK:
        last_fp = _LAST_FP_PER_SESSION.get(session_id)
        if last_fp != fp:
            # New approach for this session — reset the previous streak.
            if last_fp is not None:
                _STRIKE_COUNT.pop((session_id, last_fp), None)
            _LAST_FP_PER_SESSION[session_id] = fp
        count = _STRIKE_COUNT.get((session_id, fp), 0) + 1
        _STRIKE_COUNT[(session_id, fp)] = count
        triggered = count >= n

    if triggered:
        try:
            # Local import: keeps evaluators import-time clean of memory deps,
            # and avoids any top-line conflict with parallel session edits.
            from lib.memory import rejected as _rej

            _rej.append_entry(
                approach_fingerprint=fp,
                approach_summary=approach_summary,
                taskspec_id=taskspec_id,
                intent_category=intent_category,
                why_failed=why_failed,
                alternatives=alternatives,
            )
            logger.info(
                "consensus: 3-strike fired (session=%s fp=%s count=%d) -> REJECTED.md",
                session_id,
                fp[:12],
                count,
            )
        except Exception as exc:  # noqa: BLE001 — memory write must not abort consensus
            logger.warning("consensus: append_entry failed (non-fatal): %s", exc)
    return count


def reset_session_strikes(session_id: str) -> None:
    """Drop all fingerprint streaks for a session (called on session-end)."""
    with _STRIKE_LOCK:
        fp = _LAST_FP_PER_SESSION.pop(session_id, None)
        if fp is not None:
            _STRIKE_COUNT.pop((session_id, fp), None)
