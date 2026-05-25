#!/usr/bin/env python3
"""Idempotent DDL migration for the hermes Cloud SQL database.

Run once per environment after ``terraform apply`` brings the Cloud SQL
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

Design spec: docs/superpowers/specs/2026-05-25-cloud-sql-pgvector-store-design.md §5
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
    ap = argparse.ArgumentParser(description="Apply hermes Cloud SQL DDL migrations idempotently.")
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
