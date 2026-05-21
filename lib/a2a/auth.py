"""A2A auth — JWT mint + verify (composite identity).

Day 1 stub. Day 5 implements:
  - mint_token(target_audience, acting_for) -> JWT (via Google iamcredentials signJwt)
  - verify_token(jwt_str) -> AgentIdentity (JWKS fetch from googleapis.com)
  - AgentIdentity dataclass (sub, audience, acting_for, expiry)
  - In-memory TTL caches: minted JWTs (5min), jti replay (24h)
  - _emit_audit_log helper -> Cloud Logging structured audit entry

Per DEFAULTS-ACCEPTED.md:
  - Q1: acting_for claim shape = {human_sub, human_session_id, consent_scope}
  - Q3: GCP-only federation (JWKS source = googleapis.com)
  - Q5: opaque pseudonym:* IDs for human_sub (PHI posture)
  - Q6: manual revocation runbook (SA disable + email notification)

Spec reference: docs/specification.md §8 (authentication), §10 (audit logs).
TODO(Day 5): implement mint_token + verify_token + AgentIdentity + TTL caches.
"""

from __future__ import annotations

# Intentionally empty until Day 5.
