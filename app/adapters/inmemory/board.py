"""InMemoryBoard — the hermetic CI adapter for AbstractBoard (SP-16).

A dict-backed card store. Implements ONLY the storage primitives; the C14 gate-derived-`done`
invariant is inherited from AbstractBoard's concrete methods (NEVER overridden here — an adapter
that re-implemented create_card/set_status/mark_done could bypass the safety gate). The prod
Hermes-`kanban_db` adapter is the DEFERRED sibling.

AlwaysReadyGate is the hermetic CI stand-in for the C14 GateReader. It is a NULL stub —
required_checks_passed always returns True — so under it the root card closes UNCONDITIONALLY at
ship_effect: it makes the lifecycle observable in CI, NOT a withholding gate. The reason that is
acceptable (not self-certification): the spine reaches ship_effect only after the SP-06 eval_gate
passed AND the operator approved the ship gate. The BINDING concretion — shelling
`gh pr checks <card.gate_ref> --required` against the agent's shipped PR, injected out of the agent's
reach — is DEFERRED (SP-12), and so is populating the parent card's gate_ref with the real PR; until
both land, the live "done" is a ship-time proxy, not the PRD's external-CI-derived done.
"""

from __future__ import annotations

from typing import Optional

from app.core.board import AbstractBoard, BoardError, Card


class AlwaysReadyGate:
    """The CI stub GateReader (implements app.core.board.GateReader). A NULL gate — ALWAYS returns
    True, so it NEVER withholds (the live root closes unconditionally at ship). It makes the C14
    consult-site exercisable in CI; the binding `gh pr checks --required` reader is the DEFERRED prod
    sibling. See the module docstring for why a non-withholding stub is not self-certification here."""

    def required_checks_passed(self, card: Card) -> bool:
        return True


class InMemoryBoard(AbstractBoard):
    def __init__(self) -> None:
        self._cards: dict[str, Card] = {}
        self._seq = 0

    def _new_id(self) -> str:
        self._seq += 1
        return f"card-{self._seq}"

    def _put(self, card: Card) -> None:
        self._cards[card.id] = card

    def get_card(self, card_id: str) -> Card:
        try:
            return self._cards[card_id]
        except KeyError:
            raise BoardError(f"no such card: {card_id!r}") from None

    def list_cards(self, *, thread_id: Optional[str] = None) -> list[Card]:
        cards = list(self._cards.values())
        if thread_id is None:
            return cards
        return [c for c in cards if c.thread_id == thread_id]
