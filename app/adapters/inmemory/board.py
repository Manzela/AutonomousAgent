"""InMemoryBoard — the hermetic CI adapter for AbstractBoard (SP-16).

A dict-backed card store. Implements ONLY the storage primitives; the C14 gate-derived-`done`
invariant is inherited from AbstractBoard's concrete methods (NEVER overridden here — an adapter
that re-implemented create_card/set_status/mark_done could bypass the safety gate). The prod
Hermes-`kanban_db` adapter is the DEFERRED sibling.
"""

from __future__ import annotations

from typing import Optional

from app.core.board import AbstractBoard, BoardError, Card


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
