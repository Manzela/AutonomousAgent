"""Tests for scripts/migrate_cloud_sql.py (P2-20).

Coverage:
  Static  — every DDL block uses IF NOT EXISTS semantics (no implicit-DDL path).
  Unit    — asyncpg is mocked; migrate() applies every block and closes the
            connection even on partial failure.
  Idempotency (integration) — run migrate() twice against a real postgres;
            verify no errors and the final schema matches expectations.
            Requires CLOUD_SQL_DSN or --dsn env var; auto-skipped otherwise.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Resolve the script under test regardless of cwd.
_SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR.parent))

# Import the migration module under a temporary asyncpg stub when the real
# asyncpg package is not installed (e.g. the bare CI Python without extras).
# Unit tests mock asyncpg.connect at the call site anyway; the stub is only
# needed to get past the module-level ``import asyncpg`` in migrate_cloud_sql.
try:
    import asyncpg  # noqa: F401 — probe only

    _ASYNCPG_AVAILABLE = True
except ImportError:
    _ASYNCPG_AVAILABLE = False
    # Inject a minimal stub so the script imports cleanly.
    _stub = MagicMock()
    sys.modules.setdefault("asyncpg", _stub)

from scripts.migrate_cloud_sql import DDL_BLOCKS, migrate  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Mock factory — returns a fully-wired asyncpg connection + transaction mock
# ─────────────────────────────────────────────────────────────────────


def _make_conn_mock() -> AsyncMock:
    """Return an asyncpg connection mock with execute(), close(), and transaction() wired.

    transaction() returns an async context manager mock that commits on clean
    exit and propagates exceptions on error exit (return_value=False from
    __aexit__ means 'do not suppress the exception').
    """
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="CREATE EXTENSION")
    conn.close = AsyncMock()
    # Wire the transaction() context manager (I-3 fix: migrate() now uses
    # async with conn.transaction(): for atomic DDL application).
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=False)  # False = don't suppress exceptions
    conn.transaction = MagicMock(return_value=mock_tx)
    return conn


# ─────────────────────────────────────────────────────────────────────
# Static checks — verifiable without a database connection
# ─────────────────────────────────────────────────────────────────────


class TestDDLStaticGuarantees:
    """Every DDL block must be idempotent by construction (IF NOT EXISTS)."""

    _IDEMPOTENT_RE = re.compile(
        r"\bIF\s+NOT\s+EXISTS\b",
        re.IGNORECASE,
    )

    def test_all_blocks_have_names(self):
        """Every block must have a non-empty string name for diagnostics."""
        for name, _sql in DDL_BLOCKS:
            assert isinstance(name, str) and name.strip(), f"Empty name in DDL_BLOCKS: {name!r}"

    def test_all_blocks_use_if_not_exists(self):
        """Every CREATE / EXTENSION block must carry IF NOT EXISTS.

        This is the static proof of the idempotency claim in the script
        docstring: re-running migrate() must not raise on a populated schema.
        """
        skip_patterns = (
            # SET LOCAL / pure expression statements are inherently idempotent.
            r"^\s*SET\b",
            r"^\s*SELECT\b",
            r"^\s*DO\b",
        )
        skip_re = re.compile("|".join(skip_patterns), re.IGNORECASE)

        for name, sql in DDL_BLOCKS:
            if skip_re.match(sql.strip()):
                continue
            assert self._IDEMPOTENT_RE.search(
                sql
            ), f"DDL block '{name}' does not use IF NOT EXISTS:\n{sql}"

    def test_block_count_is_stable(self):
        """Sanity: at least the 7 baseline blocks (ext + table + 5 indexes) exist.

        5 indexes: scope, metadata_gin, gc, content_hash, embedding_hnsw.
        (Was 4 before I-7 fix added the HNSW index.)
        """
        assert len(DDL_BLOCKS) >= 7, (
            f"Expected at least 7 DDL blocks; got {len(DDL_BLOCKS)}. "
            "Did a block get accidentally removed?"
        )

    def test_extension_block_is_first(self):
        """The pgvector extension must be created before the table that uses it."""
        first_name, first_sql = DDL_BLOCKS[0]
        assert "vector" in first_sql.lower(), (
            f"First DDL block should be the pgvector extension; "
            f"got '{first_name}': {first_sql[:80]!r}"
        )

    def test_memory_records_table_exists(self):
        """The memory_records table definition must be present."""
        names = [name for name, _ in DDL_BLOCKS]
        assert any(
            "memory_records" in n for n in names
        ), f"No DDL block for memory_records found; blocks: {names}"

    def test_scope_index_column_order(self):
        """Scope index must lead with `tier` (A-4 fix).

        The dominant query in app/adapters/gcp/memory.py:237-244 filters
        WHERE tier = $2 AND project_id = ANY($3::text[]).  Putting the
        equality predicate (tier) first maximises index selectivity.

        Previous (pre-A-4-fix) order was (project_id, tier) — now corrected.
        """
        scope_sql = next(
            (sql for name, sql in DDL_BLOCKS if "scope" in name.lower()),
            None,
        )
        assert scope_sql is not None, "No scope index DDL block found"
        # Extract the column list from the ON clause: ON memory_records (tier, project_id)
        match = re.search(
            r"ON\s+memory_records\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)",
            scope_sql,
            re.IGNORECASE,
        )
        assert (
            match is not None
        ), f"Could not parse column list from scope index DDL: {scope_sql[:120]!r}"
        first_col, second_col = match.group(1).lower(), match.group(2).lower()
        assert first_col == "tier", (
            f"Scope index leading column must be 'tier' (A-4 fix); got '{first_col}'. "
            f"Full DDL: {scope_sql.strip()}"
        )
        assert (
            second_col == "project_id"
        ), f"Scope index second column must be 'project_id'; got '{second_col}'."

    def test_hnsw_index_block_exists(self):
        """An HNSW index on the embedding column must be present (I-7 fix).

        Without the HNSW index, app/adapters/gcp/memory.py:225
        `SET LOCAL hnsw.ef_search` is a silent no-op and every similarity
        query degrades to a sequential scan.
        """
        hnsw_entries = [(name, sql) for name, sql in DDL_BLOCKS if "hnsw" in sql.lower()]
        assert hnsw_entries, (
            "No HNSW index DDL block found in DDL_BLOCKS. "
            "See I-7 in audit/2026-05-27-ground-truth/findings.md."
        )
        # The HNSW index must target the embedding column with cosine ops.
        _, hnsw_sql = hnsw_entries[0]
        assert (
            "embedding" in hnsw_sql.lower()
        ), f"HNSW index must target the 'embedding' column: {hnsw_sql[:120]!r}"
        assert (
            "vector_cosine_ops" in hnsw_sql.lower()
        ), f"HNSW index must use vector_cosine_ops operator class: {hnsw_sql[:120]!r}"


# ─────────────────────────────────────────────────────────────────────
# Unit tests — asyncpg mocked
# ─────────────────────────────────────────────────────────────────────


class TestMigrateUnit:
    """Unit tests against a fully-mocked asyncpg connection."""

    def test_all_blocks_applied(self):
        """migrate() must call conn.execute() for every block in DDL_BLOCKS."""
        conn = _make_conn_mock()
        with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
            n = asyncio.run(migrate("postgresql://mock/db"))

        assert n == len(DDL_BLOCKS), f"Expected {len(DDL_BLOCKS)} blocks applied; got {n}"
        assert conn.execute.call_count == len(DDL_BLOCKS), (
            f"conn.execute called {conn.execute.call_count} times; " f"expected {len(DDL_BLOCKS)}"
        )

    def test_execute_called_with_each_sql(self):
        """Each SQL string from DDL_BLOCKS must reach conn.execute verbatim."""
        conn = _make_conn_mock()
        with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
            asyncio.run(migrate("postgresql://mock/db"))

        executed = [c.args[0] for c in conn.execute.call_args_list]
        for _, sql in DDL_BLOCKS:
            assert sql in executed, f"DDL block SQL not found in execute calls: {sql[:60]!r}"

    def test_connection_closed_on_success(self):
        """conn.close() must be called even when all blocks succeed."""
        conn = _make_conn_mock()
        with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
            asyncio.run(migrate("postgresql://mock/db"))

        conn.close.assert_awaited_once()

    def test_connection_closed_on_partial_failure(self):
        """conn.close() must be called even when a block raises mid-migration.

        I-3 fix: migrate() now wraps all blocks in conn.transaction(), so a
        partial failure triggers a rollback (via __aexit__) before re-raising.
        The try/finally around the transaction block ensures conn.close() runs
        regardless.  See test_migrate_cloud_sql_atomic.py for the rollback
        guarantee test.
        """
        conn = _make_conn_mock()
        conn.execute = AsyncMock(side_effect=[None, RuntimeError("boom"), None])
        # Make __aexit__ propagate the exception (return_value=False means
        # "do not suppress"; asyncpg's real Transaction does the same).
        conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
            with pytest.raises(RuntimeError, match="boom"):
                asyncio.run(migrate("postgresql://mock/db"))

        conn.close.assert_awaited_once()

    def test_returns_count_of_applied_blocks(self):
        """migrate() returns the number of successfully applied blocks."""
        conn = _make_conn_mock()
        with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
            n = asyncio.run(migrate("postgresql://mock/db"))

        assert isinstance(n, int)
        assert n == len(DDL_BLOCKS)

    def test_connect_called_with_dsn(self):
        """asyncpg.connect must be called with exactly the DSN provided."""
        dsn = "postgresql://user:pass@localhost:5432/hermes"  # pragma: allowlist secret
        conn = _make_conn_mock()
        with patch("asyncpg.connect", new=AsyncMock(return_value=conn)) as mock_connect:
            asyncio.run(migrate(dsn))

        mock_connect.assert_awaited_once_with(dsn)

    def test_transaction_context_manager_entered(self):
        """migrate() must use conn.transaction() as an async context manager (I-3)."""
        conn = _make_conn_mock()
        with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
            asyncio.run(migrate("postgresql://mock/db"))

        conn.transaction.assert_called_once()
        conn.transaction.return_value.__aenter__.assert_awaited_once()
        conn.transaction.return_value.__aexit__.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────
# Integration tests — real postgres (skipped when DSN not available)
# ─────────────────────────────────────────────────────────────────────

_DSN = os.environ.get("CLOUD_SQL_DSN", "")

_integration = pytest.mark.skipif(
    not _DSN,
    reason="CLOUD_SQL_DSN not set — skipping real-DB integration tests",
)


@_integration
@pytest.mark.integration
def test_migrate_idempotent_real_db():
    """Running migrate() twice against a real postgres must not raise.

    This is the primary idempotency proof: every block uses IF NOT EXISTS,
    so a second run is a no-op at the DDL level.
    """
    n1 = asyncio.run(migrate(_DSN))
    assert n1 == len(DDL_BLOCKS), f"First run applied {n1} of {len(DDL_BLOCKS)} blocks"

    n2 = asyncio.run(migrate(_DSN))
    assert n2 == len(DDL_BLOCKS), f"Second run applied {n2} of {len(DDL_BLOCKS)} blocks"


@_integration
@pytest.mark.integration
def test_migrate_schema_exists_after_run():
    """After migrate(), the memory_records table and pgvector extension must exist."""
    import asyncpg

    asyncio.run(migrate(_DSN))

    async def _check() -> dict:
        conn = await asyncpg.connect(_DSN)
        try:
            table_exists = await conn.fetchval(
                "SELECT to_regclass('public.memory_records') IS NOT NULL"
            )
            ext_exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')"
            )
            has_hnsw = await conn.fetchval(
                "SELECT COUNT(*) FROM pg_indexes "
                "WHERE tablename = 'memory_records' AND indexdef LIKE '%hnsw%'"
            )
            # A-4 fix verification: scope index must lead with tier.
            scope_index_sql = await conn.fetchval(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'memory_records' "
                "AND indexname = 'memory_records_scope_idx'"
            )
            return {
                "table_exists": table_exists,
                "ext_exists": ext_exists,
                "hnsw_index_count": has_hnsw,
                "scope_index_sql": scope_index_sql,
            }
        finally:
            await conn.close()

    info = asyncio.run(_check())
    assert info["table_exists"], "memory_records table not found after migrate()"
    assert info["ext_exists"], "pgvector extension not found after migrate()"
    assert info["hnsw_index_count"] >= 1, (
        "No HNSW index found on memory_records after migrate() — "
        "see I-7 in audit/2026-05-27-ground-truth/findings.md"
    )
    # Verify A-4 fix: scope index must lead with `tier`.
    scope_sql = info.get("scope_index_sql", "") or ""
    assert (
        "tier" in scope_sql.lower()
    ), f"Scope index does not reference tier column (A-4 fix): {scope_sql!r}"
    # The leading column in the pg_indexes indexdef expression appears first in
    # the parenthesized list; 'tier' must precede 'project_id'.
    tier_pos = scope_sql.lower().find("tier")
    project_pos = scope_sql.lower().find("project_id")
    assert tier_pos < project_pos, (
        f"Scope index has wrong column order — 'tier' must come before 'project_id'. "
        f"Got: {scope_sql!r}"
    )
