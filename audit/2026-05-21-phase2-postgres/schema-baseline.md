# Schema Baseline — Hierarchical Memory Tiers

**Database**: `hermes` (Cloud SQL Postgres 16)
**Owner**: `autonomousagent-vm-runtime@i-for-ai.iam`
**Migration Tool**: Alembic (Python-native)

## 1. Extension Dependencies

```sql
-- Enable pgvector extension (vector similarity search)
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable btree_gin for composite indexes on JSONB + scalar columns
CREATE EXTENSION IF NOT EXISTS btree_gin;
```

## 2. Episodic Memory (Event Log)

### Table: `episodic_events`

**Purpose**: Append-only log of agent interactions, prompts, tool calls, and responses. Partitioned by month to handle >100M rows.

```sql
CREATE TABLE episodic_events (
    id UUID DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agent_id TEXT NOT NULL,
    event_type TEXT NOT NULL,  -- 'prompt', 'response', 'tool_call', 'tool_result', 'error'
    payload JSONB NOT NULL,    -- Event-specific data (prompt text, tool args, etc.)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, timestamp)  -- Composite PK for partitioning
) PARTITION BY RANGE (timestamp);

-- Create initial monthly partitions (6 months ahead)
CREATE TABLE episodic_events_2026_05 PARTITION OF episodic_events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

CREATE TABLE episodic_events_2026_06 PARTITION OF episodic_events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

CREATE TABLE episodic_events_2026_07 PARTITION OF episodic_events
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');

CREATE TABLE episodic_events_2026_08 PARTITION OF episodic_events
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');

CREATE TABLE episodic_events_2026_09 PARTITION OF episodic_events
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

CREATE TABLE episodic_events_2026_10 PARTITION OF episodic_events
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
```

### Indexes (Episodic Memory)

```sql
-- Session-based retrieval (e.g., "fetch all events for session X")
CREATE INDEX idx_episodic_session_timestamp
ON episodic_events (session_id, timestamp DESC);

-- Agent-based filtering (e.g., "all events from agent Y")
CREATE INDEX idx_episodic_agent_timestamp
ON episodic_events (agent_id, timestamp DESC);

-- Event type filtering (e.g., "all errors in the last hour")
CREATE INDEX idx_episodic_event_type
ON episodic_events (event_type, timestamp DESC);

-- Payload search (e.g., "find all tool_call events where tool_name = 'WebSearch'")
CREATE INDEX idx_episodic_payload_gin
ON episodic_events USING gin (payload jsonb_path_ops);
```

### Partition Management Script

```sql
-- Automate partition creation via pg_cron (defer to Phase 2 implementation)
-- For now, manually create partitions quarterly via Alembic migration
```

### Retention Policy (Future)

Partitioning enables cheap deletion:
```sql
-- Archive events older than 12 months to GCS (Task 32)
-- Then drop partition:
DROP TABLE episodic_events_2025_05;
```

## 3. Semantic Memory (Vector Embeddings)

### Table: `semantic_embeddings`

**Purpose**: Vector representations of episodic events, documents, and skills for retrieval-augmented generation.

```sql
CREATE TABLE semantic_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL,              -- FK to episodic_events.id or external source
    source_type TEXT NOT NULL,            -- 'episodic', 'document', 'skill'
    embedding VECTOR(768),                -- e5-base 768-dim vector
    text TEXT NOT NULL,                   -- Original text content
    metadata JSONB,                       -- Source-specific metadata (session_id, tags, etc.)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Indexes (Semantic Memory)

```sql
-- Source ID lookup (e.g., "get embedding for episodic event X")
CREATE INDEX idx_semantic_source_id
ON semantic_embeddings (source_id);

-- Source type filtering (e.g., "search only skill embeddings")
CREATE INDEX idx_semantic_source_type
ON semantic_embeddings (source_type);

-- HNSW vector similarity search (cosine distance)
-- NOTE: Run CONCURRENTLY outside Alembic transaction (see pgvector-spec.md)
-- CREATE INDEX CONCURRENTLY idx_embedding_hnsw
-- ON semantic_embeddings
-- USING hnsw (embedding vector_cosine_ops)
-- WITH (m = 16, ef_construction = 64);

-- Metadata search (e.g., "find embeddings where metadata->>'category' = 'api'")
CREATE INDEX idx_semantic_metadata_gin
ON semantic_embeddings USING gin (metadata jsonb_path_ops);

-- Composite index for filtered vector search (source_type + metadata filter)
CREATE INDEX idx_semantic_source_metadata
ON semantic_embeddings USING gin (source_type, metadata);
```

### Triggers (Automatic Timestamp Updates)

```sql
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_semantic_updated_at
BEFORE UPDATE ON semantic_embeddings
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();
```

## 4. Procedural Memory (Skills & Policies)

### Table: `procedural_skills`

**Purpose**: Versioned library of agent skills, policies, and executable code snippets.

```sql
CREATE TABLE procedural_skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,            -- Skill name (e.g., 'web_search', 'code_review')
    description TEXT NOT NULL,
    code TEXT NOT NULL,                   -- Python code or JSON tool definition
    version INT NOT NULL DEFAULT 1,       -- Monotonic version counter
    language TEXT NOT NULL DEFAULT 'python',  -- 'python', 'json', 'sql'
    metadata JSONB,                       -- Tags, dependencies, author, etc.
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    deprecated_at TIMESTAMPTZ,            -- NULL if active; set on deprecation
    CONSTRAINT unique_skill_version UNIQUE (name, version)
);
```

### Indexes (Procedural Memory)

```sql
-- Name-based lookup (e.g., "get latest version of skill X")
CREATE INDEX idx_skills_name
ON procedural_skills (name, version DESC);

-- Active skills filter (exclude deprecated)
CREATE INDEX idx_skills_active
ON procedural_skills (deprecated_at)
WHERE deprecated_at IS NULL;

-- Metadata search (e.g., "find all skills tagged with 'api'")
CREATE INDEX idx_skills_metadata_gin
ON procedural_skills USING gin (metadata jsonb_path_ops);
```

### Triggers (Automatic Timestamp Updates)

```sql
CREATE TRIGGER trigger_skills_updated_at
BEFORE UPDATE ON procedural_skills
FOR EACH ROW
EXECUTE FUNCTION update_updated_at();
```

### Version Management

```sql
-- Deprecate old version when new version is added (application logic)
UPDATE procedural_skills
SET deprecated_at = NOW()
WHERE name = 'web_search' AND version < 3;
```

## 5. Schema Version Tracking

### Table: `migrations`

**Purpose**: Track applied Alembic migrations (Alembic's default table is `alembic_version`; this is supplementary audit trail).

```sql
CREATE TABLE migrations (
    id SERIAL PRIMARY KEY,
    version VARCHAR(32) UNIQUE NOT NULL,  -- Alembic revision ID (e.g., 'a1b2c3d4e5f6')
    description TEXT,                     -- Human-readable migration name
    applied_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Seed Data

```sql
-- Insert baseline migration record
INSERT INTO migrations (version, description)
VALUES ('001_baseline', 'Baseline schema: episodic, semantic, procedural memory tiers');
```

## 6. Helper Views (Query Simplification)

### View: `active_skills`

```sql
CREATE VIEW active_skills AS
SELECT id, name, description, code, version, language, metadata, created_at, updated_at
FROM procedural_skills
WHERE deprecated_at IS NULL;
```

### View: `recent_episodic_events`

```sql
CREATE VIEW recent_episodic_events AS
SELECT id, session_id, timestamp, agent_id, event_type, payload
FROM episodic_events
WHERE timestamp > NOW() - INTERVAL '7 days'
ORDER BY timestamp DESC;
```

## 7. Row-Level Security (Future)

Defer to Phase 3 (multi-tenancy):
```sql
-- Example: Restrict episodic events to session owner
-- ALTER TABLE episodic_events ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY session_isolation ON episodic_events
--     FOR ALL
--     USING (session_id = current_setting('app.session_id')::uuid);
```

## 8. Estimated Storage (MVP Scale)

| Table | Rows (MVP) | Avg Row Size | Total Size | Notes |
|-------|------------|--------------|------------|-------|
| `episodic_events` | 10M | ~1 KB | ~10 GB | Partitioned by month (12 partitions → ~1GB/partition) |
| `semantic_embeddings` | 100M | ~4 KB | ~400 GB | 768-dim vector + text + metadata |
| `procedural_skills` | 100K | ~5 KB | ~500 MB | Small; unlikely to exceed 1GB |
| **Total** | | | **~410 GB** | Fits in 1TB provisioned SSD with headroom |

## 9. Schema Evolution Strategy

### Alembic Migration Pattern

```python
# migrations/versions/002_add_embedding_metadata_index.py

def upgrade():
    op.execute("""
        CREATE INDEX CONCURRENTLY idx_semantic_metadata_category
        ON semantic_embeddings ((metadata->>'category'))
    """)

def downgrade():
    op.execute("DROP INDEX CONCURRENTLY idx_semantic_metadata_category")
```

### Schema Versioning

- **Major Changes** (breaking): Increment first digit (e.g., `001_baseline` → `100_v2_schema`)
- **Minor Changes** (additive): Increment second digit (e.g., `001_baseline` → `002_add_index`)
- **Patches** (fixes): Increment third digit (e.g., `002` → `002_001_fix_constraint`)

## 10. Backup & Restore Considerations

### Full Schema Dump (Disaster Recovery)

```bash
# Export schema-only (no data)
pg_dump -h <private-ip> -U autonomousagent-vm-runtime@i-for-ai.iam \
    --schema-only --no-owner --no-privileges \
    hermes > schema-baseline.sql
```

### PITR Restore (Point-in-Time Recovery)

```bash
# Restore database to specific timestamp (Cloud SQL managed)
gcloud sql backups create --instance=hermes-vector-db --project=i-for-ai
gcloud sql backups restore <backup-id> \
    --backup-instance=hermes-vector-db \
    --target-instance=hermes-vector-db-restored \
    --recovery-time=2026-05-21T12:00:00Z
```

## 11. Performance Tuning (Database Flags)

```hcl
# Terraform database flags (defer to cloud_sql.tf)

database_flags {
  name  = "shared_buffers"
  value = "16777216"  # 16GB (25% of RAM for 64GB instance)
}

database_flags {
  name  = "effective_cache_size"
  value = "50331648"  # 48GB (75% of RAM)
}

database_flags {
  name  = "maintenance_work_mem"
  value = "4194304"  # 4GB (for HNSW index builds)
}

database_flags {
  name  = "work_mem"
  value = "131072"  # 128MB (per query sort/hash operation)
}

database_flags {
  name  = "max_parallel_workers"
  value = "16"  # Match vCPU count
}
```

## 12. Query Examples (Application Integration)

### Episodic Retrieval

```python
# Fetch recent events for a session
query = """
    SELECT id, timestamp, event_type, payload
    FROM episodic_events
    WHERE session_id = %s AND timestamp > %s
    ORDER BY timestamp DESC
    LIMIT 100
"""
cursor.execute(query, (session_id, cutoff_timestamp))
```

### Semantic Search

```python
# Find 10 most similar embeddings to query
query = """
    SELECT id, text, metadata,
           embedding <=> %s::vector AS distance
    FROM semantic_embeddings
    WHERE source_type = 'episodic'
    ORDER BY embedding <=> %s::vector
    LIMIT 10
"""
cursor.execute(query, (query_embedding, query_embedding))
```

### Skill Lookup

```python
# Get latest active version of a skill
query = """
    SELECT code, version
    FROM active_skills
    WHERE name = %s
    ORDER BY version DESC
    LIMIT 1
"""
cursor.execute(query, (skill_name,))
```

## 13. Security Hardening (Defer to Phase 2)

### Column Encryption (Future)

```sql
-- Encrypt sensitive payload fields (e.g., API keys in episodic events)
-- Use pgcrypto extension + application-managed encryption keys
-- CREATE EXTENSION pgcrypto;
-- UPDATE episodic_events
-- SET payload = jsonb_set(
--     payload,
--     '{api_key}',
--     to_jsonb(pgp_sym_encrypt(payload->>'api_key', 'encryption_key'))
-- );
```

### Audit Logging

```sql
-- Enable pgaudit extension for compliance (Task 33)
-- CREATE EXTENSION pgaudit;
-- ALTER SYSTEM SET pgaudit.log = 'write, ddl';
```

## 14. Deployment Checklist

- [ ] Create database `hermes` via Terraform (`google_sql_database`)
- [ ] Grant IAM user `autonomousagent-vm-runtime@i-for-ai.iam` CONNECT privilege
- [ ] Run Alembic baseline migration (`alembic upgrade head`)
- [ ] Verify pgvector extension enabled (`SELECT * FROM pg_available_extensions WHERE name = 'vector'`)
- [ ] Build HNSW index CONCURRENTLY (post-migration script)
- [ ] Seed initial procedural skills (optional)
- [ ] Configure database flags (shared_buffers, maintenance_work_mem, etc.)
- [ ] Test episodic insert (smoke test)
- [ ] Test semantic search (smoke test with dummy vector)
- [ ] Test skill retrieval (smoke test)
- [ ] Backup schema to GCS (disaster recovery baseline)
