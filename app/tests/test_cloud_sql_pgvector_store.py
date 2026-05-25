"""Contract tests for CloudSqlPgvectorStore.

Runs against a session-scoped pgvector/pgvector:pg16 testcontainer.
Mirrors the contract surface tested in test_inmemory_adapters.py so
both stores share the same behavioural guarantees.

Tests require Docker to be running. Skip with: pytest -m "not docker"

Design spec: docs/superpowers/specs/2026-05-25-cloud-sql-pgvector-store-design.md §6
"""

from __future__ import annotations

import time
from typing import AsyncIterator

import numpy as np
import pytest
import pytest_asyncio

try:
    from testcontainers.postgres import PostgresContainer

    _HAS_TESTCONTAINERS = True
except ImportError:
    _HAS_TESTCONTAINERS = False

# Skip the entire module if testcontainers or Docker is unavailable.
pytestmark = [
    pytest.mark.skipif(
        not _HAS_TESTCONTAINERS,
        reason="testcontainers[postgres] not installed (install with: uv sync --extra dev)",
    ),
]


if _HAS_TESTCONTAINERS:
    import app.adapters.gcp.memory as gcp_memory  # noqa: E402
    from app.adapters.gcp.memory import CloudSqlPgvectorStore  # noqa: E402
else:
    gcp_memory = None  # type: ignore[assignment]
    CloudSqlPgvectorStore = None  # type: ignore[assignment]

from app.core.memory import EmptyScope  # noqa: E402
from app.core.schemas import MemoryRecord, MemoryTier  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Fixtures.
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def pg_container():
    """Session-scoped pgvector/pgvector:pg16 — start once, reuse across tests."""
    with PostgresContainer(
        image="pgvector/pgvector:pg16",
        username="test",
        password="test",  # pragma: allowlist secret  # testcontainer-only
        dbname="hermes_test",
        driver=None,  # raw libpq DSN — asyncpg parses it directly
    ) as c:
        yield c


def _dsn_for(c) -> str:
    """Build an asyncpg-friendly DSN from the container."""
    return (
        f"postgresql://{c.username}:{c.password}"
        f"@{c.get_container_host_ip()}:{c.get_exposed_port(5432)}"
        f"/{c.dbname}"
    )


async def _apply_test_schema(dsn: str, dim: int = 8) -> None:
    """Test-mode migration: same DDL as production but with vector(dim)."""
    import asyncpg as apg

    conn = await apg.connect(dsn)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute("DROP TABLE IF EXISTS memory_records CASCADE")
        await conn.execute(
            f"""
            CREATE TABLE memory_records (
                record_id       TEXT PRIMARY KEY,
                tier            TEXT NOT NULL
                                CHECK (tier IN ('consensus', 'episodic', 'ephemeral')),
                project_id      TEXT,
                agent_id        TEXT,
                task_id         TEXT,
                content         TEXT NOT NULL,
                embedding       vector({dim}) NOT NULL,
                metadata        JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                created_at      DOUBLE PRECISION NOT NULL,
                expires_at      DOUBLE PRECISION,
                content_hash    TEXT,
                namespace_token TEXT,
                CONSTRAINT consensus_no_project CHECK (
                    tier != 'consensus' OR project_id IS NULL
                ),
                CONSTRAINT episodic_has_project CHECK (
                    tier = 'consensus' OR project_id IS NOT NULL
                ),
                CONSTRAINT ephemeral_has_expiry CHECK (
                    tier != 'ephemeral' OR expires_at IS NOT NULL
                )
            )
            """
        )
        await conn.execute(
            """
            CREATE INDEX memory_records_embedding_hnsw
                ON memory_records
                USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """
        )
        await conn.execute(
            """
            CREATE INDEX memory_records_gc_idx
                ON memory_records (tier, expires_at)
                WHERE expires_at IS NOT NULL
            """
        )
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def store(pg_container) -> AsyncIterator[CloudSqlPgvectorStore]:
    """Fresh schema per test; reuse the session container + pool."""
    dsn = _dsn_for(pg_container)
    await _apply_test_schema(dsn, dim=8)
    # Reset the singleton pool so the new DSN takes effect.
    await gcp_memory._reset_pool_for_tests()
    s = CloudSqlPgvectorStore(dim=8, dsn=dsn)
    yield s
    await gcp_memory._reset_pool_for_tests()


def _make_record(
    record_id: str = "rec-1",
    tier: MemoryTier = MemoryTier.EPHEMERAL,
    project_id: str | None = "proj-1",
    content: str = "hello world",
    expires_at: float | None = 9999999999.0,
    embedding: np.ndarray | None = None,
) -> MemoryRecord:
    if embedding is None:
        emb = np.random.randn(8).astype(np.float32)
        emb /= np.linalg.norm(emb) + 1e-10
    else:
        emb = embedding
    return MemoryRecord(
        record_id=record_id,
        tier=tier,
        project_id=project_id,
        content=content,
        embedding=emb,
        expires_at=expires_at,
    )


# ─────────────────────────────────────────────────────────────────────
# Tests.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_and_get_round_trip(store: CloudSqlPgvectorStore) -> None:
    """put() a record, get() it back, all fields preserved."""
    rec = _make_record(
        record_id="r1",
        content="round-trip me",
    )
    await store.put(rec)
    fetched = await store.get("r1")
    assert fetched is not None
    assert fetched.record_id == "r1"
    assert fetched.content == "round-trip me"
    assert fetched.tier == MemoryTier.EPHEMERAL
    assert fetched.project_id == "proj-1"
    assert fetched.expires_at == 9999999999.0
    # Embedding round-trips bit-for-bit through pgvector's binary codec.
    assert np.allclose(fetched.embedding, rec.embedding, atol=1e-6)


@pytest.mark.asyncio
async def test_get_missing_returns_none(store: CloudSqlPgvectorStore) -> None:
    """get() on a non-existent record_id returns None."""
    result = await store.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_search_returns_closest_embedding(
    store: CloudSqlPgvectorStore,
) -> None:
    """Three records with known embeddings; search returns them ranked."""
    e1 = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
    e2 = np.array([0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
    e2 /= np.linalg.norm(e2)
    e3 = np.array([0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)

    await store.put(_make_record("r1", embedding=e1))
    await store.put(_make_record("r2", embedding=e2))
    await store.put(_make_record("r3", embedding=e3))

    results = await store.search(
        query_embedding=e1,
        tier=MemoryTier.EPHEMERAL,
        project_scopes=["proj-1"],
        k=3,
    )
    assert len(results) == 3
    # Exact match first, near-match second, orthogonal last.
    assert results[0][0].record_id == "r1"
    assert results[0][1] > 0.999  # cosine sim ~= 1.0
    assert results[1][0].record_id == "r2"
    assert results[2][0].record_id == "r3"


@pytest.mark.asyncio
async def test_search_empty_scope_raises(
    store: CloudSqlPgvectorStore,
) -> None:
    """Layer-3 defence: search() MUST reject empty scopes."""
    rec = _make_record()
    await store.put(rec)
    with pytest.raises(EmptyScope):
        await store.search(
            query_embedding=rec.embedding,
            tier=MemoryTier.EPHEMERAL,
            project_scopes=[],
        )


@pytest.mark.asyncio
async def test_search_wrong_project_returns_empty(
    store: CloudSqlPgvectorStore,
) -> None:
    """Cross-project isolation: searching project-B must NOT return project-A records."""
    rec = _make_record(project_id="proj-A")
    await store.put(rec)
    results = await store.search(
        query_embedding=rec.embedding,
        tier=MemoryTier.EPHEMERAL,
        project_scopes=["proj-B"],
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_search_respects_scope_isolation(
    store: CloudSqlPgvectorStore,
) -> None:
    """CONSENSUS records MUST NOT bleed into an EPISODIC search.

    Cross-project isolation is Layer-3; this test exercises the SQL
    scope filter that mirrors InMemoryStore's set-membership check.
    """
    consensus_emb = np.random.randn(8).astype(np.float32)
    consensus_emb /= np.linalg.norm(consensus_emb)
    cons_rec = MemoryRecord(
        record_id="cons-1",
        tier=MemoryTier.CONSENSUS,
        project_id=None,
        content="shared knowledge",
        embedding=consensus_emb,
    )
    epi_rec = _make_record(
        record_id="epi-1",
        tier=MemoryTier.EPISODIC,
        project_id="proj-A",
        expires_at=None,  # EPISODIC has no TTL requirement
        embedding=consensus_emb,  # identical embedding for ranking control
    )
    await store.put(cons_rec)
    await store.put(epi_rec)

    # Search EPISODIC scope only — CONSENSUS row must NOT be returned.
    results = await store.search(
        query_embedding=consensus_emb,
        tier=MemoryTier.EPISODIC,
        project_scopes=["proj-A"],
        k=10,
    )
    assert len(results) == 1
    assert results[0][0].record_id == "epi-1"

    # Reverse: searching CONSENSUS scope ([None]) returns only CONSENSUS.
    cons_results = await store.search(
        query_embedding=consensus_emb,
        tier=MemoryTier.CONSENSUS,
        project_scopes=[None],
        k=10,
    )
    assert len(cons_results) == 1
    assert cons_results[0][0].record_id == "cons-1"


@pytest.mark.asyncio
async def test_delete_returns_true_false(
    store: CloudSqlPgvectorStore,
) -> None:
    """delete() returns True iff a row was removed."""
    rec = _make_record(record_id="d1")
    await store.put(rec)
    first = await store.delete("d1")
    second = await store.delete("d1")
    third = await store.delete("never-existed")
    assert first is True
    assert second is False
    assert third is False


@pytest.mark.asyncio
async def test_gc_expired_removes_only_expired(
    store: CloudSqlPgvectorStore,
) -> None:
    """gc_expired() removes only rows whose expires_at <= before_ts."""
    expired = _make_record(record_id="expired", expires_at=1.0)
    alive = _make_record(record_id="alive", expires_at=9999999999.0)
    await store.put(expired)
    await store.put(alive)

    count = await store.gc_expired(MemoryTier.EPHEMERAL, before_ts=time.time())
    assert count == 1
    assert (await store.get("expired")) is None
    assert (await store.get("alive")) is not None


@pytest.mark.asyncio
async def test_dim_mismatch_put(store: CloudSqlPgvectorStore) -> None:
    """put() with wrong dim raises ValueError before hitting the DB."""
    # Create a record with dim=16, but store expects dim=8
    bad_emb = np.random.randn(16).astype(np.float32)
    bad_emb /= np.linalg.norm(bad_emb)
    bad_rec = MemoryRecord(
        record_id="bad-dim",
        tier=MemoryTier.EPHEMERAL,
        project_id="proj-1",
        content="wrong dim",
        embedding=bad_emb,
        expires_at=9999999999.0,
    )
    with pytest.raises(ValueError, match="embedding dim"):
        await store.put(bad_rec)


@pytest.mark.asyncio
async def test_dim_mismatch_search(store: CloudSqlPgvectorStore) -> None:
    """search() with wrong query dim raises ValueError before hitting the DB."""
    with pytest.raises(ValueError, match="query dim"):
        await store.search(
            query_embedding=np.zeros(16, dtype=np.float32),
            tier=MemoryTier.EPHEMERAL,
            project_scopes=["proj-1"],
        )


@pytest.mark.asyncio
async def test_upsert_updates_existing(store: CloudSqlPgvectorStore) -> None:
    """put() with the same record_id updates the row (ON CONFLICT DO UPDATE)."""
    rec1 = _make_record(record_id="upsert-1", content="original")
    await store.put(rec1)

    rec2 = _make_record(record_id="upsert-1", content="updated")
    await store.put(rec2)

    fetched = await store.get("upsert-1")
    assert fetched is not None
    assert fetched.content == "updated"
