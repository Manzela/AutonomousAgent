"""A2A canary peer — minimal echo+delay FastAPI for Day 9 e2e demo.

Implements the A2A JSON-RPC 2.0 protocol surface needed by the spike:
  POST /         — JSON-RPC dispatcher (message/send, tasks/get, tasks/cancel)
  POST /stream   — SSE streaming (message/stream)
  POST /subscribe — SSE streaming (tasks/subscribe)
  GET  /health   — liveness probe

Behavior: echo the inbound message back with a synthetic Task. SSE routes
emit 3 events with proper event:/id: fields and 0.5s delay to simulate
real agent processing.

Usage (via docker compose):
  docker compose -f deploy/docker-compose.canary.yml up -d
  curl -X POST http://localhost:9002/ -H "Content-Type: application/json" \\
    -d '{"jsonrpc":"2.0","id":1,"method":"message/send",
         "params":{"message":{"role":"USER","parts":[{"text":"ping"}]}}}'
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="A2A Canary Peer", version="0.1.0-spike")


def _result(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "agent": "canary"}


@app.post("/")
async def jsonrpc_dispatch(request: Request) -> JSONResponse:
    """JSON-RPC 2.0 dispatcher — handles message/send, tasks/get, tasks/cancel."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(content=_error(None, -32700, "Parse error"))

    req_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params") or {}

    if method == "message/send":
        task_id = f"canary-task-{uuid.uuid4()}"
        return JSONResponse(content=_result(req_id, {"id": task_id, "status": "SUBMITTED"}))

    if method == "tasks/get":
        task_id = params.get("id")
        if not task_id:
            return JSONResponse(content=_error(req_id, -32001, "Task not found"))
        return JSONResponse(content=_result(req_id, {"id": task_id, "status": "COMPLETED"}))

    if method == "tasks/cancel":
        task_id = params.get("id", "unknown")
        return JSONResponse(content=_result(req_id, {"id": task_id, "status": "CANCELED"}))

    # message/stream and tasks/subscribe are handled via dedicated SSE routes
    return JSONResponse(content=_error(req_id, -32004, f"Use /stream or /subscribe for {method}"))


async def _sse_events(request: Request, req_id: Any = 1) -> Any:
    """Emit 3 SSE frames with proper event:, id:, and JSON-RPC envelope."""
    events = [
        (
            "TaskStatusUpdateEvent",
            {"jsonrpc": "2.0", "id": req_id, "result": {"status": "WORKING"}},
        ),
        (
            "TaskArtifactUpdateEvent",
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"artifact": {"type": "text", "content": "canary echo"}},
            },
        ),
        (
            "TaskStatusUpdateEvent",
            {"jsonrpc": "2.0", "id": req_id, "result": {"status": "COMPLETED"}},
        ),
    ]
    yield "retry: 15000\n\n"
    for i, (event_type, data) in enumerate(events, start=1):
        if await request.is_disconnected():
            return
        yield f"id: {i}\nevent: {event_type}\ndata: {json.dumps(data)}\n\n"
        await asyncio.sleep(0.5)


async def _parse_req_id(request: Request) -> Any:
    """Extract the JSON-RPC request id from the inbound body, defaulting to 1."""
    try:
        body = await request.json()
        return body.get("id", 1)
    except Exception:
        return 1


@app.post("/stream")
async def stream_endpoint(request: Request) -> StreamingResponse:
    """SSE streaming for message/stream — emits 3 events with 0.5s delay."""
    req_id = await _parse_req_id(request)
    return StreamingResponse(
        _sse_events(request, req_id=req_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/subscribe")
async def subscribe_endpoint(request: Request) -> StreamingResponse:
    """SSE streaming for tasks/subscribe — same 3 events."""
    req_id = await _parse_req_id(request)
    return StreamingResponse(
        _sse_events(request, req_id=req_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("HERMES_A2A_PORT", "9001")),
        log_level="info",
    )
