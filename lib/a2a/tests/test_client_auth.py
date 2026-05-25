"""Tests: mint_token wired into client.py send_message outbound path.

Per HAND-OFF.md production checklist item:
  'Wire mint_token into client.py send_message outbound path'

Verifies:
1. When agent_identity is provided AND peers.yaml has a matching peer,
   mint_token is called and the resulting JWT appears as Authorization header.
2. When agent_identity is None, no Authorization header is sent.
3. When peers.yaml has no matching peer, sends unauthenticated (fail-open).
4. When mint_token raises, sends unauthenticated (fail-open).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.a2a.client import send_message


# ---------------------------------------------------------------------------
# Minimal AgentIdentity stub — no auth branch dependency
# ---------------------------------------------------------------------------


class _FakeIdentity:
    sub = "agent-a@autonomous-agent-2026.iam.gserviceaccount.com"
    audience = "agent-canary-spike@autonomous-agent-2026.iam.gserviceaccount.com"
    acting_for = {
        "human_sub": "pseudonym:test-user",
        "human_session_id": "sess-test",
        "consent_scope": "read:trajectories",
    }
    expiry = 9999999999
    jti = "test-jti"


_FAKE_IDENTITY = _FakeIdentity()
_BASE = "http://testserver/"
_MSG = {"role": "USER", "parts": [{"text": "hello"}]}
_SUBMITTED = {"jsonrpc": "2.0", "id": "1", "result": {"id": "task-001", "status": "SUBMITTED"}}


def _mock_post(response_json: dict) -> Any:
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = response_json
    return AsyncMock(return_value=mock)


# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_with_identity_adds_authorization_header() -> None:
    """When agent_identity + peer in peers.yaml: Authorization: Bearer <token> sent."""
    captured_headers: dict[str, str] = {}

    async def _capturing_post(url, *, json, timeout, headers=None, **kw):
        captured_headers.update(headers or {})
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json.return_value = _SUBMITTED
        return mock

    with (
        patch(
            "lib.a2a.client._lookup_peer_issuer",
            return_value="peer@project.iam.gserviceaccount.com",
        ),
        patch("lib.a2a.auth.mint_token", new=AsyncMock(return_value="fake.jwt.token")),
        patch("httpx.AsyncClient.post", side_effect=_capturing_post),
    ):
        await send_message(_BASE, _MSG, agent_identity=_FAKE_IDENTITY)

    assert "Authorization" in captured_headers
    assert captured_headers["Authorization"] == "Bearer fake.jwt.token"


@pytest.mark.asyncio
async def test_send_message_without_identity_sends_no_auth_header() -> None:
    """When agent_identity is None, no Authorization header is sent."""
    captured_headers: dict[str, str] = {}

    async def _capturing_post(url, *, json, timeout, headers=None, **kw):
        captured_headers.update(headers or {})
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json.return_value = _SUBMITTED
        return mock

    with patch("httpx.AsyncClient.post", side_effect=_capturing_post):
        await send_message(_BASE, _MSG, agent_identity=None)

    assert "Authorization" not in captured_headers


@pytest.mark.asyncio
async def test_send_message_no_peer_in_yaml_sends_unauthenticated() -> None:
    """When peers.yaml has no matching peer, sends without Authorization (fail-open)."""
    captured_headers: dict[str, str] = {}

    async def _capturing_post(url, *, json, timeout, headers=None, **kw):
        captured_headers.update(headers or {})
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json.return_value = _SUBMITTED
        return mock

    with (
        patch("lib.a2a.client._lookup_peer_issuer", return_value=None),
        patch("httpx.AsyncClient.post", side_effect=_capturing_post),
    ):
        await send_message(_BASE, _MSG, agent_identity=_FAKE_IDENTITY)

    assert "Authorization" not in captured_headers


@pytest.mark.asyncio
async def test_send_message_mint_token_failure_sends_unauthenticated() -> None:
    """When mint_token raises, sends unauthenticated and does not propagate the error."""
    captured_headers: dict[str, str] = {}

    async def _capturing_post(url, *, json, timeout, headers=None, **kw):
        captured_headers.update(headers or {})
        mock = MagicMock()
        mock.raise_for_status = MagicMock()
        mock.json.return_value = _SUBMITTED
        return mock

    async def _fail_mint(*args, **kwargs):
        raise RuntimeError("GCP unavailable")

    with (
        patch(
            "lib.a2a.client._lookup_peer_issuer",
            return_value="peer@project.iam.gserviceaccount.com",
        ),
        patch("lib.a2a.auth.mint_token", side_effect=_fail_mint),
        patch("httpx.AsyncClient.post", side_effect=_capturing_post),
    ):
        # Should NOT raise — fail-open behaviour
        result = await send_message(_BASE, _MSG, agent_identity=_FAKE_IDENTITY)

    assert "Authorization" not in captured_headers
    assert result["status"] == "SUBMITTED"
