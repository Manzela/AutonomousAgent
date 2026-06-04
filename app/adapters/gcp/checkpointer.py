"""PostgresCheckpointer — Cloud SQL Postgres checkpointer provider (SP-23)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.core.checkpointer import AbstractCheckpointer, DurabilityMode
from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)


class PostgresCheckpointer(AbstractCheckpointer):
    """Postgres-backed durable checkpointer.

    Uses AsyncPostgresSaver to persist LangGraph checkpoints to Postgres.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Optional[Any] = None
        self._saver: Optional[Any] = None

    @property
    def durability_mode(self) -> DurabilityMode:
        return "sync"

    def build_saver(self) -> BaseCheckpointSaver:
        if self._saver is None:
            raise RuntimeError(
                "PostgresCheckpointer.build_saver() called before setup(); "
                "call `await provider.setup()` first"
            )
        return self._saver

    async def setup(self) -> None:
        # Lazy imports to avoid hard dependencies when not running with gcp extra
        try:
            from psycopg_pool import AsyncConnectionPool
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:
            raise ImportError(
                "langgraph-checkpoint-postgres and psycopg_pool are required for PostgresCheckpointer. "
                "Install them or set SPINE_CHECKPOINTER=sqlite/inmemory."
            ) from exc

        # Guard against double-setup
        if self._pool is not None:
            await self.aclose()

        self._pool = AsyncConnectionPool(conninfo=self._dsn, max_size=10, open=False)
        await self._pool.open()

        # Verify connection immediately by executing a dummy query
        async with self._pool.connection() as conn:
            await conn.execute("SELECT 1")

        self._saver = AsyncPostgresSaver(self._pool)
        await self._saver.setup()

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._saver = None
