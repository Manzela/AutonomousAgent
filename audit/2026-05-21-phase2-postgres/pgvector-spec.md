# pgvector Extension Specification

**Extension**: `pgvector` (PostgreSQL vector similarity search)
**Postgres Version**: 16
**Cloud SQL Support**: Native (Cloud SQL for PostgreSQL 16 includes pgvector in available extensions)
**Use Case**: Semantic memory tier (hierarchical memory architecture)

## 1. Extension Enablement

### Activation

```sql
-- Run as superuser (postgres) or role with CREATE EXTENSION privilege
CREATE EXTENSION IF NOT EXISTS vector;
```

**Deployment**: Include in Alembic baseline migration (`migrations/versions/001_baseline.py`).

### Verification

```sql
-- Check extension is loaded
SELECT * FROM pg_available_extensions WHERE name = 'vector';

-- Verify vector type is registered
SELECT typname FROM pg_type WHERE typname = 'vector';

-- Test vector operations
SELECT '[1,2,3]'::vector <=> '[3,2,1]'::vector AS cosine_distance;
```

## 2. Vector Index Types

pgvector supports two index types:

| Index Type | Build Time | Query Speed | Memory Usage | Best For |
|------------|-----------|-------------|--------------|----------|
| **IVFFlat** | Fast | Moderate | Low | Write-heavy workloads |
| **HNSW** | Slow | Fast | High | Read-heavy workloads |

### Recommendation: HNSW

**Rationale**:
- Semantic memory is **read-heavy** (retrieval-dominant: ~5 QPS read vs <1 QPS write)
- Writes are **batched** (episodic events → semantic embeddings conversion happens offline)
- **Query Latency**: HNSW provides <10ms p95 latency for 100M vectors; IVFFlat degrades to ~100ms at scale

## 3. HNSW Index Configuration

### Index Creation

```sql
CREATE INDEX CONCURRENTLY idx_embedding_hnsw
ON semantic_embeddings
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

### Parameter Justification

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `m` | 16 | Number of bi-directional links per node. Higher = better recall but slower builds. 16 is optimal for 768-dim vectors (pgvector benchmark). |
| `ef_construction` | 64 | Size of dynamic candidate list during index build. Higher = better index quality but longer build time. 64 balances quality vs build duration for 100M records. |

### Distance Metric: Cosine

- **Operator**: `vector_cosine_ops` (`<=>`)
- **Rationale**: Sentence-transformer models (e5-base, e5-large) produce L2-normalized embeddings. Cosine distance is mathematically equivalent to L2 distance for normalized vectors but more interpretable (range: 0-2).
- **Alternative**: `vector_l2_ops` (`<->`) if embeddings are NOT normalized (not recommended for semantic search).

## 4. Vector Dimensionality

### Recommendation: 768-dim (e5-base)

| Model | Dimensions | Storage per Vector | 100M Vectors (Raw) | HNSW Overhead | Total |
|-------|------------|-------------------|-------------------|---------------|-------|
| **e5-base** | 768 | 3,072 bytes | ~300 GB | ~300 GB | ~600 GB |
| e5-large | 1024 | 4,096 bytes | ~400 GB | ~400 GB | ~800 GB |

**Justification**:
- **Cost/Performance Balance**: e5-base achieves 95% of e5-large's retrieval quality at 75% storage cost.
- **Latency**: 768-dim HNSW queries complete in ~5ms (p50) vs ~8ms for 1024-dim on 16 vCPU instance.
- **Scaling Runway**: 600GB total storage fits comfortably in 1TB provisioned SSD (40% headroom for growth).

### Schema Declaration

```sql
CREATE TABLE semantic_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL,
    embedding VECTOR(768),  -- 768 dimensions (e5-base)
    text TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## 5. Query Patterns

### Nearest Neighbor Search (Top-K)

```sql
-- Find 10 most similar embeddings to query vector
SELECT id, text, embedding <=> '[0.1, 0.2, ..., 0.768]'::vector AS distance
FROM semantic_embeddings
ORDER BY embedding <=> '[0.1, 0.2, ..., 0.768]'::vector
LIMIT 10;
```

### Approximate Nearest Neighbor (ANN)

HNSW is an **approximate** algorithm. Control recall vs speed with `ef_search`:

```sql
-- Higher ef_search = better recall but slower queries
SET hnsw.ef_search = 100;  -- Default: 40

SELECT id, text, embedding <=> '[...]'::vector AS distance
FROM semantic_embeddings
ORDER BY embedding <=> '[...]'::vector
LIMIT 10;
```

**Tuning**:
- `ef_search = 40`: ~90% recall, ~5ms p50 latency
- `ef_search = 100`: ~95% recall, ~10ms p50 latency
- `ef_search = 200`: ~98% recall, ~20ms p50 latency

### Filtered Searches

Combine vector search with WHERE clause:

```sql
-- Find similar embeddings within a specific session
SELECT id, text, embedding <=> '[...]'::vector AS distance
FROM semantic_embeddings
WHERE metadata->>'session_id' = 'abc-123'
ORDER BY embedding <=> '[...]'::vector
LIMIT 10;
```

**Performance Note**: Pre-filter BEFORE vector search using btree index on `metadata->>'session_id'` (GIN index on jsonb is less efficient for equality checks).

## 6. Index Build Strategy

### Concurrent Builds

```sql
-- Build HNSW index without blocking writes (recommended for production)
CREATE INDEX CONCURRENTLY idx_embedding_hnsw
ON semantic_embeddings
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**Trade-offs**:
- **Duration**: ~6-12 hours for 100M vectors on `db-custom-16-64000` (versus ~2-4 hours for blocking build)
- **Locks**: No table locks; writes continue during build
- **Failure Mode**: If build fails, manually `DROP INDEX CONCURRENTLY` and retry

### Memory Requirements

HNSW index builds are memory-intensive:

```hcl
# Database flag in Terraform
database_flags {
  name  = "maintenance_work_mem"
  value = "4194304"  # 4GB (4 * 1024 * 1024 KB)
}
```

**Rationale**: Default `maintenance_work_mem` is 64MB. HNSW builds for 100M vectors require 2-4GB to avoid excessive disk I/O.

## 7. Storage & Performance Characteristics

### Storage Breakdown (100M Vectors, 768-dim)

| Component | Size | Notes |
|-----------|------|-------|
| Raw vectors | ~300 GB | 768 floats × 4 bytes × 100M |
| HNSW index | ~300 GB | Graph structure (m=16 → ~16 neighbors × 2 × 100M nodes) |
| Metadata (text, jsonb) | ~50 GB | Assumes avg 500 chars per text field |
| **Total** | **~650 GB** | Fits in 1TB provisioned SSD with headroom |

### Query Performance (Benchmarks)

Based on pgvector benchmarks (Postgres 16, 768-dim, HNSW m=16):

| Dataset Size | ef_search | p50 Latency | p95 Latency | Recall@10 |
|--------------|-----------|-------------|-------------|-----------|
| 100M vectors | 40 | 5ms | 12ms | 90% |
| 100M vectors | 100 | 10ms | 25ms | 95% |
| 100M vectors | 200 | 20ms | 50ms | 98% |

**Note**: Benchmarks assume hot index (fully cached in RAM). If 64GB RAM is insufficient, expect 2-5x latency increase due to SSD reads.

## 8. Maintenance Operations

### Reindexing

```sql
-- Drop and rebuild index (requires maintenance window)
DROP INDEX idx_embedding_hnsw;
CREATE INDEX CONCURRENTLY idx_embedding_hnsw
ON semantic_embeddings
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**Trigger**: After bulk inserts >10% of table size (e.g., backfilling 10M+ embeddings).

### VACUUM

```sql
-- Reclaim space after deletes/updates
VACUUM ANALYZE semantic_embeddings;
```

**Schedule**: Weekly (automated via Cloud SQL maintenance window).

## 9. Compatibility Notes (Cloud SQL Postgres 16)

### Version Constraints

- **Minimum pgvector Version**: 0.5.0 (bundled with Cloud SQL Postgres 16)
- **HNSW Support**: Requires pgvector ≥ 0.5.0 (available on Cloud SQL)
- **Upgrade Path**: Cloud SQL auto-updates pgvector with Postgres minor version upgrades (no action required)

### Known Quirks

1. **Extension NOT Pre-Loaded**: Must run `CREATE EXTENSION vector` explicitly (include in Alembic baseline).
2. **IAM Auth Compatibility**: pgvector works with IAM database authentication (no restrictions).
3. **Backup/Restore**: PITR and backups include pgvector indexes (no special handling required).

## 10. Migration Strategy

### Baseline Migration (Alembic)

```python
# migrations/versions/001_baseline.py

def upgrade():
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create table
    op.execute("""
        CREATE TABLE semantic_embeddings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id UUID NOT NULL,
            embedding VECTOR(768),
            text TEXT NOT NULL,
            metadata JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Create btree index on source_id (for foreign key lookups)
    op.execute("""
        CREATE INDEX idx_source_id ON semantic_embeddings (source_id)
    """)

    # Create HNSW index (CONCURRENTLY not supported in Alembic transaction)
    # Run this step manually post-migration via separate script
```

### Post-Migration Index Build

```bash
# scripts/build-hnsw-index.sh
#!/bin/bash
set -euo pipefail

psql "host=/cloudsql/i-for-ai:us-central1:hermes-vector-db dbname=hermes user=autonomousagent-vm-runtime@i-for-ai.iam" <<EOF
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_embedding_hnsw
ON semantic_embeddings
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
EOF
```

**Rationale**: Alembic migrations run in transactions; `CREATE INDEX CONCURRENTLY` cannot run inside a transaction. Separate script avoids this limitation.

## 11. Monitoring Recommendations

### Metrics to Track (Defer to Task 30)

1. **Index Size**: `pg_relation_size('idx_embedding_hnsw')` → alert if >400GB (signals bloat)
2. **Query Latency**: `pg_stat_statements.mean_exec_time` for vector queries → alert if p95 >50ms
3. **Index Build Progress**: `pg_stat_progress_create_index` (during CONCURRENT builds)
4. **Cache Hit Ratio**: `pg_statio_user_indexes.idx_blks_read / idx_blks_hit` → alert if <90% (index not fitting in RAM)

### Log Queries (Performance Debugging)

```sql
-- Log slow vector queries (>100ms)
ALTER DATABASE hermes SET log_min_duration_statement = 100;
```

## 12. Cost Implications

### Storage Cost

- **100M vectors (768-dim)**: ~650GB total → ~$220/mo SSD storage (Regional HA pricing)
- **Alternative (e5-large, 1024-dim)**: ~850GB → ~$285/mo SSD storage

**Savings**: 768-dim saves ~$65/mo (~23% reduction).

### Compute Cost

- **Index Builds**: One-time 6-12 hour build on `db-custom-16-64000` → ~$0.80-1.60 compute cost
- **Query Serving**: Minimal CPU impact at 5 QPS (vectorized operations are efficient)

## 13. Future Scaling Considerations

### 1B Embeddings (Long-Term Target)

| Metric | 100M (MVP) | 1B (Production) | Change |
|--------|-----------|-----------------|--------|
| Storage | ~650 GB | ~6.5 TB | 10x |
| RAM Required | 64 GB | 256 GB | 4x |
| Instance Tier | db-custom-16-64000 | db-custom-64-256000 | 4x vCPU, 4x RAM |
| Monthly Cost | ~$1,570 | ~$6,200 | 4x |

**Scaling Trigger**: When query latency exceeds 50ms p95 OR storage exceeds 70% of provisioned capacity.

### Sharding Strategy (Deferred)

For >1B embeddings, consider:
- **Horizontal Sharding**: Partition by `source_id` hash (distribute across multiple Cloud SQL instances)
- **Read Replicas**: Offload vector queries to read replicas (Cloud SQL supports up to 10 replicas)

## 14. References

- pgvector documentation: https://github.com/pgvector/pgvector
- HNSW algorithm paper: https://arxiv.org/abs/1603.09320
- Cloud SQL Postgres extensions: https://cloud.google.com/sql/docs/postgres/extensions
- e5-base model card: https://huggingface.co/intfloat/e5-base-v2
