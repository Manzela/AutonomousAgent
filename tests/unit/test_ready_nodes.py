"""SP-11 — ready_nodes() frontier (the DAG-topology gate that fan_out dispatches per wave)."""

from __future__ import annotations

import pytest

from app.core.decompose import DecompositionError, ready_nodes


def _node(nid, deps):
    return {
        "id": nid,
        "phase": "draft",
        "summary": nid,
        "depends_on": list(deps),
        "acceptance_ref": "0",
        "allowed_paths": [f"app/{nid}.py"],
    }


def _plan(nodes):
    edges = [(dep, n["id"]) for n in nodes for dep in n["depends_on"]]
    return {"nodes": nodes, "edges": edges}


def _ids(nodes):
    return sorted(n["id"] for n in nodes)


# ── diamond A → {B,C} → D: the frontier advances wave by wave ─────────────────────────
def test_diamond_frontier_advances():
    plan = _plan([_node("A", []), _node("B", ["A"]), _node("C", ["A"]), _node("D", ["B", "C"])])
    assert _ids(ready_nodes(plan, set())) == ["A"]  # roots only
    assert _ids(ready_nodes(plan, {"A"})) == ["B", "C"]  # the parallel wave
    assert _ids(ready_nodes(plan, {"A", "B"})) == ["C"]  # D still blocked on C
    assert _ids(ready_nodes(plan, {"A", "B", "C"})) == ["D"]  # join unblocked
    assert ready_nodes(plan, {"A", "B", "C", "D"}) == []  # all done


# ── pure chain n0 → n1 → n2: a SINGLE node per wave (no spurious fan-out) ─────────────
def test_chain_yields_one_per_wave():
    plan = _plan([_node("n0", []), _node("n1", ["n0"]), _node("n2", ["n1"])])
    assert _ids(ready_nodes(plan, set())) == ["n0"]
    assert _ids(ready_nodes(plan, {"n0"})) == ["n1"]
    assert _ids(ready_nodes(plan, {"n0", "n1"})) == ["n2"]


# ── empty done → indegree-0 roots only (the first-wave breadth) ───────────────────────
def test_empty_done_is_roots():
    plan = _plan([_node("r1", []), _node("r2", []), _node("c", ["r1", "r2"])])
    assert _ids(ready_nodes(plan, set())) == ["r1", "r2"]


# ── a done node is never re-dispatched ───────────────────────────────────────────────
def test_done_node_excluded():
    plan = _plan([_node("a", []), _node("b", ["a"])])
    assert _ids(ready_nodes(plan, {"a", "b"})) == []


# ── structural validation still bites (cycle / self-loop) ────────────────────────────
def test_cycle_raises():
    # a<->b cycle (edges mirror depends_on)
    plan = _plan([_node("a", ["b"]), _node("b", ["a"])])
    with pytest.raises(DecompositionError):
        ready_nodes(plan, set())
