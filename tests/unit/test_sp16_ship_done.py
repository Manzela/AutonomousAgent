"""SP-16 slice 3 — the ship-gate `done` transition: the parent goal card reaches `done` at
ship_effect, GATE-DERIVED (C14). This makes the C14 mark_done primitive LIVE in the spine (it was
only tested before). A passing gate closes the root card; a failing gate leaves it open (fail-safe);
gate=None keeps the legacy no-done path. SpineRunner injects an AlwaysReadyGate CI stub (the real
`gh pr checks --required` GateReader is deferred). Hermetic: InMemoryBoard + a mock gate.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.adapters.inmemory.board import AlwaysReadyGate, InMemoryBoard
from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core.board import Card, project_plan
from app.core.graph import _build_nodes, _default_capability
from app.core.spine_runner import SpineRunner
from lib.anchors.spec_store import SpecStore
from lib.anchors.task_spec import Scope, TaskSpec

_CFG = {"configurable": {"thread_id": "t", "__pregel_task_id": "p1"}}


class _Gate:
    def __init__(self, verdict: bool) -> None:
        self._verdict = verdict
        self.calls: list[Card] = []

    def required_checks_passed(self, card: Card) -> bool:
        self.calls.append(card)
        return self._verdict


def _node(nid, deps=()):
    return {
        "id": nid,
        "phase": "draft",
        "summary": f"do {nid}",
        "depends_on": list(deps),
        "acceptance_ref": "0",
        "allowed_paths": [f"app/{nid}.py"],
    }


def _locked_spec(store: SpecStore) -> TaskSpec:
    spec = TaskSpec(
        title="widget",
        intent="add a widget",
        acceptance_criteria=["implement app/core/widget.py", "verify tests/unit/test_widget.py"],
        scope=Scope(in_scope=["app/core"], out_of_scope=["docs"]),
        success_metrics=["green"],
        spec_id=uuid4(),
        spec_sha="0" * 64,
        created_at=datetime.now(timezone.utc),
        created_by=0,
    )
    return store.save(spec.model_copy(update={"status": "locked"}))


def _ship_state(board) -> tuple[dict, str]:
    proj = project_plan(board, {"nodes": [_node("n0")], "edges": []}, thread_id="t")
    state = {
        "thread_id": "t",
        "board_cards": {"parent": proj.parent_id, "nodes": proj.node_cards},
        "ship": {"interrupt_id": "i1"},
        "ledger": [],  # empty -> apply_once runs the real ship effect
        "execution_counts": {},
        "goal": "g",
        "spec_sha": "0" * 64,
    }
    return state, proj.parent_id


# ── O1 the root goal card reaches `done` at ship_effect when the gate passes (C14, live) ──
async def test_ship_effect_marks_root_done_on_passing_gate():
    board = InMemoryBoard()
    state, parent_id = _ship_state(board)
    gate = _Gate(True)
    nodes = _build_nodes(_default_capability(), board=board, gate=gate)
    await nodes["ship_effect"](state, _CFG)
    assert board.get_card(parent_id).status == "done"  # gate-derived done — the root card closed
    assert gate.calls and gate.calls[0].id == parent_id  # the gate was consulted on the root card


# ── O2 a FAILING gate leaves the root card OPEN — CONSULTED-and-REFUSED, not a no-op (RED control) ──
async def test_ship_effect_failing_gate_leaves_root_open():
    board = InMemoryBoard()
    state, parent_id = _ship_state(board)
    gate = _Gate(False)
    nodes = _build_nodes(_default_capability(), board=board, gate=gate)
    await nodes["ship_effect"](state, _CFG)
    # Strong oracle: prove mark_done was REACHED + the gate REFUSED (not that nothing tried to write
    # done). The gate was consulted on the root card, and the root is EXACTLY where it started (todo),
    # not merely != done. RED: a missing/short-circuited mark_done would leave gate.calls empty.
    assert gate.calls and gate.calls[0].id == parent_id  # the gate WAS consulted on the root card
    assert (
        board.get_card(parent_id).status == "todo"
    )  # refused -> root unchanged (exact, not != done)


# ── O3 back-compat: gate=None => NO done transition (the legacy path is byte-identical) ──
async def test_ship_effect_gate_none_no_done_transition():
    board = InMemoryBoard()
    state, parent_id = _ship_state(board)
    nodes = _build_nodes(_default_capability(), board=board)  # gate=None default
    await nodes["ship_effect"](state, _CFG)
    assert board.get_card(parent_id).status != "done"


# ── O4 the done-transition is ledger-guarded: a SKIPPED (already-shipped) ship_effect re-entry does
#    NOT re-consult the gate (mark_done is idempotent + the effect is exactly-once) ──
async def test_ship_effect_done_not_redone_on_skip():
    board = InMemoryBoard()
    state, parent_id = _ship_state(board)
    nodes = _build_nodes(_default_capability(), board=board, gate=_Gate(True))
    out = await nodes["ship_effect"](state, _CFG)
    # replay with the receipt already present -> apply_once SKIPs the effect; the root is already done
    replayed_ledger = (state.get("ledger") or []) + out.get("ledger", [])
    gate2 = _Gate(False)  # a now-failing gate must NOT reopen an already-done root
    nodes2 = _build_nodes(_default_capability(), board=board, gate=gate2)
    await nodes2["ship_effect"]({**state, "ledger": replayed_ledger}, _CFG)
    assert board.get_card(parent_id).status == "done"  # stays done (idempotent)
    assert gate2.calls == []  # mark_done short-circuited the already-done card; gate not consulted


# ── O5 LIVE end-to-end: a full SpineRunner run ships and closes the root card via the stub gate ──
async def test_live_spine_closes_root_card_on_ship(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))
    board = InMemoryBoard()
    runner = SpineRunner(InMemoryCheckpointer(), board=board)
    tid = "t-e2e"
    r1 = await runner.start(thread_id=tid, goal="ship a hello world endpoint")
    signoff = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=signoff.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "ok"},
    )
    ship = r2["__interrupt__"][0]
    await runner.resume(
        thread_id=tid,
        interrupt_id=ship.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "ship it"},
    )
    parents = [c for c in board.list_cards(thread_id=tid) if c.node_id is None]
    assert parents and parents[0].status == "done"  # the shipped goal's root card is closed


# ── O6 SpineRunner defaults to the AlwaysReadyGate CI stub (the live entrypoint can close roots) ──
def test_spine_runner_defaults_to_always_ready_gate():
    runner = SpineRunner(InMemoryCheckpointer())
    assert isinstance(runner._gate, AlwaysReadyGate)
