"""Day 8 AgentCard signing tests.

Per spike-plan.md §Day 8:
  1. test_sign_then_verify_round_trip — sign a card, verify it, assert True.
  2. test_tampered_card_rejected — sign, change a field, verify, assert False.
  3. test_expired_card_rejected — card with exp in the past → ValueError.

For GCP isolation: _call_sign_blob and _fetch_public_key_for_sa are mocked
with in-process RSA key pair (no network calls).
"""

from __future__ import annotations

import base64
import time
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

# ---------------------------------------------------------------------------
# In-process test key pair — never used in production.
# ---------------------------------------------------------------------------

_TEST_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_PUBLIC_KEY = _TEST_PRIVATE_KEY.public_key()

_AGENT_SA = "agent-a@autonomous-agent-2026.iam.gserviceaccount.com"
_BASE_URL = "http://localhost:9001"


def _test_sign_bytes(data: bytes, sa_email: str = "") -> str:
    """Sign bytes with test private key — mimics GCP signBlob response."""
    signature = _TEST_PRIVATE_KEY.sign(data, padding.PKCS1v15(), hashes.SHA256())
    return base64.urlsafe_b64encode(signature).rstrip(b"=").decode()


# ---------------------------------------------------------------------------
# Task 1 tests: build_agent_card + canonicalize_card
# ---------------------------------------------------------------------------


def test_build_agent_card_has_required_fields() -> None:
    """build_agent_card returns a dict with id, capabilities, security_schemes, jwks_url."""
    from lib.a2a.agent_card import build_agent_card

    card = build_agent_card(_AGENT_SA, _BASE_URL)

    assert card["id"] == _AGENT_SA
    assert set(card["capabilities"]) == {
        "message_send",
        "message_stream",
        "task_get",
        "task_subscribe",
    }
    assert "oauth2" in card["security_schemes"]
    assert "jwt" in card["security_schemes"]
    assert card["jwks_url"] == f"https://www.googleapis.com/service_accounts/v1/jwk/{_AGENT_SA}"


def test_canonicalize_card_is_deterministic() -> None:
    """canonicalize_card returns the same bytes regardless of input key order."""
    from lib.a2a.agent_card import canonicalize_card

    card_a = {"z_key": 1, "a_key": "value", "m_key": [3, 1, 2]}
    card_b = {"m_key": [3, 1, 2], "z_key": 1, "a_key": "value"}

    assert canonicalize_card(card_a) == canonicalize_card(card_b)


def test_canonicalize_card_returns_bytes() -> None:
    """canonicalize_card output is bytes (UTF-8 encoded JSON)."""
    import json
    from lib.a2a.agent_card import canonicalize_card

    result = canonicalize_card({"key": "val"})
    assert isinstance(result, bytes)
    assert json.loads(result.decode("utf-8")) == {"key": "val"}


def test_canonicalize_card_sorts_keys_recursively() -> None:
    """Keys must be sorted at every nesting level."""
    from lib.a2a.agent_card import canonicalize_card

    card = {"b": {"z": 1, "a": 2}, "a": 0}
    raw = canonicalize_card(card).decode("utf-8")
    assert raw.index('"a":') < raw.index('"b":')
    assert raw.index('"a":2') < raw.index('"z":1')


# ---------------------------------------------------------------------------
# Task 2 tests: sign_card + verify_card_signature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sign_then_verify_round_trip() -> None:
    """Sign a card with test key; verify_card_signature returns True."""
    from lib.a2a.agent_card import build_agent_card, sign_card, verify_card_signature

    card = build_agent_card(_AGENT_SA, _BASE_URL)
    with patch("lib.a2a.agent_card._call_sign_blob", new=AsyncMock(side_effect=_test_sign_bytes)):
        with patch("lib.a2a.agent_card._fetch_public_key_for_sa", return_value=_TEST_PUBLIC_KEY):
            signed_card = await sign_card(card, _AGENT_SA)
            result = verify_card_signature(signed_card, _AGENT_SA)

    assert result is True


@pytest.mark.asyncio
async def test_tampered_card_rejected() -> None:
    """Tampered field after signing → verify_card_signature returns False."""
    from lib.a2a.agent_card import build_agent_card, sign_card, verify_card_signature

    card = build_agent_card(_AGENT_SA, _BASE_URL)
    with patch("lib.a2a.agent_card._call_sign_blob", new=AsyncMock(side_effect=_test_sign_bytes)):
        with patch("lib.a2a.agent_card._fetch_public_key_for_sa", return_value=_TEST_PUBLIC_KEY):
            signed_card = await sign_card(card, _AGENT_SA)
            tampered = {
                **signed_card,
                "id": "evil-agent@autonomous-agent-2026.iam.gserviceaccount.com",
            }
            result = verify_card_signature(tampered, _AGENT_SA)

    assert result is False


@pytest.mark.asyncio
async def test_expired_card_rejected() -> None:
    """Card with exp in the past → sign_card raises ValueError (H8).

    With H8 in place, sign_card itself rejects an already-expired card before
    calling signBlob, so verify_card_signature is never reached.
    """
    from lib.a2a.agent_card import build_agent_card, sign_card

    card = build_agent_card(_AGENT_SA, _BASE_URL)
    card["exp"] = int(time.time()) - 3600

    with patch("lib.a2a.agent_card._call_sign_blob", new=AsyncMock(side_effect=_test_sign_bytes)):
        with patch("lib.a2a.agent_card._fetch_public_key_for_sa", return_value=_TEST_PUBLIC_KEY):
            with pytest.raises(ValueError, match="expired"):
                await sign_card(card, _AGENT_SA)


# ---------------------------------------------------------------------------
# Server route test
# ---------------------------------------------------------------------------


def test_well_known_agent_card_route_returns_card() -> None:
    """GET /.well-known/agent-card.json returns a card dict with required fields."""
    from fastapi.testclient import TestClient

    from lib.a2a.server import app as server_app

    # _call_sign_blob is now async; use AsyncMock so await inside sign_card resolves correctly.
    with patch("lib.a2a.agent_card._call_sign_blob", new=AsyncMock(side_effect=_test_sign_bytes)):
        tc = TestClient(server_app)
        resp = tc.get("/.well-known/agent-card.json")

    assert resp.status_code == 200
    body = resp.json()
    assert "id" in body
    assert "capabilities" in body


def test_well_known_agent_card_returns_503_on_sign_failure() -> None:
    """GET /.well-known/agent-card.json returns 503 when signBlob fails.

    The endpoint must NOT serve an unsigned card — that exposes the agent
    identity without cryptographic attestation. Instead, return 503 with
    a JSON error body so callers know signing is temporarily unavailable.
    """
    from fastapi.testclient import TestClient

    from lib.a2a.server import app as server_app

    # Simulate signBlob failure (e.g. GCP ADC unavailable, network timeout)
    with patch(
        "lib.a2a.agent_card._call_sign_blob",
        new=AsyncMock(side_effect=RuntimeError("signBlob unavailable")),
    ):
        tc = TestClient(server_app)
        resp = tc.get("/.well-known/agent-card.json")

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "agent_card_signing_unavailable"
    assert "detail" in body
    # Verify unsigned card is NOT served (no capabilities/id in error response)
    assert "capabilities" not in body
    assert "signature" not in body
