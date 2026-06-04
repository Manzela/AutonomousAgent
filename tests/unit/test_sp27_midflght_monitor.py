"""SP-27 oracles — mid-flight monitor node.

Hermetic: only state dicts and the module-level functions are exercised.
No DB, docker, LLM, or network required.

RED/GREEN oracle design:
  P1  loop trips INTERRUPT_FOR_HUMAN (RED: if monitor ignores execution_counts)
  P2  stall/oracle-unsatisfiable trips INTERRUPT_FOR_HUMAN (RED: if monitor ignores fix_attempts)
  N1  healthy state does NOT emit steer_commands (RED: if monitor always fires)
  N4  oracle-unsatisfiable threshold is exact (RED: if threshold is wrong)
  R1  _route_after_monitor INTERRUPT_FOR_HUMAN → "__halt__" (RED: if always "eval_gate")
  R2  _route_after_monitor advisory kind → "eval_gate" (RED: if INJECT_RAG blocks)
  R3  _route_after_fan_out routes to "monitor" post-SP-27 (RED: if returns "eval_gate")
  L1  graph topology: "monitor" node present and healthy call → no steer_commands
"""

from __future__ import annotations


from app.core.graph import (
    _MONITOR_LOOP_THRESHOLD,
    _MONITOR_MAX_FIX_ATTEMPTS,
    _route_after_fan_out,
    _route_after_monitor,
    build_spine,
    monitor,
)
from app.adapters.inmemory.checkpointer import InMemoryCheckpointer

_CFG = {"configurable": {"thread_id": "t-sp27", "__pregel_task_id": "ptask-sp27"}}


# ── P1: loop detection — execution_count key hits the threshold ─────────────
async def test_monitor_loop_detection_emits_interrupt():
    """P1 RED: removing the execution_counts check causes no steer_commands → test fails."""
    state = {
        "thread_id": "t-sp27",
        "fix_attempts": 0,
        "execution_counts": {"t-sp27|fanout-leaf|fix": _MONITOR_LOOP_THRESHOLD},
    }
    delta = await monitor(state, _CFG)
    cmds = delta.get("steer_commands") or []
    assert len(cmds) == 1, f"expected 1 SteerCommand, got {cmds}"
    assert cmds[0]["kind"] == "INTERRUPT_FOR_HUMAN"
    assert "loop-detected" in cmds[0]["reason"]
    assert cmds[0]["thread_id"] == "t-sp27"


# ── P2: oracle-unsatisfiable — fix_attempts hits the threshold ───────────────
async def test_monitor_oracle_unsatisfiable_emits_interrupt():
    """P2 RED: removing the fix_attempts check causes no steer_commands → test fails."""
    state = {
        "thread_id": "t-sp27",
        "fix_attempts": _MONITOR_MAX_FIX_ATTEMPTS,
        "execution_counts": {},
    }
    delta = await monitor(state, _CFG)
    cmds = delta.get("steer_commands") or []
    assert len(cmds) == 1, f"expected 1 SteerCommand, got {cmds}"
    assert cmds[0]["kind"] == "INTERRUPT_FOR_HUMAN"
    assert "oracle-unsatisfiable" in cmds[0]["reason"]


# ── N1: healthy state — no signals, no steer_commands ───────────────────────
async def test_monitor_healthy_no_steer_commands():
    """N1 RED: if monitor always fires, steer_commands is non-empty → test fails."""
    state = {
        "thread_id": "t-sp27",
        "fix_attempts": 0,
        "execution_counts": {"key": 1},
    }
    delta = await monitor(state, _CFG)
    assert not delta.get(
        "steer_commands"
    ), f"healthy state must emit no steer_commands, got {delta.get('steer_commands')}"
    # audit entry always present
    assert delta.get("audit"), "monitor must always emit an audit entry"


# ── N4: threshold boundary — fix=MAX-1 silent; fix=MAX fires ────────────────
async def test_monitor_threshold_boundary_exact():
    """N4 RED: wrong threshold (e.g. > instead of >=) means fix=MAX doesn't trigger."""
    # One below threshold — must NOT trigger
    below = {
        "thread_id": "t-sp27",
        "fix_attempts": _MONITOR_MAX_FIX_ATTEMPTS - 1,
        "execution_counts": {},
    }
    delta_below = await monitor(below, _CFG)
    assert not delta_below.get("steer_commands"), (
        f"fix_attempts={_MONITOR_MAX_FIX_ATTEMPTS - 1} must not trigger "
        f"(threshold is {_MONITOR_MAX_FIX_ATTEMPTS})"
    )

    # AT threshold — must trigger
    at = {
        "thread_id": "t-sp27",
        "fix_attempts": _MONITOR_MAX_FIX_ATTEMPTS,
        "execution_counts": {},
    }
    delta_at = await monitor(at, _CFG)
    cmds = delta_at.get("steer_commands") or []
    assert (
        len(cmds) == 1 and cmds[0]["kind"] == "INTERRUPT_FOR_HUMAN"
    ), f"fix_attempts={_MONITOR_MAX_FIX_ATTEMPTS} must trigger INTERRUPT_FOR_HUMAN"


# ── R1: _route_after_monitor with INTERRUPT_FOR_HUMAN → "__halt__" ──────────
def test_route_after_monitor_interrupt_for_human():
    """R1 RED: if _route_after_monitor always returns "eval_gate" → test fails."""
    state = {
        "steer_commands": [
            {
                "kind": "INTERRUPT_FOR_HUMAN",
                "reason": "loop",
                "thread_id": "t",
                "step_id": "m1",
                "ts": "x",
            }
        ]
    }
    assert _route_after_monitor(state) == "__halt__"


# ── R2: _route_after_monitor advisory kind → "eval_gate" (not blocked) ──────
def test_route_after_monitor_advisory_kind_passes():
    """R2 RED: if INJECT_RAG also routes to __halt__ → test fails."""
    for advisory in ("INJECT_RAG", "SWITCH_TOOL", "CHECKPOINT_REPLAN"):
        state = {
            "steer_commands": [
                {"kind": advisory, "reason": "hint", "thread_id": "t", "step_id": "m2", "ts": "x"}
            ]
        }
        result = _route_after_monitor(state)
        assert (
            result == "eval_gate"
        ), f"advisory kind {advisory!r} must not block eval_gate, got {result!r}"

    # empty list → eval_gate
    assert _route_after_monitor({"steer_commands": []}) == "eval_gate"
    # absent key → eval_gate
    assert _route_after_monitor({}) == "eval_gate"


# ── R3: _route_after_fan_out routes to "monitor" post-SP-27 ─────────────────
def test_route_after_fan_out_routes_to_monitor():
    """R3 RED: if _route_after_fan_out still returns "eval_gate" → test fails.
    SP-27 changes the non-budget path from eval_gate to monitor."""
    # no budget preemption → should go to monitor
    assert _route_after_fan_out({}) == "monitor"
    assert _route_after_fan_out({"budget_verdict": None}) == "monitor"
    assert _route_after_fan_out({"budget_verdict": {"preempt": False}}) == "monitor"

    # budget preemption → __halt__ (unchanged from SP-R2)
    assert _route_after_fan_out({"budget_verdict": {"preempt": True}}) == "__halt__"


# ── M3: last-write semantics — healthy resume clears prior INTERRUPT_FOR_HUMAN ──
async def test_monitor_last_write_clears_prior_interrupt():
    """M3 RED: with _append semantics the prior INTERRUPT_FOR_HUMAN is sticky after resume.
    With last-write the healthy wave's empty list overwrites it → eval_gate. C9 B1 fix."""
    # Step 1: fire on high fix_attempts
    fired_delta = await monitor(
        {"thread_id": "t-sp27", "fix_attempts": _MONITOR_MAX_FIX_ATTEMPTS, "execution_counts": {}},
        _CFG,
    )
    prior_cmd = fired_delta.get("steer_commands") or []
    assert prior_cmd and prior_cmd[0]["kind"] == "INTERRUPT_FOR_HUMAN"

    # Step 2: operator intervened — fix_attempts reset to 0; prior steer_commands still in state
    # (last-write from the previous wave). With _append the prior cmd would persist forever.
    resumed_state = {
        "thread_id": "t-sp27",
        "fix_attempts": 0,  # operator reset
        "execution_counts": {},
        "steer_commands": prior_cmd,  # what _append would have left in state
    }
    resumed_delta = await monitor(resumed_state, _CFG)
    # Last-write: monitor overwrites steer_commands with [] (healthy wave)
    merged = {**resumed_state, "steer_commands": resumed_delta["steer_commands"]}
    assert _route_after_monitor(merged) == "eval_gate", (
        "healthy resume after operator reset must route to eval_gate — "
        "last-write clears the prior INTERRUPT_FOR_HUMAN"
    )


# ── M4: fix_attempts increment — oracle-unsatisfiable signal is live in production ─
async def test_monitor_increments_fix_attempts():
    """M4 RED: if monitor does not increment fix_attempts, the signal is inert in production.
    C9 B2 fix: monitor increments fix_attempts so the oracle-unsatisfiable signal fires
    after _MONITOR_MAX_FIX_ATTEMPTS complete fan_out → monitor cycles."""
    state = {"thread_id": "t-sp27", "fix_attempts": 0, "execution_counts": {}}
    delta = await monitor(state, _CFG)
    assert (
        delta.get("fix_attempts") == 1
    ), f"monitor must increment fix_attempts from 0 to 1, got {delta.get('fix_attempts')}"
    # Verify the increment propagates across waves (simulated chain)
    state2 = {**state, "fix_attempts": delta["fix_attempts"]}
    delta2 = await monitor(state2, _CFG)
    assert delta2.get("fix_attempts") == 2
    # At threshold: increment still happens (check is pre-increment)
    state_at = {
        "thread_id": "t-sp27",
        "fix_attempts": _MONITOR_MAX_FIX_ATTEMPTS,
        "execution_counts": {},
    }
    delta_at = await monitor(state_at, _CFG)
    assert delta_at.get("fix_attempts") == _MONITOR_MAX_FIX_ATTEMPTS + 1


# ── L1: graph topology — "monitor" node present and healthy path through it ─
async def test_graph_topology_includes_monitor_node():
    """L1: the compiled graph has a 'monitor' node; a direct healthy call produces
    no steer_commands; _route_after_monitor then routes to eval_gate."""
    cp = InMemoryCheckpointer()
    app = build_spine(cp.build_saver())
    graph_def = app.get_graph()
    node_names = set(graph_def.nodes.keys())
    assert (
        "monitor" in node_names
    ), f"'monitor' node absent from compiled graph; found: {sorted(node_names)}"

    # Healthy direct call → no steer_commands
    healthy_state = {
        "thread_id": "t-l1",
        "fix_attempts": 0,
        "execution_counts": {},
    }
    cfg = {"configurable": {"thread_id": "t-l1", "__pregel_task_id": "ptl1"}}
    delta = await monitor(healthy_state, cfg)
    assert not delta.get("steer_commands")
    # _route_after_monitor on the merged state → eval_gate
    merged = {**healthy_state, **(delta or {})}
    assert _route_after_monitor(merged) == "eval_gate"
