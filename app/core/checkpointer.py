"""AbstractCheckpointer — the durability injection seam (SP-01, 7th sibling ABC).

This is a PROVIDER/FACTORY, NOT a re-declaration of LangGraph's checkpointer
protocol. langgraph's BaseCheckpointSaver is already a rich ABC (get_tuple/put/
put_writes/list + async variants); re-implementing that surface would be the
over-engineering SP-22/§12 forbids. The provider yields exactly ONE writable
BaseCheckpointSaver to graph.compile(checkpointer=...) and carries the per-adapter
DURABILITY_MODE that the runner passes into astream/ainvoke (never hardcoded in
graph.py). Injected at the runner / FastAPI lifespan, never inside a node.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver

DurabilityMode = Literal["sync", "async", "exit"]


class AbstractCheckpointer(ABC):
    @property
    @abstractmethod
    def durability_mode(self) -> DurabilityMode:
        """'sync' for prod (a checkpoint is persisted before the irreversible
        super-step; the kill-resume exactly-once oracle is only provable here),
        'async'/default for CI."""
        raise NotImplementedError(f"{self.__class__.__name__}.durability_mode must be implemented")

    @abstractmethod
    def build_saver(self) -> BaseCheckpointSaver:
        """Return the single writable LangGraph checkpointer passed to compile()."""
        raise NotImplementedError(f"{self.__class__.__name__}.build_saver() must be implemented")

    async def setup(self) -> None:
        """One-time backend setup (e.g. Postgres .setup() migration). Default no-op."""
        return None

    async def aclose(self) -> None:
        """Release backend resources (e.g. close a connection pool). Default no-op."""
        return None
