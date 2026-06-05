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
from app.core.checkpointer import AbstractCheckpointer
from app.core.kill_switch import KillSwitch
from app.core.orchestrator import OrchestratorConfig
from app.core.reaper import WorkspaceReaper
from app.core.sandbox import AbstractSandbox
from app.core.spec_drafter import AbstractSpecDrafter
from app.core.spine_runner import SpineRunner
from app.core.steering import SteeringEventBus
from app.middleware import setup_rate_limiting, LIMITS

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


def _build_checkpointer() -> AbstractCheckpointer:
    """Select checkpointer based on SPINE_CHECKPOINTER env var.

    Wires sqlite and postgres checkpointers, falling back to inmemory.
    """
    kind = os.environ.get("SPINE_CHECKPOINTER", "inmemory").lower()
    if kind == "inmemory":
        return InMemoryCheckpointer()
    elif kind == "sqlite":
        sqlite_db = os.environ.get("SPINE_CHECKPOINT_DB", "/tmp/spine.db")
        from app.adapters.inmemory.file_checkpointer import SqliteFileCheckpointer

        return SqliteFileCheckpointer(sqlite_db)
    elif kind == "postgres":
        dsn = os.environ.get("CLOUD_SQL_DSN") or os.environ.get(
            "DATABASE_URL", "postgresql://localhost:5432/postgres"
        )
        from app.adapters.gcp.checkpointer import PostgresCheckpointer

        return PostgresCheckpointer(dsn)
    logger.warning("Unknown SPINE_CHECKPOINTER=%r — falling back to inmemory", kind)
    return InMemoryCheckpointer()


def _build_memory_store() -> Optional[Any]:
    """Build the production CloudSqlPgvectorStore if CLOUD_SQL_DSN is set."""
    dsn = os.environ.get("CLOUD_SQL_DSN")
    if dsn:
        logger.info("spine: CLOUD_SQL_DSN detected — initializing CloudSqlPgvectorStore")
        from app.adapters.gcp.memory import CloudSqlPgvectorStore

        dim = int(os.environ.get("SPINE_MEMORY_DIM", "256"))
        return CloudSqlPgvectorStore(dim=dim, dsn=dsn)
    return None


def _build_kill_switch(reaper: Optional[WorkspaceReaper] = None) -> KillSwitch:
    """Build the operator kill-switch (SP-IR1).

    Reads AA_KILL_SWITCH_PATH at call time (not import time) so tests can
    override the sentinel path via monkeypatch.setenv.
    """
    from pathlib import Path

    sentinel_raw = os.environ.get("AA_KILL_SWITCH_PATH")
    sentinel_path = Path(sentinel_raw) if sentinel_raw else None
    return KillSwitch(reaper=reaper, sentinel_path=sentinel_path)


def _build_sandbox() -> Optional[AbstractSandbox]:
    """Select sandbox based on SPINE_SANDBOX env var.

    Wires CloudRunJobSandbox for production (gVisor container isolation)
    or LocalSubprocessSandbox for dev/CI (POSIX rlimit isolation).
    Unset → None (legacy in-process path — backward-compatible).

    P0-1 (Go-Live audit): CloudRunJobSandbox is the only production-grade
    sandbox (is_production_grade=True). OrchestratorConfig.validate() rejects
    non-production sandboxes when SPINE_ENVIRONMENT=production.
    """
    kind = os.environ.get("SPINE_SANDBOX", "").lower()
    spine_env = os.environ.get("SPINE_ENVIRONMENT", "development").lower()
    if not kind and spine_env == "production":
        kind = "cloudrun"

    if not kind or kind == "none":
        return None
    if kind == "inmemory" or kind == "local":
        from app.adapters.inmemory.sandbox import LocalSubprocessSandbox

        return LocalSubprocessSandbox()
    if kind == "cloudrun":
        project_id = os.environ.get("GCP_PROJECT_ID", "autonomous-agent-2026")
        region = os.environ.get("GCP_REGION", "us-central1")
        job_name = os.environ.get("SPINE_SANDBOX_JOB_NAME", "aa-sandbox")
        from app.adapters.gcp.cloud_run_sandbox import CloudRunJobSandbox

        logger.info(
            "spine: wiring CloudRunJobSandbox (project=%s, region=%s, job=%s)",
            project_id,
            region,
            job_name,
        )
        return CloudRunJobSandbox(
            project_id=project_id,
            region=region,
            job_name=job_name,
            collect_logs=(spine_env == "production"),
        )
    logger.warning("Unknown SPINE_SANDBOX=%r — running without sandbox", kind)
    return None


def _build_spec_drafter() -> Optional[AbstractSpecDrafter]:
    """Select spec drafter based on SPINE_DRAFTER env var.

    "vertex" -> VertexSpecDrafter (GCP Vertex concretion)
    "inmemory" or "local" -> InMemorySpecDrafter
    Unset / Empty -> None (falls back to InMemorySpecDrafter in build_spine)
    """
    kind = os.environ.get("SPINE_DRAFTER", "").lower()
    if not kind:
        return None
    if kind == "inmemory" or kind == "local":
        from app.adapters.inmemory.spec_drafter import InMemorySpecDrafter

        return InMemorySpecDrafter()
    if kind == "vertex":
        from app.adapters.gcp.spec_drafter import VertexSpecDrafter

        project_id = os.environ.get("GCP_PROJECT_ID", "autonomous-agent-2026")
        region = os.environ.get("GCP_REGION", "us-central1")
        return VertexSpecDrafter(project=project_id, location=region)
    logger.warning("Unknown SPINE_DRAFTER=%r — falling back to default drafter", kind)
    return None


# ── App state holder ─────────────────────────────────────────────────────────


class _AppState:
    """Mutable singleton holding the SpineRunner and its adapters.

    Populated in the lifespan; read by endpoint handlers.
    """

    runner: Optional[SpineRunner] = None
    kill_switch: Optional[KillSwitch] = None
    memory_store: Optional[Any] = None


_state = _AppState()


# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Construct SpineRunner and production adapters on startup; tear down on shutdown."""
    logger.info("spine: lifespan startup — assembling SpineRunner")

    checkpointer = _build_checkpointer()
    try:
        await checkpointer.setup()
    except Exception as exc:
        kind = os.environ.get("SPINE_CHECKPOINTER", "inmemory").lower()
        if kind == "postgres":
            logger.error(
                "CRITICAL: Postgres checkpointer setup failed: %s. "
                "ALERT: Falling back to SqliteFileCheckpointer at runtime (SP-R3).",
                exc,
            )
            sqlite_db = os.environ.get("SPINE_CHECKPOINT_DB", "/tmp/spine.db")
            from app.adapters.inmemory.file_checkpointer import SqliteFileCheckpointer

            checkpointer = SqliteFileCheckpointer(sqlite_db)
            await checkpointer.setup()
        else:
            raise

    memory_store = _build_memory_store()

    board = InMemoryBoard()
    reaper = WorkspaceReaper()
    kill_switch = _build_kill_switch(reaper=reaper)
    sandbox = _build_sandbox()

    # P0-1 (Go-Live audit): validate sandbox grade in production.
    # OrchestratorConfig.validate() raises RuntimeError if a non-production-grade
    # sandbox is used when environment="production".
    spine_env = os.environ.get("SPINE_ENVIRONMENT", "development").lower()
    if spine_env == "production":
        OrchestratorConfig(
            sandbox=sandbox,
            environment="production",
        )  # raises on invalid sandbox
        logger.info("spine: production sandbox validation passed")

    # Prompt and Model Config Integrity Verification (§25 Gap)
    from lib.guardrails.integrity import verify_integrity

    verify_integrity(fail_closed=(spine_env == "production"))
    logger.info("spine: prompt & model config integrity verification passed")

    # SP-17: the SteeringEventBus for inbound channel arbitration (C15).
    # Constructed here so all adapters (Telegram, board webhook) share ONE bus.
    bus = SteeringEventBus()

    drafter = _build_spec_drafter()

    runner = SpineRunner(
        checkpointer,
        board=board,
        bus=bus,
        kill_switch=kill_switch,
        sandbox=sandbox,
        drafter=drafter,
    )

    _state.runner = runner
    _state.kill_switch = kill_switch
    _state.memory_store = memory_store

    # F-2: Auto-recovery of crashed/restarted active threads during lifespan startup.
    import asyncio

    async def _recover_active_threads() -> None:
        logger.info("spine: scanning checkpointer for active threads to recover")
        try:
            seen_threads = set()
            saver = checkpointer.build_saver()
            async for checkpoint_tuple in saver.alist(None):
                thread_id = checkpoint_tuple.config["configurable"]["thread_id"]
                if thread_id in seen_threads:
                    continue
                seen_threads.add(thread_id)

                state = runner.get_state(thread_id)
                if state and state.next:
                    has_interrupts = False
                    if getattr(state, "tasks", None):
                        for task in state.tasks:
                            if getattr(task, "interrupts", None):
                                has_interrupts = True
                                break
                    if not has_interrupts:
                        logger.info(
                            "spine: recovering active crashed thread: %s (resuming at %s)",
                            thread_id,
                            state.next,
                        )
                        try:
                            # Rehydrate workspace files/git snapshots
                            runner._rehydrate_workspaces(thread_id)
                        except Exception as rehydrate_exc:
                            logger.error(
                                "spine: workspace rehydration failed for thread %s: %s",
                                thread_id,
                                rehydrate_exc,
                            )

                        # Define background task to run the thread to completion / interrupt
                        async def _run_recovered_thread(tid: str) -> None:
                            try:
                                await runner._app.ainvoke(None, runner._cfg(tid))
                                logger.info(
                                    "spine: recovered active thread %s completed successfully", tid
                                )
                            except Exception as run_exc:
                                logger.error(
                                    "spine: recovered active thread %s failed: %s", tid, run_exc
                                )

                        asyncio.create_task(_run_recovered_thread(thread_id))
        except Exception as recovery_exc:
            logger.error(
                "spine: error during startup recovery of active threads: %s",
                recovery_exc,
                exc_info=True,
            )

    # Spawn recovery task in the background
    asyncio.create_task(_recover_active_threads())

    logger.info("spine: lifespan startup complete — SpineRunner ready")
    yield

    # Shutdown
    logger.info("spine: lifespan shutdown — releasing resources")
    await checkpointer.aclose()
    if _state.memory_store is not None:
        try:
            from app.adapters.gcp.memory import _reset_pool_for_tests

            await _reset_pool_for_tests()
        except Exception as exc:
            logger.warning("Failed to close memory_store pool: %s", exc)
    logger.info("spine: lifespan shutdown complete")


# ── FastAPI app ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="AutonomousAgent Spine",
    version="0.1.0",
    description="LangGraph spine service — the Pattern-B fix.",
    lifespan=lifespan,
)

# P0-3 (Go-Live audit): rate-limiting middleware.
# Gracefully degrades if slowapi is not installed (limiter=None → no enforcement).
_limiter = setup_rate_limiting(app)


def _limit(limit_key: str):
    """Return a SlowAPI limit decorator, or a no-op if the limiter is disabled/absent."""
    if _limiter is None:

        def _noop(fn):
            return fn

        return _noop
    return _limiter.limit(LIMITS.get(limit_key, LIMITS["default"]))


def _require_runner() -> SpineRunner:
    """Return the SpineRunner or 503 if not yet initialised."""
    if _state.runner is None:
        raise HTTPException(status_code=503, detail="SpineRunner not initialised")
    return _state.runner


# ── Endpoints ────────────────────────────────────────────────────────────────


@app.get("/healthz", tags=["ops"])
@_limit("healthz")
async def healthz(request: Request):
    """Liveness probe. Returns 200 if the service is up; 503 if the kill-switch
    is active (operator HALT)."""
    if _state.kill_switch and _state.kill_switch.is_active():
        return JSONResponse(
            status_code=503,
            content={"status": "halted", "detail": "kill-switch active"},
        )
    return {"status": "ok"}


@app.post("/goal", tags=["spine"])
@_limit("goal")
async def start_goal(req: GoalRequest, request: Request):
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
@_limit("resume")
async def resume_thread(req: ResumeRequest, request: Request):
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
@_limit("ops")
async def panic(req: PanicRequest, request: Request):
    """Operator kill-switch (SP-IR1). Triggers HALT sentinel + reaper sweep + token revocation."""
    if _state.kill_switch is None:
        raise HTTPException(status_code=503, detail="KillSwitch not configured")
    elapsed = _state.kill_switch.trigger(reason=req.reason)
    return {"status": "halted", "elapsed_s": round(elapsed, 3)}


@app.post("/rollback", tags=["ops"])
@_limit("ops")
async def rollback(req: RollbackRequest, request: Request):
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
@_limit("webhook")
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
