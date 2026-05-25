"""Day 5 JWT middleware tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from lib.a2a.server import app

client = TestClient(app)
_FAKE_IDENTITY = MagicMock()
_FAKE_IDENTITY.sub = "agent-canary-spike@autonomous-agent-2026.iam.gserviceaccount.com"


def test_no_auth_header_is_allowed() -> None:
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


def test_valid_bearer_token_is_accepted() -> None:
    with patch(
        "lib.a2a.server.verify_token", new=AsyncMock(return_value=_FAKE_IDENTITY)
    ) as mock_vt:
        resp = client.post(
            "/",
            headers={"Authorization": "Bearer fake.token"},
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "message/send",
                "params": {"message": {"role": "USER", "parts": [{"text": "hi"}]}},
            },
        )
    assert resp.status_code == 200
    mock_vt.assert_called_once()
    assert "error" not in resp.json()


def test_invalid_bearer_token_returns_32600() -> None:
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
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32600
