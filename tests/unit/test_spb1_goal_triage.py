"""SP-B1 — GoalManager ↔ decomposer entry bridge.

goal_intake creates a triage card immediately on goal arrival (before sign_off), giving the
operator a board entry before the plan is approved.  The decomposer "picks it up" by promoting
the card (triage → todo) and using it as the parent goal card.  The root card mark_done closes
at ship_effect — so the SAME card id spans the full lifecycle: triage → todo → ... → done.

All tests are hermetic: InMemoryBoard, InMemory fixtures, no DB/docker/LLM/net/gh.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.adapters.inmemory.board import AlwaysReadyGate, InMemoryBoard
from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core.board import project_plan
from app.core.graph import _build_nodes, _default_capability
from app.core.spine_runner import SpineRunner
from lib.anchors.spec_store import SpecStore
from lib.anchors.task_spec import Scope, TaskSpec

_CFG = {"configurable": {"thread_id": "t"}}
_BASE_STATE = {
    "thread_id": "t",
    "goal": "add a widget",
    "clarifications": [],
    "tasks": [],
    "ledger": [],
    "execution_counts": {},
    "decision_record": [],
    "audit": [],
    "steering_events": [],
    "cost_accumulator": {},
    "fix_attempts": 0,
    "scrubbed": True,
}


def _locked_spec(store: SpecStore) -> TaskSpec:
    spec = TaskSpec(
        title="widget",
        intent="add a widget to app/core",
        acceptance_criteria=[
            "implement app/core/widget.py with the widget class",
            "verify tests/unit/test_widget.py covers it",
        ],
        scope=Scope(in_scope=["app/core"], out_of_scope=["docs"]),
        success_metrics=["widget tests green"],
        spec_id=uuid4(),
        spec_sha="0" * 64,
        created_at=datetime.now(timezone.utc),
        created_by=0,
    )
    return store.save(spec.model_copy(update={"status": "locked"}))


# ── B1: goal_intake creates a triage card when a board is injected ──
async def test_goal_intake_creates_triage_card():
    board = InMemoryBoard()
    nodes = _build_nodes(_default_capability(), board=board)
    out = await nodes["goal_intake"](_BASE_STATE, _CFG)
    assert "triage_card_id" in out
    card = board.get_card(out["triage_card_id"])
    assert card.status == "triage"
    assert card.thread_id == "t"
    assert card.node_id is None  # the parent card, not a child


# ── B2: board=None => no triage card (byte-identical to pre-SP-B1) ──
async def test_goal_intake_no_board_no_triage_card():
    nodes = _build_nodes(_default_capability())  # board=None default
    out = await nodes["goal_intake"](_BASE_STATE, _CFG)
    assert "triage_card_id" not in out


# ── B3: idempotent — goal_intake skips card creation if triage_card_id already in state ──
async def test_goal_intake_idempotent_on_resume():
    board = InMemoryBoard()
    nodes = _build_nodes(_default_capability(), board=board)
    first = await nodes["goal_intake"](_BASE_STATE, _CFG)
    triage_id = first["triage_card_id"]
    # simulate resume: triage_card_id already in state
    state_with_triage = {**_BASE_STATE, "triage_card_id": triage_id}
    second = await nodes["goal_intake"](state_with_triage, _CFG)
    # no NEW card created; the key is absent from the second delta (no override)
    assert "triage_card_id" not in second
    assert len(board.list_cards(thread_id="t")) == 1  # RED: a guard-less path creates a duplicate


# ── B4: decompose promotes the triage card to todo and uses it as the parent ──
async def test_decompose_promotes_triage_card(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))
    sealed = _locked_spec(SpecStore(tmp_path))
    board = InMemoryBoard()
    nodes = _build_nodes(_default_capability(), board=board)
    # simulate: triage card already created by goal_intake
    triage_card = board.create_card(title="add a widget", thread_id="t", status="triage")
    state = {**_BASE_STATE, "spec_id": str(sealed.spec_id), "triage_card_id": triage_card.id}
    out = await nodes["decompose"](state, _CFG)
    bc = out["board_cards"]
    # THE BRIDGE INVARIANT: the parent IS the triage card (not a new card)
    assert bc["parent"] == triage_card.id  # RED: without SP-B1, decompose creates a NEW parent
    # The triage card was promoted to todo
    assert board.get_card(triage_card.id).status == "todo"
    # Children are created under the promoted parent
    for nid, cid in bc["nodes"].items():
        child = board.get_card(cid)
        assert child.parent_id == triage_card.id
        assert child.node_id == nid


# ── B5: decompose creates a NEW parent when no triage_card_id (backward-compat preserved) ──
async def test_decompose_creates_new_parent_without_triage(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))
    sealed = _locked_spec(SpecStore(tmp_path))
    board = InMemoryBoard()
    nodes = _build_nodes(_default_capability(), board=board)
    state = {**_BASE_STATE, "spec_id": str(sealed.spec_id)}  # no triage_card_id
    out = await nodes["decompose"](state, _CFG)
    parent_id = out["board_cards"]["parent"]
    parent = board.get_card(parent_id)
    assert parent.status == "todo"
    assert parent.node_id is None


# ── B6: triage card id == board_cards["parent"] == the card mark_done closes ──
async def test_triage_card_identity_across_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))
    sealed = _locked_spec(SpecStore(tmp_path))
    board = InMemoryBoard()
    nodes = _build_nodes(_default_capability(), board=board)
    # goal_intake
    g_out = await nodes["goal_intake"](_BASE_STATE, _CFG)
    triage_id = g_out["triage_card_id"]
    # decompose
    state = {**_BASE_STATE, **g_out, "spec_id": str(sealed.spec_id)}
    d_out = await nodes["decompose"](state, _CFG)
    parent_id = d_out["board_cards"]["parent"]
    # The bridge invariant: same card throughout
    assert parent_id == triage_id  # triage card IS the parent card
    # Simulate mark_done via the gate
    gate = AlwaysReadyGate()
    board.mark_done(parent_id, gate=gate)
    assert board.get_card(triage_id).status == "done"  # root closed


# ── B7: project_plan backward-compat — parent_card_id=None creates a new parent (API unchanged) ──
def test_project_plan_parent_card_id_none_creates_new_parent():
    board = InMemoryBoard()
    plan = {
        "nodes": [
            {
                "id": "n0",
                "summary": "do n0",
                "depends_on": [],
                "acceptance_ref": "0",
                "allowed_paths": [],
            }
        ],
        "edges": [],
    }
    proj = project_plan(board, plan, thread_id="t")  # no parent_card_id
    parent = board.get_card(proj.parent_id)
    assert parent.node_id is None
    assert parent.status == "todo"
    assert len(board.list_cards(thread_id="t")) == 2  # parent + 1 child


# ── B8: live end-to-end SpineRunner — triage card created, promoted, root closed at ship ──
async def test_live_spine_triage_card_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))
    board = InMemoryBoard()
    gate = AlwaysReadyGate()
    runner = SpineRunner(InMemoryCheckpointer(), board=board, gate=gate)
    tid = "t-spb1-e2e"
    # start: goal_intake -> clarify -> sign_off interrupt
    r1 = await runner.start(thread_id=tid, goal="add a widget end-to-end")
    # a triage card must exist immediately (before sign_off approval)
    all_cards = board.list_cards(thread_id=tid)
    triage_cards = [c for c in all_cards if c.status == "triage"]
    assert triage_cards, "triage card must be created at goal_intake before sign_off"
    triage_id = triage_cards[0].id
    # approve sign_off → flows through seal_spec -> decompose -> fan_out -> eval_gate
    signoff = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=signoff.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "ok"},
    )
    # after decompose, triage card promoted to todo (and becomes parent of child cards)
    parent_after_decompose = board.get_card(triage_id)
    assert parent_after_decompose.status in (
        "todo",
        "done",
    ), f"expected todo or done after decompose, got {parent_after_decompose.status!r}"
    children = [c for c in board.list_cards(thread_id=tid) if c.node_id is not None]
    assert children, "child node cards must exist after decompose"
    for child in children:
        assert (
            child.parent_id == triage_id
        ), f"child card parent must be the triage card, got {child.parent_id!r}"
    # approve ship_gate if present
    if r2.get("__interrupt__"):
        ship = r2["__interrupt__"][0]
        await runner.resume(
            thread_id=tid,
            interrupt_id=ship.id,
            decision={"verb": "APPROVE", "actor": "op", "reason": "ship it"},
        )
    # root card (= triage card) must be done at the end
    root = board.get_card(triage_id)
    assert root.status == "done", f"root card must be `done` after ship_effect; got {root.status!r}"
