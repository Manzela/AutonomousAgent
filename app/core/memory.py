"""Memory store ABC — layer-3 of the 5-layer cross-project isolation defence.

The ``search()`` contract REJECTS empty scope sets, so a caller cannot
accidentally request "all records" and bypass namespace isolation.

For production: subclass ``AbstractMemoryStore`` with ``CloudSqlPgvectorStore``
(Cloud SQL + HNSW pgvector). See ``INTEGRATION.md`` P-2 for the work item.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Optional

import numpy as np

from app.core.schemas import (
    MemoryRecord,
    MemoryTier,
    ProjectID,
)


class EmptyScope(Exception):
    """Raised when `search()` is called with no scope. Layer-3 defence."""


class AbstractMemoryStore(ABC):
    """Read/write contract for the hierarchical memory backing store.

    All methods are async to keep the API stable between in-memory and DB
    implementations.
    """

    @abstractmethod
    async def put(self, record: MemoryRecord) -> None:
        raise NotImplementedError(f"{self.__class__.__name__}.put() must be implemented")

    @abstractmethod
    async def search(
        self,
        *,
        query_embedding: np.ndarray,
        tier: MemoryTier,
        project_scopes: Iterable[Optional[ProjectID]],
        k: int = 10,
    ) -> list[tuple[MemoryRecord, float]]:
        """Return up to k (record, score) pairs from the given scopes.

        `project_scopes` MUST be non-empty (an empty iterable raises
        EmptyScope). To search CONSENSUS, pass `[None]`. To search across
        a project plus consensus, pass `[project_id, None]`.
        """
        raise NotImplementedError(f"{self.__class__.__name__}.search() must be implemented")

    @abstractmethod
    async def get(self, record_id: str) -> Optional[MemoryRecord]:
        raise NotImplementedError(f"{self.__class__.__name__}.get() must be implemented")

    @abstractmethod
    async def delete(self, record_id: str) -> bool:
        raise NotImplementedError(f"{self.__class__.__name__}.delete() must be implemented")

    @abstractmethod
    async def gc_expired(self, tier: MemoryTier, before_ts: float) -> int:
        """Remove `tier` records whose `expires_at <= before_ts`. Returns count."""
        raise NotImplementedError(f"{self.__class__.__name__}.gc_expired() must be implemented")
