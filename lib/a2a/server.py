"""A2A receiver — FastAPI JSON-RPC dispatch (Days 2-7).

Per spike-plan.md:
- Day 2: FastAPI app with single POST `/` endpoint accepting JSON-RPC 2.0 envelopes.
  Method dispatch table for 5 A2A methods. `message/send` returns synthetic Task.
  Others return -32004 UnsupportedOperationError.
- Day 4: SSE streaming via POST /stream and POST /subscribe (3 synthetic events).
- Day 5: JWT auth via FastAPI Depends — verify_token guard on POST /; invalid → -32600.
- Day 6: W3C traceparent extracted from inbound headers via OTel propagate.extract.
- Day 7: handle_send_message calls bridge_inbound_to_taskspec + bridge_taskspec_status_to_a2a.

Acceptance gate (from spike-plan.md):
    curl -X POST http://localhost:9001/ -H "Content-Type: application/json" \\
         -d '{"jsonrpc":"2.0","id":1,"method":"message/send",
              "params":{"message":{"role":"USER","parts":[{"text":"hi"}]}}}'
    Returns: {"jsonrpc":"2.0","id":1,
              "result":{"id":"<uuid>","status":"SUBMITTED"}}

Pinned A2A spec: e997516542bd6e3a12ecb6b4939aa0bae3b13a21
    (see audit/2026-05-21-a2a-spike-plan/SPEC-VERSION.md)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import yaml

try:
    from fastapi import Depends, FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError as _fastapi_err:
    raise ImportError(
        "lib.a2a.server requires FastAPI. Install with: uv sync --extra a2a\n"
        f"Original error: {_fastapi_err}"
    ) from _fastapi_err

from lib.a2a.agent_card import build_agent_card as _build_agent_card
from lib.a2a.agent_card import sign_card as _sign_card
from lib.a2a.auth import AgentIdentity, verify_token
from lib.a2a.scrubber import scrub_inbound_params
from lib.a2a.task_bridge import bridge_inbound_to_taskspec, bridge_taskspec_status_to_a2a
from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.propagate import extract as otel_extract

logger = logging.getLogger(__name__)

# H5: unauthenticated-request posture.
# Spike default: open (A2A_REQUIRE_AUTH unset). Production: set A2A_REQUIRE_AUTH=true.
_A2A_REQUIRE_AUTH: bool = os.getenv("A2A_REQUIRE_AUTH", "false").lower() == "true"
if not _A2A_REQUIRE_AUTH:
    logger.warning(
        "a2a: A2A_REQUIRE_AUTH not set — unauthenticated requests ALLOWED (spike posture). "
        "Set A2A_REQUIRE_AUTH=true before any production deploy."
    )

# M11: peers.yaml TTL cache (60s) — avoids per-request file reads
_PEERS_CACHE: list[str] | None = None
_PEERS_CACHE_AT: float = 0.0
_PEERS_CACHE_TTL = 60.0

# Task 6: in-process task registry — maps A2A task_id (str) → bridge TaskSpec.
# Cleared only by explicit tasks/cancel or process restart (spike-grade store).
_TASK_REGISTRY: dict[str, Any] = {}

# --- OTel tracer ----------------------------------------------------------

# Tracer is fetched lazily so test fixtures can swap in a TracerProvider
# after module import without the span silently landing on the NoOp tracer.
_TRACER_NAME = "lib.a2a.server"
_TRACER_VERSION = "0.1.0-spike"


def _get_tracer() -> otel_trace.Tracer:
    return otel_trace.get_tracer(_TRACER_NAME, _TRACER_VERSION)


# --- JSON-RPC 2.0 standard error codes (spec §5.1) -----------------------
JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

# --- A2A-specific error codes (per protocol-survey.md §11, spec §5.4) ----
A2A_TASK_NOT_FOUND = -32001
A2A_TASK_NOT_CANCELABLE = -32002
A2A_PUSH_NOTIFICATION_NOT_SUPPORTED = -32003
A2A_UNSUPPORTED_OPERATION = -32004
A2A_CONTENT_TYPE_NOT_SUPPORTED = -32005
A2A_INVALID_AGENT_RESPONSE = -32006


def _jsonrpc_error(req_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


def _jsonrpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


# --- JWT guard helpers ----------------------------------------------------


def _load_peers_config() -> list[str]:
    """Load peers.yaml allowlist with 60s TTL cache. Falls back to empty list on error."""
    global _PEERS_CACHE, _PEERS_CACHE_AT
    now = time.monotonic()
    if _PEERS_CACHE is not None and (now - _PEERS_CACHE_AT) < _PEERS_CACHE_TTL:
        return _PEERS_CACHE
    try:
        with open("config/a2a/peers.yaml") as f:
            data = yaml.safe_load(f)
        result = [p["issuer"] for p in (data.get("peers") or []) if "issuer" in p]
        logger.debug("a2a: loaded %d peer issuers from peers.yaml", len(result))
    except FileNotFoundError:
        logger.warning(
            "a2a: config/a2a/peers.yaml not found — no peers allowlisted; all inbound JWTs will be rejected"
        )
        result = []
    except Exception as exc:
        logger.error(
            "a2a: failed to load peers.yaml (%s) — all inbound JWTs will be rejected",
            type(exc).__name__,
        )
        result = []
    _PEERS_CACHE = result
    _PEERS_CACHE_AT = now
    return result


def _attach_inbound_context(request: Request) -> Any:
    """Extract W3C traceparent/tracestate from inbound headers and attach to OTel context."""
    carrier = dict(request.headers)
    ctx = otel_extract(carrier)
    return otel_context.attach(ctx)


async def _jwt_guard(request: Request) -> AgentIdentity | None:
    """FastAPI Depends guard — verifies Bearer JWT if present.

    Sets request.state.identity on success (or None when no header).
    Sets request.state.jwt_error = True on verification failure.
    The jsonrpc_dispatch handler short-circuits to -32600 when jwt_error is set.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header:
        if _A2A_REQUIRE_AUTH:
            request.state.identity = None
            request.state.jwt_error = True
            return None
        request.state.identity = None
        return None
    token_str = auth_header.removeprefix("Bearer ").strip()
    our_sa = os.getenv(
        "HERMES_A2A_SA",
        "agent-a@autonomous-agent-2026.iam.gserviceaccount.com",
    )
    peers = _load_peers_config()
    try:
        identity = await verify_token(token_str, our_sa=our_sa, peers_allowlist=peers)
    except Exception:
        request.state.identity = None
        request.state.jwt_error = True
        return None
    request.state.identity = identity
    return identity


# --- Handlers ------------------------------------------------------------


class _A2AUnsupportedOperation(Exception):
    """Raised by stub handlers for methods not yet implemented in this day.

    The dispatcher catches this and emits the A2A `-32004` error code.
    Carrying the method name avoids hard-coding it in the dispatcher.
    """

    def __init__(self, method_name: str) -> None:
        super().__init__(method_name)
        self.method_name = method_name


class _A2ATaskNotFound(Exception):
    """Raised by tasks/get and tasks/cancel when the task_id is not in the registry.

    The dispatcher catches this and emits the A2A `-32001` error code
    (A2ATaskNotFoundError per protocol-survey.md §11).
    """


async def handle_send_message(
    params: dict[str, Any],
    identity: Any | None = None,
) -> dict[str, Any]:
    """Day 7: create TaskSpec via bridge instead of returning a synthetic Task.

    Spec contract (§7.6.1): params MUST include a `message` object with
    `parts` array. bridge_inbound_to_taskspec validates and creates the TaskSpec.
    Task 6: stores the spec in _TASK_REGISTRY keyed by spec.id.
    """
    message = params.get("message")
    if not isinstance(message, dict) or "parts" not in message:
        raise ValueError("params.message.parts is required")
    spec = bridge_inbound_to_taskspec(params, identity)
    _TASK_REGISTRY[spec.id] = spec
    status = bridge_taskspec_status_to_a2a(spec)
    return {"id": spec.id, "status": status}


async def handle_tasks_get(params: dict[str, Any]) -> dict[str, Any]:
    """Task 6: return the status of a previously submitted task by id.

    Returns -32001 (A2ATaskNotFound) if the task_id is not in the registry.

    # SECURITY(spike): no ownership check — any authenticated peer with a task
    # UUID can read this task. Production: verify identity.sub == spec.owner.
    """
    task_id = params.get("id", "")
    spec = _TASK_REGISTRY.get(task_id)
    if spec is None:
        raise _A2ATaskNotFound(f"tasks/get: task {task_id!r} not found")
    return {"id": task_id, "status": bridge_taskspec_status_to_a2a(spec)}


async def handle_tasks_cancel(params: dict[str, Any]) -> dict[str, Any]:
    """Task 6: cancel a task by marking it superseded in the registry.

    Returns -32001 (A2ATaskNotFound) if the task_id is not in the registry.
    The spec is immutably updated via model_copy (dataclass replace pattern).

    # SECURITY(spike): no ownership check — any authenticated peer with a task
    # UUID can cancel this task. Production: verify identity.sub == spec.owner.
    """
    task_id = params.get("id", "")
    spec = _TASK_REGISTRY.get(task_id)
    if spec is None:
        raise _A2ATaskNotFound(f"tasks/cancel: task {task_id!r} not found")
    from dataclasses import replace as _dc_replace

    try:
        cancelled = _dc_replace(spec, status="superseded")
    except TypeError:
        # Fallback for Pydantic-based TaskSpec (model_copy)
        cancelled = spec.model_copy(update={"status": "superseded"})
    _TASK_REGISTRY[task_id] = cancelled
    return {"id": task_id, "status": bridge_taskspec_status_to_a2a(cancelled)}


async def _handle_unsupported_stream(_params: dict[str, Any]) -> None:
    raise _A2AUnsupportedOperation("message/stream")


async def _handle_unsupported_subscribe(_params: dict[str, Any]) -> None:
    raise _A2AUnsupportedOperation("tasks/subscribe")


# Dispatch table — method name → coroutine. New methods land here as the
# spike days roll forward; the dispatcher is method-agnostic.
_DISPATCH = {
    "message/send": handle_send_message,
    "message/stream": _handle_unsupported_stream,
    "tasks/get": handle_tasks_get,
    "tasks/subscribe": _handle_unsupported_subscribe,
    "tasks/cancel": handle_tasks_cancel,
}


# --- SSE streaming handlers ----------------------------------------------


async def handle_stream_message(params: dict[str, Any]) -> StreamingResponse:
    """Day 4: SSE streaming for message/stream. 3 synthetic events."""

    async def _gen() -> Any:
        for evt in [{"status": "WORKING"}, {"artifact_added": True}, {"status": "COMPLETED"}]:
            yield f"data: {json.dumps(evt)}\n\n"
            await asyncio.sleep(0)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def handle_subscribe_task(params: dict[str, Any]) -> StreamingResponse:
    """Day 4: SSE streaming for tasks/subscribe. 3 synthetic events."""

    async def _gen() -> Any:
        for evt in [{"status": "WORKING"}, {"artifact_added": True}, {"status": "COMPLETED"}]:
            yield f"data: {json.dumps(evt)}\n\n"
            await asyncio.sleep(0)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- FastAPI app ---------------------------------------------------------

app = FastAPI(
    title="A2A Spike Agent",
    description="JSON-RPC 2.0 / SSE agent-to-agent protocol — Days 2-7 spike",
    version="0.1.0-spike-day7",
)

# M3: 1MB body size limit — prevents OOM from oversized JSON payloads
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.responses import Response as _StarletteResponse  # noqa: E402


class _BodySizeLimitMiddleware(BaseHTTPMiddleware):
    _MAX_BYTES = 1 * 1024 * 1024  # 1 MB

    async def dispatch(self, request, call_next):  # type: ignore[override]
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self._MAX_BYTES:
            return _StarletteResponse(
                content='{"jsonrpc":"2.0","id":null,"error":{"code":-32600,"message":"Request too large"}}',
                status_code=413,
                media_type="application/json",
            )
        return await call_next(request)


app.add_middleware(_BodySizeLimitMiddleware)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/.well-known/agent-card.json")
async def agent_card_endpoint() -> JSONResponse:
    """Serve signed AgentCard (Day 8). Returns 503 if signing fails.

    Env vars are read lazily inside the handler so test fixtures can set them
    after module import without triggering stale-capture surprises.
    """
    agent_sa = os.environ.get(
        "A2A_AGENT_SA", "agent-a@autonomous-agent-2026.iam.gserviceaccount.com"
    )
    base_url = os.environ.get("A2A_BASE_URL", "http://localhost:9001")
    card = _build_agent_card(agent_sa, base_url)
    try:
        signed = await _sign_card(card, agent_sa)
    except Exception as exc:
        logger.warning(
            "a2a: sign_card failed (%s) — returning 503; unsigned card not served",
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=503,
            content={"error": "agent_card_signing_unavailable", "detail": type(exc).__name__},
            headers={"Retry-After": "30"},
        )
    return JSONResponse(content=signed)


# --- Day 4: SSE streaming routes -----------------------------------------


@app.post("/stream")
async def stream_endpoint(
    request: Request,
    _identity: AgentIdentity | None = Depends(_jwt_guard),
) -> StreamingResponse:
    """POST /stream - SSE streaming for message/stream (Day 4).

    JWT guard: invalid token returns JSON -32600 (cannot stream before auth).
    PHI guard: params scrubbed before handler sees them.
    """
    if getattr(request.state, "jwt_error", False):
        return JSONResponse(
            status_code=200,
            content=_jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Invalid or expired token"),
        )
    _ctx_token = _attach_inbound_context(request)
    with _get_tracer().start_as_current_span("a2a.server.stream"):
        body_bytes = await request.body()
        try:
            params = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            params = {}
        params = scrub_inbound_params(params)
        response = await handle_stream_message(params)
    otel_context.detach(_ctx_token)
    return response


@app.post("/subscribe")
async def subscribe_endpoint(
    request: Request,
    _identity: AgentIdentity | None = Depends(_jwt_guard),
) -> StreamingResponse:
    """POST /subscribe - SSE streaming for tasks/subscribe (Day 4).

    JWT guard: invalid token returns JSON -32600 (cannot stream before auth).
    PHI guard: params scrubbed before handler sees them.
    """
    if getattr(request.state, "jwt_error", False):
        return JSONResponse(
            status_code=200,
            content=_jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Invalid or expired token"),
        )
    _ctx_token = _attach_inbound_context(request)
    with _get_tracer().start_as_current_span("a2a.server.subscribe"):
        body_bytes = await request.body()
        try:
            params = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            params = {}
        params = scrub_inbound_params(params)
        response = await handle_subscribe_task(params)
    otel_context.detach(_ctx_token)
    return response


# --- JSON-RPC dispatch ---------------------------------------------------


async def _jsonrpc_dispatch_inner(request: Request) -> JSONResponse:
    """Inner dispatch body, extracted so jsonrpc_dispatch can wrap it in OTel span.

    Pipeline: parse body → validate envelope → resolve method → invoke handler.
    Each stage maps cleanly to a JSON-RPC error code, with A2A method-level
    errors layered on top.
    """
    # Stage 1: parse the raw body as JSON.
    try:
        body_bytes = await request.body()
        envelope = json.loads(body_bytes)
    except json.JSONDecodeError:
        return JSONResponse(content=_jsonrpc_error(None, JSONRPC_PARSE_ERROR, "Parse error"))

    # Stage 2: validate the envelope shape.
    req_id = envelope.get("id") if isinstance(envelope, dict) else None
    if not isinstance(envelope, dict):
        return JSONResponse(
            content=_jsonrpc_error(
                None,
                JSONRPC_INVALID_REQUEST,
                "Envelope must be a JSON object",
            )
        )
    if envelope.get("jsonrpc") != "2.0":
        return JSONResponse(
            content=_jsonrpc_error(req_id, JSONRPC_INVALID_REQUEST, "jsonrpc must be '2.0'")
        )
    method = envelope.get("method")
    if not isinstance(method, str):
        return JSONResponse(
            content=_jsonrpc_error(req_id, JSONRPC_INVALID_REQUEST, "method must be a string")
        )

    # Stage 3: resolve the method to a handler.
    handler = _DISPATCH.get(method)
    if handler is None:
        return JSONResponse(
            content=_jsonrpc_error(
                req_id,
                JSONRPC_METHOD_NOT_FOUND,
                "Unknown method",  # not echoing — method is user-controlled
            )
        )

    # Stage 4: validate params shape, scrub PHI, then invoke the handler.
    params = envelope.get("params") or {}
    if not isinstance(params, dict):
        return JSONResponse(
            content=_jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "params must be an object")
        )
    # Scrub PHI from inbound params at the A2A boundary before any handler sees them.
    # Patterns configured in config/a2a/scrubber-patterns.yaml.
    params = scrub_inbound_params(params)

    try:
        # Day 7: pass identity to message/send so it can be threaded into the TaskSpec.
        if method == "message/send":
            identity = getattr(request.state, "identity", None)
            result = await handler(params, identity)
        else:
            result = await handler(params)
    except _A2AUnsupportedOperation as exc:
        return JSONResponse(
            content=_jsonrpc_error(
                req_id,
                A2A_UNSUPPORTED_OPERATION,
                f"Method '{exc.method_name}' not yet implemented in Day 2 spike",
            )
        )
    except _A2ATaskNotFound as exc:
        logger.info("a2a: task not found: %s", str(exc))
        return JSONResponse(
            content=_jsonrpc_error(
                req_id,
                A2A_TASK_NOT_FOUND,
                "Task not found",
            )
        )
    except ValueError:
        return JSONResponse(
            content=_jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "Invalid params")
        )
    except Exception as exc:
        # Log type only — exception messages can contain caller data; method is user-controlled.
        logger.error("a2a: unhandled exception in handler exc_type=%s", type(exc).__name__)
        return JSONResponse(
            content=_jsonrpc_error(req_id, JSONRPC_INTERNAL_ERROR, "Internal server error")
        )  # M5: exception_type removed from data — leaks stack topology to caller

    return JSONResponse(content=_jsonrpc_result(req_id, result))


@app.post("/")
async def jsonrpc_dispatch(
    request: Request,
    _identity: AgentIdentity | None = Depends(_jwt_guard),
) -> JSONResponse:
    """JSON-RPC 2.0 dispatch endpoint.

    Day 5: JWT guard runs via FastAPI Depends. Invalid token → -32600.
    Day 6: W3C traceparent extracted and OTel span started.
    Day 7: message/send handler receives identity for TaskSpec bridge.
    """
    # Day 5: short-circuit on JWT error.
    if getattr(request.state, "jwt_error", False):
        return JSONResponse(
            content=_jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Invalid or expired token")
        )

    # Day 6: extract inbound OTel context and wrap dispatch in a server span.
    _ctx_token = _attach_inbound_context(request)
    with _get_tracer().start_as_current_span("a2a.server.dispatch"):
        result = await _jsonrpc_dispatch_inner(request)
    otel_context.detach(_ctx_token)
    return result
