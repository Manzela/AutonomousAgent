"""Tests for app/core/ abstract contracts and app/adapters/inmemory/ implementations.

Covers:
  - InMemoryStore: put, search, get, delete, gc_expired, EmptyScope (layer-3)
  - HashingEmbedder: embed, dim validation, embed_many, L2 normalisation
  - LocalSubprocessSandbox: basic run, timeout, network_allowed refusal
  - MemoryRecord: tier/namespace invariant (layer-1)
"""

import time

import numpy as np
import pytest

from app.adapters.inmemory.embedder import HashingEmbedder
from app.adapters.inmemory.memory import InMemoryStore
from app.adapters.inmemory.sandbox import LocalSubprocessSandbox
from app.core.embedder import project_dim
from app.core.memory import EmptyScope
from app.core.schemas import MemoryRecord, MemoryTier


# ─────────────────────────────────────────────────────────────────────
# InMemoryStore
# ─────────────────────────────────────────────────────────────────────


def _make_record(
    record_id: str = "rec-1",
    tier: MemoryTier = MemoryTier.EPHEMERAL,
    project_id: str | None = "proj-1",
    content: str = "hello world",
    dim: int = 8,
    expires_at: float | None = 9999999999.0,
) -> MemoryRecord:
    emb = np.random.randn(dim).astype(np.float32)
    emb /= np.linalg.norm(emb) + 1e-10
    return MemoryRecord(
        record_id=record_id,
        tier=tier,
        project_id=project_id,
        content=content,
        embedding=emb,
        expires_at=expires_at,
    )


@pytest.mark.asyncio
async def test_store_put_and_get():
    store = InMemoryStore(dim=8)
    rec = _make_record()
    await store.put(rec)
    assert store.size == 1
    fetched = await store.get("rec-1")
    assert fetched is not None
    assert fetched.content == "hello world"


@pytest.mark.asyncio
async def test_store_get_missing():
    store = InMemoryStore(dim=8)
    result = await store.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_store_search_returns_results():
    store = InMemoryStore(dim=8)
    rec = _make_record()
    await store.put(rec)
    query = rec.embedding  # Search for exactly the same vector
    results = await store.search(
        query_embedding=query,
        tier=MemoryTier.EPHEMERAL,
        project_scopes=["proj-1"],
        k=5,
    )
    assert len(results) == 1
    assert results[0][0].record_id == "rec-1"
    assert results[0][1] > 0.99  # cosine similarity ~ 1.0


@pytest.mark.asyncio
async def test_store_search_empty_scope_raises():
    """Layer-3 defence: search() MUST reject empty scopes."""
    store = InMemoryStore(dim=8)
    rec = _make_record()
    await store.put(rec)
    with pytest.raises(EmptyScope):
        await store.search(
            query_embedding=rec.embedding,
            tier=MemoryTier.EPHEMERAL,
            project_scopes=[],  # EMPTY — must fail
        )


@pytest.mark.asyncio
async def test_store_search_wrong_project_returns_empty():
    """Cross-project isolation: searching project-B must NOT return project-A records."""
    store = InMemoryStore(dim=8)
    rec = _make_record(project_id="proj-A")
    await store.put(rec)
    results = await store.search(
        query_embedding=rec.embedding,
        tier=MemoryTier.EPHEMERAL,
        project_scopes=["proj-B"],  # Different project
    )
    assert len(results) == 0


@pytest.mark.asyncio
async def test_store_delete():
    store = InMemoryStore(dim=8)
    rec = _make_record()
    await store.put(rec)
    assert store.size == 1
    result = await store.delete("rec-1")
    assert result is True
    assert store.size == 0
    # Double-delete returns False — assign before assert to avoid py/assert-with-side-effects
    result2 = await store.delete("rec-1")
    assert result2 is False


@pytest.mark.asyncio
async def test_store_gc_expired():
    store = InMemoryStore(dim=8)
    # Record that is already expired
    rec_expired = _make_record(record_id="expired", expires_at=1.0)
    # Record that is NOT expired
    rec_alive = _make_record(record_id="alive", expires_at=9999999999.0)
    await store.put(rec_expired)
    await store.put(rec_alive)
    assert store.size == 2
    count = await store.gc_expired(MemoryTier.EPHEMERAL, before_ts=time.time())
    assert count == 1
    assert store.size == 1
    alive_rec = await store.get("alive")
    assert alive_rec is not None
    expired_rec = await store.get("expired")
    assert expired_rec is None


@pytest.mark.asyncio
async def test_store_dim_mismatch_put():
    store = InMemoryStore(dim=8)
    rec = _make_record(dim=16)  # Wrong dim
    with pytest.raises(ValueError, match="embedding dim"):
        await store.put(rec)


@pytest.mark.asyncio
async def test_store_dim_mismatch_search():
    store = InMemoryStore(dim=8)
    with pytest.raises(ValueError, match="query dim"):
        await store.search(
            query_embedding=np.zeros(16, dtype=np.float32),
            tier=MemoryTier.EPHEMERAL,
            project_scopes=["proj-1"],
        )


# ─────────────────────────────────────────────────────────────────────
# HashingEmbedder
# ─────────────────────────────────────────────────────────────────────


def test_embedder_basic():
    emb = HashingEmbedder(dim=8)
    v = emb.embed("hello world")
    assert v.shape == (8,)
    assert v.dtype == np.float32


def test_embedder_l2_normalised():
    emb = HashingEmbedder(dim=256)
    v = emb.embed("the quick brown fox jumps over the lazy dog")
    norm = float(np.linalg.norm(v))
    assert abs(norm - 1.0) < 1e-5


def test_embedder_empty_string():
    emb = HashingEmbedder(dim=8)
    v = emb.embed("")
    assert np.allclose(v, 0.0)


def test_embedder_dim_must_be_power_of_two():
    with pytest.raises(ValueError, match="power of two"):
        HashingEmbedder(dim=7)
    with pytest.raises(ValueError, match="power of two"):
        HashingEmbedder(dim=0)


def test_embedder_embed_many():
    emb = HashingEmbedder(dim=8)
    vecs = emb.embed_many(["hello", "world"])
    assert vecs.shape == (2, 8)


def test_embedder_deterministic():
    emb = HashingEmbedder(dim=8)
    v1 = emb.embed("hello")
    v2 = emb.embed("hello")
    assert np.array_equal(v1, v2)


# ─────────────────────────────────────────────────────────────────────
# project_dim helper
# ─────────────────────────────────────────────────────────────────────


def test_project_dim_truncate():
    v = np.ones(16, dtype=np.float32)
    out = project_dim(v, 8)
    assert out.shape == (8,)
    assert abs(float(np.linalg.norm(out)) - 1.0) < 1e-5


def test_project_dim_pad():
    v = np.ones(4, dtype=np.float32)
    out = project_dim(v, 8)
    assert out.shape == (8,)
    assert abs(float(np.linalg.norm(out)) - 1.0) < 1e-5


def test_project_dim_identity():
    v = np.ones(8, dtype=np.float32)
    v /= np.linalg.norm(v)
    out = project_dim(v, 8)
    assert np.allclose(out, v)


# ─────────────────────────────────────────────────────────────────────
# LocalSubprocessSandbox
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sandbox_basic():
    sb = LocalSubprocessSandbox()
    res = await sb.run(cmd=["echo", "hello"])
    assert res.returncode == 0
    assert "hello" in res.stdout
    assert res.killed is False


@pytest.mark.asyncio
async def test_sandbox_network_allowed_refused():
    """Security boundary: sandbox MUST refuse network_allowed=True."""
    sb = LocalSubprocessSandbox()
    with pytest.raises(PermissionError, match="network isolation"):
        await sb.run(cmd=["echo"], network_allowed=True)


@pytest.mark.asyncio
async def test_sandbox_not_production_grade():
    sb = LocalSubprocessSandbox()
    assert sb.is_production_grade is False


@pytest.mark.asyncio
async def test_sandbox_timeout():
    sb = LocalSubprocessSandbox()
    res = await sb.run(cmd=["sleep", "10"], timeout_s=0.5)
    assert res.killed is True


# ─────────────────────────────────────────────────────────────────────
# MemoryRecord layer-1 invariants
# ─────────────────────────────────────────────────────────────────────


def test_consensus_rejects_project_id():
    """Layer-1: CONSENSUS records MUST have project_id=None."""
    with pytest.raises(ValueError, match="layer-1 invariant"):
        MemoryRecord(
            record_id="r1",
            tier=MemoryTier.CONSENSUS,
            project_id="proj-X",  # INVALID for CONSENSUS
            content="test",
            embedding=np.zeros(8, dtype=np.float32),
        )


def test_episodic_requires_project_id():
    """Layer-1: EPISODIC records MUST have a project_id."""
    with pytest.raises(ValueError, match="layer-1 invariant"):
        MemoryRecord(
            record_id="r1",
            tier=MemoryTier.EPISODIC,
            project_id=None,  # INVALID for EPISODIC
            content="test",
            embedding=np.zeros(8, dtype=np.float32),
        )


def test_ephemeral_requires_expires_at():
    """Layer-1: EPHEMERAL records MUST set expires_at."""
    with pytest.raises(ValueError, match="expires_at"):
        MemoryRecord(
            record_id="r1",
            tier=MemoryTier.EPHEMERAL,
            project_id="proj-1",
            content="test",
            embedding=np.zeros(8, dtype=np.float32),
            expires_at=None,  # INVALID for EPHEMERAL
        )
