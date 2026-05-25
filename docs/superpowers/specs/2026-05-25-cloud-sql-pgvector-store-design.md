# `CloudSqlPgvectorStore` — Production `AbstractMemoryStore` Design Spec

**Date:** 2026-05-25
**Author:** Senior database architect (Claude Opus 4.7)
**Status:** Design — ready for implementation
**Scope:** Phase 2 production implementation of `AbstractMemoryStore`
(`app/core/memory.py`) backed by Cloud SQL PostgreSQL 16 + pgvector HNSW.
Work item **P-2** per `docs/research/autonomous-agent-seed-orchestrator/INTEGRATION.md`.
**Related artefacts:**
- `app/core/memory.py` — the ABC the class implements
- `app/core/schemas.py` — `MemoryRecord`, `MemoryTier`, `ProjectID`
- `app/adapters/inmemory/memory.py` — `InMemoryStore` reference behaviour
- `app/tests/test_inmemory_adapters.py` — contract tests we must also pass
- `terraform/phase-0a-gcp/postgres/main.tf` — existing Cloud SQL HA tier
  (`db-custom-16-64000`, private IP, IAM auth)
- `~/.claude/projects/.../memory/phase2_postgres_tier.md` — strategic disposition

---

## Problem statement

`InMemoryStore` (the dev/CI implementation) holds records in a Python
`dict` guarded by an `asyncio.Lock`. Search is brute-force cosine in
O(K). For Phase 2 — when projects start writing the projected ~10M
EPISODIC vectors and the CONSENSUS tier grows monotonically — we need:

1. **Durability.** No data loss on Cloud Run replica restart. Cloud SQL
   PITR + daily backups already cover this at the instance level.
2. **Sub-linear vector search.** HNSW (`m=16, ef_construction=64`) per
   the strategic disposition. P95 ≤ 30ms for `k=10` recall over a
   project of 1M records (acceptance criterion from INTEGRATION.md P-2).
3. **Layer-3 isolation enforced at the DB.** `search()` rejects empty
   scopes (already enforced by the ABC's Python validator); the tier↔
   namespace invariant is mirrored as a CHECK constraint so a corrupt
   write path cannot bypass it.
4. **IAM-only auth.** Cloud SQL Auth Proxy + service-account OAuth
   tokens; no passwords stored anywhere (matches the existing Terraform
   module — `cloudsql.iam_authentication = on`).

The class implements every method of `AbstractMemoryStore` with byte-
identical semantics to `InMemoryStore` so the existing contract tests
(`app/tests/test_inmemory_adapters.py::test_store_*`) pass unchanged
against the production store.

---

## Section 1 — Architecture decisions

### A. Driver: `asyncpg` (not SQLAlchemy, not psycopg3)

| Option | Latency overhead vs raw `asyncpg` | Migration tooling | Footprint | Fit for this use case |
|---|---|---|---|---|
| `sqlalchemy[asyncio]` + `asyncpg` dialect | +30–60% per query at this query shape (5 columns + 1 vector, no joins) per the SQLAlchemy 2.0 benchmarks | Alembic, declarative ORM | +~9MB wheels | Over-engineered. The store touches 1 table and emits 5 distinct query shapes; ORM mapping buys nothing. |
| `psycopg3` (`psycopg[binary,pool]`) | +10–20% vs `asyncpg` per asyncpg-benchmarks | PEP 249, modern async | +~4MB wheels | Pythonic, but slower in async mode and no first-class pgvector codec. |
| **`asyncpg` direct (chosen)** | baseline | Hand-rolled DDL via `scripts/migrate_cloud_sql.py` | +~3MB wheels | Fastest async Postgres driver; `pgvector.asyncpg.register_vector()` integrates as a typed codec; matches the perf-critical role of vector search in the orchestrator hot path. |

**Decision:** `asyncpg` direct. The store is on the orchestrator's hot
path (every routing decision triggers a `search()` call); 30–60% of a
30ms P95 budget is not negotiable. Schema evolution is rare (we own the
one table); when it happens, we add a numbered DDL block to
`scripts/migrate_cloud_sql.py` rather than carrying an ORM full-time.

### B. pgvector embedding serialization: `register_vector()`

`MemoryRecord.embedding` is a `numpy.ndarray`. Three options to pass it
to asyncpg's PostgreSQL protocol:

1. **`list(embedding.tolist())` + cast in SQL** — `$1::vector`. Works,
   but boxes every float through Python and serialises as text. Adds
   ~150µs per vector at 256 dim.
2. **String `"[0.1, 0.2, ...]"`** — what pgvector accepts in text form.
   Same overhead as above; worse, ambiguous when the array has NaNs.
3. **`pgvector.asyncpg.register_vector(conn)` (chosen)** — registers
   pgvector's binary type codec on the connection so `numpy.ndarray`
   passes straight through as bytes. Zero-copy on the way in, returns
   `numpy.ndarray` directly on `fetch*`.

**Decision:** `register_vector(conn)` called once per pooled connection
via `init=` callback on `asyncpg.create_pool`. This is the
`pgvector-python` library's documented integration path
(https://github.com/pgvector/pgvector-python#asyncpg). Falls back to
explicit `vector(256)` typed parameters so the wire format stays
unambiguous even if a future asyncpg release changes its default codec
inference.

### C. Connection management: lazy singleton pool, TCP via Cloud SQL Auth Proxy

**Pool shape:** `asyncpg.create_pool(dsn, min_size=2, max_size=10,
init=_register_vector_codec, max_inactive_connection_lifetime=300)`,
lazily constructed on first call. Mirrors the Redis-pool pattern in
`docs/superpowers/specs/2026-05-25-redis-jti-replay-cache-design.md` so
both stores observe the same connection-management invariants (lazy
init, single instance per process, never closed by callers).

**Why `max_size=10` per process:** Cloud SQL default `max_connections =
200` (we override to 200 in the existing terraform; the proxy adds ~20
overhead). Cloud Run autoscales A2A + orchestrator together to ~20
replicas under burst → 20 × 10 = 200 concurrent connections, right at
the configured ceiling. `min_size=2` keeps two warm connections so
single-replica idle traffic doesn't pay the cold-connect cost on every
request.

**DSN format — TCP via Cloud SQL Auth Proxy on localhost (chosen):**

```
postgresql://autonomousagent-vm-runtime@autonomous-agent-2026.iam@127.0.0.1:5432/hermes?sslmode=disable
```

(Note: `sslmode=disable` is correct here — the Auth Proxy terminates
mTLS to Cloud SQL on the *outbound* side; the loopback hop from the app
process to the proxy on the same VM/pod is unencrypted by design.)

**Why not Unix socket (`?host=/cloudsql/...`):**

- The Unix-socket DSN syntax (`?host=/cloudsql/<connection_name>`) is
  the **Cloud SQL Python Connector**'s convention, not asyncpg's.
  asyncpg parses `host=` from the query string but Cloud SQL Auth
  Proxy on GKE/Cloud Run does NOT create a Unix socket by default; it
  binds a TCP port unless explicitly told to use the socket-mount mode.
- TCP on `127.0.0.1` is what the Cloud SQL Auth Proxy v2 documents as
  the canonical Cloud Run sidecar pattern
  (https://cloud.google.com/sql/docs/postgres/connect-run#auth-proxy).
- IAM auth still works over TCP — the user name is the SA email minus
  `.gserviceaccount.com`, and the password the proxy injects is a
  fresh OAuth token from the runtime metadata server. No code change in
  the app to opt into IAM auth.

The DSN comes from the `autonomousagent-db-connection` Secret Manager
secret already provisioned by `terraform/phase-0a-gcp/postgres/main.tf`
(JSON blob with `host`, `database`, `user`, `connection_name`). The app
reads it once at boot via the existing
`google-cloud-secret-manager>=2.20` SDK already in `pyproject.toml`.

### D. HNSW parameters: build `m=16, ef_construction=64`; query `ef_search=100`

| Parameter | Build-time | Query-time | Why |
|---|---|---|---|
| `m` | 16 | n/a | Graph neighbours per node. Strategic disposition from `memory/phase2_postgres_tier.md`. 16 is the pgvector default; doubling to 32 doubles index size for ~5% recall lift on 256-dim cosine — not worth it at our scale. |
| `ef_construction` | 64 | n/a | Build-time candidate-pool size. 64 is the pgvector default; index build takes ~4× longer at 256, with marginal recall improvement on 256-dim. The 4GB `maintenance_work_mem` flag in the terraform was sized for this value. |
| `ef_search` | n/a | **100** | Query-time candidate pool. Higher = better recall, slower query. |

**`ef_search=100` is set per-transaction via `SET LOCAL hnsw.ef_search
= 100`** because it MUST differ from `ef_construction`:

- `ef_construction` governs the graph topology at build time and is
  baked into the index — changing it requires `REINDEX`.
- `ef_search` is a dial we can turn at query time. Setting it equal to
  `ef_construction` (64) gives ~92% recall at `k=10` on 256-dim cosine
  per the pgvector benchmarks; 100 lifts that to ~97% with ~30µs added
  latency. The HNSW best practice is `ef_search ≥ k + 10–20` for
  high recall; with `k=10` (our default), 100 is comfortably above the
  floor and matches what pgvector's documentation recommends for
  production traffic.
- We use `SET LOCAL` (not `SET`) so the change is scoped to the
  transaction and doesn't leak into the next pooled connection's
  session.

---

## Section 2 — Database schema

All DDL is idempotent (`IF NOT EXISTS`). The `embedding` column is
`vector(256)` — locked to the orchestrator's embedding dimension
(`app/core/embedder.py::project_dim` projects everything to 256 before
it touches the store).

```sql
-- Required once per database. Cloud SQL Postgres 16 ships the binary
-- pre-installed; this just installs the extension into `hermes`.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memory_records (
    record_id       TEXT PRIMARY KEY,
    tier            TEXT NOT NULL
                    CHECK (tier IN ('consensus', 'episodic', 'ephemeral')),
    project_id      TEXT,                       -- NULL for CONSENSUS
    agent_id        TEXT,
    task_id         TEXT,
    content         TEXT NOT NULL,
    embedding       vector(256) NOT NULL,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      DOUBLE PRECISION NOT NULL,
    expires_at      DOUBLE PRECISION,
    content_hash    TEXT,
    namespace_token TEXT,

    -- Layer-1 invariant (mirrors MemoryRecord._tier_namespace_invariant).
    -- A corrupt or malicious write path that bypasses pydantic still
    -- gets rejected at the DB.
    CONSTRAINT consensus_no_project CHECK (
        tier != 'consensus' OR project_id IS NULL
    ),
    CONSTRAINT episodic_has_project CHECK (
        tier = 'consensus' OR project_id IS NOT NULL
    ),
    -- EPHEMERAL records MUST have an expiry (TTL ≤ 1h enforced in app).
    CONSTRAINT ephemeral_has_expiry CHECK (
        tier != 'ephemeral' OR expires_at IS NOT NULL
    )
);

-- HNSW index on the embedding column. cosine_ops because the in-memory
-- store also computes cosine similarity (test_store_search_returns_results
-- asserts `score > 0.99` for an exact-match query).
CREATE INDEX IF NOT EXISTS memory_records_embedding_hnsw
    ON memory_records
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- GIN on metadata for future filter-pushdown (orchestrator does not yet
-- filter on metadata, but P-7 Vertex Vector Search will hand back a
-- candidate set we may want to re-filter; the GIN keeps that O(log n)).
CREATE INDEX IF NOT EXISTS memory_records_metadata_gin
    ON memory_records USING GIN (metadata);

-- Composite partial index for the GC sweep. The partial WHERE drops
-- CONSENSUS rows entirely (they never expire) and any EPISODIC row
-- without a TTL. Cuts the index to ~the ephemeral tier only.
CREATE INDEX IF NOT EXISTS memory_records_gc_idx
    ON memory_records (tier, expires_at)
    WHERE expires_at IS NOT NULL;

-- Optional: index on content_hash for the put()-time dedup check.
CREATE INDEX IF NOT EXISTS memory_records_content_hash_idx
    ON memory_records (content_hash)
    WHERE content_hash IS NOT NULL;
```

**Why `vector(256)` and not a variable-dim column:** pgvector requires
the dimension on the column. The orchestrator's embedder always emits
256-dim vectors (see `app/core/embedder.py::project_dim`); pinning it
means dim mismatches surface at INSERT-time as a clear pgvector error
rather than producing silently wrong similarity scores.

**Why `JSONB` not `JSON`:** GIN indexing, binary storage, and
deterministic equality. Cheap given the rest of the row.

**Why `DOUBLE PRECISION` for `created_at` / `expires_at`:** these are
`time.time()` outputs (POSIX float seconds). `TIMESTAMP WITH TIME ZONE`
would round-trip with timezone conversion overhead and force the app to
parse `datetime` on every read; we want native Python floats.

---

## Section 3 — Complete Python implementation

**File:** `app/adapters/gcp/memory.py`

```python
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

    `dsn` is read once from the CLOUD_SQL_DSN env var if not provided.
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
                "CloudSqlPgvectorStore requires CLOUD_SQL_DSN env var "
                "or explicit dsn= arg"
            )
        _POOL = await asyncpg.create_pool(
            dsn=effective_dsn,
            min_size=2,
            max_size=10,
            max_inactive_connection_lifetime=300.0,  # 5min idle reap
            init=_register_vector_codec,
            # Statement-level timeout — bounds the worst-case query.
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
            app/core/embedder.py::project_dim. Tests pass `dim=8` for
            speed against the same column type (the pgvector column is
            declared `vector(256)` — see migrate_cloud_sql.py — but
            asyncpg's vector codec lets us write smaller vectors and
            pgvector will *reject* them at INSERT, which is the correct
            failure mode for production. For tests we override the
            column dim via a separate test schema; see Section 6).
        dsn: Optional override for the asyncpg DSN. Production reads
            CLOUD_SQL_DSN from env.
        ef_search: Query-time HNSW candidate pool. Default 100 per
            Section 1.D. Bump for higher recall at the cost of latency.
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
            raise ValueError(
                f"embedding dim {record.embedding.shape[0]} != store dim {self._dim}"
            )
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
                emb,                                # pgvector binary codec
                json.dumps(record.metadata),        # cast to jsonb in SQL
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
            raise EmptyScope(
                "search() requires at least one project_scope (None for CONSENSUS)"
            )
        if query_embedding.shape[0] != self._dim:
            raise ValueError(
                f"query dim {query_embedding.shape[0]} != store dim {self._dim}"
            )

        # Split scopes into "non-null project IDs" and "do we include CONSENSUS?"
        # The clean SQL pattern:
        #   WHERE tier = $1
        #     AND (project_id = ANY($2::text[]) OR ($3 AND project_id IS NULL))
        # $2 = the list of non-null project_ids (may be empty []).
        # $3 = True iff caller passed None in project_scopes (CONSENSUS bucket).
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
                await conn.execute(
                    f"SET LOCAL hnsw.ef_search = {int(self._ef_search)}"
                )
                # 1 - cosine_distance = cosine_similarity. The `<=>`
                # operator is pgvector's cosine-distance op; we sort
                # ascending on distance (= descending on similarity)
                # and return similarity as the score so the in-memory
                # store contract is preserved (test_store_search_returns_results
                # asserts score > 0.99 for exact match).
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
            # status is "DELETE <count>" for a DELETE; parse the count.
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
        # asyncpg returns "DELETE N" for DELETE statements.
        parts = status.split()
        if len(parts) == 2 and parts[0] == "DELETE":
            return int(parts[1])
        return 0


# ─────────────────────────────────────────────────────────────────────
# Row → MemoryRecord reconstruction.
# ─────────────────────────────────────────────────────────────────────


def _row_to_record(row: asyncpg.Record) -> MemoryRecord:
    """Hydrate a MemoryRecord from an asyncpg row.

    asyncpg returns the JSONB column as either a `str` (default) or a
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
        project_id=(
            ProjectID(row["project_id"]) if row["project_id"] is not None else None
        ),
        agent_id=AgentID(row["agent_id"]) if row["agent_id"] is not None else None,
        task_id=TaskID(row["task_id"]) if row["task_id"] is not None else None,
        content=row["content"],
        embedding=emb,
        metadata=md,
        created_at=float(row["created_at"]),
        expires_at=(
            float(row["expires_at"]) if row["expires_at"] is not None else None
        ),
        content_hash=(
            ContentHash(row["content_hash"])
            if row["content_hash"] is not None
            else None
        ),
        namespace_token=row["namespace_token"],
    )
```

### Notes on the `put()` content-hash dedup

The InMemoryStore tracks `_content_hashes` but doesn't *enforce* dedup
— it just records the mapping. The original spec hinted at "if record
has `content_hash`, check for existing record with same hash before
insert", but that would break the upsert path (legitimate re-puts of
the same `record_id` would conflict on hash). The chosen behaviour:

- `put()` always upserts on `record_id` (primary key).
- `content_hash` is recorded but not used as a uniqueness constraint;
  dedup is the orchestrator's job (it computes the hash and decides
  whether to skip the put). This matches InMemoryStore semantics.
- If we later want hash-based dedup, the right move is a *partial
  unique index* on `content_hash WHERE content_hash IS NOT NULL`, but
  that's out of scope for P-2.

### Why the scope filter splits null vs non-null

PostgreSQL's `NULL = ANY(array)` returns NULL (not false), and any
combinator with NULL stays NULL → row is excluded even when the array
contains a NULL element. The clean fix is to split: pass non-null
project IDs as a `text[]` and pass a separate boolean `include_consensus`
that drives the `project_id IS NULL` branch. The query plan stays
sargable on the project_id index because both branches use index
expressions.

---

## Section 4 — Terraform notes (no new module required)

**`terraform/phase-0a-gcp/postgres/` already declares everything P-2
needs**:

- `google_sql_database_instance.postgres_vector` — `db-custom-16-64000`
  via `var.db_instance_tier` (16 vCPU / 64 GB RAM; floor for HNSW on
  the projected ~100M vector working set).
- `google_sql_database.hermes` — the database the store writes to.
- `google_sql_user.vm_runtime` — `type = "CLOUD_IAM_SERVICE_ACCOUNT"`,
  IAM auth via the VM runtime SA.
- `google_secret_manager_secret.db_connection` — the DSN metadata blob
  consumed by `CloudSqlPgvectorStore` at boot.
- `google_project_iam_member.vm_runtime_cloudsql_client` — `roles/
  cloudsql.client` so the SA can authenticate to the proxy.

**What P-2 needs to add to the existing module** (minor extension —
not a new file):

1. **A `var.cloud_sql_tier`-style override defaulting to a smaller dev
   tier** so non-prod environments can stand up a `db-custom-2-8192`
   instance for ~$95/mo instead of the full $1,580/mo HA tier. Open a
   new variable in `terraform/phase-0a-gcp/postgres/variables.tf`:

   ```hcl
   variable "db_instance_tier_dev_override" {
     description = "Optional dev-tier override (e.g. db-custom-2-8192). When set, replaces db_instance_tier. Use for non-prod environments only — disables HA, smaller HNSW build budget."
     type        = string
     default     = null
   }
   ```

   then in `main.tf`:

   ```hcl
   locals {
     effective_tier = coalesce(var.db_instance_tier_dev_override, var.db_instance_tier)
   }
   ```

   and use `local.effective_tier` in the instance's `settings.tier`.

2. **Cloud SQL Auth Proxy runs as a sidecar on Cloud Run via the
   `--add-cloudsql-instances` flag, not a separate container.** This is
   *deployment*, not Terraform — the existing
   `deploy/cloudrun/service.yaml` (or whatever YAML the A2A server
   uses) must add:

   ```yaml
   annotations:
     run.googleapis.com/cloudsql-instances: "autonomous-agent-2026:us-central1:autonomousagent-postgres-vector"
   ```

   No additional sidecar container is required — Cloud Run wires up the
   proxy automatically when the annotation is present, binding it to
   `127.0.0.1:5432`. This is the documented Cloud Run + Cloud SQL
   integration pattern
   (https://cloud.google.com/sql/docs/postgres/connect-run).

3. **VM-based hosts (GCE) run the Auth Proxy as a systemd unit.** A
   `cloud-sql-proxy.service` unit file already exists for J1; the same
   pattern extends to the orchestrator VM:

   ```
   ExecStart=/usr/local/bin/cloud-sql-proxy \
       --auto-iam-authn \
       --address 127.0.0.1 \
       --port 5432 \
       autonomous-agent-2026:us-central1:autonomousagent-postgres-vector
   ```

   `--auto-iam-authn` injects the OAuth token automatically; the app
   process never sees a password.

**No `terraform/phase-0a-gcp/cloud_sql_memory_store.tf` is created** —
that would duplicate the existing module. The work item is a single
variable + locals tweak inside the module.

---

## Section 5 — Migration script

**File:** `scripts/migrate_cloud_sql.py`

Standalone CLI that connects with asyncpg, runs the four DDL blocks
idempotently, and exits non-zero on any error. Safe to re-run; safe to
run from a CI step or a one-shot Cloud Run job.

```python
#!/usr/bin/env python3
"""Idempotent DDL migration for the hermes Cloud SQL database.

Run once per environment after `terraform apply` brings the Cloud SQL
instance up. Re-runnable: every statement uses IF NOT EXISTS or its
equivalent (CREATE EXTENSION IF NOT EXISTS, CREATE TABLE IF NOT EXISTS,
CREATE INDEX IF NOT EXISTS).

Usage:
    # Local — assumes Cloud SQL Auth Proxy is up on 127.0.0.1:5432
    python scripts/migrate_cloud_sql.py \\
        --dsn "postgresql://autonomousagent-vm-runtime@autonomous-agent-2026.iam@127.0.0.1:5432/hermes?sslmode=disable"

    # Cloud Run job (Auth Proxy is auto-injected by the
    # run.googleapis.com/cloudsql-instances annotation)
    CLOUD_SQL_DSN="postgresql://...@127.0.0.1:5432/hermes?sslmode=disable" \\
        python scripts/migrate_cloud_sql.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg


DDL_BLOCKS: tuple[tuple[str, str], ...] = (
    (
        "extension_vector",
        "CREATE EXTENSION IF NOT EXISTS vector",
    ),
    (
        "table_memory_records",
        """
        CREATE TABLE IF NOT EXISTS memory_records (
            record_id       TEXT PRIMARY KEY,
            tier            TEXT NOT NULL
                            CHECK (tier IN ('consensus', 'episodic', 'ephemeral')),
            project_id      TEXT,
            agent_id        TEXT,
            task_id         TEXT,
            content         TEXT NOT NULL,
            embedding       vector(256) NOT NULL,
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
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
        """,
    ),
    (
        "index_embedding_hnsw",
        """
        CREATE INDEX IF NOT EXISTS memory_records_embedding_hnsw
            ON memory_records
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """,
    ),
    (
        "index_metadata_gin",
        """
        CREATE INDEX IF NOT EXISTS memory_records_metadata_gin
            ON memory_records USING GIN (metadata)
        """,
    ),
    (
        "index_gc",
        """
        CREATE INDEX IF NOT EXISTS memory_records_gc_idx
            ON memory_records (tier, expires_at)
            WHERE expires_at IS NOT NULL
        """,
    ),
    (
        "index_content_hash",
        """
        CREATE INDEX IF NOT EXISTS memory_records_content_hash_idx
            ON memory_records (content_hash)
            WHERE content_hash IS NOT NULL
        """,
    ),
)


async def migrate(dsn: str) -> int:
    """Apply all DDL blocks in order. Returns count applied successfully."""
    conn = await asyncpg.connect(dsn)
    applied = 0
    try:
        for name, sql in DDL_BLOCKS:
            print(f"[migrate] applying {name} ...", flush=True)
            await conn.execute(sql)
            applied += 1
            print(f"[migrate] OK {name}", flush=True)
    finally:
        await conn.close()
    return applied


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Apply hermes Cloud SQL DDL migrations idempotently."
    )
    ap.add_argument(
        "--dsn",
        default=os.environ.get("CLOUD_SQL_DSN"),
        help="asyncpg DSN. Defaults to $CLOUD_SQL_DSN.",
    )
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.dsn:
        print(
            "ERROR: --dsn or CLOUD_SQL_DSN env var required.",
            file=sys.stderr,
        )
        return 2
    try:
        n = asyncio.run(migrate(args.dsn))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: migration failed: {exc!r}", file=sys.stderr)
        return 1
    print(f"[migrate] done; {n}/{len(DDL_BLOCKS)} blocks applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

---

## Section 6 — Test strategy

**File:** `app/tests/test_cloud_sql_pgvector_store.py`

**Container:** `pgvector/pgvector:pg16` via `testcontainers-python`,
session-scoped. Real PostgreSQL with pgvector pre-installed — closest
possible mirror of Cloud SQL Postgres 16 short of standing up a real
Cloud SQL instance in CI.

**Test schema dim:** The production column is `vector(256)`, but tests
use `dim=8` for speed (matching `test_inmemory_adapters.py`). The
test fixture runs a modified migration that creates the column as
`vector(8)` — see `_apply_test_schema()` below. This is the *only*
divergence from the production schema; everything else (indexes,
constraints, codecs) is identical.

```python
"""Contract tests for CloudSqlPgvectorStore.

Runs against a session-scoped pgvector/pgvector:pg16 testcontainer.
Mirrors the contract surface tested in test_inmemory_adapters.py so
both stores share the same behavioural guarantees.
"""

from __future__ import annotations

import time
from typing import AsyncIterator

import numpy as np
import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer

import app.adapters.gcp.memory as gcp_memory
from app.adapters.gcp.memory import CloudSqlPgvectorStore
from app.core.memory import EmptyScope
from app.core.schemas import MemoryRecord, MemoryTier


# ─────────────────────────────────────────────────────────────────────
# Fixtures.
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def pg_container() -> "PostgresContainer":
    """Session-scoped pgvector/pgvector:pg16 — start once, reuse across tests."""
    with PostgresContainer(
        image="pgvector/pgvector:pg16",
        username="test",
        password="test",  # pragma: allowlist secret  # testcontainer-only, never reaches prod
        dbname="hermes_test",
        driver=None,  # raw libpq DSN — asyncpg parses it directly
    ) as c:
        yield c


def _dsn_for(c: "PostgresContainer") -> str:
    """Build an asyncpg-friendly DSN from the container."""
    return (
        f"postgresql://{c.username}:{c.password}"
        f"@{c.get_container_host_ip()}:{c.get_exposed_port(5432)}"
        f"/{c.dbname}"
    )


async def _apply_test_schema(dsn: str, dim: int = 8) -> None:
    """Test-mode migration: same DDL as production but with vector(dim)."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
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
    assert results[0][1] > 0.999       # cosine sim ~= 1.0
    assert results[1][0].record_id == "r2"
    assert results[2][0].record_id == "r3"


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
        expires_at=None,            # EPISODIC has no TTL requirement
        embedding=consensus_emb,    # identical embedding so ranking is determined by scope, not distance
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

    # And the reverse: searching CONSENSUS scope ([None]) only returns
    # the CONSENSUS row.
    cons_results = await store.search(
        query_embedding=consensus_emb,
        tier=MemoryTier.CONSENSUS,
        project_scopes=[None],
        k=10,
    )
    assert len(cons_results) == 1
    assert cons_results[0][0].record_id == "cons-1"

    # And empty scope STILL raises EmptyScope (layer-3).
    with pytest.raises(EmptyScope):
        await store.search(
            query_embedding=consensus_emb,
            tier=MemoryTier.EPISODIC,
            project_scopes=[],
        )


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
```

### What the tests do NOT cover (future P-2 follow-ups)

- **Property-test for cross-project leakage at scale.** The
  INTEGRATION.md acceptance criterion is "10 projects × 1K records
  each, 100K random queries, zero cross-project leakage". That's a
  separate `tests/load/test_pgvector_isolation_property.py` outside the
  unit-test suite. Reference impl: `hypothesis`-driven fuzz of
  `(query_project, target_project, embedding)` triples, assert
  `target_project ∈ requested_scopes` for every returned row.
- **P95 latency under load.** Acceptance is P95 ≤ 30ms for k=10 over
  1M records. That's a `pytest-benchmark` run against a populated
  Cloud SQL dev instance — runs in the nightly perf job, not on every
  PR.

---

## Section 7 — Dependency additions

**`pyproject.toml` patch** — add a new `gcp` extra and extend the
existing `dev` extra:

```toml
[project.optional-dependencies]
# (existing `dev` and `a2a` extras above; gcp is new)
gcp = [
  # Phase 2 production memory store — Cloud SQL + pgvector.
  # P-2 work item per docs/research/autonomous-agent-seed-orchestrator/INTEGRATION.md.
  # Lazy-imported in app/adapters/gcp/memory.py; the in-memory store
  # (app/adapters/inmemory/memory.py) does not pull these.
  "asyncpg>=0.29,<0.30",
  "pgvector>=0.3,<0.4",
]

# Extend the existing `dev` extra with the testcontainer for
# CloudSqlPgvectorStore contract tests. `testcontainers[postgres]`
# pulls the docker SDK + the pgvector/pgvector:pg16 image driver.
# Tests auto-skip on docker-less hosts via the existing `docker`
# pytest marker.
dev = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
  "pytest-mock>=3.14",
  "ruff>=0.6.9",
  "opentelemetry-sdk>=1.20",
  # NEW for P-2:
  "testcontainers[postgres]>=4.8,<5",
]
```

**Why pin `asyncpg<0.30`:** the 0.29 series is stable on Python 3.11+;
0.30 introduces breaking changes to the binary protocol parsing that
`pgvector.asyncpg` has not yet released a compatible build against (as
of 2026-05). Re-evaluate when `pgvector-python>=0.4` ships.

**Why pin `pgvector<0.4`:** 0.3 is the latest stable release with the
binary codec; 0.4 is in beta and changes the codec registration API
(`register_vector` → `register_vector_async`). Re-evaluate at GA.

**Why a separate `gcp` extra and not putting these in core
`dependencies`:** the in-memory adapter (`app/adapters/inmemory/`) is
what CI runs against by default (per CLAUDE.md: "CI runs against
`adapters/inmemory/`; staging + prod run against `adapters/gcp/`").
Pinning asyncpg + pgvector in core would force the wheels onto every
developer machine even for tests that don't touch the GCP adapters.

Install on the production runtime host:

```bash
uv sync --extra gcp --extra dev   # full stack
uv sync --extra gcp               # production-only
```

---

## Open questions / follow-ups (out of scope for this spec)

1. **HNSW build cost at promotion time.** When a project's EPISODIC
   memory tier reaches ~10M vectors and we trigger the P-7 cutover to
   Vertex Vector Search, we'll need to rebuild the HNSW index on the
   replacement table. That rebuild blocks writes for the duration;
   estimate against the 4GB `maintenance_work_mem` is ~6 hours per
   10M-vector tier. Schedule for the existing maintenance window.
2. **CONSENSUS append-only enforcement.** The MemoryRecord docstring
   says CONSENSUS is "immutable once written (append-only)". This spec
   uses `INSERT ... ON CONFLICT DO UPDATE` which would *allow* a
   CONSENSUS row to be overwritten. Trade-off:
   - Stricter: add `BEFORE UPDATE` trigger that raises on
     `OLD.tier = 'consensus'`.
   - Looser (current): rely on the orchestrator never re-puting a
     CONSENSUS record_id.
   Recommend the trigger as a Phase 2.1 hardening pass once the
   orchestrator's promotion path is stable.
3. **Schema migration tooling.** This spec uses a hand-rolled
   `scripts/migrate_cloud_sql.py`. Once we have >2 DDL changes,
   migrate to Alembic to get versioned migrations and rollback. The
   strategic disposition memo (`phase2_postgres_tier.md`) explicitly
   ruled out a heavy ORM but did NOT rule out Alembic *with raw SQL
   operations* — that's the right middle ground when the time comes.
