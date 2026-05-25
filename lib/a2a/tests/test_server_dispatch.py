"""Day 2 acceptance tests — JSON-RPC dispatch over FastAPI TestClient.

Per spike-plan.md §Day 2 acceptance:
  pytest lib/a2a/tests/test_server_dispatch.py -q  green
  curl -X POST http://localhost:9001/ ... returns SUBMITTED Task

The curl test is operator-verified (manual `uvicorn lib.a2a.server:app
--port 9001` then curl from the host). These pytest tests cover the
deterministic side of the gate — every dispatcher branch is exercised
without any network or runtime dependency on uvicorn.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lib.a2a.server import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Acceptance test 1: valid message/send returns SUBMITTED Task.
# ---------------------------------------------------------------------------


def test_message_send_returns_submitted_task() -> None:
    """Acceptance gate: a well-formed message/send returns a SUBMITTED
    Task with a generated id.
    """
    response = client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "message/send",
            "params": {
                "message": {"role": "USER", "parts": [{"text": "hi"}]},
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert "error" not in body
    assert body["result"]["status"] == "SUBMITTED"
    # Day 7: id is a UUID from the TaskSpec bridge (no longer has "task-" prefix)
    assert isinstance(body["result"]["id"], str) and len(body["result"]["id"]) > 0


# ---------------------------------------------------------------------------
# Acceptance test 2: unknown method returns -32601.
# ---------------------------------------------------------------------------


def test_unknown_method_returns_method_not_found() -> None:
    """JSON-RPC 2.0 §5.1: unknown methods MUST return -32601
    (Method not found).
    """
    response = client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "this/does/not/exist",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 2
    assert body["error"]["code"] == -32601
    assert "Unknown method" in body["error"]["message"]


# ---------------------------------------------------------------------------
# Acceptance test 3: malformed JSON returns -32700.
# ---------------------------------------------------------------------------


def test_malformed_json_returns_parse_error() -> None:
    """JSON-RPC 2.0 §5.1: malformed JSON MUST return -32700 (Parse error)
    with id=null.
    """
    response = client.post(
        "/",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] is None
    assert body["error"]["code"] == -32700
    assert "Parse error" in body["error"]["message"]


# ---------------------------------------------------------------------------
# Day 2 coverage extras — kept tight so failures point at one cause.
# ---------------------------------------------------------------------------


def test_health_endpoint_returns_ok() -> None:
    """/health is the docker compose healthcheck target once we wire it in."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "stub_method",
    ["message/stream", "tasks/get", "tasks/subscribe", "tasks/cancel"],
)
def test_stub_methods_return_unsupported_operation(stub_method: str) -> None:
    """A2A §5.4: Day 2 stubs MUST return -32004
    (UnsupportedOperationError) until their respective day lands them.
    """
    response = client.post(
        "/",
        json={"jsonrpc": "2.0", "id": 3, "method": stub_method},
    )
    body = response.json()
    assert body["error"]["code"] == -32004
    assert stub_method in body["error"]["message"]


def test_invalid_envelope_missing_jsonrpc_returns_invalid_request() -> None:
    """JSON-RPC 2.0 §4: envelope without `jsonrpc: "2.0"` MUST return
    -32600 (Invalid Request).
    """
    response = client.post(
        "/",
        json={"id": 4, "method": "message/send", "params": {}},
    )
    body = response.json()
    assert body["error"]["code"] == -32600


def test_message_send_with_missing_parts_returns_invalid_params() -> None:
    """params.message.parts is required per spec §7.6.1; absence MUST
    return -32602 (Invalid params), not -32603 (Internal error).
    """
    response = client.post(
        "/",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "message/send",
            "params": {"message": {"role": "USER"}},
        },
    )
    body = response.json()
    assert body["error"]["code"] == -32602
