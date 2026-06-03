"""SP-R2 — per-graph budget enforces fan_out (INLINE, independent of the F21 daily poller).

fan_out reads ONLY the in-state cost_accumulator a single graph has spent. Two checks bracket the
dispatch: (1) PRE-dispatch ADMISSION — if PRIOR waves already reached the cap, dispatch nothing
(protects a resumed graph + every wave after the first); (2) POST-dispatch ENFORCEMENT — if THIS
wave pushed cumulative spend to/over the cap, stamp preempt so _route_after_fan_out parks the graph
at __halt__ BEFORE eval_gate/ship (an over-budget run never SHIPS, even on the single live wave).
cap unset => OFF => byte-identical to today. Every oracle carries its RED control. Hermetic: no DB,
no docker, no network — the decision is pure over the accumulated cost_accumulator.
"""

from __future__ import annotations

import inspect

from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core import graph as graph_mod
from app.core.graph import _build_nodes, _default_capability, _route_after_fan_out
from app.core.schemas import AgentCapability, AgentID, ExecutionResult, TaskStatus
from app.core.spine_runner import SpineRunner

_CFG = {"configurable": {"thread_id": "t-r2"}}


def _node(nid, deps):
    return {
        "id": nid,
        "phase": "draft",
        "summary": nid,
        "depends_on": list(deps),
        "acceptance_ref": "0",
        "allowed_paths": [f"app/r2_{nid}.py"],
    }


def _plan(nodes):
    return {"nodes": nodes, "edges": [(d, n["id"]) for n in nodes for d in n["depends_on"]]}


def _state(plan, *, spend=None, tasks=None):
    st = {"thread_id": "t-r2", "goal": "g", "plan": plan, "base_ref": "HEAD"}
    if spend is not None:
        st["cost_accumulator"] = spend
    if tasks is not None:
        st["tasks"] = tasks
    return st


def _cost_capability(cost: float) -> AgentCapability:
    """A capability whose every invoke reports a fixed cost_usd (the in-process/sandbox=None
    path returns the result verbatim — orchestrator._execute_local, so cost flows through)."""

    async def _invoke(req):
        return ExecutionResult(task_id=req.task_id, status=TaskStatus.COMPLETED, cost_usd=cost)

    return AgentCapability(
        agent_id=AgentID("r2-cost"),
        version="1",
        phase="draft",
        description="cost stub",
        invoke=_invoke,
    )


# ── O1 POST-dispatch (the LIVE single-wave guard): a wave whose OWN cost exceeds the cap RUNS
#    (cost is sunk — mid-wave cancel is deferred) but is flagged preempt -> parks before ship. ──
async def test_post_dispatch_halts_single_overbudget_wave():
    # prior spend 0 (the only wave), leaf cost 5.0, cap 1.0. RED (pre-dispatch only): the wave would
    # dispatch with preempt False (prior==0 < cap) and route to ship — the dead-wire the review caught.
    nodes = _build_nodes(_cost_capability(5.0), sandbox=None, budget_usd=1.0)
    delta = await nodes["fan_out"](_state(_plan([_node("n0", [])])), _CFG)
    assert len(delta["tasks"]) == 1  # the wave DID run (cost already incurred)
    assert delta["budget_verdict"]["preempt"] is True  # ...and is parked over-budget
    assert any("OVER-BUDGET" in a for a in delta["audit"])


# ── O2 PRE-dispatch admission: prior-wave spend over the cap dispatches NOTHING ──
async def test_pre_dispatch_skips_when_prior_over_budget():
    nodes = _build_nodes(_default_capability(), sandbox=None, budget_usd=1.0)
    state = _state(_plan([_node("n0", [])]), spend={"t-r2|prior": 5.0})
    delta = await nodes["fan_out"](state, _CFG)
    assert delta["budget_verdict"]["preempt"] is True
    assert not delta.get("tasks")  # nothing dispatched -> no further spend
    assert "cost_accumulator" not in delta
    assert any("pre-dispatch" in a for a in delta["audit"])


# ── O3 RED control: a wave UNDER the cap dispatches normally and records the posture ──
async def test_under_budget_dispatches_and_records_posture():
    nodes = _build_nodes(_cost_capability(0.10), sandbox=None, budget_usd=100.0)
    delta = await nodes["fan_out"](_state(_plan([_node("n0", [])])), _CFG)
    assert delta["budget_verdict"]["preempt"] is False  # posture observable on the live wave
    assert len(delta["tasks"]) == 1  # the ready root WAS dispatched


# ── O4 back-compat: cap unset => budget OFF => no verdict key, dispatch despite huge spend ──
async def test_budget_off_when_unset_dispatches_despite_spend():
    nodes = _build_nodes(_default_capability(), sandbox=None)  # no budget_usd => OFF (default)
    state = _state(_plan([_node("n0", [])]), spend={"t-r2|prior": 999.0})
    delta = await nodes["fan_out"](state, _CFG)
    assert "budget_verdict" not in delta  # OFF path adds NO key — byte-identical to pre-SP-R2
    assert len(delta["tasks"]) == 1


# ── O5 INDEPENDENCE: the pre-emption path never reaches the F21 daily watchdog ──
async def test_preempt_independent_of_daily_watchdog(monkeypatch):
    # Behavioural: poison the daily poller — if the SP-R2 path touched it, this would raise.
    import lib.durability.budget_watchdog as wd

    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("SP-R2 must NOT call the F21 daily watchdog / spend_logs DB")

    monkeypatch.setattr(wd, "get_daily_spend_usd", _boom)
    nodes = _build_nodes(_default_capability(), sandbox=None, budget_usd=1.0)
    delta = await nodes["fan_out"](_state(_plan([_node("n0", [])]), spend={"x": 5.0}), _CFG)
    assert delta["budget_verdict"]["preempt"] is True
    # Textual: the pure primitive IMPORTS no DB / daily-watchdog machinery. Assert over the
    # import statements ONLY — the docstring NAMES those modules to document the independence,
    # so a substring-anywhere check would false-positive on its own explanation.
    import lib.durability.graph_budget as gb

    imports = "\n".join(
        ln
        for ln in inspect.getsource(gb).splitlines()
        if ln.strip().startswith(("import ", "from "))
    )
    for forbidden in ("budget_watchdog", "psycopg", "spend_logs"):
        assert forbidden not in imports, f"SP-R2 leaked daily-watchdog coupling: {forbidden}"


# ── O6 STRONGEST (multi-wave): under-budget waves advance; the crossing wave dispatches THEN
#    pre-empts (post-dispatch); a further re-drive while over-budget is admission-skipped. ──
async def test_advances_then_preempts_across_waves():
    # chain n0->n1->n2, each leaf 0.6, cap 1.0. ENTRY/EXIT spend per wave:
    #   wave0 prior 0.0 -> dispatch n0 -> 0.6 (under, preempt False)
    #   wave1 prior 0.6 -> dispatch n1 -> 1.2 (crosses: post-dispatch preempt True, n1 still ran)
    #   wave2 prior 1.2 -> PRE-dispatch admission skips (preempt True, dispatch nothing)
    # RED: budget OFF would dispatch all three with no preempt.
    nodes = _build_nodes(_cost_capability(0.6), sandbox=None, budget_usd=1.0)
    plan = _plan([_node("n0", []), _node("n1", ["n0"]), _node("n2", ["n1"])])
    done_tasks: list = []
    acc: dict = {}
    results: list = []
    for _ in range(3):
        state = _state(plan, spend=dict(acc), tasks=list(done_tasks))
        delta = await nodes["fan_out"](state, _CFG)
        ids = sorted(t["task_id"] for t in delta.get("tasks", []))
        results.append((ids, delta["budget_verdict"]["preempt"]))
        done_tasks += list(delta.get("tasks", []))
        for k, v in (delta.get("cost_accumulator") or {}).items():
            acc[k] = acc.get(k, 0.0) + v  # mirror the _merge_cost reducer
    assert results == [(["n0"], False), (["n1"], True), ([], True)], results
    assert "n2" not in done_tasks  # n2 never ran — budget protected it


# ── O7 routing: a pre-empt verdict routes fail-safe to __halt__; otherwise monitor (SP-27) ──
def test_route_after_fan_out():
    assert _route_after_fan_out({"budget_verdict": {"preempt": True}}) == "__halt__"
    # SP-27: non-preempted path now routes to "monitor" (was "eval_gate" pre-SP-27)
    assert _route_after_fan_out({"budget_verdict": {"preempt": False}}) == "monitor"
    assert _route_after_fan_out({}) == "monitor"  # budget OFF / absent -> monitor path


# ── O8 assembly: build_spine wires fan_out CONDITIONALLY (no unconditional fan_out->eval_gate) ──
def test_build_spine_wires_fan_out_conditionally():
    src = inspect.getsource(graph_mod.build_spine)
    assert "_route_after_fan_out" in src, "fan_out must route through the SP-R2 budget gate"
    assert 'add_edge("fan_out", "eval_gate")' not in src, "the unconditional edge must be gone"


# ── O9 LIVE END-TO-END: the compiled spine HALTS an over-budget run before ship (refutes the
#    dead-wire repro — drives the real graph through SpineRunner, not a hand-seeded fan_out). ──
async def test_live_spine_halts_over_budget_run_before_ship(monkeypatch):
    monkeypatch.setenv("SPINE_BUDGET_USD", "0.0001")  # read by SpineRunner._default_budget at init
    runner = SpineRunner(InMemoryCheckpointer(), capability=_cost_capability(999.0))
    tid = "t-r2-e2e"
    r1 = await runner.start(thread_id=tid, goal="ship a hello world endpoint")
    signoff = r1["__interrupt__"][0]  # paused at sign_off
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=signoff.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "ok"},
    )
    # The over-budget fan_out parked at __halt__: the graph reached END (NO ship_gate interrupt),
    # the verdict pre-empted, and NOTHING shipped (no ship receipt in the ledger).
    assert "__interrupt__" not in r2, "an over-budget run must NOT reach the ship gate"
    assert (r2.get("budget_verdict") or {}).get("preempt") is True
    assert not [rec for rec in r2.get("ledger", []) if rec.get("action_kind") == "ship"]
    assert any("budget" in a.lower() for a in r2.get("audit", []))
