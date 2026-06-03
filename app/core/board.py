"""AbstractBoard — the SP-16 kanban projection seam (C14 gate-derived `done`).

The agent's DAG is projected onto a kanban board (parent goal card + child node cards).
The board is an OUTBOUND projection by default: a graph node MUST NOT read board state
directly — any inbound human board comment reaches the graph ONLY via the (deferred) SP-17
`SteeringEventBus`, preserving `thread_id` as the single source of truth (PRD §6 SP-16 / C15).

Hybrid adapter pattern (CLAUDE.md / 04-gcp-native-adapter-plan.md):

  - app/core/board.py                  — THIS abstract base + the Card value object. The
                                         C14 invariant lives in its CONCRETE PUBLIC methods.
  - app/adapters/inmemory/board.py     — InMemoryBoard: a dict-backed store for hermetic CI.
  - app/adapters/hermes/board.py       — DEFERRED: wraps Hermes' `hermes_cli.kanban_db`
                                         (create_task/block_task/complete_task/archive_task),
                                         absent on disk (the hermes-agent submodule). A sibling,
                                         never a replacement (ABC not collapsed).

C14 — `done` is GATE-DERIVED (PRD §6 SP-16, "card `done` is gate-derived (reads
`gh pr checks --required`), refuses agent-initiated `done`"):

  The invariant is enforced at the AbstractBoard PUBLIC API — the surface the spine/agent uses:
    * `create_card(status="done")` and `set_status(.., "done")` are refused; both ALSO reject any
      value outside the CardStatus vocabulary, so case/whitespace look-alikes ("Done", "done ")
      cannot smuggle a ~done status past the exact-match guard;
    * `done` is reachable ONLY via `mark_done(card_id, gate=...)`, and ONLY when the injected
      `GateReader` confirms the required checks are green; mark_done is idempotent (an already-done
      card is returned without re-gating — resume-safe).

  HONEST BOUNDARY (the threat model is the AGENT calling the PUBLIC API): `_put` / `_new_id` are
  PROTECTED storage primitives BELOW the contract — Python cannot sandbox same-process code, so a
  caller that deliberately reaches past the facade into `_put` (or a subclass that overrides the
  concrete methods) can forge a `done` card. That is the adapter author's trust boundary, NOT the
  C14 adversary; the `test_c14_invariant_is_in_the_abstract_base` meta-test guards the shipped
  InMemoryBoard against the override path. The GateReader is likewise CALLER-INJECTED this slice —
  it becomes a binding safety control only once slice-2 binds it to the trusted `gh pr checks`
  concretion OUT of the agent's reach (DEFERRED); until then `done` is caller-trust-derived.

DEFERRED (named, NOT silently dropped):
  * the spine->cards PROJECTION wiring (decompose -> cards, status transitions) — SP-16 slice-2;
  * the Hermes `kanban_db` adapter (which owes a translation between this board vocabulary and
    Hermes' own lifecycle) + the real `gh pr checks --required` GateReader concretion;
  * (4.1) per-card live cost = SP-O1 `llm.call.cost` scoped to (thread_id, node_id) — its proof is
    a Cloud-Monitoring query (NOT hermetic), so it lands with the cost-render path;
  * (4.2) U-4 milestone comments + SP-27 SteerCommand projections (need SP-21 origin-tag dedup);
  * the Plane CE adapter (SP-21) and the Telegram bridge listener.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal, Optional, Protocol, get_args, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# The board's card lifecycle. Mirrors Hermes' kanban vocabulary (lib/kanban/notification_policy.py:11
# "triage, todo, ready, running, blocked, done, archived"; the transition map runs
# todo->ready->running->{blocked,done,failure}). `done` is the C14-guarded terminal. The DEFERRED
# Hermes `kanban_db` adapter owns any mapping nuance (e.g. its `failure` sink).
CardStatus = Literal["triage", "todo", "ready", "running", "blocked", "done", "archived"]
_CARD_STATUSES: frozenset[str] = frozenset(get_args(CardStatus))


class Card(BaseModel):
    """One kanban card. Immutable (frozen) — a transition returns a NEW card via model_copy,
    and the board re-puts it; callers never mutate a Card in place."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    title: str
    thread_id: str  # the SP-01 correlation key — every card is scoped to its graph
    status: CardStatus = "todo"
    node_id: Optional[str] = None  # the SP-02 TaskNode.id (None for the parent goal card)
    parent_id: Optional[str] = None  # the goal card id (None for the parent itself)
    gate_ref: Optional[str] = None  # the PR ref the C14 GateReader reads (e.g. "PR#42")
    body: Optional[str] = Field(default=None)


class BoardError(Exception):
    """A board contract violation (unknown card, an invalid status, or a C14-refused `done`)."""


class GateNotPassedError(BoardError):
    """C14: `mark_done` was called but the GateReader reports the required checks are not green."""


@runtime_checkable
class GateReader(Protocol):
    """The C14 gate: answers whether a card's required checks are green (so it may move to
    `done`). Prod concretion shells `gh pr checks <card.gate_ref> --required`; CI injects a mock.
    NOTE: this is a duck-type — `mark_done` trusts whatever gate the caller injects; it is a
    binding control only once slice-2 binds the trusted concretion out of the agent's reach."""

    def required_checks_passed(self, card: Card) -> bool: ...


@dataclass(frozen=True)
class BoardProjection:
    """The result of project_plan: the parent goal card id + the node_id->card_id map. A typed
    result (NOT a magic "__root__" key in the node map) so a node literally named "__root__"
    cannot clobber the parent entry."""

    parent_id: str
    node_cards: dict[str, str]


class AbstractBoard(ABC):
    """A kanban board the spine projects its DAG onto. The C14 gate-derived-`done` invariant is
    enforced HERE (concrete public methods) so every adapter inherits it; adapters implement ONLY
    the storage primitives (`_put` / `get_card` / `list_cards` / `_new_id`)."""

    # --- storage primitives (adapter-implemented; PROTECTED — below the C14 contract) ---
    @abstractmethod
    def _put(self, card: Card) -> None:
        """Insert or replace a card by id (the board's only write primitive)."""
        raise NotImplementedError

    @abstractmethod
    def get_card(self, card_id: str) -> Card:
        """Return the card, or raise BoardError if absent (never a silent None)."""
        raise NotImplementedError

    @abstractmethod
    def list_cards(self, *, thread_id: Optional[str] = None) -> list[Card]:
        """Every card, optionally filtered to one thread (graph)."""
        raise NotImplementedError

    @abstractmethod
    def _new_id(self) -> str:
        """Mint a fresh unique card id."""
        raise NotImplementedError

    # --- C14-guarded PUBLIC operations (the agent-facing contract; the invariant lives here) ---
    @staticmethod
    def _check_status(status: str, *, allow_done: bool) -> None:
        """Reject any value outside the CardStatus vocabulary (closes the case/whitespace
        look-alike bypass — "Done"/" done" are NOT members), and refuse agent-initiated `done`."""
        if status not in _CARD_STATUSES:
            raise BoardError(f"invalid card status {status!r} (allowed: {sorted(_CARD_STATUSES)})")
        if status == "done" and not allow_done:
            raise BoardError(
                "C14: `done` is gate-derived — use mark_done(card_id, gate=...); "
                "the agent may not self-mark a card done"
            )

    def create_card(
        self,
        *,
        title: str,
        thread_id: str,
        node_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        status: CardStatus = "todo",
        gate_ref: Optional[str] = None,
        body: Optional[str] = None,
    ) -> Card:
        """Create a card. C14: a card cannot be BORN `done` (nor any non-vocabulary status)."""
        self._check_status(status, allow_done=False)
        card = Card(
            id=self._new_id(),
            title=title,
            thread_id=thread_id,
            status=status,
            node_id=node_id,
            parent_id=parent_id,
            gate_ref=gate_ref,
            body=body,
        )
        self._put(card)
        return card

    def set_status(self, card_id: str, status: CardStatus) -> Card:
        """Transition a card's status. C14: `done` is NEVER reachable here (nor a look-alike
        variant) — the agent may not self-mark done; `done` comes ONLY from mark_done(gate=...)."""
        self._check_status(status, allow_done=False)
        card = self.get_card(card_id).model_copy(update={"status": status})
        self._put(card)
        return card

    def mark_done(self, card_id: str, *, gate: GateReader) -> Card:
        """The ONLY path to `done` (C14): move to `done` iff the injected GateReader confirms the
        card's required checks are green; otherwise raise GateNotPassedError and leave it unchanged.
        Idempotent: an already-`done` card is returned WITHOUT re-gating (resume-safe)."""
        card = self.get_card(card_id)
        if card.status == "done":
            return card
        if not gate.required_checks_passed(card):
            raise GateNotPassedError(
                f"C14: required checks not green for card {card_id} (gate_ref={card.gate_ref!r}) "
                f"— refusing `done`"
            )
        done = card.model_copy(update={"status": "done"})
        self._put(done)
        return done


def project_plan(
    board: AbstractBoard,
    plan: Any,
    *,
    thread_id: str,
    goal_title: str = "goal",
) -> BoardProjection:
    """Project a TaskGraph (SP-02 plan) onto the board: ONE parent goal card + one child card per
    TaskNode (child.parent_id = parent, child.node_id = node id). Returns a BoardProjection
    (parent_id + {node_id: card_id}). Pure OUTBOUND projection — creates cards, reads nothing.

    Validates the node ids UP FRONT (every node has a non-empty `id`; no duplicates) and raises
    BoardError on a malformed plan rather than a bare KeyError or a silently-orphaned card. The
    SP-02 decomposer already guarantees this, but project_plan accepts an untyped `plan`."""
    nodes = (plan or {}).get("nodes", [])
    seen: set[str] = set()
    for node in nodes:
        nid = node.get("id")
        if not nid:
            raise BoardError("project_plan: a TaskGraph node is missing its 'id'")
        if nid in seen:
            raise BoardError(f"project_plan: duplicate node id {nid!r}")
        seen.add(nid)

    parent = board.create_card(title=goal_title, thread_id=thread_id)
    node_cards: dict[str, str] = {}
    for node in nodes:
        nid = node["id"]
        child = board.create_card(
            title=node.get("summary") or nid,
            thread_id=thread_id,
            node_id=nid,
            parent_id=parent.id,
        )
        node_cards[nid] = child.id
    return BoardProjection(parent_id=parent.id, node_cards=node_cards)
