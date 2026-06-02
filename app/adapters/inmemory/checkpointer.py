"""InMemoryCheckpointer — CI/skeleton durability provider. Zero new deps.

Wraps langgraph's InMemorySaver. One saver instance per provider — the shared
instance is what lets a fresh compile() resume a prior checkpoint (the in-process
stand-in for process death)."""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.core.checkpointer import AbstractCheckpointer, DurabilityMode


class InMemoryCheckpointer(AbstractCheckpointer):
    def __init__(self) -> None:
        self._saver = InMemorySaver()

    @property
    def durability_mode(self) -> DurabilityMode:
        return "async"

    def build_saver(self) -> BaseCheckpointSaver:
        return self._saver
