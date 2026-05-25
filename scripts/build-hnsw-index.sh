#!/usr/bin/env bash
# scripts/build-hnsw-index.sh — Rebuild HNSW index CONCURRENTLY after bulk load.
#
# Run this AFTER bulk-inserting records via migrate_cloud_sql.py or a data
# migration. Building HNSW CONCURRENTLY does NOT lock the table for reads
# and writes — it's safe to run against a live production database.
#
# This script is kept OUTSIDE Alembic / migrate_cloud_sql.py on purpose:
# HNSW index builds can take hours on 10M+ vectors and should be run as
# an operator-initiated job, not as part of a CI/CD migration step.
#
# Design spec: docs/superpowers/specs/2026-05-25-cloud-sql-pgvector-store-design.md §1.D
#
# Usage:
#   # Assumes Cloud SQL Auth Proxy is up on 127.0.0.1:5432
#   ./scripts/build-hnsw-index.sh [DSN]
#
#   # Or with explicit DSN:
#   ./scripts/build-hnsw-index.sh "postgresql://...@127.0.0.1:5432/hermes?sslmode=disable"
#
# Prerequisites:
#   - Cloud SQL Auth Proxy running on localhost:5432
#   - psql (PostgreSQL client) installed
#   - Sufficient maintenance_work_mem (4GB recommended for 10M+ vectors)
set -euo pipefail

DSN="${1:-${CLOUD_SQL_DSN:-postgresql://autonomousagent-vm-runtime@autonomous-agent-2026.iam@127.0.0.1:5432/hermes?sslmode=disable}}"

echo "=== HNSW Index Rebuild (CONCURRENTLY) ==="
echo "DSN: ${DSN%%@*}@***"
echo ""

# Step 1: Drop the existing HNSW index if it exists.
# We drop first because CREATE INDEX IF NOT EXISTS doesn't support
# CONCURRENTLY, and REINDEX CONCURRENTLY is only PG 12+ (we're on 16).
echo "[1/3] Dropping existing HNSW index (if any)..."
psql "${DSN}" -c "DROP INDEX IF EXISTS memory_records_embedding_hnsw;"
echo "OK: Index dropped."
echo ""

# Step 2: Set maintenance_work_mem for the index build.
# 4GB is sized for up to ~10M 256-dim vectors per the Terraform module's
# db-custom-16-64000 instance type (64GB RAM). The SET is session-scoped
# and does NOT affect other connections.
echo "[2/3] Building HNSW index CONCURRENTLY (m=16, ef_construction=64)..."
echo "       This may take several hours for 10M+ vectors."
psql "${DSN}" -c "SET maintenance_work_mem = '4GB';" -c "
CREATE INDEX CONCURRENTLY memory_records_embedding_hnsw
    ON memory_records
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
"
echo "OK: HNSW index built."
echo ""

# Step 3: Verify the index exists and is valid.
echo "[3/3] Verifying index..."
psql "${DSN}" -c "
SELECT indexname, pg_size_pretty(pg_relation_size(indexname::regclass)) AS size
FROM pg_indexes
WHERE tablename = 'memory_records' AND indexname = 'memory_records_embedding_hnsw';
"

echo ""
echo "=== Done. HNSW index rebuild complete. ==="
