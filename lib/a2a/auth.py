"""A2A auth — JWT mint + verify (composite identity).

Day 5 implementation. See audit/2026-05-21-a2a-spike-plan/auth-design.md.

Design decisions (locked via DEFAULTS-ACCEPTED.md):
  Q1: acting_for = {human_sub, human_session_id, consent_scope}
  Q3: GCP-only federation — JWKS from googleapis.com
  Q5: opaque pseudonym:* IDs for human_sub
  Q6: manual revocation (SA disable + email notification)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os as _os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import cachetools
import httpx
import jwt

# Graceful degradation: if the `redis` package is not installed (e.g. a
# stripped-down dev install without the `[a2a]` extra), auth must still
# import and fall back to L1-only mode. See spec §5 / CRITICAL constraints.
try:
    import redis.asyncio as redis_async
    import redis.exceptions as _redis_exc

    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover — defensive only; CI installs `redis`
    redis_async = None  # type: ignore[assignment]
    _redis_exc = None  # type: ignore[assignment]
    _REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)
_audit_logger = logging.getLogger("a2a.audit")
_audit_logger.addHandler(logging.NullHandler())

_MINT_CACHE: cachetools.TTLCache[tuple[str, str], str] = cachetools.TTLCache(
    maxsize=10_000, ttl=240
)
_MINT_LOCK: asyncio.Lock | None = (
    None  # initialized lazily (avoids event-loop-before-creation error)
)

# ---------------------------------------------------------------------------
# Redis-backed jti replay cache
# Spec: docs/superpowers/specs/2026-05-25-redis-jti-replay-cache-design.md
#
# The old per-process `_JTI_CACHE` (100K maxsize, 600s TTL) has been
# DELETED — it was unsafe in production because each Cloud Run replica
# held its own copy, breaking the replay-detection guarantee. It is
# replaced by:
#   - Redis (primary) via `_get_redis_pool` + `_jti_set_redis`
#     (atomic SET NX EX 600 — distributed, cross-replica).
#   - `_JTI_L1_FALLBACK` (in-process TTLCache, 60s TTL, 300K maxsize)
#     used only when Redis is unreachable. The 60s TTL bounds the
#     cross-replica replay window during a Memorystore outage.
# ---------------------------------------------------------------------------

_REDIS_POOL: Any = None  # redis.asyncio.ConnectionPool | None when redis is installed
# Created eagerly at import time — asyncio.Lock() does not require a
# running event loop to instantiate in Python 3.10+. See spec §3 for why
# eager creation avoids the double-init race the old lazy _JTI_LOCK had.
_REDIS_POOL_LOCK: asyncio.Lock = asyncio.Lock()

_L1_FALLBACK_TTL_SECS: int = 60
_JTI_L1_FALLBACK: cachetools.TTLCache[tuple[str, str], bool] = cachetools.TTLCache(
    maxsize=300_000,  # 5K/sec burst × 60s = 300K — covers full L1 TTL window
    ttl=_L1_FALLBACK_TTL_SECS,
)
_JTI_L1_LOCK: asyncio.Lock = asyncio.Lock()  # eager — safe in Python 3.12

_JWKS_CACHE: cachetools.TTLCache[str, list[dict]] = cachetools.TTLCache(
    maxsize=1_000,
    ttl=900,  # 15 min — matches Google JWKS Cache-Control: max-age=900
)
# M6: negative cache — if JWKS fetch fails (429/503), back off for 30s
_JWKS_FAIL_CACHE: cachetools.TTLCache[str, str] = cachetools.TTLCache(maxsize=100, ttl=30)
_JWKS_LOCK: asyncio.Lock | None = (
    None  # initialized lazily (avoids event-loop-before-creation error)
)


def _get_jwks_lock() -> asyncio.Lock:
    global _JWKS_LOCK
    if _JWKS_LOCK is None:
        _JWKS_LOCK = asyncio.Lock()
    return _JWKS_LOCK


def _get_mint_lock() -> asyncio.Lock:
    global _MINT_LOCK
    if _MINT_LOCK is None:
        _MINT_LOCK = asyncio.Lock()
    return _MINT_LOCK


def _safe_url(url: str) -> str:
    """Redact password from REDIS_URL for safe logging."""
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    _creds, host = rest.rsplit("@", 1)
    return f"{scheme}://***@{host}"


async def _get_redis_pool() -> Any:
    """Return the shared Redis connection pool, or None if not configured.

    Returns None (does not raise) when `REDIS_URL` is unset OR the `redis`
    package is not installed — callers fall through to L1-only mode.

    Lazy pool init (needs REDIS_URL + running event loop); eager lock
    (safe in Python 3.10+, avoids the double-init race a lazy lock has).
    """
    global _REDIS_POOL
    if not _REDIS_AVAILABLE:
        return None
    if _REDIS_POOL is not None:
        return _REDIS_POOL
    url = _os.environ.get("REDIS_URL")
    if not url:
        return None
    async with _REDIS_POOL_LOCK:
        if _REDIS_POOL is not None:  # double-checked locking
            return _REDIS_POOL
        timeout = float(_os.environ.get("REDIS_CONNECT_TIMEOUT_SECS", "2.0"))
        _REDIS_POOL = redis_async.ConnectionPool.from_url(
            url,
            max_connections=20,
            decode_responses=True,
            socket_connect_timeout=timeout,
            socket_timeout=timeout,
            health_check_interval=30,
        )
        logger.info(
            "a2a.auth: initialised Redis pool for jti replay cache (%s)",
            _safe_url(url),
        )
        return _REDIS_POOL


async def _jti_set_redis(replay_key: tuple[str, str], pool: Any) -> bool:
    """Atomically claim (issuer, jti) in Redis via SET NX EX 600.

    Returns:
        True  — key was newly created; this is the first time we have
                seen this jti; **accept** the token.
        False — NX condition failed; key already existed; this is a
                replay; **reject** the token.

    Raises:
        redis.exceptions.ConnectionError / TimeoutError — Redis
            unreachable; caller decides L1 fallback vs fail-closed per
            A2A_JTI_FAIL_MODE. Other RedisError subclasses (ResponseError,
            DataError, AuthenticationError) propagate as-is so they
            surface real bugs instead of silently degrading to L1.

    The `Redis` client is constructed per-call as a thin wrapper around
    the pool — `async with` returns the connection to the pool on exit.
    Bare `Redis(connection_pool=pool)` without the context manager would
    leak pool slots because CPython GC of async coroutines is not
    deterministic.
    """
    issuer, jti = replay_key
    key = f"jti:{issuer}:{jti}"
    async with redis_async.Redis(connection_pool=pool) as client:
        result = await client.set(key, "1", nx=True, ex=600)
    return result is True


@dataclass(frozen=True)
class AgentIdentity:
    """Verified composite identity extracted from an inbound A2A JWT."""

    sub: str
    audience: str
    acting_for: dict
    expiry: int
    jti: str


# ---------------------------------------------------------------------------
# JWKS fetch
# ---------------------------------------------------------------------------

_JWKS_URL_TEMPLATE = "https://www.googleapis.com/service_accounts/v1/jwk/{sa_email}"


async def _fetch_jwks(sa_email: str) -> list[dict]:
    lock = _get_jwks_lock()
    async with lock:  # single-flight: no thundering herd on cache miss
        # Positive cache hit
        cached = _JWKS_CACHE.get(sa_email)
        if cached is not None:
            return cached
        # M6: negative cache — back off if JWKS endpoint recently failed
        if sa_email in _JWKS_FAIL_CACHE:
            raise ValueError(f"JWKS fetch for {sa_email} recently failed; backing off for 30s")
        url = _JWKS_URL_TEMPLATE.format(sa_email=sa_email)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as exc:
            _JWKS_FAIL_CACHE[sa_email] = type(exc).__name__
            raise
        keys = resp.json().get("keys", [])
        _JWKS_CACHE[sa_email] = keys
        return keys


# ---------------------------------------------------------------------------
# verify_token
# ---------------------------------------------------------------------------


async def verify_token(
    jwt_str: str,
    *,
    our_sa: str,
    peers_allowlist: list[str],
) -> AgentIdentity:
    """Verify an inbound A2A JWT and return the caller's AgentIdentity."""
    try:
        unverified = jwt.decode(
            jwt_str,
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["RS256"],
        )
    except jwt.DecodeError as exc:
        _emit_audit_log("rejected_invalid_sig", None, None, None, None)
        raise ValueError(f"JWT decode error: {exc}") from exc

    issuer: str = unverified.get("iss", "")
    # validate issuer format before allowlist lookup. Reject anything
    # that doesn't look like a GCP SA email to prevent injection/confusion.
    if not issuer or not issuer.endswith(".iam.gserviceaccount.com"):
        _emit_audit_log("rejected_invalid_issuer_format", None, None, None, None, peer_sa=issuer)
        raise ValueError(f"issuer format invalid (expected *.iam.gserviceaccount.com): {issuer!r}")
    if issuer not in peers_allowlist:
        _emit_audit_log("rejected_not_allowlisted", None, None, None, None, peer_sa=issuer)
        raise ValueError(f"issuer not allowlisted: {issuer!r}")

    jwk_entries = await _fetch_jwks(issuer)
    if not jwk_entries:
        _emit_audit_log("rejected_invalid_sig", None, None, None, None, peer_sa=issuer)
        raise ValueError(f"JWKS empty for {issuer}")

    # Match the JWT header's kid to the correct JWKS entry — avoids silent
    # breakage when Google rotates keys and keys[0] is no longer the signer.
    try:
        header = jwt.get_unverified_header(jwt_str)
        kid = header.get("kid")
    except jwt.DecodeError:
        kid = None
    if kid:
        candidates = [k for k in jwk_entries if k.get("kid") == kid] or jwk_entries
    else:
        candidates = jwk_entries
    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(candidates[0]))

    try:
        payload = jwt.decode(
            jwt_str,
            public_key,
            algorithms=["RS256"],
            audience=our_sa,
            leeway=5,
        )
    except jwt.ExpiredSignatureError as exc:
        _emit_audit_log("rejected_expired", None, None, None, None, peer_sa=issuer)
        raise ValueError("expired") from exc
    except jwt.InvalidAudienceError as exc:
        _emit_audit_log("rejected_invalid_sig", None, None, None, None, peer_sa=issuer)
        raise ValueError(f"audience mismatch: {exc}") from exc
    except jwt.InvalidSignatureError as exc:
        _emit_audit_log("rejected_invalid_sig", None, None, None, None, peer_sa=issuer)
        raise ValueError(f"invalid signature: {exc}") from exc

    jti: str | None = payload.get("jti")
    if not jti:
        _emit_audit_log("rejected_missing_jti", None, None, None, None, peer_sa=issuer)
        raise ValueError("jti required in JWT payload")
    replay_key = (issuer, jti)

    # --- Distributed jti replay check (Redis primary, L1 fallback) ---
    # Per spec §1: default fail-OPEN with 60s L1 bounded-replay window.
    # Operator override: A2A_JTI_FAIL_MODE=closed. Read per-call (not
    # captured at module import) so tests + revisions can flip the knob
    # without re-importing the module.
    fail_closed = _os.getenv("A2A_JTI_FAIL_MODE", "closed").lower() == "closed"
    redis_pool = await _get_redis_pool()

    if redis_pool is not None:
        try:
            is_first = await _jti_set_redis(replay_key, redis_pool)
            if not is_first:
                _emit_audit_log("rejected_replay", None, None, None, None, peer_sa=issuer)
                raise ValueError("jti replay")
        except (
            _redis_exc.ConnectionError,
            _redis_exc.TimeoutError,
        ) as exc:
            # Narrow except: only availability failures fall through to
            # L1 / fail-closed. ResponseError, DataError, AuthenticationError
            # propagate so deploy bugs surface immediately (spec §5.5).
            if fail_closed:
                _emit_audit_log(
                    "rejected_redis_unavailable",
                    None,
                    None,
                    None,
                    None,
                    peer_sa=issuer,
                )
                raise ValueError("jti check unavailable (fail-closed mode)") from exc
            logger.warning(
                "a2a.auth: Redis jti cache unreachable (%s) — falling back "
                "to L1 (fail-open, 60s TTL)",
                type(exc).__name__,
            )
            async with _JTI_L1_LOCK:
                if _JTI_L1_FALLBACK.get(replay_key):
                    # L1 caught a replay — emit rejected, NOT accepted_redis_unavailable.
                    # Emitting accepted before this check creates false-positive monitoring
                    # alerts whenever L1 correctly blocks an attack during Redis outage.
                    _emit_audit_log(
                        "rejected_replay_l1",
                        None,
                        None,
                        None,
                        None,
                        peer_sa=issuer,
                    )
                    raise ValueError("jti replay (L1 fallback)")
                _JTI_L1_FALLBACK[replay_key] = True
            # Emit accepted only after L1 confirms this is a first-time token.
            _emit_audit_log(
                "accepted_redis_unavailable",
                None,
                None,
                None,
                None,
                peer_sa=issuer,
            )
    elif fail_closed:
        # Redis not configured / not installed + fail-closed mode:
        # reject everything. (Operator misconfiguration alert.)
        _emit_audit_log("rejected_redis_unavailable", None, None, None, None, peer_sa=issuer)
        raise ValueError("jti check unavailable (Redis not configured, fail-closed mode)")
    else:
        # No Redis configured — L1-only mode (dev / CI / pre-prod).
        async with _JTI_L1_LOCK:
            if _JTI_L1_FALLBACK.get(replay_key):
                _emit_audit_log("rejected_replay_l1", None, None, None, None, peer_sa=issuer)
                raise ValueError("jti replay (L1 only)")
            _JTI_L1_FALLBACK[replay_key] = True

    acting_for: dict = payload.get("acting_for", {})
    identity = AgentIdentity(
        sub=issuer,
        audience=payload.get("aud", our_sa),
        acting_for=acting_for,
        expiry=payload.get("exp", 0),
        jti=jti,
    )
    _emit_audit_log("accepted", identity, None, None, None)
    return identity


# ---------------------------------------------------------------------------
# mint_token
# ---------------------------------------------------------------------------

_IAM_SIGN_JWT_URL = (
    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}:signJwt"
)


async def _call_sign_jwt(our_sa: str, payload_json: str) -> str:
    """Call GCP IAM Credentials signJwt REST API using Application Default Credentials."""
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(google.auth.transport.requests.Request())
    url = _IAM_SIGN_JWT_URL.format(sa_email=our_sa)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            url,
            json={"payload": payload_json},
            headers={"Authorization": f"Bearer {credentials.token}"},
        )
        resp.raise_for_status()
    return resp.json()["signedJwt"]


async def mint_token(our_sa: str, target_audience: str, acting_for: dict[str, Any]) -> str:
    """Mint a signed JWT cached for 240s keyed on (target_audience, json(acting_for))."""
    cache_key = (target_audience, json.dumps(acting_for, sort_keys=True))
    lock = _get_mint_lock()
    async with lock:  # single-flight: holds lock across sign — avoids duplicate signJwt calls
        cached = _MINT_CACHE.get(cache_key)
        if cached is not None:
            return cached
        now = int(time.time())
        payload = {
            "iss": our_sa,
            "sub": our_sa,
            "aud": target_audience,
            "iat": now,
            "exp": now + 300,
            "jti": str(uuid.uuid4()),
            "acting_for": acting_for,
        }
        token = await _call_sign_jwt(our_sa, json.dumps(payload))
        _MINT_CACHE[cache_key] = token
        return token


# ---------------------------------------------------------------------------
# _emit_audit_log — HIPAA structured log
# ---------------------------------------------------------------------------


def _emit_audit_log(
    decision: str,
    identity: AgentIdentity | None,
    method: str | None,
    task_id: str | None,
    trace_id: str | None,
    peer_sa: str | None = None,
) -> None:
    """Emit one HIPAA-compliant structured log entry via the 'a2a.audit' logger.

    Application startup must route logging.getLogger('a2a.audit') to a sink
    (e.g. Cloud Logging). propagate=True (default) ensures records reach the
    root handler if no dedicated sink is configured.
    """
    entry: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "level": "INFO",
        "event": "auth_decision",
        "decision": decision,
        "peer_agent_id": (identity.sub if identity else peer_sa) or "",
        "peer_human_sub": (identity.acting_for.get("human_sub") if identity else "") or "",
        "method": method or "",
        "task_id": task_id or "",
        "jti": (identity.jti if identity else "") or "",
        "trace_id": trace_id or "",
    }
    _audit_logger.info(json.dumps(entry))
