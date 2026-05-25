"""A2A (Agent-to-Agent) protocol plugin — Day 1 scaffolding shell.

Spike scope: bidirectional A2A integration with one canary peer, behind a
feature flag, observable in Cloud Trace + Phoenix, with signed audit logs.

Day 1 deliverable: this module loads, registers with Hermes, and does
nothing useful. The Day 2-10 build-out fills in:
  - server.py        — FastAPI JSON-RPC dispatch (Day 2)
  - client.py        — outbound A2A client (Day 3)
  - SSE streaming    — message/stream + tasks/subscribe (Day 4)
  - auth.py          — JWT mint + verify (Day 5)
  - telemetry        — traceparent propagation (Day 6)
  - task_bridge.py   — TaskSpec <-> A2A Task (Day 7)
  - agent_card.py    — signed /.well-known/agent-card.json (Day 8)

See: audit/2026-05-21-a2a-spike-plan/spike-plan.md for the day-by-day plan.
See: audit/2026-05-21-a2a-spike-plan/SPEC-VERSION.md for the pinned spec SHA.
See: audit/2026-05-21-a2a-spike-plan/DEFAULTS-ACCEPTED.md for Q1-Q12 lock-in.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# GCP service account email format — name and project-id each:
#   starts [a-z], ends [a-z0-9], body [a-z0-9-], total 6-30 chars
_SA_EMAIL_RE = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)


def _on_session_start(session_id: str = "", **_: Any) -> None:
    """Day 1 no-op hook. Day 7 wires this to AgentCard discovery refresh."""
    logger.debug("a2a: on_session_start session=%s (no-op until Day 7)", session_id)


def register(ctx) -> None:  # type: ignore[type-arg]
    """Plugin entry point — registers with Hermes PluginManager.

    Reads HERMES_A2A_ENABLED at call time (not import time) so tests can
    monkeypatch os.environ without importlib.reload().

    Day 1: registers a single no-op `on_session_start` hook so the plugin
    loader logs `register: a2a` once at startup (acceptance gate).
    """
    enabled_raw = os.getenv("HERMES_A2A_ENABLED")

    # Deprecation: default is currently true; will flip to false next release.
    if enabled_raw is None:
        logger.warning(
            "a2a: HERMES_A2A_ENABLED env var not set; defaulting true "
            "(will change to false in next release — set explicitly to suppress this warning)"
        )

    enabled = (enabled_raw or "true").lower() == "true"

    if not enabled:
        logger.info("a2a: HERMES_A2A_ENABLED=false; plugin skipped — no routes registered")
        return

    # H6 — validate SA identity format before wiring anything
    sa = os.getenv("HERMES_A2A_SA", "")
    if not sa or not _SA_EMAIL_RE.fullmatch(sa):
        raise RuntimeError(
            f"A2A: HERMES_A2A_SA is missing or invalid: {sa!r}. "
            "Must be a GCP service account email "
            "(<name>@<project>.iam.gserviceaccount.com)"
        )
    logger.info("a2a: HERMES_A2A_SA validated: %s", sa)

    ctx.register_hook("on_session_start", _on_session_start)
    logger.info("a2a: plugin registered")
