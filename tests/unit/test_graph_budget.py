"""SP-R2 — the pure per-graph budget primitive (lib/durability/graph_budget.py).

INLINE per-graph (per-thread) aggregate-SPEND cap, INDEPENDENT of the F21 daily
watchdog (lib/durability/budget_watchdog.py): it reads ONLY a single graph's in-state
cost_accumulator and decides whether the NEXT fan-out wave is pre-empted. No DB, no
HALT_F21, no daily reset. Every assertion carries its RED control (off-when-None,
off-when-non-positive, the >= boundary) so it provably bites.
"""

from __future__ import annotations

import pytest

from lib.durability.graph_budget import (
    GraphBudgetVerdict,
    aggregate_spend,
    budget_verdict,
)


# ── aggregate_spend: sum the per-node cost_accumulator ('<tid>|<node>' -> cost_usd) ──
def test_aggregate_spend_empty_and_none_is_zero():
    assert aggregate_spend(None) == 0.0
    assert aggregate_spend({}) == 0.0


def test_aggregate_spend_sums_per_node_costs():
    acc = {"t|n0": 1.25, "t|n1": 2.75, "t|n2": 0.0}
    assert aggregate_spend(acc) == pytest.approx(4.0)


def test_aggregate_spend_ignores_non_numeric_values():
    # Defensive: a malformed accumulator value must not crash the safety gate.
    acc = {"t|n0": 3.0, "t|bad": None, "t|str": "x"}  # type: ignore[dict-item]
    assert aggregate_spend(acc) == pytest.approx(3.0)


# ── budget_verdict: the pure pre-emption decision ──
def test_budget_off_when_cap_none():
    # cap=None => budget OFF: never pre-empt, no matter how high the spend (back-compat).
    v = budget_verdict(1_000_000.0, None)
    assert isinstance(v, GraphBudgetVerdict)
    assert v.preempt is False
    assert v.cap_usd is None


def test_budget_off_when_cap_non_positive():
    # cap<=0 is a misconfiguration, NOT "spend zero allowed": treat as OFF (RED would pre-empt).
    assert budget_verdict(5.0, 0.0).preempt is False
    assert budget_verdict(5.0, -1.0).preempt is False


def test_preempt_when_spend_exceeds_cap():
    v = budget_verdict(5.0, 1.0)
    assert v.preempt is True
    assert v.spend_usd == pytest.approx(5.0)
    assert v.cap_usd == pytest.approx(1.0)


def test_no_preempt_when_strictly_under_cap():
    # RED control for the GREEN above: clearly under the cap must NOT pre-empt.
    assert budget_verdict(0.99, 1.0).preempt is False


def test_exact_boundary_preempts_fail_safe():
    # The >= boundary is a DELIBERATE fail-safe: once cumulative spend has REACHED the cap the
    # budget is consumed, so the next wave is pre-empted (dispatching it would spend BEYOND cap).
    # RED: a '>' boundary would let spend==cap dispatch one more (unbounded-by-one) wave.
    assert budget_verdict(1.0, 1.0).preempt is True


def test_verdict_reason_is_human_readable_and_pii_free():
    # The reason is persisted (checkpoint + audit), so it must be a plain, non-PII string.
    v = budget_verdict(5.0, 1.0)
    assert "@" not in v.reason and v.reason  # no email-shaped content; non-empty
