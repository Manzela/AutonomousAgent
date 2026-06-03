"""SP-R3 oracles — SqliteFileCheckpointer vendor-lock fallback contract.

Hermetic: no network/docker. Uses in-memory SQLite (':memory:') except where the
test specifically proves file-path durability (F4b, F7b) — those use tmp_path.

These oracles prove the SP-R3 vendor-lock fallback contract:

  F1  langgraph + sqlite fallback modules are importable (import-path independence)
  F2  setup() creates a valid AsyncSqliteSaver (setup contract)
  F3  build_saver() raises if called before setup() (guard-rails)
  F4  a checkpoint PUT then GET on the same connection round-trips correctly
  F4b file-path durability: a checkpoint survives provider teardown+reconstruct
      (the REAL SP-R3 claim — a new provider on the SAME file sees prior state)
  F5  SpineState schema is stable — all expected fields present in graph_state
  F6  aclose() tears down the connection (no resource leak on normal exit)
  F7  double setup() without aclose() cleans up the prior connection (no leak)
  F7b reconnect cycle: setup→aclose→setup works on a real file path

RED/GREEN design:
  F1: if langgraph-checkpoint-sqlite were removed, the fallback import fails — FAIL
  F4b: ':memory:' loses state across provider restart (B1 C9 gap); a file-path
       provider B reading what provider A wrote proves durable failover — FAIL
  F5: if a SpineState field is dropped/renamed, a resume after failover silently
      loses data — FAIL
  F7: double-setup without aclose leaked the first aiosqlite connection (B4 C9 gap);
      the guard in setup() must close the prior connection first — FAIL
"""

from __future__ import annotations

import pytest

from app.adapters.inmemory.file_checkpointer import SqliteFileCheckpointer


# ── F1: fallback modules are importable ──────────────────────────────────────


def test_fallback_modules_importable():
    """F1: langgraph + the SQLite checkpointer are importable as a standalone fallback."""
    import langgraph  # noqa: F401
    from langgraph.checkpoint.base import BaseCheckpointSaver  # noqa: F401
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: F401


# ── F2: setup() creates a valid saver ────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_creates_valid_saver():
    """F2: setup() produces an AsyncSqliteSaver that is a LangGraph BaseCheckpointSaver."""
    from langgraph.checkpoint.base import BaseCheckpointSaver

    provider = SqliteFileCheckpointer(":memory:")
    await provider.setup()
    saver = provider.build_saver()
    assert isinstance(saver, BaseCheckpointSaver)
    await provider.aclose()


# ── F3: build_saver() guards against pre-setup calls ─────────────────────────


def test_build_saver_raises_before_setup():
    """F3: build_saver() before setup() raises RuntimeError (not silent None)."""
    provider = SqliteFileCheckpointer(":memory:")
    with pytest.raises(RuntimeError, match="before setup"):
        provider.build_saver()


# ── F4: checkpoint round-trip on the same connection ─────────────────────────


@pytest.mark.asyncio
async def test_checkpoint_round_trip_same_connection():
    """F4: a value PUT via the saver can be GET'd back on the same connection."""
    from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata

    provider = SqliteFileCheckpointer(":memory:")
    await provider.setup()
    saver = provider.build_saver()

    config = {"configurable": {"thread_id": "spr3-f4", "checkpoint_ns": "", "checkpoint_id": "1"}}
    checkpoint: Checkpoint = {
        "v": 1,
        "id": "1",
        "ts": "2026-06-03T00:00:00+00:00",
        "channel_values": {"goal": "test-goal-spr3"},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }
    metadata: CheckpointMetadata = {"source": "input", "step": 0, "writes": {}, "parents": {}}

    saved_config = await saver.aput(config, checkpoint, metadata, {})
    assert saved_config is not None

    result = await saver.aget_tuple(config)
    assert result is not None
    assert result.checkpoint["channel_values"]["goal"] == "test-goal-spr3"

    await provider.aclose()


# ── F4b: file-path durability — survives provider teardown+reconstruct ────────


@pytest.mark.asyncio
async def test_file_checkpoint_survives_provider_restart(tmp_path):
    """F4b (B1 C9 gap): the REAL SP-R3 durable-failover claim. A checkpoint written
    by provider A on a real file path is readable by a BRAND-NEW provider B on the
    same file — proves the SQLite backend persists across connection teardown.

    Note: ':memory:' databases lose all state on aclose() — only a real file path
    delivers the failover guarantee this adapter exists for."""
    from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata

    db_path = tmp_path / "spine.db"
    config = {"configurable": {"thread_id": "spr3-f4b", "checkpoint_ns": "", "checkpoint_id": "1"}}
    checkpoint: Checkpoint = {
        "v": 1,
        "id": "1",
        "ts": "2026-06-03T00:00:00+00:00",
        "channel_values": {"goal": "failover-goal"},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }
    metadata: CheckpointMetadata = {"source": "input", "step": 0, "writes": {}, "parents": {}}

    # Provider A: write then close (simulate the original process dying)
    provider_a = SqliteFileCheckpointer(db_path)
    await provider_a.setup()
    await provider_a.build_saver().aput(config, checkpoint, metadata, {})
    await provider_a.aclose()

    # Provider B: fresh object on the same file (simulate the failover process)
    provider_b = SqliteFileCheckpointer(db_path)
    await provider_b.setup()
    result = await provider_b.build_saver().aget_tuple(config)
    await provider_b.aclose()

    assert result is not None, "failover provider B must see checkpoint written by provider A"
    assert result.checkpoint["channel_values"]["goal"] == "failover-goal"


# ── F5: SpineState schema stability contract ──────────────────────────────────


def test_spine_state_schema_stable():
    """F5: all expected SpineState fields exist — catches schema drift before a
    forced Postgres→SQLite failover reveals a serialization mismatch at runtime.

    SCHEMA CONTRACT (as of 2026-06-03): update this set when the schema is
    intentionally changed, then note the schema version in a comment."""
    import typing

    from app.core.graph_state import SpineState

    EXPECTED_FIELDS = {
        "audit",
        "base_ref",
        "board_cards",
        "budget_verdict",
        "changed_paths",
        "clarifications",
        "cost_accumulator",
        "decision_record",
        "eval_verdict",
        "execution_counts",
        "fix_attempts",
        "goal",
        "ledger",
        "plan",
        "pre_decompose_checkpoint_id",
        "replan_parent",
        "scrubbed",
        "ship",
        "sign_off",
        "spec_draft",
        "spec_id",
        "spec_sha",
        "steer_commands",
        "steering_events",
        "symlink_paths",
        "tasks",
        "thread_id",
        "triage_card_id",
        "workspace_ref",
        "workspace_refs",
    }

    actual_fields = set(typing.get_type_hints(SpineState).keys())
    missing = EXPECTED_FIELDS - actual_fields
    added = actual_fields - EXPECTED_FIELDS

    assert not missing, (
        f"SpineState is missing expected fields (SP-R3 schema drift): {sorted(missing)}. "
        "Update EXPECTED_FIELDS if the schema change is intentional."
    )
    assert not added, (
        f"SpineState has new fields not in the contract (SP-R3 schema drift): {sorted(added)}. "
        "Add them to EXPECTED_FIELDS after reviewing the checkpoint serialization impact."
    )


# ── F6: aclose() tears down the connection ───────────────────────────────────


@pytest.mark.asyncio
async def test_aclose_releases_connection():
    """F6: aclose() closes the aiosqlite connection; subsequent build_saver() raises."""
    provider = SqliteFileCheckpointer(":memory:")
    await provider.setup()
    assert provider.build_saver() is not None

    await provider.aclose()

    with pytest.raises(RuntimeError, match="before setup"):
        provider.build_saver()


# ── F7: double setup() without aclose() cleans up prior connection ────────────


@pytest.mark.asyncio
async def test_double_setup_does_not_leak_connection():
    """F7 (B4 C9 gap): calling setup() twice without aclose() in between must close
    the FIRST aiosqlite connection before opening a second. A leaked connection keeps
    the SQLite file locked on disk (POSIX advisory lock), preventing other processes
    from opening it for the failover.

    Verifies that the second setup() yields a different connection object from the
    first (i.e. it actually reconnected) and that the prior object is closed."""
    import aiosqlite

    provider = SqliteFileCheckpointer(":memory:")
    await provider.setup()
    conn_1 = provider._conn
    assert isinstance(conn_1, aiosqlite.Connection)

    # Second setup() without aclose() — must NOT leave conn_1 open
    await provider.setup()
    conn_2 = provider._conn

    assert conn_2 is not conn_1, "second setup() must create a new connection"
    # conn_1 should be closed — in aiosqlite, _connection is None when closed
    assert conn_1._connection is None, "first connection must be closed by second setup()"

    await provider.aclose()


# ── F7b: reconnect cycle on a real file path ─────────────────────────────────


@pytest.mark.asyncio
async def test_reconnect_after_aclose_file_path(tmp_path):
    """F7b: setup→aclose→setup cycle on a real file path creates a fresh connection."""
    from langgraph.checkpoint.base import BaseCheckpointSaver

    db_path = tmp_path / "reconnect.db"
    provider = SqliteFileCheckpointer(db_path)

    await provider.setup()
    await provider.aclose()

    await provider.setup()
    saver = provider.build_saver()
    assert isinstance(saver, BaseCheckpointSaver)
    await provider.aclose()
