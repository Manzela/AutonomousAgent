"""A2A AgentCard — discovery + signing.

Day 1 stub. Day 8 implements:
  - Serve GET /.well-known/agent-card.json (signed via canonicalization JCS / RFC 8785)
  - fetch_peer_card(peer_url) -> AgentCard with signature verification
  - canonicalize_card(card) -> bytes (RFC 8785 JCS for stable hashing)
  - Card refresh on session_start hook (Day 1 hook stub already wired)

Per DEFAULTS-ACCEPTED.md Q9: signing-key rotation gated by engineering
on-call in dev/staging; sponsor + privacy officer co-sign in prod.

Spec reference: docs/specification.md §4 (AgentCard), §5 (discovery).
TODO(Day 8): implement AgentCard pydantic model + JCS canonicalization + signing.
"""

from __future__ import annotations

# Intentionally empty until Day 8.
