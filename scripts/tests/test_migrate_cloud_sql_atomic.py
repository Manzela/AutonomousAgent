"""Atomicity tests for scripts/migrate_cloud_sql.py (I-3 companion).

Verifies that the transaction wrapper introduced by I-3 (findings.md P1.D I-3)
ensures partial migration failures roll back completely, leaving the schema in a
clean state rather than half-migrated.

Audit-plan W1.D gate command:
    pytest scripts/tests/test_migrate_cloud_sql_atomic.py::test_partial_failure_rolls_back -x
Expected: Pass
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_SCRIPTS_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR.parent))

try:
    import asyncpg  # noqa: F401 — probe only
except ImportError:
    _stub = MagicMock()
    sys.modules.setdefault("asyncpg", _stub)

from scripts.migrate_cloud_sql import DDL_BLOCKS, migrate  # noqa: E402


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _make_conn(*, fail_on_block: int = -1) -> AsyncMock:
    """Build a connection mock that raises RuntimeError on the N-th execute().

    Args:
        fail_on_block: 0-based index of the DDL block whose execute() should
            raise.  -1 (default) means all succeed.
    """
    conn = AsyncMock()
    if fail_on_block < 0:
        conn.execute = AsyncMock(return_value="OK")
    else:
        # Build a realistic side_effect list: OK for blocks before fail_on_block,
        # then RuntimeError, then subsequent calls never reached.
        call_side_effects = []
        for i in range(len(DDL_BLOCKS)):
            if i < fail_on_block:
                call_side_effects.append(None)  # success (return None ~ "OK")
            elif i == fail_on_block:
                call_side_effects.append(RuntimeError(f"DDL block {i} failed"))
            else:
                call_side_effects.append(None)  # unreachable in current impl
        conn.execute = AsyncMock(side_effect=call_side_effects)

    conn.close = AsyncMock()

    # Transaction mock: __aenter__ begins, __aexit__ propagates exceptions
    # (return_value=False == "do not suppress").  A real asyncpg Transaction
    # would also issue ROLLBACK on failure; here we just verify the interface
    # is used correctly.
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=mock_tx)
    return conn


# ─────────────────────────────────────────────────────────────────────
# Atomicity / rollback contract
# ─────────────────────────────────────────────────────────────────────


class TestMigrateAtomicity:
    """Verifies the I-3 fix: all-or-nothing DDL via conn.transaction()."""

    def test_partial_failure_rolls_back(self):
        """A failure mid-migration must propagate out of migrate() so the
        caller (or the transaction itself) can observe and roll back.

        Verification protocol:
            1. Patch asyncpg.connect to return a failing mock.
            2. Run migrate() — expect RuntimeError to propagate.
            3. Confirm conn.transaction().__aexit__ was called with exc_info
               (meaning the context manager saw the exception and had the chance
               to issue ROLLBACK before re-raising).
            4. Confirm conn.close() still ran (try/finally).
        """
        fail_idx = 2  # fail on the 3rd block (index_scope)
        conn = _make_conn(fail_on_block=fail_idx)

        with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
            with pytest.raises(RuntimeError, match=f"DDL block {fail_idx} failed"):
                asyncio.run(migrate("postgresql://mock/db"))

        # The transaction context manager must have been used.
        conn.transaction.assert_called_once()

        # __aexit__ must have been called with the exception info (3-tuple with
        # RuntimeError as exc_type), signalling the opportunity for ROLLBACK.
        tx = conn.transaction.return_value
        tx.__aexit__.assert_awaited_once()
        aexit_call = tx.__aexit__.await_args
        assert aexit_call is not None, "__aexit__ was not awaited"
        exc_type, exc_val, exc_tb = aexit_call.args
        assert (
            exc_type is RuntimeError
        ), f"__aexit__ called with exc_type={exc_type!r}; expected RuntimeError"
        assert isinstance(
            exc_val, RuntimeError
        ), f"__aexit__ exc_val is not a RuntimeError: {exc_val!r}"

        # Connection must be closed in all cases (try/finally guarantee).
        conn.close.assert_awaited_once()

    def test_success_commits(self):
        """On success, __aexit__(None, None, None) is called — meaning commit."""
        conn = _make_conn(fail_on_block=-1)

        with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
            n = asyncio.run(migrate("postgresql://mock/db"))

        assert n == len(DDL_BLOCKS)
        tx = conn.transaction.return_value
        tx.__aenter__.assert_awaited_once()
        tx.__aexit__.assert_awaited_once()
        aexit_call = tx.__aexit__.await_args
        exc_type, exc_val, exc_tb = aexit_call.args
        assert exc_type is None, (
            f"Successful migrate() must call __aexit__(None, None, None) to commit; "
            f"got exc_type={exc_type!r}"
        )

    @pytest.mark.parametrize("fail_idx", [0, 1, len(DDL_BLOCKS) - 1])
    def test_rollback_at_any_block_index(self, fail_idx: int):
        """The rollback guarantee holds regardless of which block fails."""
        conn = _make_conn(fail_on_block=fail_idx)

        with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
            with pytest.raises(RuntimeError):
                asyncio.run(migrate("postgresql://mock/db"))

        tx = conn.transaction.return_value
        aexit_call = tx.__aexit__.await_args
        assert aexit_call is not None
        exc_type, _, _ = aexit_call.args
        assert exc_type is RuntimeError, (
            f"Block {fail_idx}: __aexit__ must receive RuntimeError for rollback; "
            f"got {exc_type!r}"
        )
        conn.close.assert_awaited_once()

    def test_execute_stops_after_first_failure(self):
        """No further DDL must execute after the first failure (transaction aborted)."""
        fail_idx = 1  # second block
        conn = _make_conn(fail_on_block=fail_idx)

        with patch("asyncpg.connect", new=AsyncMock(return_value=conn)):
            with pytest.raises(RuntimeError):
                asyncio.run(migrate("postgresql://mock/db"))

        # Only blocks 0..fail_idx should have been attempted (fail_idx + 1 calls).
        assert conn.execute.call_count == fail_idx + 1, (
            f"Expected {fail_idx + 1} execute calls (blocks 0..{fail_idx}); "
            f"got {conn.execute.call_count}"
        )
