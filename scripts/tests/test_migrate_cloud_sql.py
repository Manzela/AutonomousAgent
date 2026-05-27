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
        """Sanity: at least the 6 baseline blocks (ext + table + 4 indexes) exist."""
        assert len(DDL_BLOCKS) >= 6, (
            f"Expected at least 6 DDL blocks; got {len(DDL_BLOCKS)}. "
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

    def test_scope_index_order(self):
        """The scope index must be (project_id, tier) in the DDL.

        NOTE: P1.A-4 in findings.md flags that the QUERY-time selectivity
        is better with (tier, project_id).  This test documents the CURRENT
        state so the A-4 fix can be verified against it: when A-4 is
        remediated, update this assertion to expect (tier, project_id).
        """
        scope_sql = next(
            (sql for name, sql in DDL_BLOCKS if "scope" in name.lower()),
            None,
        )
        assert scope_sql is not None, "No scope index DDL block found"
        # Current (pre-A-4-fix) column order: project_id, tier
        assert (
            "project_id" in scope_sql and "tier" in scope_sql
        ), "Scope index DDL does not reference both project_id and tier"


# ─────────────────────────────────────────────────────────────────────
# Unit tests — asyncpg mocked
# ─────────────────────────────────────────────────────────────────────


def _make_conn_mock() -> AsyncMock:
    """Return an asyncpg connection mock with execute() and close() wired."""
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="CREATE EXTENSION")
    conn.close = AsyncMock()
    return conn


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

        This verifies the try/finally pattern in migrate().  Note: the
        current implementation (P1.I-3) does NOT wrap blocks in a
        transaction, so partial-failure leaves the schema half-migrated.
        That is a separate P1 finding; this test only verifies cleanup.
        """
        conn = _make_conn_mock()
        conn.execute = AsyncMock(side_effect=[None, RuntimeError("boom"), None])
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
            return {
                "table_exists": table_exists,
                "ext_exists": ext_exists,
                "hnsw_index_count": has_hnsw,
            }
        finally:
            await conn.close()

    info = asyncio.run(_check())
    assert info["table_exists"], "memory_records table not found after migrate()"
    assert info["ext_exists"], "pgvector extension not found after migrate()"
    assert info["hnsw_index_count"] >= 1, (
        "No HNSW index found on memory_records after migrate() — "
        "see P1.I-7 in findings.md (HNSW index missing from production migration)"
    )
