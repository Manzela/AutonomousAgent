"""Day 7 TaskSpec wiring tests."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from lib.a2a.server import app

client = TestClient(app)


@dataclass
class _FakeTaskSpec:
    id: str = "taskspec-real-001"


_FAKE_SPEC = _FakeTaskSpec()


def test_send_message_returns_spec_id_and_bridged_status() -> None:
    with (
        patch("lib.a2a.server.bridge_inbound_to_taskspec", return_value=_FAKE_SPEC) as mock_bridge,
        patch(
            "lib.a2a.server.bridge_taskspec_status_to_a2a", return_value="SUBMITTED"
        ) as mock_status,
    ):
        resp = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 20,
                "method": "message/send",
                "params": {"message": {"role": "USER", "parts": [{"text": "bridge"}]}},
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
    with (
        patch("lib.a2a.server.bridge_inbound_to_taskspec", return_value=_FAKE_SPEC) as mock_bridge,
        patch("lib.a2a.server.bridge_taskspec_status_to_a2a", return_value="SUBMITTED"),
    ):
        client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "message/send",
                "params": {"message": {"role": "USER", "parts": [{"text": "no auth"}]}},
            },
        )
    call_args = mock_bridge.call_args
    identity_arg = (
        call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("identity")
    )
    assert identity_arg is None


def test_send_message_passes_identity_when_auth_present() -> None:
    fake_identity = MagicMock()
    fake_identity.sub = "agent-canary@autonomous-agent-2026.iam.gserviceaccount.com"
    with (
        patch("lib.a2a.server.verify_token", new=AsyncMock(return_value=fake_identity)),
        patch("lib.a2a.server.bridge_inbound_to_taskspec", return_value=_FAKE_SPEC) as mock_bridge,
        patch("lib.a2a.server.bridge_taskspec_status_to_a2a", return_value="SUBMITTED"),
    ):
        client.post(
            "/",
            headers={"Authorization": "Bearer fake.jwt"},
            json={
                "jsonrpc": "2.0",
                "id": 22,
                "method": "message/send",
                "params": {"message": {"role": "USER", "parts": [{"text": "with auth"}]}},
            },
        )
    call_args = mock_bridge.call_args
    identity_arg = (
        call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("identity")
    )
    assert identity_arg is fake_identity


def test_bridge_exception_maps_to_32603() -> None:
    with patch(
        "lib.a2a.server.bridge_inbound_to_taskspec",
        side_effect=RuntimeError("anchors down"),
    ):
        resp = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 23,
                "method": "message/send",
                "params": {"message": {"role": "USER", "parts": [{"text": "broken"}]}},
            },
        )
    assert resp.json()["error"]["code"] == -32603
