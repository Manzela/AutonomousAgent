import pytest
import numpy as np
from app.core.schemas import MemoryRecord, MemoryTier
from app.adapters.inmemory.memory import InMemoryStore
from app.adapters.inmemory.embedder import HashingEmbedder
from app.adapters.inmemory.sandbox import LocalSubprocessSandbox


@pytest.mark.asyncio
async def test_inmemory_store():
    store = InMemoryStore(dim=8)
    assert store.size == 0
    rec = MemoryRecord(
        record_id="rec-1",
        tier=MemoryTier.EPHEMERAL,
        project_id="proj-1",
        content="hello",
        embedding=np.zeros(8, dtype=np.float32),
        expires_at=9999999999.0,
    )
    await store.put(rec)
    assert store.size == 1


def test_hashing_embedder():
    emb = HashingEmbedder(dim=8)
    v = emb.embed("hello world")
    assert v.shape == (8,)


@pytest.mark.asyncio
async def test_sandbox():
    sb = LocalSubprocessSandbox()
    res = await sb.run(cmd=["echo", "test"])
    assert res.returncode == 0
    assert "test" in res.stdout
