"""Hermetic unit tests for the FastAPI spine service (app/main.py).

Tests the Pattern-B fix: SpineRunner assembled into a running HTTP service.
All tests use InMemoryCheckpointer — no Docker, no network, no real LLM.

Oracle map:
  test_healthz_ok               — liveness probe returns 200
  test_healthz_halted           — liveness probe returns 503 when kill-switch active
  test_start_goal_returns_interrupt — POST /goal returns sign_off interrupt (also proves
                                      _serialise_result converts LG Interrupt objects)
  test_start_goal_kill_switch_blocks — POST /goal returns 503 when halted
  test_start_goal_missing_fields — POST /goal 422 on validation error
  test_resume_approve_reaches_ship_gate — POST /resume APPROVE → ship_gate interrupt
  test_resume_reject_halts      — POST /resume REJECT → spine halts
  test_get_state                — GET /state/{tid} returns thread state
  test_panic_triggers_halt      — POST /panic triggers kill-switch
  test_rollback_503_when_unconfigured — POST /rollback 503 when no rollback adapter
  test_telegram_webhook_503_when_unconfigured — POST /webhook/telegram 503 when unconfigured
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import _state, app, lifespan


@pytest.fixture(autouse=True)
async def _fresh_app(tmp_path, monkeypatch):
    """Start the lifespan for each test, ensuring a fresh SpineRunner."""
    sentinel = tmp_path / "kill-switch"
    monkeypatch.setenv("SPINE_DECISION_RECORD_PATH", str(tmp_path / "dr.jsonl"))
    monkeypatch.setenv("AA_KILL_SWITCH_PATH", str(sentinel))
    async with lifespan(app):
        yield
    # Clean up: clear kill-switch sentinel if it was triggered during the test
    if _state.kill_switch is not None:
        _state.kill_switch.clear()
    # Reset state after each test
    _state.runner = None
    _state.kill_switch = None


@pytest.fixture()
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Healthz ──────────────────────────────────────────────────────────────────


async def test_healthz_ok(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_healthz_halted(client):
    _state.kill_switch.trigger("test")
    resp = await client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["status"] == "halted"


# ── POST /goal ───────────────────────────────────────────────────────────────


async def test_start_goal_returns_interrupt(client):
    resp = await client.post(
        "/goal",
        json={"thread_id": "t1", "goal": "build a hello world endpoint"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "__interrupt__" in body
    assert body["__interrupt__"][0]["value"]["gate"] == "sign_off"


async def test_start_goal_kill_switch_blocks(client):
    _state.kill_switch.trigger("test")
    resp = await client.post(
        "/goal",
        json={"thread_id": "t-blocked", "goal": "should be rejected"},
    )
    assert resp.status_code == 503
    assert "kill-switch" in resp.json()["detail"]


async def test_start_goal_missing_fields(client):
    resp = await client.post("/goal", json={"thread_id": "t-bad"})
    assert resp.status_code == 422  # validation error


# ── POST /resume ─────────────────────────────────────────────────────────────


async def test_resume_approve_reaches_ship_gate(client):
    # Start
    r1 = await client.post(
        "/goal",
        json={"thread_id": "t-resume", "goal": "build X"},
    )
    assert r1.status_code == 200
    iid = r1.json()["__interrupt__"][0]["id"]

    # Resume with APPROVE
    r2 = await client.post(
        "/resume",
        json={
            "thread_id": "t-resume",
            "interrupt_id": iid,
            "decision": {"verb": "APPROVE", "actor": "op", "reason": "lgtm"},
        },
    )
    assert r2.status_code == 200
    body = r2.json()
    assert "__interrupt__" in body
    assert body["__interrupt__"][0]["value"]["gate"] == "ship"


async def test_resume_reject_halts(client):
    # Start
    r1 = await client.post(
        "/goal",
        json={"thread_id": "t-reject", "goal": "build Y"},
    )
    iid = r1.json()["__interrupt__"][0]["id"]

    # Resume with REJECT
    r2 = await client.post(
        "/resume",
        json={
            "thread_id": "t-reject",
            "interrupt_id": iid,
            "decision": {"verb": "REJECT", "actor": "op", "reason": "scope too broad"},
        },
    )
    assert r2.status_code == 200
    assert "__interrupt__" not in r2.json()


# ── GET /state ───────────────────────────────────────────────────────────────


async def test_get_state(client):
    # Start a run to create state
    await client.post(
        "/goal",
        json={"thread_id": "t-state", "goal": "build Z"},
    )
    resp = await client.get("/state/t-state")
    assert resp.status_code == 200
    body = resp.json()
    assert body["thread_id"] == "t-state"
    assert "next" in body
    assert "sign_off" in body["next"]  # interrupted at sign_off


# ── POST /panic ──────────────────────────────────────────────────────────────


async def test_panic_triggers_halt(client):
    resp = await client.post(
        "/panic",
        json={"reason": "emergency shutdown"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "halted"
    assert "elapsed_s" in body
    assert _state.kill_switch.is_active()


# ── POST /rollback ───────────────────────────────────────────────────────────


async def test_rollback_503_when_unconfigured(client):
    resp = await client.post(
        "/rollback",
        json={"service": "my-svc", "revision": "rev-1"},
    )
    assert resp.status_code == 503
    assert "RevisionRollback not configured" in resp.json()["detail"]


# ── POST /webhook/telegram ───────────────────────────────────────────────────


async def test_telegram_webhook_503_when_unconfigured(client):
    resp = await client.post(
        "/webhook/telegram",
        json={"update_id": 123, "message": {"text": "/start"}},
    )
    assert resp.status_code == 503
    assert "TelegramAdapter not configured" in resp.json()["detail"]


async def test_main_wires_cloud_sql_pgvector_store(monkeypatch):
    monkeypatch.setenv("CLOUD_SQL_DSN", "postgresql://localhost:5432/dummy_db")
    # Reset state to ensure fresh initialization
    _state.runner = None
    _state.kill_switch = None
    _state.memory_store = None

    async with lifespan(app):
        assert _state.memory_store is not None
        from app.adapters.gcp.memory import CloudSqlPgvectorStore

        assert isinstance(_state.memory_store, CloudSqlPgvectorStore)


async def test_main_checkpointer_postgres_fallback(monkeypatch, tmp_path):
    """SP-R3: checkpointer fallback at runtime from Postgres to SQLite on failure."""
    monkeypatch.setenv("SPINE_CHECKPOINTER", "postgres")
    monkeypatch.setenv("SPINE_CHECKPOINT_DB", str(tmp_path / "sqlite_fallback.db"))

    from app.adapters.gcp.checkpointer import PostgresCheckpointer

    async def mock_setup(self):
        raise RuntimeError("Postgres connection failed!")

    monkeypatch.setattr(PostgresCheckpointer, "setup", mock_setup)

    _state.runner = None
    _state.kill_switch = None
    _state.memory_store = None

    async with lifespan(app):
        assert _state.runner is not None
        from app.adapters.inmemory.file_checkpointer import SqliteFileCheckpointer

        assert isinstance(_state.runner._provider, SqliteFileCheckpointer)


# ── F-1 & F-3 gap fixes: sandbox production defaults & spec drafter routing ──
def test_build_sandbox_production_default(monkeypatch):
    """Verify that _build_sandbox defaults to cloudrun in production when unset."""
    monkeypatch.setenv("SPINE_ENVIRONMENT", "production")
    monkeypatch.delenv("SPINE_SANDBOX", raising=False)

    from app.main import _build_sandbox
    from app.adapters.gcp.cloud_run_sandbox import CloudRunJobSandbox

    sb = _build_sandbox()
    assert isinstance(sb, CloudRunJobSandbox)
    assert sb._project_id == "autonomous-agent-2026"  # new project ID default


def test_build_spec_drafter_routing(monkeypatch):
    """Verify that _build_spec_drafter builds the correct spec drafter concretion."""
    from app.main import _build_spec_drafter
    from app.adapters.inmemory.spec_drafter import InMemorySpecDrafter

    # 1. Unset env var -> None (falls back to InMemorySpecDrafter in build_spine)
    monkeypatch.delenv("SPINE_DRAFTER", raising=False)
    assert _build_spec_drafter() is None

    # 2. "inmemory" -> InMemorySpecDrafter
    monkeypatch.setenv("SPINE_DRAFTER", "inmemory")
    assert isinstance(_build_spec_drafter(), InMemorySpecDrafter)

    # 3. "vertex" -> VertexSpecDrafter
    monkeypatch.setenv("SPINE_DRAFTER", "vertex")
    from app.adapters.gcp.spec_drafter import VertexSpecDrafter

    assert isinstance(_build_spec_drafter(), VertexSpecDrafter)


def test_build_sandbox_collect_logs(monkeypatch):
    """Verify that CloudRunJobSandbox is built with collect_logs=True in production."""
    from app.main import _build_sandbox
    from app.adapters.gcp.cloud_run_sandbox import CloudRunJobSandbox

    # Dev/development environment
    monkeypatch.setenv("SPINE_ENVIRONMENT", "development")
    monkeypatch.setenv("SPINE_SANDBOX", "cloudrun")
    sb_dev = _build_sandbox()
    assert isinstance(sb_dev, CloudRunJobSandbox)
    assert sb_dev._collect_logs is False

    # Production environment
    monkeypatch.setenv("SPINE_ENVIRONMENT", "production")
    monkeypatch.setenv("SPINE_SANDBOX", "cloudrun")
    sb_prod = _build_sandbox()
    assert isinstance(sb_prod, CloudRunJobSandbox)
    assert sb_prod._collect_logs is True


async def test_lifespan_startup_recovery(monkeypatch):
    """Verify that active crashed threads are recovered during lifespan startup."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.main import lifespan, app
    from langgraph.checkpoint.base import CheckpointTuple

    # Mock checkpointer, saver
    mock_checkpointer = MagicMock()
    mock_saver = MagicMock()
    mock_checkpointer.build_saver.return_value = mock_saver
    mock_checkpointer.setup = AsyncMock()
    mock_checkpointer.aclose = AsyncMock()

    monkeypatch.setattr("app.main._build_checkpointer", lambda: mock_checkpointer)

    # Yield one active checkpoint tuple (state.next is non-empty, tasks has no interrupts)
    ckpt_tuple = CheckpointTuple(
        config={"configurable": {"thread_id": "test-recover-tid"}},
        checkpoint={"ts": "2026-06-05T09:00:00Z", "next": ("decompose",)},
        metadata={},
        parent_config=None,
    )

    async def mock_alist(*args, **kwargs):
        yield ckpt_tuple

    mock_saver.alist = mock_alist

    # Mock SpineRunner and state
    mock_runner = MagicMock()
    mock_state = MagicMock()
    mock_state.next = ("decompose",)
    mock_state.tasks = ()  # no interrupts
    mock_runner.get_state.return_value = mock_state

    # Verify helper methods
    mock_runner._rehydrate_workspaces = MagicMock()
    mock_runner._app = MagicMock()
    mock_runner._app.ainvoke = AsyncMock()
    mock_runner._cfg.return_value = {"configurable": {"thread_id": "test-recover-tid"}}

    with patch("app.main.SpineRunner", return_value=mock_runner):
        async with lifespan(app):
            # Give background task a moment to run
            await asyncio.sleep(0.05)

    mock_runner._rehydrate_workspaces.assert_called_once_with("test-recover-tid")
    mock_runner._app.ainvoke.assert_called_once_with(
        None, {"configurable": {"thread_id": "test-recover-tid"}}
    )


async def test_telegram_webhook_configured(monkeypatch, client):
    """Verify that when TELEGRAM_BOT_TOKEN is set, /webhook/telegram correctly routes to adapter and resumes the thread."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.main import _state, lifespan, app

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:mock_token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "999")

    # Reset state to force lifespan re-initialization
    _state.runner = None
    _state.kill_switch = None

    # Mock route_to_interrupt in SteeringEventBus
    mock_route = AsyncMock()

    with patch("app.main.SpineRunner") as MockRunner:
        mock_runner = MockRunner.return_value
        # Mock methods that would be called
        mock_adapter = MagicMock()
        mock_adapter.handle_update.return_value = True
        mock_runner.require_telegram_adapter.return_value = mock_adapter

        mock_bus = MagicMock()
        mock_bus.route_to_interrupt = mock_route
        mock_runner.require_bus.return_value = mock_bus

        async with lifespan(app):
            # Send a webhook request
            resp = await client.post(
                "/webhook/telegram",
                json={
                    "update_id": 1001,
                    "callback_query": {
                        "id": "cq-1001",
                        "data": "approve:iid1",
                        "from": {"id": 999},
                    },
                },
            )
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}

            # Let the background task run
            await asyncio.sleep(0.05)

        # Assert that the adapter was called and routing occurred
        mock_adapter.handle_update.assert_called_once()
        mock_route.assert_called_once_with(mock_runner, thread_id="telegram", interrupt_id="iid1")


async def test_graph_wires_notifier_calls(monkeypatch, client):
    """Verify that execution nodes call notifier.notify and notifier.notify_gate at appropriate lifecycle points."""
    from unittest.mock import MagicMock
    from app.main import _state, lifespan, app
    from app.core.spine_runner import SpineRunner
    from app.core.graph import build_spine

    # Force Telegram enablement
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:mock_token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "999")

    # Reset state
    _state.runner = None
    _state.kill_switch = None

    # Mock notifier calls
    mock_notify = MagicMock()
    mock_notify_gate = MagicMock()

    original_init = SpineRunner.__init__

    def custom_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # Replace the instantiated notifier with a mock
        self._notifier = MagicMock()
        self._notifier.notify = mock_notify
        self._notifier.notify_gate = mock_notify_gate
        # Update the app with the mock notifier
        self._app = build_spine(
            self._provider.build_saver(),
            capability=self._capability,
            sandbox=self._sandbox,
            drafter=self._drafter,
            cap=self._cap,
            lease=self._lease,
            budget_usd=self._budget,
            board=self._board,
            gate=self._gate,
            reaper=self._reaper,
            decomposer=self._decomposer,
            notifier=self._notifier,
        )

    monkeypatch.setattr(SpineRunner, "__init__", custom_init)

    async with lifespan(app):
        # 1. Start a goal. This will hit goal_intake -> clarify -> sign_off (interrupt)
        resp = await client.post(
            "/goal",
            json={"thread_id": "t-notify-test", "goal": "build hello world"},
        )
        assert resp.status_code == 200

        # 2. Verify notify_gate was called for sign_off_gate
        assert mock_notify_gate.call_count == 1
        call_kwargs = mock_notify_gate.call_args[1]
        assert call_kwargs["thread_id"] == "t-notify-test"
        assert call_kwargs["event_key"].startswith("sign_off_gate:")
        assert call_kwargs["interrupt_id"] is not None
