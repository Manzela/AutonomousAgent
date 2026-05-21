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
from typing import Any

logger = logging.getLogger(__name__)


def _on_session_start(session_id: str = "", **_: Any) -> None:
    """Day 1 no-op hook. Day 7 wires this to AgentCard discovery refresh."""
    logger.debug("a2a: on_session_start session=%s (no-op until Day 7)", session_id)


def register(ctx) -> None:
    """Plugin entry point — registers with Hermes PluginManager.

    Day 1: registers a single no-op `on_session_start` hook so the plugin
    loader logs `register: a2a` once at startup (acceptance gate).

    Day 2+: this function grows to wire the FastAPI server, A2A client,
    auth middleware, TaskSpec bridge, and telemetry exporters.
    """
    ctx.register_hook("on_session_start", _on_session_start)
    logger.info("a2a: plugin registered (Day 1 scaffolding shell)")
