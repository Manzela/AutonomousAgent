"""Tests for lib/a2a/auth.py — Day 5 JWT auth acceptance criteria.

Covers auth-design.md §11:
  - AgentIdentity shape and frozen dataclass behaviour
  - mint_token TTL cache (only one signJwt call for repeated mints)
  - verify_token jti replay rejection
  - verify_token expired JWT rejection
  - verify_token audience mismatch rejection
  - verify_token non-allowlisted issuer rejection
  - _emit_audit_log HIPAA structured fields (every decision emits one entry)
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.backends import default_backend as _default_backend
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

from lib.a2a.auth import AgentIdentity, _emit_audit_log, mint_token, verify_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AGENT_SA = "agent-a@autonomous-agent-2026.iam.gserviceaccount.com"
_CANARY_SA = "agent-canary-spike@autonomous-agent-2026.iam.gserviceaccount.com"
_ACTING_FOR = {
    "human_sub": "pseudonym:abc123",
    "human_session_id": "sess-001",
    "consent_scope": "read:trajectories",
}

# Generate a fresh RSA key pair at module import time — test-only, never persisted.

_TEST_PRIVATE_KEY = _rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=_default_backend(),
)
_TEST_PUBLIC_KEY = _TEST_PRIVATE_KEY.public_key()


def _make_token(
    iss: str = _CANARY_SA,
    aud: str = _AGENT_SA,
    acting_for: dict = None,
    exp_offset: int = 300,
    jti: str = "test-jti-001",
) -> str:
    payload = {
        "iss": iss,
        "sub": iss,
        "aud": aud,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_offset,
        "jti": jti,
        "acting_for": acting_for or _ACTING_FOR,
    }
    return jwt.encode(payload, _TEST_PRIVATE_KEY, algorithm="RS256")


def _fake_jwk() -> dict:
    """Return a JWK dict wrapping our test RSA public key."""
    import base64

    pub = _TEST_PUBLIC_KEY
    pub_numbers = pub.public_numbers()

    def _b64url(n: int, length: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url(pub_numbers.n, 256),
        "e": _b64url(pub_numbers.e, 3),
    }


# ---------------------------------------------------------------------------
# Test 1 — AgentIdentity shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_token_returns_agent_identity():
    """verify_token returns a frozen AgentIdentity with correct fields."""
    token = _make_token()
    with patch("lib.a2a.auth._fetch_jwks", new=AsyncMock(return_value=[_fake_jwk()])):
        from lib.a2a.auth import _JTI_L1_FALLBACK

        _JTI_L1_FALLBACK.clear()
        identity = await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])
    assert identity.sub == _CANARY_SA
    assert identity.audience == _AGENT_SA
    assert identity.acting_for["human_sub"] == _ACTING_FOR["human_sub"]
    assert isinstance(identity.expiry, int)
    assert identity.jti == "test-jti-001"


# ---------------------------------------------------------------------------
# Test 2-5 — verify_token rejection cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_token_jti_replay_rejected():
    """Same JWT presented twice — second call raises ValueError('jti replay')."""
    from lib.a2a.auth import _JTI_L1_FALLBACK

    _JTI_L1_FALLBACK.clear()
    token = _make_token(jti="replay-jti-unique")
    with patch("lib.a2a.auth._fetch_jwks", new=AsyncMock(return_value=[_fake_jwk()])):
        await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])
        with pytest.raises(ValueError, match="jti replay"):
            await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])


@pytest.mark.asyncio
async def test_verify_token_expired_rejected(caplog):
    from lib.a2a.auth import _JTI_L1_FALLBACK
    import logging

    _JTI_L1_FALLBACK.clear()
    token = _make_token(exp_offset=-10, jti="exp-jti")
    with caplog.at_level(logging.INFO, logger="a2a.audit"):
        with patch("lib.a2a.auth._fetch_jwks", new=AsyncMock(return_value=[_fake_jwk()])):
            with pytest.raises(ValueError, match="expired"):
                await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])
    import json as _json

    audit_records = [r for r in caplog.records if r.name == "a2a.audit"]
    assert audit_records, "expected audit log record"
    entry = _json.loads(audit_records[-1].getMessage())
    assert entry.get("decision") in (
        "rejected_expired",
        "rejected_invalid_sig",
        "rejected_not_allowlisted",
        "rejected_replay",
    )


@pytest.mark.asyncio
async def test_verify_token_audience_mismatch_rejected(caplog):
    from lib.a2a.auth import _JTI_L1_FALLBACK
    import logging

    _JTI_L1_FALLBACK.clear()
    token = _make_token(aud="peer-b@autonomous-agent-2026.iam.gserviceaccount.com", jti="aud-jti")
    with caplog.at_level(logging.INFO, logger="a2a.audit"):
        with patch("lib.a2a.auth._fetch_jwks", new=AsyncMock(return_value=[_fake_jwk()])):
            with pytest.raises(ValueError, match="audience"):
                await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])
    import json as _json

    audit_records = [r for r in caplog.records if r.name == "a2a.audit"]
    assert audit_records, "expected audit log record"
    entry = _json.loads(audit_records[-1].getMessage())
    assert entry.get("decision") in (
        "rejected_expired",
        "rejected_invalid_sig",
        "rejected_not_allowlisted",
        "rejected_replay",
    )


@pytest.mark.asyncio
async def test_verify_token_non_allowlisted_issuer_rejected(caplog):
    from lib.a2a.auth import _JTI_L1_FALLBACK
    import logging

    _JTI_L1_FALLBACK.clear()
    token = _make_token(iss="rogue@other-project.iam.gserviceaccount.com", jti="rogue-jti")
    with caplog.at_level(logging.INFO, logger="a2a.audit"):
        with patch("lib.a2a.auth._fetch_jwks", new=AsyncMock(return_value=[_fake_jwk()])):
            with pytest.raises(ValueError, match="not allowlisted"):
                await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])
    import json as _json

    audit_records = [r for r in caplog.records if r.name == "a2a.audit"]
    assert audit_records, "expected audit log record"
    entry = _json.loads(audit_records[-1].getMessage())
    assert entry.get("decision") in (
        "rejected_expired",
        "rejected_invalid_sig",
        "rejected_not_allowlisted",
        "rejected_replay",
    )


# ---------------------------------------------------------------------------
# Test 6-7 — mint_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_token_returns_decodable_jwt():
    from lib.a2a.auth import _MINT_CACHE

    _MINT_CACHE.clear()
    fake_signed = _make_token(iss=_AGENT_SA, aud=_CANARY_SA)
    with patch("lib.a2a.auth._call_sign_jwt", new=AsyncMock(return_value=fake_signed)):
        token = await mint_token(
            our_sa=_AGENT_SA, target_audience=_CANARY_SA, acting_for=_ACTING_FOR
        )
    decoded = jwt.decode(token, options={"verify_signature": False})
    assert decoded["iss"] == _AGENT_SA
    assert decoded["aud"] == _CANARY_SA
    assert decoded["acting_for"]["human_sub"] == _ACTING_FOR["human_sub"]
    assert "jti" in decoded
    assert "exp" in decoded


@pytest.mark.asyncio
async def test_mint_token_uses_cache_on_repeat_call():
    from lib.a2a.auth import _MINT_CACHE

    _MINT_CACHE.clear()
    fake_signed = _make_token(iss=_AGENT_SA, aud=_CANARY_SA)
    mock_sign = AsyncMock(return_value=fake_signed)
    with patch("lib.a2a.auth._call_sign_jwt", new=mock_sign):
        t1 = await mint_token(_AGENT_SA, _CANARY_SA, _ACTING_FOR)
        t2 = await mint_token(_AGENT_SA, _CANARY_SA, _ACTING_FOR)
    assert t1 == t2
    assert mock_sign.call_count == 1


# ---------------------------------------------------------------------------
# Test 8 — _emit_audit_log HIPAA fields
# ---------------------------------------------------------------------------


def test_emit_audit_log_hipaa_fields(caplog):
    from lib.a2a.auth import _JTI_L1_FALLBACK
    import logging

    _JTI_L1_FALLBACK.clear()
    identity = AgentIdentity(
        sub=_CANARY_SA,
        audience=_AGENT_SA,
        acting_for=_ACTING_FOR,
        expiry=int(time.time()) + 300,
        jti="audit-jti-001",
    )
    with caplog.at_level(logging.INFO, logger="a2a.audit"):
        _emit_audit_log(
            decision="accepted",
            identity=identity,
            method="message/send",
            task_id="task-abc",
            trace_id="00-traceid-spanid-01",
        )
    audit_records = [r for r in caplog.records if r.name == "a2a.audit"]
    assert audit_records, "expected one audit log record"
    entry = json.loads(audit_records[0].getMessage())
    assert entry["decision"] == "accepted"
    assert entry["peer_agent_id"] == _CANARY_SA
    assert "peer_human_sub" in entry
    assert entry["method"] == "message/send"
    assert entry["task_id"] == "task-abc"
    assert entry["jti"] == "audit-jti-001"
    assert entry["trace_id"] == "00-traceid-spanid-01"
    assert "ts" in entry
    assert entry.get("level") == "INFO"
    assert (
        entry.get("event") == "auth_decision"
    ), f"expected event='auth_decision', got {entry.get('event')!r}"
