# P-3 A2A Peer-Execution Dispatch — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans`.

**Goal:** Extend `AgentCapability` with a `peer_endpoint` field and implement `_execute_via_a2a()` so the orchestrator routes tasks to peer agents over the A2A JSON-RPC boundary instead of dispatching in-process.

**Architecture:** The dispatch layer lives in `app/core/orchestrator.py` as module-level async functions (`execute`, `_execute_via_a2a`, `_execute_local`) rather than class methods, because `SeedOrchestrator` has not yet been ported into `app/` (that is P-12/P-13). The branching logic is `capability.peer_endpoint is not None` → A2A path; otherwise → local `invoke()`. The `lib.a2a.client.send_message` import is lazy (inside the method body) to avoid circular imports and to respect the collision boundary — `lib/a2a/` is Claude Code's territory.

**Tech Stack:** Python 3.12, Pydantic v2, `asyncio`, `lib.a2a.client.send_message` (httpx + JSON-RPC 2.0), `pytest-asyncio`, `unittest.mock`.

---

## Non-Negotiable Constraints

| Rule | Detail |
|------|--------|
| Do NOT modify `lib/a2a/` | Call `lib.a2a.client.send_message` via lazy import, never touch the file |
| `peer_endpoint` defaults to `None` | Backward-compatible; existing capabilities without the field behave identically |
| Lazy import in `_execute_via_a2a` | `from lib.a2a.client import send_message` inside the method body — not at module top |
| Stage specific files only | Never `git add -A`; add each file by name |
| Branch | `feat/p3-a2a-peer-dispatch` |
| PR title | `feat(app): P-3 a2a peer-execution dispatch — route to peer via send_message` |
| No `lib/a2a/` diff | `git diff --name-only main \| grep "lib/a2a"` must return nothing |

---

## Pre-Flight Checklist

```bash
# Verify you are on the correct branch
git checkout feat/p3-a2a-peer-dispatch

# Confirm clean baseline — all existing tests pass before you write a line
.venv/bin/python -m pytest app/tests/ -q --no-header 2>&1 | tail -3
# Expected: XX passed in N.NNs  (no failures)
```

---

## Task 1: Extend `AgentCapability` with `peer_endpoint`

**File:** `app/core/schemas.py`
**What:** Add `peer_endpoint: str | None = None` to `AgentCapability` with a `@model_validator` that rejects non-HTTP(S) values.

### Step 1a — Write the failing test first

Add to `app/tests/test_peer_dispatch.py` (or create if absent):

```python
def test_agent_capability_peer_endpoint_validates_url():
    from app.core.schemas import AgentCapability
    from app.core.schemas import AgentID
    from pydantic import ValidationError

    # Valid http:// must be accepted
    cap = AgentCapability(
        agent_id=AgentID("t"),
        version="1.0.0",
        phase="draft",
        description="test capability for URL validator",
        peer_endpoint="http://peer:9001/",
    )
    assert cap.peer_endpoint == "http://peer:9001/"

    # Valid https:// must also be accepted
    cap2 = AgentCapability(
        agent_id=AgentID("t"),
        version="1.0.0",
        phase="draft",
        description="test capability for URL validator",
        peer_endpoint="https://secure-peer.example.com/",
    )
    assert cap2.peer_endpoint == "https://secure-peer.example.com/"

    # Non-HTTP URL must be rejected
    with pytest.raises(ValidationError):
        AgentCapability(
            agent_id=AgentID("t"),
            version="1.0.0",
            phase="draft",
            description="test capability for URL validator",
            peer_endpoint="not-a-url",
        )

    # ftp:// must be rejected
    with pytest.raises(ValidationError):
        AgentCapability(
            agent_id=AgentID("t"),
            version="1.0.0",
            phase="draft",
            description="test capability for URL validator",
            peer_endpoint="ftp://bad-peer:9001/",
        )


def test_peer_endpoint_none_is_default():
    from app.core.schemas import AgentCapability, AgentID

    cap = AgentCapability(
        agent_id=AgentID("legacy-agent"),
        version="0.1.0",
        phase="research",
        description="legacy agent without peer support",
    )
    assert cap.peer_endpoint is None
    assert cap.invoke is None
```

**Run command (expect FAIL before implementation):**

```bash
.venv/bin/python -m pytest app/tests/test_peer_dispatch.py::test_agent_capability_peer_endpoint_validates_url -v
```

**Expected before implementation:** `FAILED` with `AttributeError: 'AgentCapability' object has no attribute 'peer_endpoint'` or `ValidationError`.

### Step 1b — Implement

In `app/core/schemas.py`, locate the `AgentCapability` class (after `lifecycle` and `invoke` fields) and add:

```python
    # A2A peer-execution endpoint (P-3). None = local dispatch (default).
    # When set, _execute() routes tasks via lib.a2a.client.send_message
    # to the peer agent at this URL instead of running the agent module locally.
    peer_endpoint: Optional[str] = None

    @model_validator(mode="after")
    def _validate_peer_endpoint(self) -> "AgentCapability":
        if self.peer_endpoint is not None:
            if not self.peer_endpoint.startswith(("http://", "https://")):
                raise ValueError(
                    f"peer_endpoint must start with http:// or https://, "
                    f"got {self.peer_endpoint!r}"
                )
        return self
```

**Run command (expect PASS after implementation):**

```bash
.venv/bin/python -m pytest app/tests/test_peer_dispatch.py::test_agent_capability_peer_endpoint_validates_url app/tests/test_peer_dispatch.py::test_peer_endpoint_none_is_default -v
```

**Expected:** `2 passed`

### Step 1c — Commit

```bash
git add app/core/schemas.py app/tests/test_peer_dispatch.py
git commit -m "feat(schemas): add peer_endpoint field to AgentCapability with URL validator"
```

---

## Task 2: Implement `_execute_via_a2a()` in `app/core/orchestrator.py`

**File:** `app/core/orchestrator.py`
**What:** Add the A2A dispatch path. The public `execute()` function branches on `capability.peer_endpoint is not None`; when set, it calls `_execute_via_a2a()` which lazily imports and calls `lib.a2a.client.send_message`.

### Step 2a — Write the failing test first

Add to `app/tests/test_peer_dispatch.py`:

```python
@pytest.mark.asyncio
async def test_peer_capability_calls_send_message_with_correct_url():
    """A capability with peer_endpoint calls send_message with that URL."""
    from unittest.mock import AsyncMock, patch
    from app.core.orchestrator import execute
    from app.core.schemas import AgentCapability, AgentID, TaskRequest

    peer_url = "http://agent-canary:9001/"
    fake_task = {"id": "task-peer-001", "status": "SUBMITTED"}

    with patch("lib.a2a.client.send_message", new=AsyncMock(return_value=fake_task)) as mock_send:
        cap = AgentCapability(
            agent_id=AgentID("agent-test-01"),
            version="1.0.0",
            phase="draft",
            description="test capability for peer dispatch",
            peer_endpoint=peer_url,
        )
        req = TaskRequest(task_id="task-001", summary="compute delta-v for lunar transfer")
        result = await execute(req, cap)

    mock_send.assert_called_once()
    call_args = mock_send.call_args
    # First positional arg is peer_url
    assert call_args.args[0] == peer_url
    # Second positional arg is the message dict
    msg = call_args.args[1]
    assert msg["role"] == "USER"
    assert "compute delta-v" in msg["parts"][0]["text"]
```

**Run command (expect FAIL before implementation):**

```bash
.venv/bin/python -m pytest app/tests/test_peer_dispatch.py::test_peer_capability_calls_send_message_with_correct_url -v
```

**Expected before implementation:** `FAILED` — `ImportError: cannot import name 'execute' from 'app.core.orchestrator'` or `AssertionError`.

### Step 2b — Implement

Replace (or create) `app/core/orchestrator.py` with the full implementation. The module must contain:

1. `execute(request, capability, *, agent_identity, peer_timeout_s, local_timeout_s) -> ExecutionResult` — the public entry point that branches on `capability.peer_endpoint is not None`.
2. `_execute_via_a2a(request, capability, *, agent_identity, timeout_s) -> ExecutionResult` — the A2A dispatch path.
3. `_execute_local(request, capability, *, timeout_s) -> ExecutionResult` — the unchanged in-process path.
4. `_map_a2a_status(status: str) -> TaskStatus` — maps A2A status strings to `TaskStatus` enum values.

Full implementation:

```python
"""A2A peer-execution dispatch layer (P-3).

**Scope:** This module implements the A2A routing layer for P-3 only —
the ``execute()`` entry point that branches between local and peer dispatch.
It does NOT contain the full seed ``Orchestrator`` class (router, registry,
policy update loops, etc.). Those components are ported in subsequent
work items (P-1, P-12, P-13 per INTEGRATION.md).

Per 04-gcp-native-adapter-plan.md: the dispatch logic lives here as a
module-level function (not a class method) because the full orchestrator
class has not yet been ported to app/core/. When P-12/P-13 are implemented,
``execute()`` should be moved into ``Orchestrator._execute()`` and this module
becomes the implementation of that method.

See INTEGRATION.md §P-3 for acceptance criteria.

Collision boundary: this module calls ``lib.a2a.client.send_message`` but
does NOT modify ``lib/a2a/`` — that is Claude Code territory.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.core.schemas import (
    AgentCapability,
    ExecutionResult,
    TaskRequest,
    TaskStatus,
)

logger = logging.getLogger(__name__)

# Default timeout for A2A peer requests (seconds).
_DEFAULT_PEER_TIMEOUT_S = 30.0

# Default task timeout for local dispatch (seconds).
_DEFAULT_LOCAL_TIMEOUT_S = 60.0


async def execute(
    request: TaskRequest,
    capability: AgentCapability,
    *,
    agent_identity: Any = None,
    peer_timeout_s: float = _DEFAULT_PEER_TIMEOUT_S,
    local_timeout_s: float = _DEFAULT_LOCAL_TIMEOUT_S,
) -> ExecutionResult:
    """Dispatch a task to a capability — local or across the A2A boundary.

    If ``capability.peer_endpoint`` is set, routes via A2A ``send_message``.
    Otherwise executes the agent module in-process via ``cap.invoke()``.

    Args:
        request: The task to execute.
        capability: The chosen expert's descriptor.
        agent_identity: SA identity for outbound JWT minting (Day 5+).
            Pass ``None`` for the pre-production posture per INTEGRATION.md §P-3
            (fail-open auth).
        peer_timeout_s: httpx timeout for A2A peer requests.
        local_timeout_s: asyncio timeout for local invoke().

    Returns:
        ``ExecutionResult`` with proper status, timing, and cost accounting.
    """
    if capability.peer_endpoint is not None:
        return await _execute_via_a2a(
            request,
            capability,
            agent_identity=agent_identity,
            timeout_s=peer_timeout_s,
        )
    return await _execute_local(
        request,
        capability,
        timeout_s=local_timeout_s,
    )


async def _execute_via_a2a(
    request: TaskRequest,
    capability: AgentCapability,
    *,
    agent_identity: Any = None,
    timeout_s: float = _DEFAULT_PEER_TIMEOUT_S,
) -> ExecutionResult:
    """Route task to peer agent via A2A JSON-RPC ``send_message``.

    Constructs an A2A Message from the orchestrator's ``TaskRequest``,
    sends to ``capability.peer_endpoint``, and wraps the peer's Task
    response into an ``ExecutionResult``.

    The peer's SA email is looked up from ``config/a2a/peers.yaml`` by URL;
    auth is fail-open if the peer is not configured — pre-production posture
    per INTEGRATION.md §P-3.
    """
    # Lazy import to avoid circular dependencies and to respect the collision
    # boundary: lib.a2a.client is Claude Code territory — we call it, never modify.
    from lib.a2a.client import send_message  # type: ignore[import-untyped]

    assert capability.peer_endpoint is not None  # Narrowing for type checker

    # Build the A2A Message from the orchestrator request.
    # Spec §7.6.1 requires at least `parts` with one text entry.
    intent_text = request.summary or f"task:{request.task_id}"
    message: dict[str, Any] = {
        "role": "USER",
        "parts": [{"text": intent_text}],
        "metadata": {
            "orchestrator_task_id": request.task_id,
            "phase": request.phase,
            "project_id": str(request.project_id) if request.project_id else None,
        },
    }

    t0 = time.monotonic()
    try:
        task = await send_message(
            capability.peer_endpoint,
            message,
            agent_identity=agent_identity,
            timeout=timeout_s,
        )
        duration = time.monotonic() - t0

        logger.info(
            "a2a peer dispatch: task_id=%s peer=%s status=%s duration=%.3fs",
            request.task_id,
            capability.peer_endpoint,
            task.get("status", "UNKNOWN"),
            duration,
        )

        return ExecutionResult(
            task_id=request.task_id,
            status=_map_a2a_status(task.get("status", "UNKNOWN")),
            agent_id=capability.agent_id,
            output=task,
            error=None,
            duration_s=duration,
            cost_usd=0.0,  # Peer costs tracked on the peer side
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )

    except asyncio.TimeoutError:
        duration = time.monotonic() - t0
        logger.warning(
            "a2a peer dispatch timeout: task_id=%s peer=%s timeout=%.1fs",
            request.task_id,
            capability.peer_endpoint,
            timeout_s,
        )
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error=f"a2a_peer_timeout: {capability.peer_endpoint} after {timeout_s}s",
            duration_s=duration,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )

    except asyncio.CancelledError:
        raise  # Never swallow cancellation

    except Exception as exc:
        duration = time.monotonic() - t0
        # Detect A2AError subclasses to surface the error code for operators.
        try:
            from lib.a2a.client import A2AError  # type: ignore[import-untyped]

            is_a2a = isinstance(exc, A2AError)
        except ImportError:
            is_a2a = False

        if is_a2a:
            logger.error(
                "a2a peer dispatch protocol error: task_id=%s peer=%s code=%d msg=%s",
                request.task_id,
                capability.peer_endpoint,
                exc.code,  # type: ignore[union-attr]
                exc,
            )
            error_str = f"a2a_peer_error: code={exc.code} msg={exc}"  # type: ignore[union-attr]
        else:
            logger.error(
                "a2a peer dispatch error: task_id=%s peer=%s error=%r",
                request.task_id,
                capability.peer_endpoint,
                exc,
            )
            error_str = f"a2a_peer_error: {exc!r}"

        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error=error_str,
            duration_s=duration,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )


async def _execute_local(
    request: TaskRequest,
    capability: AgentCapability,
    *,
    timeout_s: float = _DEFAULT_LOCAL_TIMEOUT_S,
) -> ExecutionResult:
    """Execute task via the capability's in-process ``invoke`` coroutine.

    This is the unchanged local dispatch path from the seed orchestrator.
    If ``invoke`` is None and ``peer_endpoint`` is also None, the capability
    is unusable — we return FAILED.
    """
    t0 = time.monotonic()

    if capability.invoke is None:
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error="capability_has_no_invoke_and_no_peer",
            duration_s=0.0,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )

    try:
        result = await asyncio.wait_for(
            capability.invoke(request),
            timeout=min(timeout_s, request.deadline_s or 60.0),
        )
        if not isinstance(result, ExecutionResult):
            return ExecutionResult(
                task_id=request.task_id,
                status=TaskStatus.FAILED,
                agent_id=capability.agent_id,
                output=None,
                error="invoke_returned_non_execution_result",
                duration_s=time.monotonic() - t0,
                cost_usd=0.0,
                tokens_in=0,
                tokens_out=0,
                artifacts=(),
            )
        return result

    except asyncio.TimeoutError:
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error="task_timeout",
            duration_s=time.monotonic() - t0,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )

    except asyncio.CancelledError:
        raise

    except Exception as exc:
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error=f"invoke_exception: {exc!r}",
            duration_s=time.monotonic() - t0,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )


def _map_a2a_status(status: str) -> TaskStatus:
    """Map an A2A Task status string to the orchestrator's TaskStatus enum.

    A2A uses: SUBMITTED, WORKING, INPUT_REQUIRED, COMPLETED, CANCELED, FAILED.
    The orchestrator uses: PENDING, INFLIGHT, COMPLETED, FAILED, REFUSED.

    INPUT_REQUIRED maps to FAILED because the peer is blocked waiting for
    human input that the orchestrator cannot provide.  Operators are warned
    via a log message so the blockage is surfaced rather than silently
    treated as in-progress work.
    """
    if status == "INPUT_REQUIRED":
        logger.warning(
            "a2a: peer returned INPUT_REQUIRED — task is blocked waiting for human input; "
            "orchestrator has no mechanism to unblock this task (treating as FAILED)"
        )
    mapping = {
        "SUBMITTED": TaskStatus.INFLIGHT,
        "WORKING": TaskStatus.INFLIGHT,
        "INPUT_REQUIRED": TaskStatus.FAILED,
        "COMPLETED": TaskStatus.COMPLETED,
        "CANCELED": TaskStatus.FAILED,
        "FAILED": TaskStatus.FAILED,
    }
    return mapping.get(status, TaskStatus.FAILED)
```

**Run command (expect PASS after implementation):**

```bash
.venv/bin/python -m pytest app/tests/test_peer_dispatch.py::test_peer_capability_calls_send_message_with_correct_url -v
```

**Expected:** `1 passed`

### Step 2c — Commit

```bash
git add app/core/orchestrator.py
git commit -m "feat(orchestrator): implement _execute_via_a2a peer dispatch via A2A boundary"
```

---

## Task 3: Test Message Shape

**What:** Verify the A2A message sent to peers contains `role=USER`, `parts[0].text` contains the intent, and `metadata` includes `orchestrator_task_id` and `phase`.

### Step 3a — Write the failing test

Add to `app/tests/test_peer_dispatch.py`:

```python
@pytest.mark.asyncio
async def test_peer_dispatch_sends_correct_message_shape():
    """The A2A message sent to the peer contains required fields."""
    from unittest.mock import patch
    from app.core.orchestrator import execute
    from app.core.schemas import AgentCapability, AgentID, TaskRequest

    captured_messages: list[dict] = []
    peer_url = "http://agent-canary:9001/"

    async def _capture(url, message, **kw):
        captured_messages.append({"url": url, "message": message, "kwargs": kw})
        return {"id": "t-001", "status": "SUBMITTED"}

    with patch("lib.a2a.client.send_message", side_effect=_capture):
        cap = AgentCapability(
            agent_id=AgentID("agent-test-01"),
            version="1.0.0",
            phase="draft",
            description="test capability for message shape",
            peer_endpoint=peer_url,
        )
        req = TaskRequest(task_id="task-001", summary="find optimal policy")
        await execute(req, cap)

    assert len(captured_messages) == 1
    call = captured_messages[0]

    # URL matches the peer_endpoint
    assert call["url"] == peer_url

    # Message has required A2A structure
    msg = call["message"]
    assert msg["role"] == "USER"
    assert len(msg["parts"]) == 1
    assert "find optimal policy" in msg["parts"][0]["text"]

    # Metadata carries orchestrator context
    assert msg["metadata"]["orchestrator_task_id"] == "task-001"
    assert msg["metadata"]["phase"] == "draft"
```

**Run command (expect FAIL before implementation):**

```bash
.venv/bin/python -m pytest app/tests/test_peer_dispatch.py::test_peer_dispatch_sends_correct_message_shape -v
```

**Expected before implementation:** `FAILED` — no `execute` or wrong message shape.

**Run command (expect PASS after implementation from Task 2):**

```bash
.venv/bin/python -m pytest app/tests/test_peer_dispatch.py::test_peer_dispatch_sends_correct_message_shape -v
```

**Expected:** `1 passed`

### Step 3b — Commit

```bash
git add app/tests/test_peer_dispatch.py
git commit -m "test(orchestrator): verify A2A message shape — role, parts, metadata"
```

---

## Task 4: Test Local-Only Capabilities Are Unaffected

**What:** Verify that calling `execute()` on a capability with `peer_endpoint=None` does NOT call `send_message`, and that the `invoke` coroutine is called instead.

### Step 4a — Write the failing tests

Add to `app/tests/test_peer_dispatch.py`:

```python
@pytest.mark.asyncio
async def test_local_dispatch_does_not_call_send_message():
    """Local dispatch must NOT call send_message."""
    from unittest.mock import AsyncMock, patch
    from app.core.orchestrator import execute
    from app.core.schemas import AgentCapability, AgentID, ExecutionResult, TaskRequest, TaskStatus

    mock_invoke = AsyncMock(
        return_value=ExecutionResult(
            task_id="task-001",
            status=TaskStatus.COMPLETED,
        )
    )

    with patch("lib.a2a.client.send_message", new=AsyncMock()) as mock_send:
        cap = AgentCapability(
            agent_id=AgentID("agent-test-01"),
            version="1.0.0",
            phase="draft",
            description="local-only capability",
            invoke=mock_invoke,
        )
        req = TaskRequest(task_id="task-001", summary="local task")
        await execute(req, cap)

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_local_dispatch_calls_invoke():
    """A capability without peer_endpoint routes via invoke()."""
    from unittest.mock import AsyncMock
    from app.core.orchestrator import execute
    from app.core.schemas import AgentCapability, AgentID, ExecutionResult, TaskRequest, TaskStatus

    mock_invoke = AsyncMock(
        return_value=ExecutionResult(
            task_id="task-001",
            status=TaskStatus.COMPLETED,
            agent_id=AgentID("agent-test-01"),
            output="done",
        )
    )

    cap = AgentCapability(
        agent_id=AgentID("agent-test-01"),
        version="1.0.0",
        phase="draft",
        description="local-only capability",
        invoke=mock_invoke,
    )
    req = TaskRequest(task_id="task-001", summary="local task")
    result = await execute(req, cap)

    assert result.status == TaskStatus.COMPLETED
    assert result.output == "done"
    mock_invoke.assert_called_once()
```

**Run command (expect FAIL before implementation):**

```bash
.venv/bin/python -m pytest \
  app/tests/test_peer_dispatch.py::test_local_dispatch_does_not_call_send_message \
  app/tests/test_peer_dispatch.py::test_local_dispatch_calls_invoke \
  -v
```

**Expected before implementation:** `FAILED` — no `execute`.

**Run command (expect PASS after implementation from Task 2):**

```bash
.venv/bin/python -m pytest \
  app/tests/test_peer_dispatch.py::test_local_dispatch_does_not_call_send_message \
  app/tests/test_peer_dispatch.py::test_local_dispatch_calls_invoke \
  -v
```

**Expected:** `2 passed`

### Step 4b — Commit

```bash
git add app/tests/test_peer_dispatch.py
git commit -m "test(orchestrator): verify local dispatch does not call send_message"
```

---

## Task 5: Integration Self-Check

**What:** Run the full app test suite. Verify all tests pass. Verify `lib/a2a/` files are untouched.

### Step 5a — Run full suite

```bash
.venv/bin/python -m pytest app/tests/ -q --no-header 2>&1 | tail -5
```

**Expected:** All tests pass. Example output:
```
.............................................
45 passed in 0.90s
```

### Step 5b — Verify no `lib/a2a/` diff

```bash
git diff --name-only main | grep "lib/a2a" && echo "ERROR: should not touch lib/a2a" || echo "CLEAN"
```

**Expected:** `CLEAN`

### Step 5c — Acceptance criteria smoke test

```bash
# AgentCapability has peer_endpoint field and validator works
.venv/bin/python -c "
from app.core.schemas import AgentCapability, AgentID
c = AgentCapability(
    agent_id=AgentID('t'),
    version='1.0.0',
    phase='draft',
    description='smoke test',
    peer_endpoint='http://peer:9001/'
)
assert c.peer_endpoint == 'http://peer:9001/'
print('OK: peer_endpoint accepted')

try:
    AgentCapability(
        agent_id=AgentID('t'),
        version='1.0.0',
        phase='draft',
        description='smoke test',
        peer_endpoint='not-a-url'
    )
    print('ERROR: should have raised')
except Exception as e:
    print(f'OK: invalid URL rejected ({type(e).__name__})')
"
```

**Expected:**
```
OK: peer_endpoint accepted
OK: invalid URL rejected (ValidationError)
```

### Step 5d — Final commit and branch summary

```bash
git add app/tests/test_peer_dispatch.py
git commit -m "test(orchestrator): add integration self-check for P-3 acceptance criteria"
```

---

## Full Test Suite Reference

All tests that must pass on `feat/p3-a2a-peer-dispatch`:

| Test | Covers |
|------|--------|
| `test_peer_dispatch_calls_send_message` | Peer route → `send_message` called |
| `test_peer_dispatch_sends_correct_message_shape` | Message: `role=USER`, `parts[0].text`, `metadata` |
| `test_peer_dispatch_passes_agent_identity` | `agent_identity` forwarded to `send_message` |
| `test_peer_dispatch_timeout` | `asyncio.TimeoutError` → `FAILED` result, no exception propagation |
| `test_peer_dispatch_error` | `ConnectionError` → `FAILED` with `a2a_peer_error` prefix |
| `test_peer_dispatch_a2a_error` | `A2AUnsupportedOperation` → `FAILED` with error code in message |
| `test_peer_dispatch_a2a_task_not_found` | `A2ATaskNotFound` → `FAILED` with code `-32001` |
| `test_local_dispatch_calls_invoke` | Local path → `invoke()` called |
| `test_local_dispatch_does_not_call_send_message` | Local path → `send_message` NOT called |
| `test_local_dispatch_no_invoke_fails` | `invoke=None`, `peer_endpoint=None` → `FAILED` gracefully |
| `test_local_dispatch_timeout` | `invoke` exceeds `local_timeout_s` → `FAILED` |
| `test_local_dispatch_invoke_exception` | `invoke` raises → `FAILED` with `invoke_exception` prefix |
| `test_local_dispatch_bad_return_type` | `invoke` returns wrong type → `FAILED` |
| `test_a2a_status_mapping` | All 6 A2A statuses map correctly |
| `test_agent_capability_peer_endpoint_validates_url` | URL validator rejects non-HTTP(S) |
| `test_peer_endpoint_valid_http` | `http://` accepted |
| `test_peer_endpoint_valid_https` | `https://` accepted |
| `test_peer_endpoint_none_is_default` | `peer_endpoint` defaults to `None` |
| `test_peer_endpoint_invalid_url_rejected` | `ftp://` rejected |
| `test_peer_endpoint_bare_hostname_rejected` | Bare hostname rejected |
| `test_existing_capability_unchanged` | Backward compat: old capabilities work |

---

## PR Checklist

Before opening the PR:

- [ ] `git diff --name-only main | grep "lib/a2a"` returns nothing
- [ ] `.venv/bin/python -m pytest app/tests/ -q --no-header` exits 0
- [ ] `peer_endpoint` defaults to `None` (verified by `test_peer_endpoint_none_is_default`)
- [ ] `send_message` is imported lazily inside `_execute_via_a2a` (not at module top)
- [ ] No `git add -A` used — all staged files listed explicitly
- [ ] PR title: `feat(app): P-3 a2a peer-execution dispatch — route to peer via send_message`
- [ ] Branch: `feat/p3-a2a-peer-dispatch`
