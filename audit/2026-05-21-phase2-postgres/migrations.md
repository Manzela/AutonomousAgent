# Alembic Migration Strategy

**Tool**: Alembic (Python database migration framework)
**Database**: Cloud SQL Postgres 16 (`hermes` on `i-for-ai`)
**Rationale**: Python-native, SQLAlchemy integration optional, supports raw SQL migrations, mature ecosystem

## 1. Tooling Decision: Alembic vs Alternatives

| Tool | Pros | Cons | Verdict |
|------|------|------|---------|
| **Alembic** | Python-native; SQLAlchemy-aware but can use raw SQL; widely adopted; supports branching migrations | Learning curve for non-Python teams | **CHOSEN** (best fit for Python-heavy agent codebase) |
| sqlx | Rust-native; compile-time checked queries; fast | No Python integration; requires Rust toolchain | Rejected (stack mismatch) |
| Flyway | Language-agnostic; Java-based; enterprise features | Requires JVM; overkill for single-DB setup | Rejected (complexity vs benefit) |
| Django Migrations | Excellent Python integration; ORM-driven | Tightly coupled to Django; not suitable for standalone DB | Rejected (no Django in stack) |
| Liquibase | XML/YAML/SQL; enterprise-grade; rollback support | Heavy; requires Java | Rejected (complexity) |

**Decision**: Alembic is the best fit for a Python-centric autonomous agent with PostgreSQL.

## 2. Directory Structure

```
autonomous-agent/
├── alembic/
│   ├── versions/
│   │   ├── 001_baseline.py              # Initial schema (episodic, semantic, procedural)
│   │   ├── 002_add_embedding_index.py   # HNSW index (post-migration script)
│   │   ├── 003_add_partition_2026_11.py # Monthly partition creation
│   │   └── ...
│   ├── env.py                           # Alembic environment config (DB connection)
│   ├── script.py.mako                   # Template for new migrations
│   └── README.md                        # Migration runbook
├── alembic.ini                          # Alembic configuration file
├── scripts/
│   ├── build-hnsw-index.sh              # Post-migration HNSW index build
│   └── create-monthly-partition.sh      # Automated partition creation
└── requirements.txt                     # Python dependencies (alembic, psycopg3, etc.)
```

## 3. Alembic Configuration (`alembic.ini`)

```ini
[alembic]
# Path to migration scripts
script_location = alembic

# Template for generating new migration files
file_template = %%(year)d%%(month).2d%%(day).2d_%%(hour).2d%%(minute).2d_%%(slug)s

# Database connection URL (override via env var for security)
# Format: postgresql+psycopg://user@/dbname?host=/cloudsql/i-for-ai:us-central1:hermes-vector-db
sqlalchemy.url =

# Timezone for migration timestamps
timezone = UTC

# Truncate slug to 40 chars for readability
truncate_slug_length = 40

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = INFO
handlers = console

[logger_sqlalchemy]
level = WARN
handlers = console
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers = console
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %Y-%m-%d %H:%M:%S
```

## 4. Environment Configuration (`alembic/env.py`)

### Override for IAM Authentication + Cloud SQL Proxy

```python
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Alembic Config object (from alembic.ini)
config = context.config

# Configure logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata object (optional — only needed if using SQLAlchemy ORM models)
# For raw SQL migrations, leave as None
target_metadata = None

def get_url():
    """Fetch database connection URL from environment or Secret Manager."""
    # Option 1: Environment variable (CI/local dev)
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    # Option 2: Fetch from Secret Manager (production VM)
    from google.cloud import secretmanager
    client = secretmanager.SecretManagerServiceClient()
    secret_name = "projects/i-for-ai/secrets/autonomousagent-db-connection/versions/latest"
    response = client.access_secret_version(request={"name": secret_name})
    import json
    db_config = json.loads(response.payload.data.decode("UTF-8"))

    # Build connection URL for Cloud SQL Proxy (Unix socket)
    return (
        f"postgresql+psycopg://{db_config['user']}@/{db_config['database']}"
        f"?host=/cloudsql/{db_config['connection_name']}"
    )

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generate SQL without DB connection)."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to DB and apply)."""
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # No connection pooling for migrations
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

### Environment Variables

```bash
# Local development (Cloud SQL Proxy running locally)
export DATABASE_URL="postgresql+psycopg://autonomousagent-vm-runtime@i-for-ai.iam@/hermes?host=/cloudsql/i-for-ai:us-central1:hermes-vector-db"

# CI (GitHub Actions)
export DATABASE_URL="postgresql+psycopg://autonomousagent-github-ci@i-for-ai.iam@/hermes?host=/cloudsql/i-for-ai:us-central1:hermes-vector-db"
```

## 5. Baseline Migration (`alembic/versions/001_baseline.py`)

```python
"""Baseline schema: episodic, semantic, procedural memory tiers

Revision ID: 001_baseline
Revises:
Create Date: 2026-05-21 10:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# Revision identifiers
revision: str = '001_baseline'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    """Apply baseline schema."""

    # Enable extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin")

    # Episodic events (partitioned by timestamp)
    op.execute("""
        CREATE TABLE episodic_events (
            id UUID DEFAULT gen_random_uuid(),
            session_id UUID NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            agent_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (id, timestamp)
        ) PARTITION BY RANGE (timestamp)
    """)

    # Create initial monthly partitions (6 months ahead)
    partitions = [
        ("2026_05", "2026-05-01", "2026-06-01"),
        ("2026_06", "2026-06-01", "2026-07-01"),
        ("2026_07", "2026-07-01", "2026-08-01"),
        ("2026_08", "2026-08-01", "2026-09-01"),
        ("2026_09", "2026-09-01", "2026-10-01"),
        ("2026_10", "2026-10-01", "2026-11-01"),
    ]
    for suffix, start, end in partitions:
        op.execute(f"""
            CREATE TABLE episodic_events_{suffix} PARTITION OF episodic_events
            FOR VALUES FROM ('{start}') TO ('{end}')
        """)

    # Episodic indexes
    op.execute("""
        CREATE INDEX idx_episodic_session_timestamp
        ON episodic_events (session_id, timestamp DESC)
    """)
    op.execute("""
        CREATE INDEX idx_episodic_agent_timestamp
        ON episodic_events (agent_id, timestamp DESC)
    """)
    op.execute("""
        CREATE INDEX idx_episodic_event_type
        ON episodic_events (event_type, timestamp DESC)
    """)
    op.execute("""
        CREATE INDEX idx_episodic_payload_gin
        ON episodic_events USING gin (payload jsonb_path_ops)
    """)

    # Semantic embeddings
    op.execute("""
        CREATE TABLE semantic_embeddings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id UUID NOT NULL,
            source_type TEXT NOT NULL,
            embedding VECTOR(768),
            text TEXT NOT NULL,
            metadata JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Semantic indexes (HNSW deferred to post-migration script)
    op.execute("""
        CREATE INDEX idx_semantic_source_id
        ON semantic_embeddings (source_id)
    """)
    op.execute("""
        CREATE INDEX idx_semantic_source_type
        ON semantic_embeddings (source_type)
    """)
    op.execute("""
        CREATE INDEX idx_semantic_metadata_gin
        ON semantic_embeddings USING gin (metadata jsonb_path_ops)
    """)

    # Semantic updated_at trigger
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trigger_semantic_updated_at
        BEFORE UPDATE ON semantic_embeddings
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at()
    """)

    # Procedural skills
    op.execute("""
        CREATE TABLE procedural_skills (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name TEXT UNIQUE NOT NULL,
            description TEXT NOT NULL,
            code TEXT NOT NULL,
            version INT NOT NULL DEFAULT 1,
            language TEXT NOT NULL DEFAULT 'python',
            metadata JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            deprecated_at TIMESTAMPTZ,
            CONSTRAINT unique_skill_version UNIQUE (name, version)
        )
    """)

    # Procedural indexes
    op.execute("""
        CREATE INDEX idx_skills_name
        ON procedural_skills (name, version DESC)
    """)
    op.execute("""
        CREATE INDEX idx_skills_active
        ON procedural_skills (deprecated_at)
        WHERE deprecated_at IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_skills_metadata_gin
        ON procedural_skills USING gin (metadata jsonb_path_ops)
    """)

    # Procedural updated_at trigger
    op.execute("""
        CREATE TRIGGER trigger_skills_updated_at
        BEFORE UPDATE ON procedural_skills
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at()
    """)

    # Migrations audit table
    op.execute("""
        CREATE TABLE migrations (
            id SERIAL PRIMARY KEY,
            version VARCHAR(32) UNIQUE NOT NULL,
            description TEXT,
            applied_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Seed baseline migration record
    op.execute("""
        INSERT INTO migrations (version, description)
        VALUES ('001_baseline', 'Baseline schema: episodic, semantic, procedural memory tiers')
    """)

    # Helper views
    op.execute("""
        CREATE VIEW active_skills AS
        SELECT id, name, description, code, version, language, metadata, created_at, updated_at
        FROM procedural_skills
        WHERE deprecated_at IS NULL
    """)
    op.execute("""
        CREATE VIEW recent_episodic_events AS
        SELECT id, session_id, timestamp, agent_id, event_type, payload
        FROM episodic_events
        WHERE timestamp > NOW() - INTERVAL '7 days'
        ORDER BY timestamp DESC
    """)

def downgrade() -> None:
    """Rollback baseline schema."""
    op.execute("DROP VIEW IF EXISTS recent_episodic_events")
    op.execute("DROP VIEW IF EXISTS active_skills")
    op.execute("DROP TABLE IF EXISTS migrations")
    op.execute("DROP TABLE IF EXISTS procedural_skills CASCADE")
    op.execute("DROP TABLE IF EXISTS semantic_embeddings CASCADE")
    op.execute("DROP TABLE IF EXISTS episodic_events CASCADE")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at() CASCADE")
    op.execute("DROP EXTENSION IF EXISTS btree_gin")
    op.execute("DROP EXTENSION IF EXISTS vector")
```

## 6. Post-Migration HNSW Index Build

**Rationale**: `CREATE INDEX CONCURRENTLY` cannot run inside Alembic transaction. Separate script required.

### Script: `scripts/build-hnsw-index.sh`

```bash
#!/bin/bash
# Build HNSW index for semantic_embeddings.embedding column
# Run this AFTER applying baseline migration

set -euo pipefail

INSTANCE_CONNECTION_NAME="i-for-ai:us-central1:hermes-vector-db"
DB_NAME="hermes"
DB_USER="autonomousagent-vm-runtime@i-for-ai.iam"

echo "Building HNSW index on semantic_embeddings.embedding..."
echo "This may take 6-12 hours for 100M vectors. DO NOT INTERRUPT."

psql "host=/cloudsql/${INSTANCE_CONNECTION_NAME} dbname=${DB_NAME} user=${DB_USER}" <<EOF
-- Build HNSW index concurrently (no table locks)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_embedding_hnsw
ON semantic_embeddings
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- Verify index was created
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE tablename = 'semantic_embeddings' AND indexname = 'idx_embedding_hnsw';

-- Log to migrations table
INSERT INTO migrations (version, description)
VALUES ('002_hnsw_index', 'Built HNSW index on semantic_embeddings.embedding');
EOF

echo "HNSW index build complete!"
```

### Trigger

Run after `alembic upgrade head`:
```bash
alembic upgrade head
./scripts/build-hnsw-index.sh
```

## 7. Monthly Partition Creation

### Script: `scripts/create-monthly-partition.sh`

```bash
#!/bin/bash
# Create next month's partition for episodic_events
# Schedule via cron on the 1st of each month

set -euo pipefail

INSTANCE_CONNECTION_NAME="i-for-ai:us-central1:hermes-vector-db"
DB_NAME="hermes"
DB_USER="autonomousagent-vm-runtime@i-for-ai.iam"

# Calculate next month (YYYY_MM format)
NEXT_MONTH=$(date -u -d "next month" +%Y_%m)
NEXT_MONTH_START=$(date -u -d "next month" +%Y-%m-01)
MONTH_AFTER_NEXT_START=$(date -u -d "2 months" +%Y-%m-01)

echo "Creating partition: episodic_events_${NEXT_MONTH}"

psql "host=/cloudsql/${INSTANCE_CONNECTION_NAME} dbname=${DB_NAME} user=${DB_USER}" <<EOF
CREATE TABLE IF NOT EXISTS episodic_events_${NEXT_MONTH} PARTITION OF episodic_events
FOR VALUES FROM ('${NEXT_MONTH_START}') TO ('${MONTH_AFTER_NEXT_START}');

-- Verify partition was created
SELECT schemaname, tablename
FROM pg_tables
WHERE tablename = 'episodic_events_${NEXT_MONTH}';

-- Log to migrations table
INSERT INTO migrations (version, description)
VALUES ('partition_${NEXT_MONTH}', 'Created partition episodic_events_${NEXT_MONTH}');
EOF

echo "Partition episodic_events_${NEXT_MONTH} created!"
```

### Cron Schedule (VM)

```cron
# Run on 1st of each month at 00:00 UTC
0 0 1 * * /opt/autonomous-agent/scripts/create-monthly-partition.sh >> /var/log/partition-creation.log 2>&1
```

## 8. Migration Workflow

### Development

```bash
# 1. Generate new migration
alembic revision -m "add_embedding_metadata_index"

# 2. Edit generated file (alembic/versions/<timestamp>_add_embedding_metadata_index.py)
#    - Implement upgrade() and downgrade() functions

# 3. Apply migration
alembic upgrade head

# 4. Verify
alembic current
```

### Production

```bash
# 1. Review migration SQL (dry run)
alembic upgrade head --sql > migration.sql
less migration.sql

# 2. Backup database
gcloud sql backups create --instance=hermes-vector-db --project=i-for-ai

# 3. Apply migration
alembic upgrade head

# 4. Verify
alembic current
alembic history
```

### Rollback

```bash
# Rollback last migration
alembic downgrade -1

# Rollback to specific revision
alembic downgrade 001_baseline

# Verify
alembic current
```

## 9. Testing Migrations

### Local Test Database

```bash
# Spin up local Postgres 16 (Docker)
docker run -d --name postgres-test \
  -e POSTGRES_DB=hermes \
  -e POSTGRES_USER=test \
  -e POSTGRES_PASSWORD=test \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# Set test DATABASE_URL
export DATABASE_URL="postgresql+psycopg://test:test@localhost:5432/hermes"

# Run migrations
alembic upgrade head

# Verify
psql $DATABASE_URL -c "\dt"
psql $DATABASE_URL -c "\di"

# Cleanup
docker rm -f postgres-test
```

### CI Integration (GitHub Actions)

```yaml
# .github/workflows/test-migrations.yml
name: Test Alembic Migrations

on: [pull_request]

jobs:
  test-migrations:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_DB: hermes
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: alembic upgrade head
        env:
          DATABASE_URL: postgresql+psycopg://test:test@localhost:5432/hermes
      - run: alembic current
      - run: alembic downgrade base
```

## 10. Dependencies (`requirements.txt`)

```txt
alembic==1.13.0
psycopg[binary]==3.1.12
sqlalchemy==2.0.23
google-cloud-secret-manager==2.16.4
```

## 11. Migration Naming Conventions

| Type | Example | Notes |
|------|---------|-------|
| Schema change | `001_baseline.py` | Initial or major schema version |
| Add index | `002_add_embedding_index.py` | Index creation (non-blocking) |
| Add column | `003_add_user_email.py` | Additive changes |
| Data migration | `004_backfill_embeddings.py` | Data transformation (separate from schema) |
| Partition | `partition_2026_11.py` | Auto-generated monthly partition |

## 12. Rollback Safety

### Safe Rollbacks

- Adding indexes (CONCURRENTLY)
- Adding columns (non-NULL with default)
- Creating tables/views

### Unsafe Rollbacks (Data Loss)

- Dropping columns
- Dropping tables
- Changing column types (narrowing)

**Mitigation**: Test rollback on staging before production.

## 13. Monitoring Migration Health

### Metrics to Track

1. **Migration Duration**: Log `alembic upgrade head` runtime
2. **Index Build Progress**: Query `pg_stat_progress_create_index` for CONCURRENT builds
3. **Lock Contention**: Monitor `pg_locks` during migration
4. **Disk Usage**: Check partition sizes post-migration

### Alerting

```bash
# Alert if migration takes >30 minutes
timeout 1800 alembic upgrade head || echo "ALERT: Migration timeout"
```

## 14. Future Enhancements (Defer to Phase 3)

- **Branching Migrations**: Support multiple feature branches with Alembic's branching model
- **Blue/Green Deployments**: Run migrations on standby replica, promote after verification
- **Automated Partition Management**: Use pg_partman extension for automatic partition creation/archival
- **Migration Smoke Tests**: Auto-run schema validation queries after each migration
