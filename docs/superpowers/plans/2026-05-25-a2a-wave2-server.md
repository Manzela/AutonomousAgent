# A2A Days 4-7 Server Integration (Wave 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire SSE streaming, JWT auth middleware, OTel context propagation, and TaskSpec bridging into `lib/a2a/server.py` — completing the Days 4-7 server-side deliverables from spike-plan.md.

**Architecture:** Three concerns layer onto the existing FastAPI dispatcher: (1) new dedicated HTTP routes (`POST /stream`, `POST /subscribe`) emit `StreamingResponse` with SSE frames — the JSON-RPC dispatcher keeps returning `-32004` for `message/stream`/`tasks/subscribe` as spec §5.4 allows; (2) a FastAPI `Depends` guard calls `verify_token` when `Authorization` is present, attaches the resulting `AgentIdentity` to `request.state.identity`; (3) `opentelemetry.propagate.extract` reads the `traceparent` header before handler dispatch, attaching the context so all child spans join the same trace tree. Day 7 replaces the synthetic `{"id": ..., "status": "SUBMITTED"}` return from `handle_send_message` with a real `TaskSpec` via `bridge_inbound_to_taskspec` + `bridge_taskspec_status_to_a2a`.

**Tech Stack:** `fastapi>=0.115`, `opentelemetry-sdk>=1.23`, `opentelemetry-api>=1.23`, `opentelemetry-exporter-in-memory` (test-only via `opentelemetry-sdk`), `httpx>=0.27` (TestClient), `pyjwt[crypto]>=2.9`, `cryptography>=43.0`, `python-ulid>=3.0`, `cachetools>=5.5` (all in `a2a` extra).

**Worktree:** `feat/a2a-day4-server`

**Prerequisites:** `feat/a2a-day5-auth` + `feat/a2a-day7-bridge` + `feat/a2a-day6-otel` all merged to `main` before this worktree is created. (Day 5/6/7 leaf modules must exist before server.py wires them.)

**Env setup:** `uv sync --extra a2a --extra dev`

**Test command:** `uv run pytest lib/a2a/tests/ -v`

---

## Worktree setup (run once)

```bash
git checkout main && git pull
git checkout -b feat/a2a-day4-server
uv sync --extra a2a --extra dev
uv run ruff format lib/a2a/server.py   # idempotent baseline — already formatted
```

Expected output of last command:
```
1 file left unchanged
```

---

## Current state of server.py (Day 2 baseline)

- `POST /` JSON-RPC dispatcher (`jsonrpc_dispatch`): handles `message/send` (returns synthetic `{"id": "task-...", "status": "SUBMITTED"}`); all other methods return `-32004`.
- `/health` returns `{"status": "ok"}`.
- No auth, no OTel, no SSE routes, no TaskSpec wiring.

---

## Task 1: Day 4 — SSE streaming routes (`POST /stream`, `POST /subscribe`)

**Files:**
- Modify: `lib/a2a/server.py`
- Create: `lib/a2a/tests/test_streaming.py`

### Step 1: Write failing tests first

Create `lib/a2a/tests/test_streaming.py`:

```python
"""Day 4 streaming acceptance tests — SSE routes on FastAPI TestClient.

Per spike-plan.md §Day 4:
  - POST /stream emits 3 SSE events: WORKING, artifact_added, COMPLETED.
  - POST /subscribe emits 3 SSE events in the same shape.
  - Content-Type is text/event-stream.
  - JSON-RPC dispatcher still returns -32004 for message/stream and
    tasks/subscribe (polling clients — spec §5.4 allows this).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from lib.a2a.server import app

client = TestClient(app)


def _parse_sse_events(raw: bytes) -> list[dict]:
    """Parse `data: <json>\\n\\n` SSE frames into a list of dicts."""
    events = []
    for chunk in raw.split(b"\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith(b"data: "):
            payload = chunk[len(b"data: "):]
            events.append(json.loads(payload))
    return events


# ---------------------------------------------------------------------------
# POST /stream
# ---------------------------------------------------------------------------


def test_stream_route_content_type_is_event_stream() -> None:
    """POST /stream must respond with text/event-stream content type."""
    with client.stream(
        "POST",
        "/stream",
        json={"message": {"role": "USER", "parts": [{"text": "hi"}]}},
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]


def test_stream_route_emits_three_events_in_order() -> None:
    """POST /stream must emit exactly 3 SSE events: WORKING, artifact_added, COMPLETED."""
    with client.stream(
        "POST",
        "/stream",
        json={"message": {"role": "USER", "parts": [{"text": "hi"}]}},
    ) as resp:
        body = resp.read()

    events = _parse_sse_events(body)
    assert len(events) == 3, f"expected 3 SSE events, got {len(events)}: {events}"
    assert events[0] == {"status": "WORKING"}
    assert events[1] == {"artifact_added": True}
    assert events[2] == {"status": "COMPLETED"}


# ---------------------------------------------------------------------------
# POST /subscribe
# ---------------------------------------------------------------------------


def test_subscribe_route_content_type_is_event_stream() -> None:
    """POST /subscribe must respond with text/event-stream."""
    with client.stream("POST", "/subscribe", json={"id": "task-abc"}) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]


def test_subscribe_route_emits_three_events_in_order() -> None:
    """POST /subscribe must emit 3 SSE events: WORKING, artifact_added, COMPLETED."""
    with client.stream("POST", "/subscribe", json={"id": "task-abc"}) as resp:
        body = resp.read()

    events = _parse_sse_events(body)
    assert len(events) == 3, f"expected 3 SSE events, got {len(events)}: {events}"
    assert events[0] == {"status": "WORKING"}
    assert events[1] == {"artifact_added": True}
    assert events[2] == {"status": "COMPLETED"}


# ---------------------------------------------------------------------------
# JSON-RPC dispatcher still returns -32004 for streaming methods (spec §5.4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["message/stream", "tasks/subscribe"])
def test_jsonrpc_dispatcher_still_returns_unsupported_for_streaming(method: str) -> None:
    """Polling clients hitting POST / for streaming methods get -32004 per spec §5.4."""
    resp = client.post(
        "/",
        json={"jsonrpc": "2.0", "id": 1, "method": method},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32004
```

Run to confirm failures:
```bash
uv run pytest lib/a2a/tests/test_streaming.py -v 2>&1 | tail -20
```

Expected: `FAILED` on all `test_stream_*` and `test_subscribe_*` tests; `PASSED` on the `-32004` parametrize (it already works from Day 2).

### Step 2: Implement SSE handlers and routes in server.py

Add to `lib/a2a/server.py` after the existing imports:

```python
import asyncio
from fastapi.responses import StreamingResponse
```

Add the two handler functions before the `_DISPATCH` table:

```python
async def handle_stream_message(params: dict[str, Any]) -> StreamingResponse:
    """Day 4 handler: emit 3 SSE events for message/stream.

    Returned directly from POST /stream (not via JSON-RPC dispatcher).
    Content-Type: text/event-stream per A2A spec §5.3.
    """

    async def _event_generator() -> Any:
        events = [
            {"status": "WORKING"},
            {"artifact_added": True},
            {"status": "COMPLETED"},
        ]
        for evt in events:
            yield f"data: {json.dumps(evt)}\n\n"
            await asyncio.sleep(0)  # allow the event loop to flush

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def handle_subscribe_task(params: dict[str, Any]) -> StreamingResponse:
    """Day 4 handler: emit 3 SSE events for tasks/subscribe.

    Returned directly from POST /subscribe (not via JSON-RPC dispatcher).
    """

    async def _event_generator() -> Any:
        events = [
            {"status": "WORKING"},
            {"artifact_added": True},
            {"status": "COMPLETED"},
        ]
        for evt in events:
            yield f"data: {json.dumps(evt)}\n\n"
            await asyncio.sleep(0)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

Add the two dedicated routes after the existing `POST /` route:

```python
@app.post("/stream")
async def stream_endpoint(request: Request) -> StreamingResponse:
    """POST /stream — SSE streaming for message/stream (Day 4).

    Not JSON-RPC wrapped. StreamingResponse cannot be returned from
    jsonrpc_dispatch (which must return JSONResponse). Documented in
    spike-plan.md §Day 4 as acceptable for the spike.
    """
    body_bytes = await request.body()
    try:
        params = json.loads(body_bytes) if body_bytes else {}
    except json.JSONDecodeError:
        params = {}
    return await handle_stream_message(params)


@app.post("/subscribe")
async def subscribe_endpoint(request: Request) -> StreamingResponse:
    """POST /subscribe — SSE streaming for tasks/subscribe (Day 4)."""
    body_bytes = await request.body()
    try:
        params = json.loads(body_bytes) if body_bytes else {}
    except json.JSONDecodeError:
        params = {}
    return await handle_subscribe_task(params)
```

- [ ] Apply the edits above to `lib/a2a/server.py`.

### Step 3: Run the streaming tests

```bash
uv run pytest lib/a2a/tests/test_streaming.py -v
```

Expected output (all 6 green):
```
PASSED lib/a2a/tests/test_streaming.py::test_stream_route_content_type_is_event_stream
PASSED lib/a2a/tests/test_streaming.py::test_stream_route_emits_three_events_in_order
PASSED lib/a2a/tests/test_streaming.py::test_subscribe_route_content_type_is_event_stream
PASSED lib/a2a/tests/test_streaming.py::test_subscribe_route_emits_three_events_in_order
PASSED lib/a2a/tests/test_streaming.py::test_jsonrpc_dispatcher_still_returns_unsupported_for_streaming[message/stream]
PASSED lib/a2a/tests/test_streaming.py::test_jsonrpc_dispatcher_still_returns_unsupported_for_streaming[tasks/subscribe]

6 passed in 0.XXs
```

Full regression:
```bash
uv run pytest lib/a2a/tests/ -v 2>&1 | tail -10
```

Expected: all previously passing tests still green, 6 new ones added.

- [ ] Confirm all tests green before proceeding to Task 2.

---

## Task 2: Day 5 — JWT middleware as FastAPI `Depends`

**Files:**
- Modify: `lib/a2a/server.py`
- Create: `lib/a2a/tests/test_jwt_middleware.py`

**Precondition:** `lib/a2a/auth.py` is implemented (from `feat/a2a-day5-auth`, merged to main before this worktree was created). It exports `verify_token(jwt_str: str) -> AgentIdentity` and the `AgentIdentity` dataclass.

### Step 1: Write failing tests

Create `lib/a2a/tests/test_jwt_middleware.py`:

```python
"""Day 5 JWT middleware tests — FastAPI Depends guard on POST /.

Per spike-plan.md §Day 5 acceptance:
  - Request with no Authorization header: passes (allow-all without token).
  - Request with valid Bearer token: verify_token called; identity attached to
    request.state.identity; handler receives it.
  - Request with invalid Bearer token: returns JSON-RPC -32600 (InvalidRequest)
    with message "Invalid or expired token".

We mock verify_token to avoid real GCP signJwt calls in CI.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from lib.a2a.server import app

client = TestClient(app)

_FAKE_TOKEN = "Bearer eyJfake.token.here"

_FAKE_IDENTITY = MagicMock()
_FAKE_IDENTITY.sub = "agent-canary-spike@autonomous-agent-2026.iam.gserviceaccount.com"


def test_no_auth_header_is_allowed() -> None:
    """No Authorization header → request passes; identity is None."""
    resp = client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {"message": {"role": "USER", "parts": [{"text": "hi"}]}},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" not in body
    assert body["result"]["status"] == "SUBMITTED"


def test_valid_bearer_token_is_accepted() -> None:
    """Valid Bearer token → verify_token called once; request proceeds normally."""
    with patch("lib.a2a.server.verify_token", return_value=_FAKE_IDENTITY) as mock_vt:
        resp = client.post(
            "/",
            headers={"Authorization": _FAKE_TOKEN},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "message/send",
                "params": {"message": {"role": "USER", "parts": [{"text": "hi"}]}},
            },
        )
    assert resp.status_code == 200
    mock_vt.assert_called_once()
    body = resp.json()
    assert "error" not in body


def test_invalid_bearer_token_returns_32600() -> None:
    """Invalid Bearer token → JSON-RPC -32600 InvalidRequest returned."""
    with patch("lib.a2a.server.verify_token", side_effect=ValueError("bad token")):
        resp = client.post(
            "/",
            headers={"Authorization": "Bearer this.is.invalid"},
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "message/send",
                "params": {"message": {"role": "USER", "parts": [{"text": "hi"}]}},
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32600
    assert "Invalid or expired token" in body["error"]["message"]


def test_malformed_authorization_header_returns_32600() -> None:
    """Authorization header that is not 'Bearer <token>' shape → -32600."""
    with patch("lib.a2a.server.verify_token", side_effect=ValueError("bad header")):
        resp = client.post(
            "/",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "message/send",
                "params": {"message": {"role": "USER", "parts": [{"text": "hi"}]}},
            },
        )
    # Basic auth is not the expected scheme; verify_token raises ValueError
    # because the token string is garbage. We still expect -32600.
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == -32600
```

Run to confirm failures:
```bash
uv run pytest lib/a2a/tests/test_jwt_middleware.py -v 2>&1 | tail -20
```

Expected: `FAILED` on `test_valid_bearer_token_is_accepted`, `test_invalid_bearer_token_returns_32600`, `test_malformed_authorization_header_returns_32600` (verify_token not yet imported in server.py). `test_no_auth_header_is_allowed` passes (no change needed).

### Step 2: Wire verify_token as FastAPI Depends in server.py

Add to the imports block at the top of `lib/a2a/server.py`:

```python
from fastapi import Depends

# Day 5: imported lazily to avoid circular import if auth.py imports server.py.
# verify_token is only called when Authorization header is present.
from lib.a2a.auth import AgentIdentity, verify_token
```

Add the dependency function and wire it into `jsonrpc_dispatch`:

```python
async def _jwt_guard(request: Request) -> AgentIdentity | None:
    """FastAPI dependency: verify JWT if Authorization header is present.

    - No header → returns None (allow-all; no auth enforced in spike).
    - Valid Bearer token → returns AgentIdentity; attached to request.state.identity.
    - Invalid/expired token → raises HTTPException that FastAPI converts to JSON.
      We instead return a JSONResponse directly so the error stays JSON-RPC shaped.

    Note: returning a JSONResponse from a Depends is a FastAPI anti-pattern.
    The correct approach is raising HTTPException. We bypass that here because
    A2A errors must be JSON-RPC 2.0 shaped (not HTTP error pages), and the spike
    is not using FastAPI exception handlers yet (Day 7 wires a proper handler).
    The `_jwt_guard` dependency attaches identity to request.state.identity directly;
    `jsonrpc_dispatch` checks for the sentinel early-exit response.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header:
        request.state.identity = None
        return None

    # Strip "Bearer " prefix; pass remainder to verify_token.
    token_str = auth_header.removeprefix("Bearer ").strip()
    try:
        identity = verify_token(token_str)
    except Exception:
        # Attach a sentinel so jsonrpc_dispatch knows to short-circuit.
        request.state.identity = None
        request.state.jwt_error = True
        return None

    request.state.identity = identity
    return identity
```

Modify the `jsonrpc_dispatch` signature and add an early-exit check:

```python
@app.post("/")
async def jsonrpc_dispatch(
    request: Request,
    _identity: AgentIdentity | None = Depends(_jwt_guard),
) -> JSONResponse:
    # Day 5: short-circuit on JWT error before parsing the body.
    if getattr(request.state, "jwt_error", False):
        return JSONResponse(
            content=_jsonrpc_error(
                None,
                JSONRPC_INVALID_REQUEST,
                "Invalid or expired token",
            )
        )
    # ... rest of existing dispatch pipeline unchanged ...
```

- [ ] Apply the edits above to `lib/a2a/server.py`.

### Step 3: Run JWT tests

```bash
uv run pytest lib/a2a/tests/test_jwt_middleware.py -v
```

Expected output (all 4 green):
```
PASSED lib/a2a/tests/test_jwt_middleware.py::test_no_auth_header_is_allowed
PASSED lib/a2a/tests/test_jwt_middleware.py::test_valid_bearer_token_is_accepted
PASSED lib/a2a/tests/test_jwt_middleware.py::test_invalid_bearer_token_returns_32600
PASSED lib/a2a/tests/test_jwt_middleware.py::test_malformed_authorization_header_returns_32600

4 passed in 0.XXs
```

Full regression:
```bash
uv run pytest lib/a2a/tests/ -v 2>&1 | tail -10
```

- [ ] Confirm all tests green before proceeding to Task 3.

---

## Task 3: Day 6 — OTel traceparent propagation in dispatcher and SSE routes

**Files:**
- Modify: `lib/a2a/server.py`
- Create: `lib/a2a/tests/test_otel_propagation.py`

**Precondition:** `opentelemetry-sdk` and `opentelemetry-api` are installed (verify: `uv run python -c "import opentelemetry.sdk.trace; print('ok')"`).

### Step 1: Write failing tests

Create `lib/a2a/tests/test_otel_propagation.py`:

```python
"""Day 6 OTel traceparent propagation tests.

Per spike-plan.md §Day 6:
  - POST / with a W3C traceparent header → the resulting server span joins
    the same trace_id as the inbound header.
  - POST /stream with a traceparent header → same trace_id propagated into
    the SSE handler span.
  - We use InMemorySpanExporter to assert trace_id matches without hitting
    Cloud Trace.
"""
from __future__ import annotations

from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import pytest


@pytest.fixture(autouse=True)
def _reset_otel_provider():
    """Install a fresh InMemorySpanExporter for each test, restore after."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    exporter.clear()


def test_jsonrpc_dispatch_extracts_traceparent(_reset_otel_provider):
    """POST / with W3C traceparent → server span joins the inbound trace tree."""
    from lib.a2a.server import app
    client = TestClient(app)

    # Version 00, 32-char trace id, 16-char span id, flags 01
    trace_id_hex = "4bf92f3577b34da6a3ce929d0e0e4736"
    parent_span_hex = "00f067aa0ba902b7"
    traceparent = f"00-{trace_id_hex}-{parent_span_hex}-01"

    resp = client.post(
        "/",
        headers={"traceparent": traceparent},
        json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "message/send",
            "params": {"message": {"role": "USER", "parts": [{"text": "otel test"}]}},
        },
    )
    assert resp.status_code == 200
    assert "error" not in resp.json()

    spans = _reset_otel_provider.get_finished_spans()
    assert len(spans) >= 1, "at least one span must be recorded"
    # The span's trace_id must match the inbound traceparent.
    expected_trace_id = int(trace_id_hex, 16)
    server_span = spans[-1]
    assert server_span.context.trace_id == expected_trace_id, (
        f"trace_id mismatch: expected {expected_trace_id:#034x}, "
        f"got {server_span.context.trace_id:#034x}"
    )


def test_stream_route_extracts_traceparent(_reset_otel_provider):
    """POST /stream with W3C traceparent → SSE handler span joins the trace."""
    from lib.a2a.server import app
    client = TestClient(app)

    trace_id_hex = "1234567890abcdef1234567890abcdef"  # pragma: allowlist secret
    parent_span_hex = "fedcba0987654321"  # pragma: allowlist secret
    traceparent = f"00-{trace_id_hex}-{parent_span_hex}-01"

    with client.stream(
        "POST",
        "/stream",
        headers={"traceparent": traceparent},
        json={"message": {"role": "USER", "parts": [{"text": "hi"}]}},
    ) as resp:
        resp.read()
    assert resp.status_code == 200

    spans = _reset_otel_provider.get_finished_spans()
    assert len(spans) >= 1
    expected_trace_id = int(trace_id_hex, 16)
    server_span = spans[-1]
    assert server_span.context.trace_id == expected_trace_id
```

Run to confirm failures:
```bash
uv run pytest lib/a2a/tests/test_otel_propagation.py -v 2>&1 | tail -20
```

Expected: both tests `FAILED` — no OTel extraction wired yet.

### Step 2: Wire OTel extraction in server.py

Add to the imports block:

```python
from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.propagate import extract as otel_extract
```

Add a helper for starting a server span inside the extracted context:

```python
_tracer = otel_trace.get_tracer("lib.a2a.server", "0.1.0-spike")


def _attach_inbound_context(request: Request) -> otel_context.Context:
    """Extract W3C traceparent/tracestate from request headers and attach.

    Returns the token from context.attach() — caller must call
    context.detach(token) after the span ends.
    """
    carrier = dict(request.headers)
    ctx = otel_extract(carrier)
    return otel_context.attach(ctx)
```

In `jsonrpc_dispatch`, add context extraction before the dispatch pipeline (after the jwt_error check):

```python
@app.post("/")
async def jsonrpc_dispatch(
    request: Request,
    _identity: AgentIdentity | None = Depends(_jwt_guard),
) -> JSONResponse:
    if getattr(request.state, "jwt_error", False):
        return JSONResponse(
            content=_jsonrpc_error(None, JSONRPC_INVALID_REQUEST, "Invalid or expired token")
        )

    # Day 6: extract W3C traceparent and attach to OTel context.
    _ctx_token = _attach_inbound_context(request)
    with _tracer.start_as_current_span("a2a.server.dispatch") as span:
        try:
            result = await _jsonrpc_dispatch_inner(request)
        finally:
            otel_context.detach(_ctx_token)
    return result
```

Extract the existing dispatch logic into `_jsonrpc_dispatch_inner(request) -> JSONResponse` (private async function, verbatim move of the existing body of `jsonrpc_dispatch`). This keeps the span wrapping clean.

For the SSE routes, add context extraction at the top of each endpoint:

```python
@app.post("/stream")
async def stream_endpoint(request: Request) -> StreamingResponse:
    _ctx_token = _attach_inbound_context(request)
    with _tracer.start_as_current_span("a2a.server.stream"):
        body_bytes = await request.body()
        try:
            params = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            params = {}
        response = await handle_stream_message(params)
    otel_context.detach(_ctx_token)
    return response


@app.post("/subscribe")
async def subscribe_endpoint(request: Request) -> StreamingResponse:
    _ctx_token = _attach_inbound_context(request)
    with _tracer.start_as_current_span("a2a.server.subscribe"):
        body_bytes = await request.body()
        try:
            params = json.loads(body_bytes) if body_bytes else {}
        except json.JSONDecodeError:
            params = {}
        response = await handle_subscribe_task(params)
    otel_context.detach(_ctx_token)
    return response
```

- [ ] Apply the edits above to `lib/a2a/server.py`.

### Step 3: Run OTel tests

```bash
uv run pytest lib/a2a/tests/test_otel_propagation.py -v
```

Expected output (both green):
```
PASSED lib/a2a/tests/test_otel_propagation.py::test_jsonrpc_dispatch_extracts_traceparent
PASSED lib/a2a/tests/test_otel_propagation.py::test_stream_route_extracts_traceparent

2 passed in 0.XXs
```

Full regression:
```bash
uv run pytest lib/a2a/tests/ -v 2>&1 | tail -10
```

- [ ] Confirm all tests green before proceeding to Task 4.

---

## Task 4: Day 7 — TaskSpec wiring in `handle_send_message`

**Files:**
- Modify: `lib/a2a/server.py`
- Create: `lib/a2a/tests/test_taskspec_wiring.py`

**Precondition:** `lib/a2a/task_bridge.py` is implemented (from `feat/a2a-day7-bridge`, merged to main). It exports `bridge_inbound_to_taskspec(params, identity)` returning a `TaskSpec`-shaped object with `.id` and `bridge_taskspec_status_to_a2a(spec)` returning a string status.

### Step 1: Write failing tests

Create `lib/a2a/tests/test_taskspec_wiring.py`:

```python
"""Day 7 TaskSpec wiring tests — handle_send_message calls the bridge.

Per spike-plan.md §Day 7:
  - handle_send_message must call bridge_inbound_to_taskspec(params, identity).
  - Result id comes from spec.id, not a synthetic ULID.
  - Result status comes from bridge_taskspec_status_to_a2a(spec).
  - If no identity (no auth header), bridge is called with identity=None.
  - Bridge errors propagate as -32603 (InternalError).

We mock both bridge functions to avoid Hermes anchors dependency in CI.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from lib.a2a.server import app

client = TestClient(app)


@dataclass
class _FakeTaskSpec:
    id: str = "taskspec-real-001"


_FAKE_SPEC = _FakeTaskSpec()
_FAKE_STATUS = "SUBMITTED"


def test_send_message_returns_spec_id_and_bridged_status() -> None:
    """handle_send_message returns spec.id and bridge_taskspec_status_to_a2a result."""
    with (
        patch(
            "lib.a2a.server.bridge_inbound_to_taskspec",
            return_value=_FAKE_SPEC,
        ) as mock_bridge,
        patch(
            "lib.a2a.server.bridge_taskspec_status_to_a2a",
            return_value=_FAKE_STATUS,
        ) as mock_status,
    ):
        resp = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 20,
                "method": "message/send",
                "params": {
                    "message": {"role": "USER", "parts": [{"text": "bridge test"}]}
                },
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "error" not in body, f"unexpected error: {body.get('error')}"
    assert body["result"]["id"] == "taskspec-real-001"
    assert body["result"]["status"] == "SUBMITTED"
    mock_bridge.assert_called_once()
    mock_status.assert_called_once_with(_FAKE_SPEC)


def test_send_message_passes_none_identity_when_no_auth() -> None:
    """Without Authorization header, identity=None is passed to the bridge."""
    with (
        patch(
            "lib.a2a.server.bridge_inbound_to_taskspec",
            return_value=_FAKE_SPEC,
        ) as mock_bridge,
        patch("lib.a2a.server.bridge_taskspec_status_to_a2a", return_value="SUBMITTED"),
    ):
        client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "message/send",
                "params": {
                    "message": {"role": "USER", "parts": [{"text": "no auth"}]}
                },
            },
        )

    call_args = mock_bridge.call_args
    # Second positional arg or kwarg 'identity' must be None
    identity_arg = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("identity")
    assert identity_arg is None


def test_send_message_passes_identity_when_auth_present() -> None:
    """With a valid Authorization header, identity is passed to the bridge."""
    from unittest.mock import MagicMock as MM
    fake_identity = MM()
    fake_identity.sub = "agent-canary@autonomous-agent-2026.iam.gserviceaccount.com"

    with (
        patch("lib.a2a.server.verify_token", return_value=fake_identity),
        patch(
            "lib.a2a.server.bridge_inbound_to_taskspec",
            return_value=_FAKE_SPEC,
        ) as mock_bridge,
        patch("lib.a2a.server.bridge_taskspec_status_to_a2a", return_value="SUBMITTED"),
    ):
        client.post(
            "/",
            headers={"Authorization": "Bearer fake.jwt.token"},
            json={
                "jsonrpc": "2.0",
                "id": 22,
                "method": "message/send",
                "params": {
                    "message": {"role": "USER", "parts": [{"text": "with auth"}]}
                },
            },
        )

    call_args = mock_bridge.call_args
    identity_arg = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("identity")
    assert identity_arg is fake_identity


def test_bridge_exception_maps_to_32603() -> None:
    """If bridge_inbound_to_taskspec raises, -32603 InternalError is returned."""
    with (
        patch(
            "lib.a2a.server.bridge_inbound_to_taskspec",
            side_effect=RuntimeError("anchors unavailable"),
        ),
    ):
        resp = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 23,
                "method": "message/send",
                "params": {
                    "message": {"role": "USER", "parts": [{"text": "bridge down"}]}
                },
            },
        )

    body = resp.json()
    assert body["error"]["code"] == -32603
```

Run to confirm failures:
```bash
uv run pytest lib/a2a/tests/test_taskspec_wiring.py -v 2>&1 | tail -20
```

Expected: all 4 `FAILED` — bridge not yet imported or called.

### Step 2: Wire TaskSpec bridge in server.py

Add to the imports block:

```python
from lib.a2a.task_bridge import bridge_inbound_to_taskspec, bridge_taskspec_status_to_a2a
```

Replace the `handle_send_message` function body:

```python
async def handle_send_message(
    params: dict[str, Any],
    identity: Any | None = None,
) -> dict[str, Any]:
    """Day 7 handler: create a TaskSpec via the bridge instead of returning a synthetic Task.

    Spec contract (§7.6.1): params MUST include a `message` object with `parts`.
    The bridge creates a real TaskSpec (via lib.anchors) and returns its id + status.
    identity comes from request.state.identity set by the Day 5 JWT guard.
    """
    message = params.get("message")
    if not isinstance(message, dict) or "parts" not in message:
        raise ValueError("params.message.parts is required")

    spec = bridge_inbound_to_taskspec(params, identity)
    status = bridge_taskspec_status_to_a2a(spec)
    return {"id": spec.id, "status": status}
```

In `_jsonrpc_dispatch_inner` (the extracted inner function from Task 3), change the `handler(params)` call for `message/send` to pass identity:

```python
    # Stage 4: validate params shape and invoke the handler.
    params = envelope.get("params") or {}
    if not isinstance(params, dict):
        return JSONResponse(
            content=_jsonrpc_error(req_id, JSONRPC_INVALID_PARAMS, "params must be an object")
        )

    try:
        # Day 7: pass identity to handle_send_message; other handlers don't need it.
        if method == "message/send":
            identity = getattr(request.state, "identity", None)
            result = await handler(params, identity)
        else:
            result = await handler(params)
    except _A2AUnsupportedOperation as exc:
        ...
```

- [ ] Apply the edits above to `lib/a2a/server.py`.

### Step 3: Run TaskSpec tests

```bash
uv run pytest lib/a2a/tests/test_taskspec_wiring.py -v
```

Expected output (all 4 green):
```
PASSED lib/a2a/tests/test_taskspec_wiring.py::test_send_message_returns_spec_id_and_bridged_status
PASSED lib/a2a/tests/test_taskspec_wiring.py::test_send_message_passes_none_identity_when_no_auth
PASSED lib/a2a/tests/test_taskspec_wiring.py::test_send_message_passes_identity_when_auth_present
PASSED lib/a2a/tests/test_taskspec_wiring.py::test_bridge_exception_maps_to_32603

4 passed in 0.XXs
```

Full regression:
```bash
uv run pytest lib/a2a/tests/ -v 2>&1 | tail -15
```

- [ ] Confirm all tests green before proceeding to Task 5.

---

## Task 5: Integration test + final regression + ruff format + PR

**Files:**
- Create: `lib/a2a/tests/test_server_integration.py`
- No new source files.

### Step 1: Write integration test exercising all four wires together

Create `lib/a2a/tests/test_server_integration.py`:

```python
"""Days 4-7 integration test — all four wires active simultaneously.

Exercises the combined path: JWT guard → OTel extraction → TaskSpec bridge
→ SSE streaming, all in one TestClient session. Uses the same mock strategy
as the individual task tests (mock verify_token + bridge functions).

This test is NOT a replacement for the Day 10 e2e demo — it only validates
that the wires compose correctly in the same FastAPI process without
interference.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from lib.a2a.server import app

client = TestClient(app)


@dataclass
class _FakeSpec:
    id: str = "integration-spec-001"


@pytest.fixture(autouse=True)
def _fresh_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    exporter.clear()


def test_full_message_send_with_jwt_otel_bridge(_fresh_exporter) -> None:
    """message/send with JWT + traceparent → bridge called, trace_id propagated."""
    trace_id_hex = "aabbccddeeff00112233445566778899"
    traceparent = f"00-{trace_id_hex}-0011223344556677-01"

    fake_identity = MagicMock()
    fake_identity.sub = "agent-canary@autonomous-agent-2026.iam.gserviceaccount.com"

    with (
        patch("lib.a2a.server.verify_token", return_value=fake_identity),
        patch("lib.a2a.server.bridge_inbound_to_taskspec", return_value=_FakeSpec()) as mock_bridge,
        patch("lib.a2a.server.bridge_taskspec_status_to_a2a", return_value="SUBMITTED"),
    ):
        resp = client.post(
            "/",
            headers={
                "Authorization": "Bearer fake.jwt",
                "traceparent": traceparent,
            },
            json={
                "jsonrpc": "2.0",
                "id": 99,
                "method": "message/send",
                "params": {"message": {"role": "USER", "parts": [{"text": "integration"}]}},
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert "error" not in body
    assert body["result"]["id"] == "integration-spec-001"
    assert body["result"]["status"] == "SUBMITTED"

    # Bridge was called with the fake identity.
    call_identity = mock_bridge.call_args.args[1] if len(mock_bridge.call_args.args) > 1 \
        else mock_bridge.call_args.kwargs.get("identity")
    assert call_identity is fake_identity

    # OTel: at least one span with the correct trace_id.
    spans = _fresh_exporter.get_finished_spans()
    assert len(spans) >= 1
    expected_trace_id = int(trace_id_hex, 16)
    assert any(s.context.trace_id == expected_trace_id for s in spans), \
        f"no span with trace_id {expected_trace_id:#034x}: {[s.context.trace_id for s in spans]}"


def test_sse_stream_with_traceparent_and_three_events(_fresh_exporter) -> None:
    """POST /stream with traceparent → 3 SSE events received, span recorded."""
    trace_id_hex = "cafebabe00000000cafebabe00000000"
    traceparent = f"00-{trace_id_hex}-cafebabe00000001-01"

    with client.stream(
        "POST",
        "/stream",
        headers={"traceparent": traceparent},
        json={"message": {"role": "USER", "parts": [{"text": "stream integration"}]}},
    ) as resp:
        body = resp.read()

    assert resp.status_code == 200
    events = [
        json.loads(chunk.strip()[len(b"data: "):])
        for chunk in body.split(b"\n\n")
        if chunk.strip().startswith(b"data: ")
    ]
    assert len(events) == 3
    assert events[0] == {"status": "WORKING"}
    assert events[2] == {"status": "COMPLETED"}

    spans = _fresh_exporter.get_finished_spans()
    assert len(spans) >= 1
    expected_trace_id = int(trace_id_hex, 16)
    assert any(s.context.trace_id == expected_trace_id for s in spans)
```

### Step 2: Run integration tests + full suite

```bash
uv run pytest lib/a2a/tests/test_server_integration.py -v
```

Expected output (both green):
```
PASSED lib/a2a/tests/test_server_integration.py::test_full_message_send_with_jwt_otel_bridge
PASSED lib/a2a/tests/test_server_integration.py::test_sse_stream_with_traceparent_and_three_events

2 passed in 0.XXs
```

Final full suite:
```bash
uv run pytest lib/a2a/tests/ -v
```

Expected: all tests pass. Count should be at minimum:
- 7 existing Day 2 tests (test_server_dispatch.py) + client/plugin tests
- 6 new streaming tests
- 4 JWT middleware tests
- 2 OTel propagation tests
- 4 TaskSpec wiring tests
- 2 integration tests

### Step 3: Lint + format

```bash
uv run ruff format lib/a2a/server.py lib/a2a/tests/test_streaming.py \
    lib/a2a/tests/test_jwt_middleware.py lib/a2a/tests/test_otel_propagation.py \
    lib/a2a/tests/test_taskspec_wiring.py lib/a2a/tests/test_server_integration.py
uv run ruff check lib/a2a/ --fix
```

Expected: `All checks passed.`

### Step 4: Commit and push

```bash
git add lib/a2a/server.py \
    lib/a2a/tests/test_streaming.py \
    lib/a2a/tests/test_jwt_middleware.py \
    lib/a2a/tests/test_otel_propagation.py \
    lib/a2a/tests/test_taskspec_wiring.py \
    lib/a2a/tests/test_server_integration.py
git commit -m "feat(a2a): days 4-7 server — SSE streaming + JWT auth + OTel + TaskSpec wiring"
git push -u origin feat/a2a-day4-server
gh pr create \
    --title "feat(a2a): days 4-7 server — SSE streaming + JWT auth + OTel + TaskSpec wiring" \
    --body "Wires POST /stream + POST /subscribe (SSE), JWT verify_token Depends guard, W3C traceparent extraction via opentelemetry.propagate, and TaskSpec bridge into handle_send_message. All 4 daily acceptance gates from spike-plan.md §Days 4-7 are green. Regression: full lib/a2a/tests/ suite passes." \
    --base main
```

- [ ] PR created and CI green.
