from __future__ import annotations
import asyncio
import time
from typing import Optional

import numpy as np

from app.core.schemas import (
    ContentHash,
    MemoryRecord,
    MemoryTier,
    ProjectID,
)
from app.core.memory import AbstractMemoryStore


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

    async def _search(
        self,
        *,
        query_embedding: np.ndarray,
        tier: MemoryTier,
        project_scopes: list[Optional[ProjectID]],
        k: int = 10,
    ) -> list[tuple[MemoryRecord, float]]:
        # Note: EmptyScope guard is enforced by AbstractMemoryStore.search() before
        # this method is called; project_scopes is always non-empty here.
        scopes = project_scopes
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
