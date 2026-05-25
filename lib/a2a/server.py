"""A2A receiver — FastAPI JSON-RPC dispatch (Day 2).

Per spike-plan.md §Day 2:
- FastAPI app with single POST `/` endpoint accepting JSON-RPC 2.0 envelopes.
- Method dispatch table for the 5 A2A methods used in the canary demo:
  `message/send`, `message/stream`, `tasks/get`, `tasks/subscribe`,
  `tasks/cancel`.
- Day 2 implements `message/send` only (returns a synthetic Task with state
  `SUBMITTED`); the other 4 return `-32004` UnsupportedOperationError per
  A2A spec §5.4 (Day 3/4/7 fill them in).
- `/health` liveness endpoint returns 200 with `{"status": "ok"}`.
- No auth on Day 2 (allow-all). Day 5 wires `verify_token` middleware via
  `lib/a2a/auth.py`.

Acceptance gate (from spike-plan.md):
    curl -X POST http://localhost:9001/ -H "Content-Type: application/json" \\
         -d '{"jsonrpc":"2.0","id":1,"method":"message/send",
              "params":{"message":{"role":"USER","parts":[{"text":"hi"}]}}}'
    Returns: {"jsonrpc":"2.0","id":1,
              "result":{"id":"task-...","status":"SUBMITTED"}}

Pinned A2A spec: e997516542bd6e3a12ecb6b4939aa0bae3b13a21
    (see audit/2026-05-21-a2a-spike-plan/SPEC-VERSION.md)

Day 3+ will replace the synthetic Task with a real round-trip via
`lib/a2a/client.py`. Day 4 adds SSE streaming. Day 5 wires JWT auth.
Day 6 adds OTel traceparent propagation. Day 7 swaps the synthetic Task
for a real TaskSpec via `lib/a2a/task_bridge.py`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from ulid import ULID

logger = logging.getLogger(__name__)

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


# --- Handlers ------------------------------------------------------------


class _A2AUnsupportedOperation(Exception):
    """Raised by stub handlers for methods not yet implemented in this day.

    The dispatcher catches this and emits the A2A `-32004` error code.
    Carrying the method name avoids hard-coding it in the dispatcher.
    """

    def __init__(self, method_name: str) -> None:
        super().__init__(method_name)
        self.method_name = method_name


async def handle_send_message(params: dict[str, Any]) -> dict[str, Any]:
    """Day 2 handler: return a synthetic Task in SUBMITTED state.

    Spec contract (§7.6.1): params MUST include a `message` object with
    `parts` array. We don't validate the parts schema here (Day 7 does
    that via task_bridge); Day 2 just asserts the field exists so the
    "invalid params" path is exercised.
    """
    message = params.get("message")
    if not isinstance(message, dict) or "parts" not in message:
        raise ValueError("params.message.parts is required")
    task_id = f"task-{ULID()}"
    return {"id": task_id, "status": "SUBMITTED"}


async def _handle_unsupported_stream(_params: dict[str, Any]) -> None:
    raise _A2AUnsupportedOperation("message/stream")


async def _handle_unsupported_get(_params: dict[str, Any]) -> None:
    raise _A2AUnsupportedOperation("tasks/get")


async def _handle_unsupported_subscribe(_params: dict[str, Any]) -> None:
    raise _A2AUnsupportedOperation("tasks/subscribe")


async def _handle_unsupported_cancel(_params: dict[str, Any]) -> None:
    raise _A2AUnsupportedOperation("tasks/cancel")


# Dispatch table — method name → coroutine. New methods land here as the
# spike days roll forward; the dispatcher is method-agnostic.
_DISPATCH = {
    "message/send": handle_send_message,
    "message/stream": _handle_unsupported_stream,
    "tasks/get": _handle_unsupported_get,
    "tasks/subscribe": _handle_unsupported_subscribe,
    "tasks/cancel": _handle_unsupported_cancel,
}


# --- FastAPI app ---------------------------------------------------------

app = FastAPI(
    title="A2A Spike Agent",
    description=("JSON-RPC 2.0 / SSE agent-to-agent protocol — spike Day 2 minimal dispatch"),
    version="0.1.0-spike-day2",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/")
async def jsonrpc_dispatch(request: Request) -> JSONResponse:
    """JSON-RPC 2.0 dispatch endpoint.

    Pipeline: parse body → validate envelope → resolve method → invoke
    handler. Each stage maps cleanly to a JSON-RPC error code, with A2A
    method-level errors layered on top.
    """
    # Stage 1: parse the raw body as JSON.
    try:
        body_bytes = await request.body()
        envelope = json.loads(body_bytes)
    except json.JSONDecodeError as exc:
        return JSONResponse(
            content=_jsonrpc_error(None, JSONRPC_PARSE_ERROR, f"Parse error: {exc}")
        )

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
                f"Unknown method: {method}",
            )
        )

    # Stage 4: validate params shape and invoke the handler.
    params = envelope.get("params") or {}
    if not isinstance(params, dict):
        return JSONResponse(
            content=_jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "params must be an object")
        )

    try:
        result = await handler(params)
    except _A2AUnsupportedOperation as exc:
        return JSONResponse(
            content=_jsonrpc_error(
                req_id,
                A2A_UNSUPPORTED_OPERATION,
                f"Method '{exc.method_name}' not yet implemented in Day 2 spike",
            )
        )
    except ValueError as exc:
        return JSONResponse(
            content=_jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, f"Invalid params: {exc}")
        )
    except Exception as exc:
        # Use logger.exception (not .error) to capture the traceback. The
        # data field carries only the exception *type*, not the message —
        # message bodies can carry caller data and we don't want to echo
        # that back unbounded over the wire.
        logger.exception("a2a: unhandled exception in handler for method=%s", method)
        return JSONResponse(
            content=_jsonrpc_error(
                req_id,
                JSONRPC_INTERNAL_ERROR,
                "Internal server error",
                {"exception_type": type(exc).__name__},
            )
        )

    return JSONResponse(content=_jsonrpc_result(req_id, result))
