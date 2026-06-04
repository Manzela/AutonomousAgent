"""SP-16 (slice 1) — AbstractBoard + InMemory adapter + C14 gate-derived `done`.

The load-bearing safety property is **C14**: a card's `done` is GATE-DERIVED, never agent-initiated.
The invariant is enforced at the AbstractBoard PUBLIC API (create_card/set_status refuse `done` AND
any non-vocabulary look-alike; `done` is reachable ONLY via mark_done(card_id, gate=...)). `_put` is
a protected primitive BELOW the contract (Python can't sandbox same-process code) — the agent uses
the public API, and the meta-test pins that the InMemory adapter does not override the C14 methods.
Every oracle carries its RED control. Hermetic: in-memory store + mock gate.
"""

from __future__ import annotations

import inspect

import pytest

from app.adapters.inmemory.board import InMemoryBoard
from app.core.board import (
    AbstractBoard,
    BoardError,
    BoardProjection,
    Card,
    GateNotPassedError,
    project_plan,
)


class _Gate:
    """A mock C14 gate-reader: records the card it was asked about and returns a fixed verdict."""

    def __init__(self, verdict: bool) -> None:
        self._verdict = verdict
        self.calls: list[Card] = []

    def required_checks_passed(self, card: Card) -> bool:
        self.calls.append(card)
        return self._verdict


def _board() -> InMemoryBoard:
    return InMemoryBoard()


def _plan(nodes):
    return {"nodes": nodes, "edges": [(d, n["id"]) for n in nodes for d in n["depends_on"]]}


def _node(nid, deps=()):
    return {
        "id": nid,
        "phase": "draft",
        "summary": f"do {nid}",
        "depends_on": list(deps),
        "acceptance_ref": "0",
        "allowed_paths": [f"app/{nid}.py"],
    }


# ── O1 basic CRUD: create -> get round-trips with the default non-done status ──
def test_create_and_get_card():
    b = _board()
    c = b.create_card(title="t", thread_id="th1", node_id="n0")
    assert c.status == "todo" and c.thread_id == "th1" and c.node_id == "n0"
    assert b.get_card(c.id) == c
    with pytest.raises(BoardError):
        b.get_card("nope")  # unknown id is an error, not a silent None


# ── O2 non-done transitions are allowed (Hermes vocab: todo -> ready -> running -> blocked) ──
def test_set_status_non_done_transitions():
    b = _board()
    c = b.create_card(title="t", thread_id="th1")
    assert b.set_status(c.id, "ready").status == "ready"
    assert b.set_status(c.id, "running").status == "running"
    assert b.set_status(c.id, "blocked").status == "blocked"


# ── O3 C14 (load-bearing): the agent CANNOT self-mark `done` — incl. case/whitespace variants ──
def test_c14_refuses_agent_initiated_done():
    b = _board()
    c = b.create_card(title="t", thread_id="th1")
    with pytest.raises(BoardError):
        b.set_status(c.id, "done")  # RED: a board allowing this lets the agent self-certify
    # The look-alike bypass: "Done"/" done"/"DONE"/"done\n" must NOT smuggle a ~done status past
    # the exact-match guard (they are not CardStatus members) — each is rejected, status unchanged.
    for variant in ("Done", "DONE", " done", "done\n", "done "):
        with pytest.raises(BoardError):
            b.set_status(c.id, variant)
    assert b.get_card(c.id).status == "todo"  # unchanged after every refusal
    # ...and a card cannot be CREATED done (or a look-alike) either — the same invariant at birth.
    with pytest.raises(BoardError):
        b.create_card(title="t2", thread_id="th1", status="done")
    with pytest.raises(BoardError):
        b.create_card(title="t3", thread_id="th1", status="Done")


# ── O4 C14: `done` is reachable ONLY via mark_done, and ONLY when the gate passes ──
def test_c14_mark_done_is_gate_derived():
    b = _board()
    c = b.create_card(title="t", thread_id="th1", node_id="n0", gate_ref="PR#42")
    # Failing gate -> refuse, status unchanged (RED control).
    failing = _Gate(False)
    with pytest.raises(GateNotPassedError):
        b.mark_done(c.id, gate=failing)
    assert b.get_card(c.id).status != "done"
    assert failing.calls and failing.calls[0].gate_ref == "PR#42"  # gate got the real card
    # Passing gate -> done.
    passing = _Gate(True)
    assert b.mark_done(c.id, gate=passing).status == "done"
    assert b.get_card(c.id).status == "done"


# ── O5 mark_done is IDEMPOTENT: an already-done card is returned WITHOUT re-gating (resume-safe) ──
def test_mark_done_idempotent():
    b = _board()
    c = b.create_card(title="t", thread_id="th1")
    b.mark_done(c.id, gate=_Gate(True))
    # A second mark_done — even with a now-FAILING gate — short-circuits (gate not consulted).
    failing = _Gate(False)
    assert b.mark_done(c.id, gate=failing).status == "done"
    assert failing.calls == []  # the gate was NEVER called for an already-done card


# ── O6 the C14 invariant lives in the ABC's concrete methods (the shipped adapter can't bypass) ──
def test_c14_invariant_is_in_the_abstract_base():
    # create_card/set_status/mark_done are defined on AbstractBoard, NOT overridden by InMemoryBoard,
    # so the gate-derived-done invariant holds for the shipped adapter (the override path; a hostile
    # subclass reaching past the facade is the adapter author's trust boundary, not the C14 threat).
    for name in ("set_status", "mark_done", "create_card"):
        assert name in AbstractBoard.__dict__, f"{name} must be concrete on AbstractBoard (C14)"
        assert name not in InMemoryBoard.__dict__, f"{name} must NOT be overridden by an adapter"


# ── O7 DAG -> cards: a plan projects a parent goal card + one child card per node ──
def test_project_plan_to_cards():
    b = _board()
    plan = _plan([_node("n0"), _node("n1", ["n0"]), _node("n2", ["n1"])])
    proj = project_plan(b, plan, thread_id="th1", goal_title="ship X")
    assert isinstance(proj, BoardProjection)
    parent = b.get_card(proj.parent_id)
    assert parent.title == "ship X" and parent.node_id is None
    children = [b.get_card(proj.node_cards[nid]) for nid in ("n0", "n1", "n2")]
    assert all(ch.parent_id == proj.parent_id for ch in children)  # hierarchy linked
    assert [ch.node_id for ch in children] == ["n0", "n1", "n2"]  # node_id preserved
    assert b.list_cards(thread_id="th1") and not b.list_cards(thread_id="other")


# ── O8 project_plan validates its (untyped) input — no `__root__` clobber, no orphans/KeyError ──
def test_project_plan_rejects_malformed_plan():
    b = _board()
    # A node literally named "__root__" no longer clobbers the parent (typed return, not a magic key).
    proj = project_plan(b, _plan([_node("__root__")]), thread_id="th1")
    assert proj.parent_id != proj.node_cards["__root__"]  # parent + the node are DISTINCT cards
    # Duplicate node id -> BoardError (not a silently-orphaned card).
    with pytest.raises(BoardError):
        project_plan(_board(), _plan([_node("dup"), _node("dup")]), thread_id="th1")
    # A node missing 'id' -> BoardError (not a bare KeyError).
    with pytest.raises(BoardError):
        project_plan(_board(), {"nodes": [{"summary": "no id"}], "edges": []}, thread_id="th1")


# ── O9 outbound-only invariant: graph nodes WRITE the board projection but never READ it (C15) ──
def test_graph_nodes_do_not_read_board_state():
    # SP-16 slice-2 WIRES the board: a spine node WRITES the outbound projection (project_plan ->
    # create_card, set_status). C15 still forbids a node from READING board state for control flow —
    # inbound board state reaches the graph ONLY via the (deferred) SP-17 SteeringEventBus. So NO
    # board READ call appears in the spine; a read would make the board a parallel store and break
    # thread_id-is-the-single-source-of-truth. RED: a node calling board.get_card/list_comment fails.
    import app.core.graph as graph_mod

    src = inspect.getsource(graph_mod)
    for read_call in (".get_card(", ".list_cards(", ".get_comment(", ".list_comment("):
        assert read_call not in src, (
            f"a spine node READS board state via {read_call!r} — the board is outbound-only; "
            f"inbound board state reaches the graph ONLY via the SP-17 SteeringEventBus (C15)"
        )
