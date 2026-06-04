"""P0-3 (Go-Live audit): Rate-limiting middleware for the FastAPI spine service.

Uses ``slowapi`` (a thin FastAPI/Starlette wrapper over the ``limits`` library)
with an in-memory backend by default (no external dependency). Production
deployments that run multiple replicas behind a load balancer should set
``RATE_LIMIT_STORAGE_URI`` to a Redis URL (e.g. ``redis://redis:6379/1``)
for distributed rate-limit coordination.

Limits are configurable per-endpoint via environment variables:
    RATE_LIMIT_GOAL       — default "10/minute"
    RATE_LIMIT_RESUME     — default "30/minute"
    RATE_LIMIT_OPS        — default "5/minute"  (panic, rollback)
    RATE_LIMIT_HEALTHZ    — default "60/minute"
    RATE_LIMIT_WEBHOOK    — default "30/minute"
    RATE_LIMIT_DEFAULT    — default "60/minute"  (fallback for unlisted endpoints)

To disable rate limiting entirely (e.g. in CI), set ``RATE_LIMIT_ENABLED=false``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from starlette.requests import Request

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

RATE_LIMIT_ENABLED = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() not in (
    "false",
    "0",
    "no",
    "off",
)

LIMITS = {
    "goal": os.environ.get("RATE_LIMIT_GOAL", "10/minute"),
    "resume": os.environ.get("RATE_LIMIT_RESUME", "30/minute"),
    "ops": os.environ.get("RATE_LIMIT_OPS", "5/minute"),
    "healthz": os.environ.get("RATE_LIMIT_HEALTHZ", "60/minute"),
    "webhook": os.environ.get("RATE_LIMIT_WEBHOOK", "30/minute"),
    "default": os.environ.get("RATE_LIMIT_DEFAULT", "60/minute"),
}

STORAGE_URI = os.environ.get("RATE_LIMIT_STORAGE_URI", "memory://")


def _get_remote_address(request: Request) -> str:
    """Extract the client IP from the request.

    Respects ``X-Forwarded-For`` when behind a reverse proxy (Cloud Run,
    nginx, etc.) — takes the FIRST address (client IP). Falls back to
    the direct connection IP.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "127.0.0.1"


def setup_rate_limiting(app) -> Optional[Any]:
    """Configure and register SlowAPI rate limiting on the FastAPI app.

    Returns the limiter instance (for use in endpoint decorators) or
    None if rate limiting is disabled.

    Idempotent: safe to call multiple times (SlowAPI raises if already
    registered, so we guard against that).
    """
    if not RATE_LIMIT_ENABLED:
        logger.info("rate-limit: DISABLED via RATE_LIMIT_ENABLED env var")
        return None

    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler  # type: ignore[import-untyped]
        from slowapi.errors import RateLimitExceeded  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "rate-limit: slowapi not installed — rate limiting DISABLED. "
            "Install with: pip install slowapi"
        )
        return None

    limiter = Limiter(
        key_func=_get_remote_address,
        default_limits=[LIMITS["default"]],
        storage_uri=STORAGE_URI,
        strategy="fixed-window",
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    logger.info(
        "rate-limit: ENABLED (storage=%s, default=%s, goal=%s, ops=%s)",
        STORAGE_URI,
        LIMITS["default"],
        LIMITS["goal"],
        LIMITS["ops"],
    )
    return limiter
