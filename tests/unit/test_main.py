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
