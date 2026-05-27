"""Real Redis integration tests for the JTI replay cache (T-3 fix).

T-3 finding (audit/2026-05-27-ground-truth/findings.md P1.F T-3):
    "Tested ONLY against fakeredis. No real-redis integration test. The
    fail-open / fail-closed branch with a real Redis outage is unverified."

This file tests the A2A auth module's distributed JTI replay cache against
a real Redis instance.  Requires:
    export REDIS_URL=redis://localhost:6379

When REDIS_URL is not set, every test is automatically skipped — safe for
normal unit-test CI. Set REDIS_URL in integration CI (see .github/workflows/).

Audit-plan W1.F T-3 gate command:
    REDIS_URL=redis://localhost:6379 pytest lib/a2a/tests/test_replay_cache_real_redis.py -x
Expected: Pass.
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.backends import default_backend as _default_backend
from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

import lib.a2a.auth as auth_mod

# ---------------------------------------------------------------------------
# Skip condition
# ---------------------------------------------------------------------------

_REDIS_URL = os.environ.get("REDIS_URL", "")
_HAVE_REAL_REDIS = bool(_REDIS_URL)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _HAVE_REAL_REDIS,
        reason=(
            "Requires REDIS_URL env var pointing at a real Redis instance. "
            "T-3 fix (audit/2026-05-27-ground-truth/findings.md P1.F T-3): "
            "fakeredis-only tests cannot verify real Redis fail-open/fail-closed behavior."
        ),
    ),
]

# ---------------------------------------------------------------------------
# JWT scaffolding — identical to test_auth_distributed.py but standalone
# ---------------------------------------------------------------------------

_AGENT_SA = "agent-a@autonomous-agent-2026.iam.gserviceaccount.com"
_CANARY_SA = "agent-canary@autonomous-agent-2026.iam.gserviceaccount.com"


def _gen_key_pair():
    private = _rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=_default_backend()
    )
    return private, private.public_key()


def _mint_jwt(private_key, audience: str, issuer: str, jti: str | None = None) -> str:
    from cryptography.hazmat.primitives import serialization

    now = int(time.time())
    payload = {
        "iss": issuer,
        "sub": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + 300,
        "jti": jti or f"test-{now}",
        "google.cloud.agent_to_agent": {
            "target_agent": audience,
            "acting_for": issuer,
        },
    }
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return jwt.encode(payload, pem, algorithm="RS256")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_auth_module():
    """Clear the auth module's pool + L1 cache between tests."""
    from cachetools import TTLCache

    original_pool = getattr(auth_mod, "_redis_pool", None)
    auth_mod._JTI_L1_FALLBACK = TTLCache(maxsize=1024, ttl=300)
    yield
    # Restore original pool reference (or None) — let the test manage pool lifetime.
    if original_pool is not None:
        auth_mod._redis_pool = original_pool


@pytest.fixture()
def key_pair():
    return _gen_key_pair()


# ---------------------------------------------------------------------------
# Helper — build a real async redis pool pointing at REDIS_URL
# ---------------------------------------------------------------------------


async def _real_pool():
    """Connect a real redis.asyncio ConnectionPool to REDIS_URL."""
    from redis.asyncio import ConnectionPool

    pool = ConnectionPool.from_url(_REDIS_URL, max_connections=4)
    return pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRealRedisJTIReplay:
    """JTI replay detection against a real Redis instance (T-3)."""

    def test_first_use_accepted(self, key_pair):
        """A fresh JTI against real Redis is accepted."""
        private, public = key_pair

        async def _run():
            pool = await _real_pool()
            auth_mod._redis_pool = pool
            # Patch token verification to bypass Google OIDC key fetch.
            with patch.object(
                auth_mod,
                "_verify_google_oidc",
                return_value={
                    "sub": _AGENT_SA,
                    "iss": _AGENT_SA,
                    "aud": _CANARY_SA,
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 300,
                    "jti": "real-redis-test-001",
                    "google.cloud.agent_to_agent": {
                        "target_agent": _CANARY_SA,
                        "acting_for": _AGENT_SA,
                    },
                },
            ):
                from redis.asyncio import Redis

                r = Redis(connection_pool=pool)
                # Clear any leftover key from prior runs.
                await r.delete("jti:real-redis-test-001")
                await r.aclose()

                token = _mint_jwt(private, _CANARY_SA, _AGENT_SA, jti="real-redis-test-001")
                result = await auth_mod.verify_token(token, our_sa=_CANARY_SA)
                assert result is not None, "Fresh JTI must be accepted"
            await pool.aclose()

        asyncio.run(_run())

    def test_replay_rejected_real_redis(self, key_pair):
        """The same JTI submitted twice is rejected by the real Redis cache."""
        private, public = key_pair
        jti = f"replay-real-{int(time.time())}"

        async def _run():
            pool = await _real_pool()
            auth_mod._redis_pool = pool

            claims = {
                "sub": _AGENT_SA,
                "iss": _AGENT_SA,
                "aud": _CANARY_SA,
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "jti": jti,
                "google.cloud.agent_to_agent": {
                    "target_agent": _CANARY_SA,
                    "acting_for": _AGENT_SA,
                },
            }
            with patch.object(auth_mod, "_verify_google_oidc", return_value=claims):
                from redis.asyncio import Redis

                r = Redis(connection_pool=pool)
                await r.delete(f"jti:{jti}")
                await r.aclose()

                token = _mint_jwt(private, _CANARY_SA, _AGENT_SA, jti=jti)
                result1 = await auth_mod.verify_token(token, our_sa=_CANARY_SA)
                assert result1 is not None, "First submission must be accepted"

                result2 = await auth_mod.verify_token(token, our_sa=_CANARY_SA)
                assert result2 is None, "Replay must be rejected"
            await pool.aclose()

        asyncio.run(_run())

    def test_redis_down_fail_open(self, key_pair):
        """With A2A_JTI_FAIL_MODE=open and Redis unreachable, token is ACCEPTED."""
        private, _ = key_pair
        jti = f"failopen-{int(time.time())}"

        async def _run():
            # Use a deliberately-unreachable Redis URL.
            from redis.asyncio import ConnectionPool

            bad_pool = ConnectionPool.from_url("redis://127.0.0.1:1", max_connections=1)
            auth_mod._redis_pool = bad_pool
            auth_mod._JTI_L1_FALLBACK.clear()

            claims = {
                "sub": _AGENT_SA,
                "iss": _AGENT_SA,
                "aud": _CANARY_SA,
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "jti": jti,
                "google.cloud.agent_to_agent": {
                    "target_agent": _CANARY_SA,
                    "acting_for": _AGENT_SA,
                },
            }
            with (
                patch.object(auth_mod, "_verify_google_oidc", return_value=claims),
                patch.dict(os.environ, {"A2A_JTI_FAIL_MODE": "open"}),
            ):
                token = _mint_jwt(private, _CANARY_SA, _AGENT_SA, jti=jti)
                result = await auth_mod.verify_token(token, our_sa=_CANARY_SA)
            # Fail-open: token accepted despite Redis being unreachable.
            assert result is not None, "Fail-open: token must be accepted when Redis is down"
            await bad_pool.aclose()

        asyncio.run(_run())

    def test_redis_down_fail_closed(self, key_pair):
        """With A2A_JTI_FAIL_MODE=closed and Redis unreachable, token is REJECTED."""
        private, _ = key_pair
        jti = f"failclosed-{int(time.time())}"

        async def _run():
            from redis.asyncio import ConnectionPool

            bad_pool = ConnectionPool.from_url("redis://127.0.0.1:1", max_connections=1)
            auth_mod._redis_pool = bad_pool
            auth_mod._JTI_L1_FALLBACK.clear()

            claims = {
                "sub": _AGENT_SA,
                "iss": _AGENT_SA,
                "aud": _CANARY_SA,
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "jti": jti,
                "google.cloud.agent_to_agent": {
                    "target_agent": _CANARY_SA,
                    "acting_for": _AGENT_SA,
                },
            }
            with (
                patch.object(auth_mod, "_verify_google_oidc", return_value=claims),
                patch.dict(os.environ, {"A2A_JTI_FAIL_MODE": "closed"}),
            ):
                token = _mint_jwt(private, _CANARY_SA, _AGENT_SA, jti=jti)
                result = await auth_mod.verify_token(token, our_sa=_CANARY_SA)
            # Fail-closed: token rejected because Redis is unreachable.
            assert result is None, "Fail-closed: token must be rejected when Redis is down"
            await bad_pool.aclose()

        asyncio.run(_run())

    def test_l1_cache_detects_replay_during_redis_outage(self, key_pair):
        """L1 in-process cache still detects replay when Redis is down (T-3 L1 fallback test)."""
        private, _ = key_pair
        jti = f"l1replay-{int(time.time())}"

        async def _run():
            from redis.asyncio import ConnectionPool

            bad_pool = ConnectionPool.from_url("redis://127.0.0.1:1", max_connections=1)
            auth_mod._redis_pool = bad_pool
            auth_mod._JTI_L1_FALLBACK.clear()

            claims = {
                "sub": _AGENT_SA,
                "iss": _AGENT_SA,
                "aud": _CANARY_SA,
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "jti": jti,
                "google.cloud.agent_to_agent": {
                    "target_agent": _CANARY_SA,
                    "acting_for": _AGENT_SA,
                },
            }
            with (
                patch.object(auth_mod, "_verify_google_oidc", return_value=claims),
                patch.dict(os.environ, {"A2A_JTI_FAIL_MODE": "open"}),
            ):
                token = _mint_jwt(private, _CANARY_SA, _AGENT_SA, jti=jti)
                result1 = await auth_mod.verify_token(token, our_sa=_CANARY_SA)
                # First call accepted via fail-open (Redis unreachable, L1 empty).
                assert result1 is not None, "First call under Redis outage + fail-open must accept"

                # Second call: Redis still unreachable, but L1 has the JTI.
                result2 = await auth_mod.verify_token(token, our_sa=_CANARY_SA)
                assert result2 is None, "L1 cache must reject replay even during Redis outage"
            await bad_pool.aclose()

        asyncio.run(_run())
