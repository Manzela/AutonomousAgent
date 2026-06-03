"""SP-R2 — per-graph (per-thread) aggregate-SPEND cap: INLINE fan-out pre-emption.

This is the per-graph budget the ``cost_accumulator`` reducer (app/core/graph_state.py)
was reserved for ("wires to budget_watchdog later"). It is INDEPENDENT of the F21 DAILY
watchdog in ``lib/durability/budget_watchdog.py``:

  * the daily poller queries the LiteLLM ``LiteLLM_SpendLogs`` Postgres table for the
    UTC-day aggregate across ALL graphs and touches ``/data/HALT_F21`` on exhaustion;
  * THIS primitive reads ONLY the in-state ``cost_accumulator`` a SINGLE graph has
    spent — no DB, no psycopg, no HALT sentinel, no daily reset.

``fan_out`` calls ``budget_verdict`` at TWO points that bracket the dispatch:
  1. PRE-dispatch ADMISSION — if PRIOR waves already reached the cap, dispatch nothing
     (protects a resumed graph + every wave AFTER the first).
  2. POST-dispatch ENFORCEMENT — if THIS wave pushed cumulative spend to/over the cap,
     the verdict pre-empts so the graph parks at ``__halt__`` BEFORE eval_gate/ship — an
     over-budget run never SHIPS, even on the single live wave (the spine has no
     eval_gate->fan_out loop yet — SP-11-deferred — so the first wave is the only wave).

Both bound spend, at different scopes; neither subsumes the other (PRD §6 SP-R6:
"a global cap ... complements SP-R2's per-graph budget"). Keeping SP-R2 a pure, side-
effect-free decision is what lets ``fan_out`` call it on every super-step without a
network/disk dependency, and what lets the unit oracles run hermetically.

DEFERRED (named, not silently dropped):
  * MID-fan-out IN-FLIGHT cancellation — SP-R2 bounds blast radius to at most the ONE
    crossing wave (its spend is already incurred when POST-dispatch sees it) and then
    halts; cancelling already-gather'd leaves the instant a running tally crosses the cap
    (an asyncio.gather cancel), or refusing the first wave via a pre-run cost oracle, is a
    separate concern.
  * the LIVE operator-escalation interrupt (PRD §4 anti-drift clause (d) / SP-27) — SP-R2
    parks fail-safe at ``__halt__``; the interactive "approve more budget" interrupt is SP-27.
  * token-denominated budgets — this slice caps USD (the ``cost_usd`` the executor
    reports); a token cap reusing the same shape is additive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class GraphBudgetVerdict:
    """The pre-emption decision for one ``fan_out`` super-step.

    ``preempt`` True iff the per-graph budget is configured AND cumulative spend has
    reached it. ``spend_usd`` is the aggregate this graph has spent so far; ``cap_usd``
    is the configured per-graph ceiling (None/<=0 == OFF); ``reason`` is a plain,
    PII-free string persisted on the checkpoint + audit trail.
    """

    preempt: bool
    spend_usd: float
    cap_usd: Optional[float]
    reason: str


def aggregate_spend(cost_accumulator: Optional[Mapping[str, float]]) -> float:
    """Sum the per-node ``cost_accumulator`` (keyed ``'<thread_id>|<node_id>' -> cost_usd``)
    into this graph's cumulative spend. None/empty -> 0.0. Non-numeric values are skipped
    defensively so a malformed accumulator can never crash the safety gate."""
    if not cost_accumulator:
        return 0.0
    return float(sum(v for v in cost_accumulator.values() if isinstance(v, (int, float))))


def budget_verdict(spend_usd: float, cap_usd: Optional[float]) -> GraphBudgetVerdict:
    """Pure per-graph pre-emption decision (no I/O, no side effects).

    ``cap_usd`` None or <= 0 => budget OFF (never pre-empt — the default, so an
    unconfigured spine behaves exactly as before). Otherwise PRE-EMPT once cumulative
    ``spend_usd`` has REACHED the cap (``>=``): a DELIBERATE fail-safe — the budget is
    consumed, so dispatching the next wave would spend beyond it. A ``>`` boundary would
    let ``spend == cap`` dispatch one further wave (unbounded-by-one)."""
    if cap_usd is None or cap_usd <= 0:
        return GraphBudgetVerdict(False, spend_usd, cap_usd, "budget-off")
    if spend_usd >= cap_usd:
        return GraphBudgetVerdict(
            True,
            spend_usd,
            cap_usd,
            f"over-budget: spend={spend_usd:.4f} >= cap={cap_usd:.4f} (USD)",
        )
    return GraphBudgetVerdict(
        False,
        spend_usd,
        cap_usd,
        f"under-budget: spend={spend_usd:.4f} < cap={cap_usd:.4f} (USD)",
    )
