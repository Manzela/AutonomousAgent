"""4-judge consensus + 5th-judge tiebreak per spec §P1-2.

Rules:
- 3+ accept (>=75%) → accept
- 3+ reject (>=75%) → reject
- otherwise → escalate to 5th judge (Opus 4.7); if still tied → Fail-Loud
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from lib.evaluators.judge import JudgeResult

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
    accept_threshold: float = 0.75,
    reject_threshold: float = 0.75,
) -> ConsensusResult:
    """Apply consensus rule to the judge panel.

    If `fifth_judge` is None and the panel doesn't reach a 75% majority
    OR contains any 'unsure', returns verdict='needs_5th_judge' (caller
    must dispatch the 5th judge and call again with fifth_judge set).

    With fifth_judge set, returns final verdict (or fail_loud if still tied).
    """
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
