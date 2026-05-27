"""A2A AgentCard — discovery + signing (Day 8).

Per spike-plan.md §Day 8:
  - build_agent_card(our_sa, base_url) -> dict
  - canonicalize_card(card) -> bytes  (RFC 8785 JCS — stdlib json.dumps + sort_keys)
  - sign_card(card, our_sa) -> dict   (adds 'signature' via GCP signBlob)
  - verify_card_signature(card_with_sig, issuer_sa) -> bool

_call_sign_blob and _fetch_public_key_for_sa are injectable in tests.

Per DEFAULTS-ACCEPTED.md Q9: signing-key rotation gated by engineering
on-call in dev/staging; sponsor + privacy officer co-sign in prod.

Spec reference: docs/specification.md §4 (AgentCard), §5 (discovery).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

_CAPABILITIES = ["message_send", "message_stream", "task_get", "task_subscribe"]
_SECURITY_SCHEMES = ["oauth2", "jwt"]
_CARD_TTL_SECONDS = 86_400


def build_agent_card(our_sa: str, base_url: str) -> dict[str, Any]:
    """Build an unsigned AgentCard dict for this agent."""
    now = int(time.time())
    return {
        "id": our_sa,
        "base_url": base_url.rstrip("/"),
        "capabilities": _CAPABILITIES,
        "security_schemes": _SECURITY_SCHEMES,
        "jwks_url": f"https://www.googleapis.com/service_accounts/v1/jwk/{our_sa}",
        "iat": now,
        "exp": now + _CARD_TTL_SECONDS,
    }


def canonicalize_card(card: dict[str, Any]) -> bytes:
    """RFC 8785-compatible canonicalization via stdlib json.dumps.

    Keys are sorted recursively; no extra whitespace; UTF-8 encoded.
    'signature' is excluded — it is not part of the signed payload.

    Equivalent to JCS for the ASCII-only key names used in AgentCard.
    """
    payload = {k: v for k, v in card.items() if k != "signature"}
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )


# ---------------------------------------------------------------------------
# Private helpers — mock these in tests to avoid GCP calls
# ---------------------------------------------------------------------------


async def _call_sign_blob(data: bytes, sa_email: str) -> str:
    """Call GCP IAM signBlob. Returns base64url-encoded signature. Now async."""
    import google.auth
    import google.auth.transport.requests

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    req = google.auth.transport.requests.Request()
    await asyncio.to_thread(credentials.refresh, req)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}:signBlob",
            json={"payload": base64.b64encode(data).decode()},
            headers={"Authorization": f"Bearer {credentials.token}"},
        )
        resp.raise_for_status()
    sig_bytes = base64.b64decode(resp.json()["signedBlob"])
    return base64.urlsafe_b64encode(sig_bytes).rstrip(b"=").decode()


def _fetch_public_key_for_sa(sa_email: str):
    """Fetch RSA public key from Google JWKS endpoint."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

    resp = httpx.get(
        f"https://www.googleapis.com/service_accounts/v1/jwk/{sa_email}",
        timeout=10.0,
    )
    resp.raise_for_status()
    # Use the first key — agent cards don't carry a kid header to match against.
    # In production, pick the key whose kid matches the signature header.
    # For the spike a single-key SA is the norm; add kid matching in v2.
    keys = resp.json().get("keys", [])
    if not keys:
        raise ValueError(f"JWKS empty for {sa_email}")
    jwk = keys[0]

    def _b64url_int(s: str) -> int:
        s += "=" * ((4 - len(s) % 4) % 4)
        return int.from_bytes(base64.urlsafe_b64decode(s), "big")

    return RSAPublicNumbers(_b64url_int(jwk["e"]), _b64url_int(jwk["n"])).public_key(
        default_backend()
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def sign_card(card: dict[str, Any], our_sa: str) -> dict[str, Any]:
    """Sign the canonicalized card via GCP signBlob.

    Validates exp bounds before signing (H8):
      - Rejects cards that are already expired (exp <= now).
      - Rejects cards whose exp exceeds the maximum allowed TTL by more than 60 s.
    """
    unsigned = {k: v for k, v in card.items() if k != "signature"}
    now = int(time.time())
    exp = unsigned.get("exp", 0)
    if exp <= now:
        raise ValueError(f"sign_card: card is already expired (exp={exp}, now={now})")
    if exp - now > _CARD_TTL_SECONDS + 60:
        raise ValueError(
            f"sign_card: card exp exceeds maximum TTL "
            f"(exp={exp}, max_allowed={now + _CARD_TTL_SECONDS + 60})"
        )
    return {**unsigned, "signature": await _call_sign_blob(canonicalize_card(unsigned), our_sa)}


def verify_card_signature(card_with_sig: dict[str, Any], issuer_sa: str) -> bool:
    """Verify the signature on a signed AgentCard.

    Raises:
        ValueError: Card is expired.
        KeyError: 'signature' field missing.
    """
    if card_with_sig.get("exp", 0) < int(time.time()):
        raise ValueError("AgentCard is expired")
    sig_b64url: str = card_with_sig["signature"]
    canonical_bytes = canonicalize_card(card_with_sig)
    public_key = _fetch_public_key_for_sa(issuer_sa)
    try:
        sig_bytes = base64.urlsafe_b64decode(sig_b64url + "=" * ((4 - len(sig_b64url) % 4) % 4))
        public_key.verify(sig_bytes, canonical_bytes, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception as exc:
        logger.warning(
            "a2a: AgentCard signature verification failed for issuer=%s exc_type=%s",
            issuer_sa,
            type(exc).__name__,
        )
        return False
