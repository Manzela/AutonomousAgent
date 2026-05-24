"""Tests for lib/a2a/client.py — Day 3 round-trip against in-process server.

Uses httpx.ASGITransport so there is NO network involved: the client hits
the Day-2 FastAPI app directly via ASGI. This validates the full envelope
construction → dispatch → response decoding pipeline.

Acceptance gate (spike-plan.md §Day 3):
  - send_message returns a Task dict with "id" and "status" == "SUBMITTED"
  - Error codes map to the correct A2AError subclass
  - Malformed peer responses raise A2AInvalidAgentResponse
  - TransportError triggers retry up to _MAX_RETRIES then re-raises
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from lib.a2a.client import (
    A2AError,
    A2AInvalidAgentResponse,
    A2ARPCError,
    A2ATaskNotFound,
    A2AUnsupportedOperation,
    cancel_task,
    get_task,
    send_message,
)
from lib.a2a.server import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = "http://testserver/"
_MSG = {"role": "USER", "parts": [{"text": "hi"}]}


def _transport() -> httpx.ASGITransport:
    """In-process ASGI transport — zero network, full FastAPI dispatch."""
    return httpx.ASGITransport(app=app)


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_returns_submitted_task() -> None:
    async with httpx.AsyncClient(transport=_transport(), base_url=_BASE) as client:
        with patch("lib.a2a.client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            task = await send_message(_BASE, _MSG)

    assert isinstance(task, dict), "result must be a dict"
    assert "id" in task, "Task must have an 'id' field"
    assert task["id"].startswith("task-"), f"unexpected id prefix: {task['id']}"
    assert task["status"] == "SUBMITTED", f"unexpected status: {task['status']}"


@pytest.mark.asyncio
async def test_send_message_missing_parts_raises_a2a_rpc_error() -> None:
    """Params without 'parts' → server returns -32602; client raises A2ARPCError."""
    bad_msg: dict[str, Any] = {"role": "USER"}  # no 'parts'

    async with httpx.AsyncClient(transport=_transport(), base_url=_BASE) as client:
        with patch("lib.a2a.client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(A2ARPCError) as exc_info:
                await send_message(_BASE, bad_msg)

    assert exc_info.value.code == -32602


@pytest.mark.asyncio
async def test_send_message_malformed_result_raises_invalid_agent_response() -> None:
    """If peer returns a result missing 'id', raise A2AInvalidAgentResponse."""

    class _BrokenApp:
        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            import json

            body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "not-a-task"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [[b"content-type", b"application/json"]],
                }
            )
            await send({"type": "http.response.body", "body": body})

    transport = httpx.ASGITransport(app=_BrokenApp())
    async with httpx.AsyncClient(transport=transport, base_url=_BASE) as client:
        with patch("lib.a2a.client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(A2AInvalidAgentResponse):
                await send_message(_BASE, _MSG)


# ---------------------------------------------------------------------------
# Error code mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tasks_get_unsupported_raises_a2a_unsupported_operation() -> None:
    """tasks/get is a Day-2 stub → server returns -32004."""
    async with httpx.AsyncClient(transport=_transport(), base_url=_BASE) as client:
        with patch("lib.a2a.client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(A2AUnsupportedOperation) as exc_info:
                await get_task(_BASE, "task-does-not-exist")

    assert exc_info.value.code == -32004


@pytest.mark.asyncio
async def test_cancel_task_unsupported_raises_a2a_unsupported_operation() -> None:
    """tasks/cancel is a Day-2 stub → server returns -32004."""
    async with httpx.AsyncClient(transport=_transport(), base_url=_BASE) as client:
        with patch("lib.a2a.client.httpx.AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with pytest.raises(A2AUnsupportedOperation) as exc_info:
                await cancel_task(_BASE, "task-does-not-exist")

    assert exc_info.value.code == -32004


@pytest.mark.asyncio
async def test_error_subclass_hierarchy() -> None:
    """All A2AError subclasses must be catchable as A2AError."""
    with pytest.raises(A2AError):
        raise A2ATaskNotFound(-32001, "not found")


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transport_error_retries_then_raises() -> None:
    """TransportError triggers up to _MAX_RETRIES attempts then re-raises."""
    from lib.a2a import client as client_mod

    call_count = 0

    async def _fake_sleep(delay: float) -> None:
        # Don't actually sleep in tests
        pass

    async def _failing_post(*args: Any, **kwargs: Any) -> None:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("refused")

    with (
        patch.object(client_mod, "_MAX_RETRIES", 3),
        patch("lib.a2a.client.asyncio.sleep", side_effect=_fake_sleep),
        patch("httpx.AsyncClient.post", side_effect=_failing_post),
    ):
        with pytest.raises(httpx.TransportError):
            await send_message(_BASE, _MSG)

    assert call_count == 3, f"expected 3 attempts, got {call_count}"


# ---------------------------------------------------------------------------
# URL normalisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trailing_slash_normalised() -> None:
    """Peer URL with or without trailing slash resolves to the same endpoint."""
    async with httpx.AsyncClient(transport=_transport(), base_url=_BASE) as client:
        for url in [_BASE, _BASE.rstrip("/")]:
            with patch("lib.a2a.client.httpx.AsyncClient") as mock_cls:
                mock_cls.return_value.__aenter__ = AsyncMock(return_value=client)
                mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                task = await send_message(url, _MSG)
            assert "id" in task
