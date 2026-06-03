"""SP-16 slice 2 — the board projection is WIRED into the live spine.

decompose projects the locked plan onto an injected board (parent goal card + child node cards),
idempotently (a re-run never duplicates cards); fan_out reflects execution (dispatched -> running,
FAILED -> blocked; `done` is NEVER set — C14 gate-derived at ship, deferred). board=None keeps the
legacy no-projection path (byte-identical to pre-SP-16). Hermetic: InMemoryBoard + the InMemory
decomposer + a sealed spec in a tmp SpecStore.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.adapters.inmemory.board import InMemoryBoard
from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core.board import project_plan
from app.core.graph import _build_nodes, _default_capability
from app.core.schemas import AgentCapability, AgentID, ExecutionResult, TaskStatus
from app.core.spine_runner import SpineRunner
from lib.anchors.spec_store import SpecStore
from lib.anchors.task_spec import Scope, TaskSpec

_CFG = {"configurable": {"thread_id": "t"}}


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


def _node(nid, deps=()):
    return {
        "id": nid,
        "phase": "draft",
        "summary": f"do {nid}",
        "depends_on": list(deps),
        "acceptance_ref": "0",
        "allowed_paths": [f"app/{nid}.py"],
    }


def _selective_capability(fail_ids: set[str]) -> AgentCapability:
    async def _invoke(req):
        status = TaskStatus.FAILED if req.task_id in fail_ids else TaskStatus.COMPLETED
        return ExecutionResult(task_id=req.task_id, status=status)

    return AgentCapability(
        agent_id=AgentID("sel"),
        version="1",
        phase="draft",
        description="selective",
        invoke=_invoke,
    )


# ── O1 decompose projects the DAG onto the board: a parent goal card + one child per node ──
async def test_decompose_projects_cards(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))
    sealed = _locked_spec(SpecStore(tmp_path))
    board = InMemoryBoard()
    nodes = _build_nodes(_default_capability(), board=board)
    out = await nodes["decompose"](
        {"thread_id": "t", "goal": "add a widget", "spec_id": str(sealed.spec_id)}, _CFG
    )
    bc = out["board_cards"]
    cards = board.list_cards(thread_id="t")
    assert len(cards) == 3  # 1 parent goal card + 2 node cards (one per acceptance criterion)
    parent = board.get_card(bc["parent"])
    assert parent.node_id is None and parent.title == "add a widget"
    assert set(bc["nodes"]) == {n["id"] for n in out["plan"]["nodes"]}
    for nid, cid in bc["nodes"].items():
        ch = board.get_card(cid)
        assert ch.node_id == nid and ch.parent_id == parent.id and ch.status == "todo"


# ── O2 idempotent: re-running decompose with a prior projection does NOT duplicate cards ──
async def test_decompose_projection_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))
    sealed = _locked_spec(SpecStore(tmp_path))
    board = InMemoryBoard()
    nodes = _build_nodes(_default_capability(), board=board)
    state = {"thread_id": "t", "goal": "g", "spec_id": str(sealed.spec_id)}
    out1 = await nodes["decompose"](state, _CFG)
    n_after_first = len(board.list_cards())
    # feed board_cards back (as the checkpointer would on a re-entry) and re-run
    out2 = await nodes["decompose"]({**state, "board_cards": out1["board_cards"]}, _CFG)
    assert "board_cards" not in out2  # no re-projection delta
    assert len(board.list_cards()) == n_after_first  # RED: a guard-less project would duplicate


# ── O3 back-compat: board=None => NO projection (no board_cards key, byte-identical) ──
async def test_board_none_no_projection(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))
    sealed = _locked_spec(SpecStore(tmp_path))
    nodes = _build_nodes(_default_capability())  # board=None default
    out = await nodes["decompose"]({"thread_id": "t", "spec_id": str(sealed.spec_id)}, _CFG)
    assert "board_cards" not in out


# ── O4 fan_out reflects execution: dispatched -> running; a FAILED leaf -> blocked; never done ──
async def test_fan_out_transitions_card_status():
    board = InMemoryBoard()
    plan = {"nodes": [_node("n0"), _node("n1")], "edges": []}
    proj = project_plan(board, plan, thread_id="t")
    board_cards = {"parent": proj.parent_id, "nodes": proj.node_cards}
    nodes = _build_nodes(_selective_capability({"n1"}), sandbox=None, board=board)
    state = {
        "thread_id": "t",
        "goal": "g",
        "plan": plan,
        "base_ref": "HEAD",
        "board_cards": board_cards,
    }
    await nodes["fan_out"](state, _CFG)
    assert board.get_card(proj.node_cards["n0"]).status == "running"  # COMPLETED -> running
    assert board.get_card(proj.node_cards["n1"]).status == "blocked"  # FAILED -> blocked
    # C14: fan_out NEVER marks a card done (done is gate-derived at ship — deferred)
    assert all(c.status != "done" for c in board.list_cards(thread_id="t"))


# ── O5 LIVE end-to-end: SpineRunner injects a board; a real run renders the DAG as cards ──
async def test_live_spine_renders_dag_as_cards(tmp_path, monkeypatch):
    monkeypatch.setenv("SPINE_SPEC_STORE", str(tmp_path))
    board = InMemoryBoard()
    runner = SpineRunner(InMemoryCheckpointer(), board=board)
    tid = "t-e2e"
    r1 = await runner.start(thread_id=tid, goal="ship a hello world endpoint")
    signoff = r1["__interrupt__"][0]
    await runner.resume(
        thread_id=tid,
        interrupt_id=signoff.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "ok"},
    )
    # past seal_spec -> decompose: the board now holds the projected cards for this thread.
    cards = board.list_cards(thread_id=tid)
    assert cards, "the live spine must project the DAG onto the injected board"
    assert any(c.node_id is None for c in cards)  # the parent goal card
    assert any(c.node_id is not None for c in cards)  # at least one child node card


# ── O6 the SpineRunner default board is an InMemoryBoard (the live entrypoint always projects) ──
def test_spine_runner_defaults_to_inmemory_board():
    runner = SpineRunner(InMemoryCheckpointer())
    assert isinstance(runner._board, InMemoryBoard)
