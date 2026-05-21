"""
Memory store ABC + in-memory implementation.

The store is layer-3 of the 5-layer cross-project isolation defence
(`01-phase1-mathematical-spec.md` §2.3): the `search()` method REJECTS
empty scope sets, so a caller cannot accidentally request "all records"
and get back anything outside the namespace they hold.

For production: subclass `AbstractMemoryStore` with `PostgresStore`
(Cloud SQL + HNSW pgvector). See `INTEGRATION.md` P-2 for the work item.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Iterable, Optional

import numpy as np

from .schemas import (
    ContentHash,
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
    async def put(self, record: MemoryRecord) -> None: ...

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

    @abstractmethod
    async def get(self, record_id: str) -> Optional[MemoryRecord]: ...

    @abstractmethod
    async def delete(self, record_id: str) -> bool: ...

    @abstractmethod
    async def gc_expired(self, tier: MemoryTier, before_ts: float) -> int:
        """Remove `tier` records whose `expires_at <= before_ts`. Returns count."""


class InMemoryStore(AbstractMemoryStore):
    """Async-safe in-memory store. Dev/CI grade.

    Vector search is brute-force cosine; for K records it is O(K). At K ~ 10⁴
    on a 256-dim space this is well under a millisecond.

    NOT for production — there is no persistence, no replication, no HNSW.
    See INTEGRATION.md work item P-2 for the Postgres replacement.
    """

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim
        self._records: dict[str, MemoryRecord] = {}
        self._content_hashes: dict[ContentHash, str] = {}
        self._lock = asyncio.Lock()

    @property
    def size(self) -> int:
        return len(self._records)

    async def put(self, record: MemoryRecord) -> None:
        if record.embedding.shape[0] != self._dim:
            raise ValueError(f"embedding dim {record.embedding.shape[0]} != store dim {self._dim}")
        async with self._lock:
            self._records[record.record_id] = record
            if record.content_hash is not None:
                self._content_hashes[record.content_hash] = record.record_id

    async def search(
        self,
        *,
        query_embedding: np.ndarray,
        tier: MemoryTier,
        project_scopes: Iterable[Optional[ProjectID]],
        k: int = 10,
    ) -> list[tuple[MemoryRecord, float]]:
        scopes = list(project_scopes)
        if not scopes:
            # Layer-3 defence: reject empty scopes — refuses ambient authority.
            raise EmptyScope("search() requires at least one project_scope (None for CONSENSUS)")
        if query_embedding.shape[0] != self._dim:
            raise ValueError(f"query dim {query_embedding.shape[0]} != store dim {self._dim}")
        scope_set: set[Optional[ProjectID]] = set(scopes)

        async with self._lock:
            candidates: list[tuple[MemoryRecord, float]] = []
            qnorm = float(np.linalg.norm(query_embedding))
            if qnorm == 0.0:
                return []
            q = query_embedding / qnorm
            for rec in self._records.values():
                if rec.tier != tier:
                    continue
                if rec.project_id not in scope_set:
                    continue
                if rec.expires_at is not None and rec.expires_at <= time.time():
                    continue
                e = rec.embedding
                en = float(np.linalg.norm(e))
                if en == 0.0:
                    continue
                score = float(np.dot(q, e / en))
                candidates.append((rec, score))
            candidates.sort(key=lambda t: t[1], reverse=True)
            return candidates[:k]

    async def get(self, record_id: str) -> Optional[MemoryRecord]:
        async with self._lock:
            return self._records.get(record_id)

    async def delete(self, record_id: str) -> bool:
        async with self._lock:
            rec = self._records.pop(record_id, None)
            if rec is None:
                return False
            if rec.content_hash is not None:
                self._content_hashes.pop(rec.content_hash, None)
            return True

    async def gc_expired(self, tier: MemoryTier, before_ts: float) -> int:
        async with self._lock:
            doomed = [
                rid
                for rid, rec in self._records.items()
                if rec.tier == tier and rec.expires_at is not None and rec.expires_at <= before_ts
            ]
            for rid in doomed:
                rec = self._records.pop(rid, None)
                if rec is not None and rec.content_hash is not None:
                    self._content_hashes.pop(rec.content_hash, None)
            return len(doomed)
