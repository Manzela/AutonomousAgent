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
import time
import uuid
from dataclasses import dataclass
from typing import Any

import cachetools
import httpx
import jwt

logger = logging.getLogger(__name__)
_audit_logger = logging.getLogger("a2a.audit")
_audit_logger.addHandler(logging.NullHandler())

_MINT_CACHE: cachetools.TTLCache[tuple[str, str], str] = cachetools.TTLCache(
    maxsize=10_000, ttl=240
)
_MINT_LOCK: asyncio.Lock | None = (
    None  # initialized lazily (avoids event-loop-before-creation error)
)
_JTI_CACHE: cachetools.TTLCache[tuple[str, str], bool] = cachetools.TTLCache(
    maxsize=100_000,
    ttl=600,  # 2× the token lifetime (tokens expire in 300s) — ensures jti is tracked
    # for the full replay window even if the entry was added near token exp
)
_JTI_LOCK: asyncio.Lock | None = (
    None  # initialized lazily (avoids event-loop-before-creation error)
)
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


def _get_jti_lock() -> asyncio.Lock:
    global _JTI_LOCK
    if _JTI_LOCK is None:
        _JTI_LOCK = asyncio.Lock()
    return _JTI_LOCK


def _get_mint_lock() -> asyncio.Lock:
    global _MINT_LOCK
    if _MINT_LOCK is None:
        _MINT_LOCK = asyncio.Lock()
    return _MINT_LOCK


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
    lock = _get_jti_lock()
    async with lock:
        if _JTI_CACHE.get(replay_key):
            _emit_audit_log("rejected_replay", None, None, None, None, peer_sa=issuer)
            raise ValueError("jti replay")
        _JTI_CACHE[replay_key] = True

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
