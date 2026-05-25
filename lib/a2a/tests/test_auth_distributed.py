"""Tests for the Redis-backed distributed jti replay cache in lib/a2a/auth.

Spec: docs/superpowers/specs/2026-05-25-redis-jti-replay-cache-design.md

Required cases (per the spec's §6 test strategy + builder contract):

  1. First jti accepted, second rejected (Redis path, replay detection).
  2. Redis down + fail-open => token ACCEPTED (L1 fallback path).
  3. Redis down + A2A_JTI_FAIL_MODE=closed => token REJECTED.
  4. L1 replay detection during Redis down (same jti twice while Redis is
     unreachable; second call rejected by L1).

These tests use fakeredis[asyncio] to stand in for Memorystore. The
fakeredis pool is injected by monkey-patching `_get_redis_pool` on the
auth module so we don't depend on environment state (REDIS_URL).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis as far
import jwt
import pytest
from cryptography.hazmat.backends import default_backend as _default_backend
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
from redis.asyncio import ConnectionPool
from redis.exceptions import ConnectionError as RedisConnectionError

import lib.a2a.auth as auth_mod

# ---------------------------------------------------------------------------
# JWT scaffolding (same shape as test_auth.py — kept local so the
# distributed tests stand alone and can be removed/migrated easily).
# ---------------------------------------------------------------------------

_AGENT_SA = "agent-a@autonomous-agent-2026.iam.gserviceaccount.com"
_CANARY_SA = "agent-canary-spike@autonomous-agent-2026.iam.gserviceaccount.com"
_ACTING_FOR = {
    "human_sub": "pseudonym:dist-001",
    "human_session_id": "sess-dist-001",
    "consent_scope": "read:trajectories",
}

_TEST_PRIVATE_KEY = _rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=_default_backend(),
)
_TEST_PUBLIC_KEY = _TEST_PRIVATE_KEY.public_key()


def _make_token(jti: str = "dist-jti-001") -> str:
    now = int(time.time())
    payload = {
        "iss": _CANARY_SA,
        "sub": _CANARY_SA,
        "aud": _AGENT_SA,
        "iat": now,
        "exp": now + 300,
        "jti": jti,
        "acting_for": _ACTING_FOR,
    }
    return jwt.encode(payload, _TEST_PRIVATE_KEY, algorithm="RS256")


def _fake_jwk() -> dict:
    import base64

    pub_numbers = _TEST_PUBLIC_KEY.public_numbers()

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
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis_pool(monkeypatch):
    """Replace _get_redis_pool() with a fakeredis-backed pool.

    Also resets _JTI_L1_FALLBACK between tests for isolation.
    """
    server = far.FakeServer()
    pool = ConnectionPool(connection_class=far.FakeAsyncRedisConnection, server=server)

    async def _fake_get_pool():
        return pool

    monkeypatch.setattr(auth_mod, "_get_redis_pool", _fake_get_pool)
    # Reset module-level state — auth.py exposes _JTI_L1_FALLBACK after the
    # refactor (old _JTI_CACHE is deleted per spec §5 stale-state removal).
    auth_mod._JTI_L1_FALLBACK.clear()
    yield pool


@pytest.fixture
def redis_down(monkeypatch):
    """Simulate Memorystore outage by making _jti_set_redis raise ConnectionError."""

    async def _raise(*_args, **_kwargs):
        raise RedisConnectionError("simulated: Memorystore unreachable")

    monkeypatch.setattr(auth_mod, "_jti_set_redis", _raise)
    auth_mod._JTI_L1_FALLBACK.clear()
    yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_jti_accepted_second_rejected(fake_redis_pool):
    """SET NX EX semantics: first verify accepts, second raises 'jti replay'."""
    token = _make_token(jti="dist-first-rep-001")
    with patch("lib.a2a.auth._fetch_jwks", new=AsyncMock(return_value=[_fake_jwk()])):
        identity = await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])
        assert identity.jti == "dist-first-rep-001"

        with pytest.raises(ValueError, match="jti replay"):
            await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])


@pytest.mark.asyncio
async def test_redis_down_fail_open_accepts(redis_down, monkeypatch):
    """Redis ConnectionError + default fail-open => token accepted via L1."""
    # Default fail mode is 'open' — verify _FAIL_MODE env var is unset.
    monkeypatch.delenv("A2A_JTI_FAIL_MODE", raising=False)

    token = _make_token(jti="dist-failopen-001")
    with patch("lib.a2a.auth._fetch_jwks", new=AsyncMock(return_value=[_fake_jwk()])):
        identity = await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])
    assert identity.jti == "dist-failopen-001"


@pytest.mark.asyncio
async def test_redis_down_fail_closed_rejects(redis_down, monkeypatch):
    """Redis ConnectionError + A2A_JTI_FAIL_MODE=closed => ValueError."""
    monkeypatch.setenv("A2A_JTI_FAIL_MODE", "closed")

    token = _make_token(jti="dist-failclosed-001")
    with patch("lib.a2a.auth._fetch_jwks", new=AsyncMock(return_value=[_fake_jwk()])):
        with pytest.raises(ValueError, match="unavailable"):
            await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])


@pytest.mark.asyncio
async def test_l1_replay_detection_during_redis_down(redis_down, monkeypatch):
    """While Redis is down (fail-open), L1 must still catch same-jti replays."""
    monkeypatch.delenv("A2A_JTI_FAIL_MODE", raising=False)

    token = _make_token(jti="dist-l1-replay-001")
    with patch("lib.a2a.auth._fetch_jwks", new=AsyncMock(return_value=[_fake_jwk()])):
        # First call: accepted via L1 fallback.
        identity = await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])
        assert identity.jti == "dist-l1-replay-001"

        # Second call: L1 has the entry — must reject with 'replay' message.
        with pytest.raises(ValueError, match="replay"):
            await verify_token(token, our_sa=_AGENT_SA, peers_allowlist=[_CANARY_SA])
