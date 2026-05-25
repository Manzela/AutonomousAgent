"""Tests for tasks/get and tasks/cancel dispatch handlers (Task 6)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_tasks_get_returns_task_after_send() -> None:
    """tasks/get returns the task created by a prior message/send."""
    from lib.a2a.server import _TASK_REGISTRY, app

    _TASK_REGISTRY.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First create a task
        send_resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {"message": {"parts": [{"text": "hello"}]}},
            },
        )
        assert send_resp.status_code == 200
        send_body = send_resp.json()
        assert "result" in send_body, f"Expected result, got: {send_body}"
        task_id = send_body["result"]["id"]

        # Then get it
        get_resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tasks/get",
                "params": {"id": task_id},
            },
        )
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert "result" in body, f"Expected result, got: {body}"
    assert body["result"]["id"] == task_id
    assert body["result"]["status"] == "SUBMITTED"


@pytest.mark.asyncio
async def test_tasks_get_unknown_returns_task_not_found_error() -> None:
    """tasks/get with an unknown task_id returns -32001 (A2ATaskNotFound)."""
    from lib.a2a.server import _TASK_REGISTRY, app

    _TASK_REGISTRY.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/get",
                "params": {"id": "does-not-exist"},
            },
        )
    body = resp.json()
    assert "error" in body, f"Expected error, got: {body}"
    assert body["error"]["code"] == -32001  # A2A_TASK_NOT_FOUND


@pytest.mark.asyncio
async def test_tasks_cancel_marks_superseded() -> None:
    """tasks/cancel updates registry to superseded and returns CANCELED status."""
    from lib.a2a.server import _TASK_REGISTRY, app

    _TASK_REGISTRY.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        send_resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {"message": {"parts": [{"text": "hello"}]}},
            },
        )
        assert send_resp.status_code == 200
        task_id = send_resp.json()["result"]["id"]

        cancel_resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tasks/cancel",
                "params": {"id": task_id},
            },
        )
    body = cancel_resp.json()
    assert "result" in body, f"Expected result, got: {body}"
    assert body["result"]["id"] == task_id
    assert body["result"]["status"] == "CANCELED"


@pytest.mark.asyncio
async def test_tasks_cancel_unknown_returns_task_not_found_error() -> None:
    """tasks/cancel with an unknown task_id returns -32001 (A2ATaskNotFound)."""
    from lib.a2a.server import _TASK_REGISTRY, app

    _TASK_REGISTRY.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/cancel",
                "params": {"id": "does-not-exist"},
            },
        )
    body = resp.json()
    assert "error" in body, f"Expected error, got: {body}"
    assert body["error"]["code"] == -32001  # A2A_TASK_NOT_FOUND


@pytest.mark.asyncio
async def test_tasks_cancel_updates_registry_state() -> None:
    """After tasks/cancel, a subsequent tasks/get returns CANCELED."""
    from lib.a2a.server import _TASK_REGISTRY, app

    _TASK_REGISTRY.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        send_resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "1",
                "method": "message/send",
                "params": {"message": {"parts": [{"text": "hello"}]}},
            },
        )
        task_id = send_resp.json()["result"]["id"]

        await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tasks/cancel",
                "params": {"id": task_id},
            },
        )

        get_resp = await client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": "3",
                "method": "tasks/get",
                "params": {"id": task_id},
            },
        )
    body = get_resp.json()
    assert "result" in body, f"Expected result, got: {body}"
    assert body["result"]["status"] == "CANCELED"
