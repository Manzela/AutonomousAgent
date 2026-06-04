"""SqliteFileCheckpointer — file-based durability provider (SP-R3 vendor-lock fallback).

Wraps AsyncSqliteSaver (langgraph-checkpoint-sqlite) with an explicit aiosqlite
connection so the caller controls the connection lifecycle (setup/aclose). The
SQLite file is created at the given path; if the path is ":memory:" the saver is
in-process only (same semantics as InMemoryCheckpointer but via the SQLite codepath,
useful for schema contract tests).

SP-R3 contract: this adapter is kept + tested for ≥1 release as a vendor-lock
fallback. If the prod Postgres adapter fails, operators can fall back to this
file-based tier by pointing SPINE_CHECKPOINT_DB at a local SQLite path. The schema
contract test (test_spr3_file_checkpointer.py) proves the LangGraph checkpoint
schema is stable so a schema drift is caught before a forced failover.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.core.checkpointer import AbstractCheckpointer, DurabilityMode


class SqliteFileCheckpointer(AbstractCheckpointer):
    """SP-R3 vendor-lock fallback: SQLite-backed durable checkpointer.

    Usage:
        provider = SqliteFileCheckpointer(Path("/tmp/spine.db"))
        await provider.setup()
        app = build_spine(provider.build_saver(), ...)
        ...
        await provider.aclose()

    For in-process tests, pass ":memory:" as the db_path.
    """

    def __init__(self, db_path: Union[Path, str] = ":memory:") -> None:
        self._db_path = str(db_path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._saver: Optional[AsyncSqliteSaver] = None

    @property
    def durability_mode(self) -> DurabilityMode:
        return "sync"

    def build_saver(self) -> BaseCheckpointSaver:
        if self._saver is None:
            raise RuntimeError(
                "SqliteFileCheckpointer.build_saver() called before setup(); "
                "call `await provider.setup()` first"
            )
        return self._saver

    async def setup(self) -> None:
        # Guard against double-setup: close the prior connection before reopening.
        if self._conn is not None:
            await self.aclose()
        self._conn = await aiosqlite.connect(self._db_path)
        # NOTE: AsyncSqliteSaver pins the running event loop at construction time.
        # Call setup() and all subsequent aput/aget operations from the SAME event loop.
        # A process-restart / new asyncio.run() requires a fresh setup() call.
        self._saver = AsyncSqliteSaver(self._conn)
        await self._saver.setup()

    async def aclose(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            self._saver = None
