"""SP-J1 hermetic unit tests for judge calibration drift monitor.

All tests run without any LLM, network, or file I/O (except the registry tests
which use a tmp_path fixture).

Oracles:
  1.  cohens_kappa: perfect agreement → κ = 1.0.
  2.  cohens_kappa: total disagreement → κ < 0.
  3.  cohens_kappa: chance agreement (random) → κ ≈ 0.
  4.  cohens_kappa: length mismatch raises ValueError.
  5.  cohens_kappa: empty lists raise ValueError.
  6.  JudgeCalibrationMonitor.score: aligned judge → κ ≥ threshold, no alert.
  7.  JudgeCalibrationMonitor.score: misaligned judge (all wrong) → κ < 0, alert fires.
  8.  JudgeCalibrationMonitor.score: partially misaligned → alert at moderate-κ boundary.
  9.  JudgeCalibrationMonitor.score: quarantine fires when κ < quarantine threshold.
  10. JudgeCalibrationMonitor.score: alert_callback receives (judge_id, kappa, quarantined).
  11. JudgeCalibrationMonitor.score: missing rating raises ValueError.
  12. kappa_tier: maps κ to named tier correctly.
  13. JudgeQuarantineRegistry: quarantine + is_quarantined round-trip.
  14. JudgeQuarantineRegistry: double-quarantine is idempotent (one JSONL entry).
  15. JudgeQuarantineRegistry: release clears quarantine flag.
  16. JudgeQuarantineRegistry: persists across registry re-load.
"""

from __future__ import annotations


import pytest

from lib.evaluators.judge_calibration import (
    GoldItem,
    JudgeCalibrationMonitor,
    JudgeQuarantineRegistry,
    JudgeRating,
    cohens_kappa,
    kappa_tier,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _gold(item_id: str, human_verdict: str, axis: str = "code-correctness") -> GoldItem:
    return GoldItem(
        item_id=item_id,
        description=f"item {item_id}",
        human_verdict=human_verdict,
        axis=axis,
    )


def _rating(item_id: str, judge_id: str, verdict: str) -> JudgeRating:
    return JudgeRating(item_id=item_id, judge_id=judge_id, judge_verdict=verdict)


# ── Oracle 1 — perfect agreement → κ = 1.0 ───────────────────────────────────


def test_cohens_kappa_perfect_agreement():
    h = ["accept", "reject", "accept", "accept", "reject"]
    j = ["accept", "reject", "accept", "accept", "reject"]
    assert cohens_kappa(h, j) == pytest.approx(1.0)


# ── Oracle 2 — total disagreement → κ < 0 ─────────────────────────────────────


def test_cohens_kappa_total_disagreement():
    # Balanced marginals (2 accept, 2 reject) with judge fully inverted → κ = -1.0
    h = ["accept", "accept", "reject", "reject"]
    j = ["reject", "reject", "accept", "accept"]
    kappa = cohens_kappa(h, j)
    assert kappa == pytest.approx(-1.0)


# ── Oracle 3 — chance agreement → κ ≈ 0 ──────────────────────────────────────


def test_cohens_kappa_chance_agreement():
    h = ["accept", "accept", "reject", "reject"]
    j = ["accept", "reject", "accept", "reject"]
    kappa = cohens_kappa(h, j)
    assert abs(kappa) < 0.2


# ── Oracle 4 — length mismatch raises ValueError ─────────────────────────────


def test_cohens_kappa_length_mismatch_raises():
    with pytest.raises(ValueError, match="length mismatch"):
        cohens_kappa(["accept", "reject"], ["accept"])


# ── Oracle 5 — empty lists raise ValueError ──────────────────────────────────


def test_cohens_kappa_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        cohens_kappa([], [])


# ── Oracle 6 — aligned judge → no alert, κ ≥ threshold ────────────────────────


def test_monitor_aligned_judge_no_alert():
    gold = [
        _gold("g1", "accept"),
        _gold("g2", "reject"),
        _gold("g3", "accept"),
        _gold("g4", "accept"),
        _gold("g5", "reject"),
    ]
    ratings = [
        _rating("g1", "j-good", "accept"),
        _rating("g2", "j-good", "reject"),
        _rating("g3", "j-good", "accept"),
        _rating("g4", "j-good", "accept"),
        _rating("g5", "j-good", "reject"),
    ]
    monitor = JudgeCalibrationMonitor()
    result = monitor.score(gold, ratings, "j-good")
    assert result.kappa == pytest.approx(1.0)
    assert result.alert_fired is False
    assert result.quarantined is False
    assert result.passes is True


# ── Oracle 7 — misaligned judge → alert fires, κ < 0 ─────────────────────────


def test_monitor_misaligned_judge_fires_alert():
    gold = [
        _gold("g1", "accept"),
        _gold("g2", "accept"),
        _gold("g3", "reject"),
        _gold("g4", "reject"),
    ]
    # Judge gets all 4 wrong
    ratings = [
        _rating("g1", "j-bad", "reject"),
        _rating("g2", "j-bad", "reject"),
        _rating("g3", "j-bad", "accept"),
        _rating("g4", "j-bad", "accept"),
    ]
    monitor = JudgeCalibrationMonitor()
    result = monitor.score(gold, ratings, "j-bad")
    assert result.kappa < 0
    assert result.alert_fired is True


# ── Oracle 8 — partially misaligned → alert near moderate boundary ─────────────


def test_monitor_partial_misalignment_alert():
    # 4 items, judge gets 1 wrong out of 4 → P_o = 0.75
    # With balanced marginals κ ≈ 0.5 (moderate, below 0.6 threshold)
    gold = [
        _gold("g1", "accept"),
        _gold("g2", "accept"),
        _gold("g3", "reject"),
        _gold("g4", "reject"),
    ]
    ratings = [
        _rating("g1", "j-mid", "accept"),
        _rating("g2", "j-mid", "accept"),
        _rating("g3", "j-mid", "reject"),
        _rating("g4", "j-mid", "accept"),  # wrong: human=reject, judge=accept
    ]
    monitor = JudgeCalibrationMonitor(kappa_alert_threshold=0.6)
    result = monitor.score(gold, ratings, "j-mid")
    # κ = (0.75 - 0.5) / (1 - 0.5) = 0.5 < 0.6 → alert
    assert result.kappa == pytest.approx(0.5)
    assert result.alert_fired is True
    assert result.quarantined is False  # 0.5 > quarantine threshold 0.4


# ── Oracle 9 — quarantine fires when κ < quarantine threshold ─────────────────


def test_monitor_quarantine_fires_below_threshold():
    # 4 items, judge gets 3 wrong → P_o = 0.25
    # κ = (0.25 - 0.5) / (1 - 0.5) = -0.5 < 0.4 → quarantine
    gold = [
        _gold("g1", "accept"),
        _gold("g2", "accept"),
        _gold("g3", "reject"),
        _gold("g4", "reject"),
    ]
    ratings = [
        _rating("g1", "j-bad2", "reject"),  # wrong
        _rating("g2", "j-bad2", "reject"),  # wrong
        _rating("g3", "j-bad2", "accept"),  # wrong
        _rating("g4", "j-bad2", "reject"),  # correct
    ]
    monitor = JudgeCalibrationMonitor()
    result = monitor.score(gold, ratings, "j-bad2")
    assert result.quarantined is True
    assert result.alert_fired is True


# ── Oracle 10 — alert_callback receives correct args ──────────────────────────


def test_monitor_alert_callback_called():
    calls: list[tuple] = []
    monitor = JudgeCalibrationMonitor(alert_callback=lambda jid, k, q: calls.append((jid, k, q)))
    # Balanced gold (2 accept, 2 reject), judge inverts all → κ = -1.0
    gold = [
        _gold("g1", "accept"),
        _gold("g2", "accept"),
        _gold("g3", "reject"),
        _gold("g4", "reject"),
    ]
    ratings = [
        _rating("g1", "j-cb", "reject"),
        _rating("g2", "j-cb", "reject"),
        _rating("g3", "j-cb", "accept"),
        _rating("g4", "j-cb", "accept"),
    ]
    result = monitor.score(gold, ratings, "j-cb")
    assert result.alert_fired is True
    assert len(calls) == 1
    jid, k, q = calls[0]
    assert jid == "j-cb"
    assert k < 0
    assert isinstance(q, bool)


# ── Oracle 11 — missing rating raises ValueError ─────────────────────────────


def test_monitor_missing_rating_raises():
    gold = [_gold("g1", "accept"), _gold("g2", "reject")]
    ratings = [_rating("g1", "j-x", "accept")]  # g2 missing
    monitor = JudgeCalibrationMonitor()
    with pytest.raises(ValueError, match="missing ratings"):
        monitor.score(gold, ratings, "j-x")


# ── Oracle 12 — kappa_tier maps correctly ─────────────────────────────────────


def test_kappa_tier_mapping():
    assert kappa_tier(0.95) == "almost-perfect"
    assert kappa_tier(0.70) == "substantial"
    assert kappa_tier(0.50) == "moderate"
    assert kappa_tier(0.30) == "fair"
    assert kappa_tier(0.10) == "slight"
    assert kappa_tier(-0.10) == "poor"


# ── Oracle 13 — registry quarantine + is_quarantined ─────────────────────────


def test_registry_quarantine_is_quarantined(tmp_path):
    reg = JudgeQuarantineRegistry(path=tmp_path / "q.jsonl")
    assert reg.is_quarantined("j-opus") is False
    reg.quarantine("j-opus", kappa=-0.3)
    assert reg.is_quarantined("j-opus") is True


# ── Oracle 14 — double quarantine idempotent ─────────────────────────────────


def test_registry_double_quarantine_idempotent(tmp_path):
    p = tmp_path / "q.jsonl"
    reg = JudgeQuarantineRegistry(path=p)
    reg.quarantine("j-dup", kappa=0.2)
    reg.quarantine("j-dup", kappa=0.1)  # second call — should not append
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 1


# ── Oracle 15 — release clears quarantine ─────────────────────────────────────


def test_registry_release_clears(tmp_path):
    reg = JudgeQuarantineRegistry(path=tmp_path / "q.jsonl")
    reg.quarantine("j-rel", kappa=0.1)
    assert reg.is_quarantined("j-rel") is True
    reg.release("j-rel")
    assert reg.is_quarantined("j-rel") is False


# ── Oracle 16 — registry persists across reload ───────────────────────────────


def test_registry_persists_across_reload(tmp_path):
    p = tmp_path / "q.jsonl"
    reg1 = JudgeQuarantineRegistry(path=p)
    reg1.quarantine("j-persist", kappa=0.05)
    # Reload from the same file
    reg2 = JudgeQuarantineRegistry(path=p)
    assert reg2.is_quarantined("j-persist") is True
