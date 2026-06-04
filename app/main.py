"""AutonomousAgent FastAPI service — the Pattern-B fix.

Assembles SpineRunner with production adapters and exposes the spine as an HTTP
service.  All adapter selection is environment-driven; InMemory defaults for CI.

Endpoints:
  POST /goal           — start a new spine run
  POST /resume         — resume an interrupted run (sign_off / ship_gate)
  GET  /state/{tid}    — get current thread state
  POST /webhook/telegram — inbound Telegram webhook (SP-13)
  POST /panic          — operator kill-switch (SP-IR1)
  POST /rollback       — operator deployment rollback (SP-26)
  GET  /healthz        — liveness probe

Environment variables:
  SPINE_CHECKPOINTER   — "inmemory" (default) | "sqlite" | "postgres"
  SPINE_SANDBOX        — "inmemory" (default) | "cloudrun"
  SPINE_BOARD          — "inmemory" (default)
  SPINE_KILL_SWITCH    — sentinel path (default /tmp/aa-kill-switch)
  TELEGRAM_BOT_TOKEN   — if set, wires TelegramAdapter + TelegramNotifier
  AA_GITHUB_APP_*      — GitHub App credentials for token revocation
  All SpineRunner env vars (SPINE_BUDGET_USD, SPINE_MAX_ACTIVE, etc.)
"""

from __future__ import annotations

import dataclasses
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.adapters.inmemory.board import InMemoryBoard
from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core.kill_switch import KillSwitch
from app.core.reaper import WorkspaceReaper
from app.core.spine_runner import SpineRunner
from app.core.steering import SteeringEventBus

logger = logging.getLogger(__name__)


# ── Request / response models ────────────────────────────────────────────────


class GoalRequest(BaseModel):
    """POST /goal body."""

    thread_id: str = Field(..., min_length=1, description="Unique thread identifier")
    goal: str = Field(..., min_length=1, description="Natural-language goal")


class ResumeRequest(BaseModel):
    """POST /resume body."""

    thread_id: str = Field(..., min_length=1)
    interrupt_id: str = Field(..., min_length=1)
    decision: dict[str, Any] = Field(..., description="Decision payload (verb, actor, reason)")


class PanicRequest(BaseModel):
    """POST /panic body."""

    reason: str = Field(default="operator panic", min_length=1)


class RollbackRequest(BaseModel):
    """POST /rollback body."""

    service: str = Field(..., min_length=1, description="Cloud Run service name")
    revision: str = Field(..., min_length=1, description="Target revision tag")
    traffic_percent: int = Field(default=100, ge=0, le=100)


# ── Adapter factories ────────────────────────────────────────────────────────


def _build_checkpointer() -> InMemoryCheckpointer:
    """Select checkpointer based on SPINE_CHECKPOINTER env var.

    Currently only InMemory is wired; the SqliteFileCheckpointer (SP-R3)
    and the Cloud SQL checkpointer (SP-23) are deferred production tiers
    that will be added here as elif branches.
    """
    kind = os.environ.get("SPINE_CHECKPOINTER", "inmemory").lower()
    if kind == "inmemory":
        return InMemoryCheckpointer()
    # DEFERRED: elif kind == "sqlite": ...
    # DEFERRED: elif kind == "postgres": ...
    logger.warning("Unknown SPINE_CHECKPOINTER=%r — falling back to inmemory", kind)
    return InMemoryCheckpointer()


def _build_kill_switch(reaper: Optional[WorkspaceReaper] = None) -> KillSwitch:
    """Build the operator kill-switch (SP-IR1).

    Reads AA_KILL_SWITCH_PATH at call time (not import time) so tests can
    override the sentinel path via monkeypatch.setenv.
    """
    from pathlib import Path

    sentinel_raw = os.environ.get("AA_KILL_SWITCH_PATH")
    sentinel_path = Path(sentinel_raw) if sentinel_raw else None
    return KillSwitch(reaper=reaper, sentinel_path=sentinel_path)


# ── App state holder ─────────────────────────────────────────────────────────


class _AppState:
    """Mutable singleton holding the SpineRunner and its adapters.

    Populated in the lifespan; read by endpoint handlers.
    """

    runner: Optional[SpineRunner] = None
    kill_switch: Optional[KillSwitch] = None


_state = _AppState()


# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Construct SpineRunner and production adapters on startup; tear down on shutdown."""
    logger.info("spine: lifespan startup — assembling SpineRunner")

    checkpointer = _build_checkpointer()
    await checkpointer.setup()

    board = InMemoryBoard()
    reaper = WorkspaceReaper()
    kill_switch = _build_kill_switch(reaper=reaper)

    # SP-17: the SteeringEventBus for inbound channel arbitration (C15).
    # Constructed here so all adapters (Telegram, board webhook) share ONE bus.
    bus = SteeringEventBus()

    runner = SpineRunner(
        checkpointer,
        board=board,
        bus=bus,
        kill_switch=kill_switch,
    )

    _state.runner = runner
    _state.kill_switch = kill_switch

    logger.info("spine: lifespan startup complete — SpineRunner ready")
    yield

    # Shutdown
    logger.info("spine: lifespan shutdown — releasing resources")
    await checkpointer.aclose()
    logger.info("spine: lifespan shutdown complete")


# ── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="AutonomousAgent Spine",
    version="0.1.0",
    description="LangGraph spine service — the Pattern-B fix.",
    lifespan=lifespan,
)


def _require_runner() -> SpineRunner:
    """Return the SpineRunner or 503 if not yet initialised."""
    if _state.runner is None:
        raise HTTPException(status_code=503, detail="SpineRunner not initialised")
    return _state.runner


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/healthz", tags=["ops"])
async def healthz():
    """Liveness probe. Returns 200 if the service is up; 503 if the kill-switch
    is active (operator HALT)."""
    if _state.kill_switch and _state.kill_switch.is_active():
        return JSONResponse(
            status_code=503,
            content={"status": "halted", "detail": "kill-switch active"},
        )
    return {"status": "ok"}


@app.post("/goal", tags=["spine"])
async def start_goal(req: GoalRequest):
    """Start a new spine run for the given goal.

    Returns the initial state (which will contain __interrupt__ for sign_off).
    """
    runner = _require_runner()
    if _state.kill_switch and _state.kill_switch.is_active():
        raise HTTPException(status_code=503, detail="kill-switch active — refusing new work")
    try:
        result = await runner.start(thread_id=req.thread_id, goal=req.goal)
        return _serialise_result(result)
    except Exception as exc:
        logger.exception("spine: start_goal failed tid=%s", req.thread_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/resume", tags=["spine"])
async def resume_thread(req: ResumeRequest):
    """Resume an interrupted spine run (sign_off / ship_gate decision).

    The decision dict must contain at minimum: verb, actor, reason.
    """
    runner = _require_runner()
    try:
        result = await runner.resume(
            thread_id=req.thread_id,
            interrupt_id=req.interrupt_id,
            decision=req.decision,
        )
        return _serialise_result(result)
    except Exception as exc:
        logger.exception("spine: resume failed tid=%s", req.thread_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/state/{thread_id}", tags=["spine"])
async def get_state(thread_id: str):
    """Get the current state snapshot for a thread."""
    runner = _require_runner()
    try:
        state = runner.get_state(thread_id)
        return {
            "thread_id": thread_id,
            "next": list(state.next) if state.next else [],
            "values": _make_json_safe(state.values) if state.values else {},
        }
    except Exception as exc:
        logger.exception("spine: get_state failed tid=%s", thread_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/panic", tags=["ops"])
async def panic(req: PanicRequest):
    """Operator kill-switch (SP-IR1). Triggers HALT sentinel + reaper sweep + token revocation."""
    if _state.kill_switch is None:
        raise HTTPException(status_code=503, detail="KillSwitch not configured")
    elapsed = _state.kill_switch.trigger(reason=req.reason)
    return {"status": "halted", "elapsed_s": round(elapsed, 3)}


@app.post("/rollback", tags=["ops"])
async def rollback(req: RollbackRequest):
    """Operator deployment rollback (SP-26). Retargets Cloud Run traffic to a prior revision."""
    runner = _require_runner()
    try:
        rb = runner.require_revision_rollback()
        result = await rb.rollback_to(revision_name=req.revision)
        return {"status": "rolled_back", "detail": dataclasses.asdict(result)}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("spine: rollback failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/webhook/telegram", tags=["integrations"])
async def telegram_webhook(request: Request):
    """Inbound Telegram webhook (SP-13). Delegates to TelegramAdapter."""
    runner = _require_runner()
    try:
        adapter = runner.require_telegram_adapter()
        body = await request.json()
        thread_id = str(
            body.get("thread_id")
            or (body.get("message") or {}).get("chat", {}).get("id", "telegram")
        )
        adapter.handle_update(body, thread_id=thread_id)
        return {"status": "ok"}
    except RuntimeError as exc:
        # TelegramAdapter not configured — expected in non-Telegram deployments
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("spine: telegram webhook failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Helpers ──────────────────────────────────────────────────────────────────


def _serialise_result(result: dict) -> dict:
    """Convert a spine result dict to JSON-safe form.

    Interrupt objects need special handling — they are LangGraph Interrupt instances
    with .id and .value attributes, not plain dicts.
    """
    out = _make_json_safe(result)
    if "__interrupt__" in result:
        out["__interrupt__"] = [
            {"id": intr.id, "value": intr.value} for intr in result["__interrupt__"]
        ]
    return out


def _make_json_safe(obj: Any) -> Any:
    """Recursively convert non-serialisable values to JSON-safe forms."""
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_json_safe(v) for v in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    # Fallback: str() for anything else (numpy arrays, custom objects, etc.)
    return str(obj)
