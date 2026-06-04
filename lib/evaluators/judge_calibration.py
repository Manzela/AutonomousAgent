"""SP-J1 — Judge-calibration drift monitor ("eval of the eval").

A small human-labeled gold set is scored by each SP-06 judge on a schedule.
Cohen's κ (kappa) vs the human labels is tracked; when κ drops below the
threshold the judge is quarantined and a Telegram/Kanban alert is emitted.

Design:
  - cohens_kappa(): pure function — testable without any judge infra.
  - JudgeCalibrationMonitor.score(): applies a judge to a gold set and
    computes κ, returning a CalibrationResult.
  - JudgeQuarantineRegistry: records quarantined judge IDs in a flat JSONL
    file; SP-06 checks this before routing to a judge.

Thresholds (from inter-rater reliability literature):
  κ < 0.4   — poor agreement → quarantine + page
  κ < 0.6   — moderate (below substantial) → alert only
  κ ≥ 0.6   — substantial agreement → judge passes calibration
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

logger = logging.getLogger(__name__)

Verdict = Literal["accept", "reject"]

# Agreement tier labels (from Landis & Koch 1977)
_KAPPA_TIER = [
    (0.80, "almost-perfect"),
    (0.60, "substantial"),
    (0.40, "moderate"),
    (0.20, "fair"),
    (0.00, "slight"),
    (float("-inf"), "poor"),
]

KAPPA_QUARANTINE_THRESHOLD: float = 0.40
KAPPA_ALERT_THRESHOLD: float = 0.60


@dataclass(frozen=True)
class GoldItem:
    """A single human-labeled evaluation item.

    Args:
        item_id: Unique identifier.
        description: Short description of the work-product being judged.
        human_verdict: The ground-truth human label.
        axis: Which judge axis this tests (code-correctness, safety, …).
    """

    item_id: str
    description: str
    human_verdict: Verdict
    axis: str


@dataclass(frozen=True)
class JudgeRating:
    """A single judge verdict on a GoldItem.

    Args:
        item_id: Must match the GoldItem.item_id.
        judge_id: Identifier for the judge model/config.
        judge_verdict: The judge's verdict for this item.
    """

    item_id: str
    judge_id: str
    judge_verdict: Verdict


@dataclass(frozen=True)
class CalibrationResult:
    judge_id: str
    kappa: float
    n_items: int
    observed_agreement: float
    alert_fired: bool
    quarantined: bool
    tier: str

    @property
    def passes(self) -> bool:
        return self.kappa >= KAPPA_ALERT_THRESHOLD


def kappa_tier(kappa: float) -> str:
    for threshold, label in _KAPPA_TIER:
        if kappa >= threshold:
            return label
    return "poor"


def cohens_kappa(
    human_verdicts: list[Verdict],
    judge_verdicts: list[Verdict],
) -> float:
    """Compute Cohen's κ for two binary verdict lists.

    Args:
        human_verdicts: Ground-truth labels (list of "accept" | "reject").
        judge_verdicts: Judge outputs in the same order.

    Returns:
        κ ∈ [-1, 1]. Returns 0.0 if n=0 or if both raters agree 100% on
        a single class (indeterminate κ; treated as 0).

    Raises:
        ValueError: if lists are different lengths or empty.
    """
    if len(human_verdicts) != len(judge_verdicts):
        raise ValueError(
            f"cohens_kappa: length mismatch {len(human_verdicts)} vs {len(judge_verdicts)}"
        )
    n = len(human_verdicts)
    if n == 0:
        raise ValueError("cohens_kappa: empty verdict lists")

    categories = ("accept", "reject")
    # Build 2×2 confusion matrix
    # rows = human, cols = judge
    counts: dict[tuple[str, str], int] = {(h, j): 0 for h in categories for j in categories}
    for h, j in zip(human_verdicts, judge_verdicts):
        counts[(h, j)] += 1

    # P_o = observed agreement
    p_o = sum(counts[(c, c)] for c in categories) / n

    # P_e = expected agreement by chance
    p_e = 0.0
    for c in categories:
        row_sum = sum(counts[(c, j)] for j in categories)  # human marginal
        col_sum = sum(counts[(h, c)] for h in categories)  # judge marginal
        p_e += (row_sum / n) * (col_sum / n)

    if p_e >= 1.0:
        return 0.0
    return (p_o - p_e) / (1.0 - p_e)


class JudgeCalibrationMonitor:
    """Score a judge against a gold set and compute κ.

    Args:
        kappa_alert_threshold: κ below this fires an alert (default 0.6).
        kappa_quarantine_threshold: κ below this quarantines the judge (default 0.4).
        alert_callback: called with (judge_id, kappa, quarantined) on drift.
    """

    def __init__(
        self,
        *,
        kappa_alert_threshold: float = KAPPA_ALERT_THRESHOLD,
        kappa_quarantine_threshold: float = KAPPA_QUARANTINE_THRESHOLD,
        alert_callback: Optional[Callable[[str, float, bool], None]] = None,
    ) -> None:
        self._alert_threshold = kappa_alert_threshold
        self._quarantine_threshold = kappa_quarantine_threshold
        self._alert_callback = alert_callback

    def score(
        self,
        gold_set: list[GoldItem],
        judge_ratings: list[JudgeRating],
        judge_id: str,
    ) -> CalibrationResult:
        """Score a judge against the gold set.

        Args:
            gold_set: Human-labeled items.
            judge_ratings: One JudgeRating per GoldItem for this judge.
            judge_id: Must match all JudgeRating.judge_id values.

        Returns:
            CalibrationResult with κ, tier, alert/quarantine flags.

        Raises:
            ValueError: if judge_ratings don't cover all gold_set items.
        """
        ratings_by_item = {r.item_id: r for r in judge_ratings if r.judge_id == judge_id}
        missing = [g.item_id for g in gold_set if g.item_id not in ratings_by_item]
        if missing:
            raise ValueError(
                f"JudgeCalibrationMonitor: judge {judge_id!r} missing ratings for items: {missing}"
            )

        human_verdicts = [g.human_verdict for g in gold_set]
        judge_verdicts = [ratings_by_item[g.item_id].judge_verdict for g in gold_set]

        kappa = cohens_kappa(human_verdicts, judge_verdicts)
        n_agreements = sum(h == j for h, j in zip(human_verdicts, judge_verdicts))
        p_o = n_agreements / len(gold_set)
        tier = kappa_tier(kappa)

        alert_fired = kappa < self._alert_threshold
        quarantined = kappa < self._quarantine_threshold

        if alert_fired:
            level = "QUARANTINE" if quarantined else "ALERT"
            logger.warning(
                "judge_calibration: %s judge=%s kappa=%.3f tier=%s n=%d",
                level,
                judge_id,
                kappa,
                tier,
                len(gold_set),
            )
            if self._alert_callback:
                self._alert_callback(judge_id, kappa, quarantined)

        return CalibrationResult(
            judge_id=judge_id,
            kappa=kappa,
            n_items=len(gold_set),
            observed_agreement=p_o,
            alert_fired=alert_fired,
            quarantined=quarantined,
            tier=tier,
        )


class JudgeQuarantineRegistry:
    """Persist and check quarantined judge IDs.

    The SP-06 eval gate calls is_quarantined(judge_id) before routing a request
    to a judge; a quarantined judge is bypassed and an operator alert is sent.

    The registry is backed by a JSONL file; each line is a JSON object:
        {"judge_id": "...", "kappa": ..., "quarantined_at": "..."}

    The file path defaults to config/quarantined_judges.jsonl.
    """

    DEFAULT_PATH = Path("config/quarantined_judges.jsonl")

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or self.DEFAULT_PATH
        self._quarantined: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    self._quarantined.add(entry["judge_id"])
                except (json.JSONDecodeError, KeyError):
                    pass

    def quarantine(self, judge_id: str, kappa: float, quarantined_at: str = "") -> None:
        if judge_id in self._quarantined:
            return
        self._quarantined.add(judge_id)
        entry = {
            "judge_id": judge_id,
            "kappa": kappa,
            "quarantined_at": quarantined_at,
        }
        os.makedirs(self._path.parent, exist_ok=True)
        with open(self._path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        logger.warning("judge_calibration: quarantined judge=%s kappa=%.3f", judge_id, kappa)

    def is_quarantined(self, judge_id: str) -> bool:
        return judge_id in self._quarantined

    def release(self, judge_id: str) -> None:
        self._quarantined.discard(judge_id)

    @property
    def quarantined_ids(self) -> frozenset[str]:
        return frozenset(self._quarantined)
