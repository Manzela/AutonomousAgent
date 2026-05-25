"""Cloud SQL + pgvector implementation of AbstractMemoryStore.

P-2 work item per docs/research/autonomous-agent-seed-orchestrator/
INTEGRATION.md. Subclasses AbstractMemoryStore with the same contract:
search() rejects empty scopes (layer-3), the tier↔namespace invariant
is enforced both by MemoryRecord's pydantic validator (layer-1) and by
CHECK constraints on memory_records (defence in depth).

Connection management: lazy singleton asyncpg pool. The DSN comes from
the autonomousagent-db-connection Secret Manager secret provisioned by
terraform/phase-0a-gcp/postgres/main.tf; IAM auth via Cloud SQL Auth
Proxy on 127.0.0.1:5432. No passwords are stored or logged anywhere.

Embeddings are passed through pgvector's binary codec (registered once
per pooled connection via init=) so numpy.ndarray round-trips with zero
Python-level box/unbox.

Design spec: docs/superpowers/specs/2026-05-25-cloud-sql-pgvector-store-design.md
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Iterable, Optional

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

from app.core.memory import AbstractMemoryStore, EmptyScope
from app.core.schemas import (
    AgentID,
    ContentHash,
    MemoryRecord,
    MemoryTier,
    ProjectID,
    TaskID,
)


# ─────────────────────────────────────────────────────────────────────
# Pool singleton.
# ─────────────────────────────────────────────────────────────────────

_POOL: Optional[asyncpg.Pool] = None
_POOL_LOCK = asyncio.Lock()


async def _register_vector_codec(conn: asyncpg.Connection) -> None:
    """Register pgvector binary codec on every pooled connection."""
    await register_vector(conn)


async def _get_pool(dsn: Optional[str] = None) -> asyncpg.Pool:
    """Lazily construct (or return) the process-wide pool.

    ``dsn`` is read once from the CLOUD_SQL_DSN env var if not provided.
    In production, set CLOUD_SQL_DSN from the
    autonomousagent-db-connection Secret Manager secret at boot.
    """
    global _POOL
    if _POOL is not None:
        return _POOL
    async with _POOL_LOCK:
        if _POOL is not None:  # raced — another coroutine won
            return _POOL
        effective_dsn = dsn or os.environ.get("CLOUD_SQL_DSN")
        if not effective_dsn:
            raise RuntimeError(
                "CloudSqlPgvectorStore requires CLOUD_SQL_DSN env var " "or explicit dsn= arg"
            )
        _POOL = await asyncpg.create_pool(
            dsn=effective_dsn,
            min_size=2,
            max_size=10,
            max_inactive_connection_lifetime=300.0,  # 5min idle reap
            init=_register_vector_codec,
            # Statement-level timeout — bounds the worst-case query.
            # HNSW index pages are mmap'd lazily; the first query on a cold
            # replica may take 1–3s to fault index pages into RAM.
            # command_timeout=10.0 provides headroom; a process-boot warmup
            # query is recommended to avoid confusing QueryCanceledError.
            command_timeout=10.0,
        )
        return _POOL


async def _reset_pool_for_tests() -> None:
    """Test-only hook — close the pool and let the next call recreate it."""
    global _POOL
    async with _POOL_LOCK:
        if _POOL is not None:
            await _POOL.close()
            _POOL = None


# ─────────────────────────────────────────────────────────────────────
# Store.
# ─────────────────────────────────────────────────────────────────────


class CloudSqlPgvectorStore(AbstractMemoryStore):
    """Production AbstractMemoryStore — Cloud SQL Postgres 16 + pgvector HNSW.

    Args:
        dim: Embedding dimension. Validated against the pgvector column
            dimension at put()/search() time. Defaults to 256, matching
            app/core/embedder.py::project_dim. Tests pass ``dim=8`` for
            speed against a test schema with ``vector(8)``.
        dsn: Optional override for the asyncpg DSN. Production reads
            CLOUD_SQL_DSN from env.
        ef_search: Query-time HNSW candidate pool. Default 100 per
            the design spec Section 1.D. Bump for higher recall at the
            cost of latency.
    """

    def __init__(
        self,
        dim: int = 256,
        dsn: Optional[str] = None,
        ef_search: int = 100,
    ) -> None:
        self._dim = dim
        self._dsn = dsn
        self._ef_search = ef_search

    # ─────────────────────────────────────────────────────────────
    # put()
    # ─────────────────────────────────────────────────────────────

    async def put(self, record: MemoryRecord) -> None:
        if record.embedding.shape[0] != self._dim:
            raise ValueError(f"embedding dim {record.embedding.shape[0]} != store dim {self._dim}")
        # Defence: ensure float32 contiguous — pgvector binary codec
        # tolerates either, but explicit cast avoids surprise alignment
        # faults on some asyncpg builds.
        emb = np.ascontiguousarray(record.embedding, dtype=np.float32)

        pool = await _get_pool(self._dsn)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO memory_records (
                    record_id, tier, project_id, agent_id, task_id,
                    content, embedding, metadata, created_at,
                    expires_at, content_hash, namespace_token
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, $10, $11, $12)
                ON CONFLICT (record_id) DO UPDATE SET
                    tier            = EXCLUDED.tier,
                    project_id      = EXCLUDED.project_id,
                    agent_id        = EXCLUDED.agent_id,
                    task_id         = EXCLUDED.task_id,
                    content         = EXCLUDED.content,
                    embedding       = EXCLUDED.embedding,
                    metadata        = EXCLUDED.metadata,
                    created_at      = EXCLUDED.created_at,
                    expires_at      = EXCLUDED.expires_at,
                    content_hash    = EXCLUDED.content_hash,
                    namespace_token = EXCLUDED.namespace_token
                """,
                record.record_id,
                record.tier.value,
                record.project_id,
                record.agent_id,
                record.task_id,
                record.content,
                emb,  # pgvector binary codec
                json.dumps(record.metadata),  # cast to jsonb in SQL
                float(record.created_at),
                None if record.expires_at is None else float(record.expires_at),
                record.content_hash,
                record.namespace_token,
            )

    # ─────────────────────────────────────────────────────────────
    # search()
    # ─────────────────────────────────────────────────────────────

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
            # Layer-3 defence — identical wording to InMemoryStore.
            raise EmptyScope("search() requires at least one project_scope (None for CONSENSUS)")
        if query_embedding.shape[0] != self._dim:
            raise ValueError(f"query dim {query_embedding.shape[0]} != store dim {self._dim}")

        # Split scopes into "non-null project IDs" and "do we include CONSENSUS?"
        # PostgreSQL's NULL = ANY(array) returns NULL (not false) — we must
        # split the filter into a text[] branch and a boolean IS NULL branch.
        include_consensus = any(s is None for s in scopes)
        non_null_scopes: list[str] = [str(s) for s in scopes if s is not None]

        q = np.ascontiguousarray(query_embedding, dtype=np.float32)
        now_ts = time.time()
        pool = await _get_pool(self._dsn)

        async with pool.acquire() as conn:
            # Per-transaction ef_search override. SET LOCAL scopes the
            # change to the transaction so it does NOT leak into the
            # next caller's session via the pool.
            async with conn.transaction():
                await conn.execute(f"SET LOCAL hnsw.ef_search = {int(self._ef_search)}")
                # 1 - cosine_distance = cosine_similarity. The `<=>` operator
                # is pgvector's cosine-distance op; we sort ascending on
                # distance (= descending on similarity) and return similarity
                # as the score so the InMemoryStore contract is preserved
                # (test_store_search_returns_results asserts score > 0.99).
                rows = await conn.fetch(
                    """
                    SELECT record_id, tier, project_id, agent_id, task_id,
                           content, embedding, metadata, created_at,
                           expires_at, content_hash, namespace_token,
                           1.0 - (embedding <=> $1::vector) AS score
                    FROM memory_records
                    WHERE tier = $2
                      AND (project_id = ANY($3::text[])
                           OR ($4 AND project_id IS NULL))
                      AND (expires_at IS NULL OR expires_at > $5)
                    ORDER BY embedding <=> $1::vector
                    LIMIT $6
                    """,
                    q,
                    tier.value,
                    non_null_scopes,
                    include_consensus,
                    now_ts,
                    int(k),
                )

        out: list[tuple[MemoryRecord, float]] = []
        for row in rows:
            out.append((_row_to_record(row), float(row["score"])))
        return out

    # ─────────────────────────────────────────────────────────────
    # get()
    # ─────────────────────────────────────────────────────────────

    async def get(self, record_id: str) -> Optional[MemoryRecord]:
        pool = await _get_pool(self._dsn)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT record_id, tier, project_id, agent_id, task_id,
                       content, embedding, metadata, created_at,
                       expires_at, content_hash, namespace_token
                FROM memory_records
                WHERE record_id = $1
                """,
                record_id,
            )
        if row is None:
            return None
        return _row_to_record(row)

    # ─────────────────────────────────────────────────────────────
    # delete()
    # ─────────────────────────────────────────────────────────────

    async def delete(self, record_id: str) -> bool:
        pool = await _get_pool(self._dsn)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "DELETE FROM memory_records WHERE record_id = $1 RETURNING record_id",
                record_id,
            )
        return row is not None

    # ─────────────────────────────────────────────────────────────
    # gc_expired()
    # ─────────────────────────────────────────────────────────────

    async def gc_expired(self, tier: MemoryTier, before_ts: float) -> int:
        pool = await _get_pool(self._dsn)
        async with pool.acquire() as conn:
            # asyncpg returns "DELETE N" for DELETE statements.
            status = await conn.execute(
                """
                DELETE FROM memory_records
                WHERE tier = $1
                  AND expires_at IS NOT NULL
                  AND expires_at <= $2
                """,
                tier.value,
                float(before_ts),
            )
        parts = status.split()
        if len(parts) == 2 and parts[0] == "DELETE":
            return int(parts[1])
        return 0


# ─────────────────────────────────────────────────────────────────────
# Row → MemoryRecord reconstruction.
# ─────────────────────────────────────────────────────────────────────


def _row_to_record(row: asyncpg.Record) -> MemoryRecord:
    """Hydrate a MemoryRecord from an asyncpg row.

    asyncpg returns the JSONB column as either a ``str`` (default) or a
    parsed dict if a JSON codec is registered. We handle both because
    pgvector.asyncpg.register_vector() does NOT touch the JSON codec.
    """
    md = row["metadata"]
    if isinstance(md, str):
        md = json.loads(md)
    # pgvector binary codec returns a numpy array directly.
    emb = row["embedding"]
    if not isinstance(emb, np.ndarray):
        emb = np.asarray(emb, dtype=np.float32)
    return MemoryRecord(
        record_id=row["record_id"],
        tier=MemoryTier(row["tier"]),
        project_id=(ProjectID(row["project_id"]) if row["project_id"] is not None else None),
        agent_id=AgentID(row["agent_id"]) if row["agent_id"] is not None else None,
        task_id=TaskID(row["task_id"]) if row["task_id"] is not None else None,
        content=row["content"],
        embedding=emb,
        metadata=md,
        created_at=float(row["created_at"]),
        expires_at=(float(row["expires_at"]) if row["expires_at"] is not None else None),
        content_hash=(
            ContentHash(row["content_hash"]) if row["content_hash"] is not None else None
        ),
        namespace_token=row["namespace_token"],
    )
