"""Day 4 streaming acceptance tests."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from lib.a2a.server import app

client = TestClient(app)


def _parse_sse_events(raw: bytes) -> list[dict]:
    events = []
    for chunk in raw.split(b"\n\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.startswith(b"data: "):
            payload = chunk[len(b"data: ") :]
            events.append(json.loads(payload))
    return events


def test_stream_route_content_type_is_event_stream() -> None:
    with client.stream(
        "POST", "/stream", json={"message": {"role": "USER", "parts": [{"text": "hi"}]}}
    ) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]


def test_stream_route_emits_three_events_in_order() -> None:
    with client.stream(
        "POST", "/stream", json={"message": {"role": "USER", "parts": [{"text": "hi"}]}}
    ) as resp:
        body = resp.read()
    events = _parse_sse_events(body)
    assert len(events) == 3, f"expected 3 SSE events, got {len(events)}: {events}"
    assert events[0] == {"status": "WORKING"}
    assert events[1] == {"artifact_added": True}
    assert events[2] == {"status": "COMPLETED"}


def test_subscribe_route_content_type_is_event_stream() -> None:
    with client.stream("POST", "/subscribe", json={"id": "task-abc"}) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]


def test_subscribe_route_emits_three_events_in_order() -> None:
    with client.stream("POST", "/subscribe", json={"id": "task-abc"}) as resp:
        body = resp.read()
    events = _parse_sse_events(body)
    assert len(events) == 3, f"expected 3 SSE events, got {len(events)}: {events}"
    assert events[0] == {"status": "WORKING"}
    assert events[1] == {"artifact_added": True}
    assert events[2] == {"status": "COMPLETED"}


@pytest.mark.parametrize("method", ["message/stream", "tasks/subscribe"])
def test_jsonrpc_dispatcher_still_returns_unsupported_for_streaming(method: str) -> None:
    resp = client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": method})
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32004
