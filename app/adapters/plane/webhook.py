"""Plane CE webhook normaliser — SP-21.

Converts Plane CE webhook payloads into SteeringEvents and ingests them into the
SP-17 SteeringEventBus.  Implements the C15 no-ping-pong contract:

  - Comments posted by the agent carry ``<!-- plane-origin: {"origin": "agent"} -->``
    (embedded by PlaneBoard.add_comment).  This normaliser extracts the origin and
    calls ``bus.put(event, origin="agent")``, which the bus drops at ingest.
  - Human comments have no such tag (origin defaults to ``"human"``); they become
    SteeringEvents routed to the open interrupt() for the matching thread.

Supported Plane CE webhook events (``event`` field):
  ``comment.created``  — a new comment on a Work Item.
  (all other event types are silently ignored and return None)

GitHub two-way sync (``issue.sync`` webhook) is Plane-Pro-only — NOT implemented
for Plane CE (free tier).  The dedup invariant (identical webhook payloads → only
one SteeringEvent) is satisfied by the SteeringEventBus (channel, origin_id) ledger.

DEFERRED (named, NOT silently dropped):
  * issue.updated event type (status-change → steering);
  * webhook signature verification (HMAC-SHA256 ``X-Plane-Signature`` header);
  * Plane MCP server event subscription.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from app.core.graph_state import SteeringEvent
from app.core.steering import SteeringEventBus

logger = logging.getLogger(__name__)

# Matches the origin tag embedded by PlaneBoard.add_comment:
# <!-- plane-origin: {"origin": "agent"} -->
_ORIGIN_RE = re.compile(r"<!-- plane-origin: ({[^}]+}) -->")


def _extract_origin(body: str) -> str:
    """Parse the origin tag from a Plane comment body.  Returns "human" if absent
    or if the JSON cannot be parsed (fail-safe: unknown → treated as human)."""
    if not body:
        return "human"
    m = _ORIGIN_RE.search(body)
    if not m:
        return "human"
    try:
        return json.loads(m.group(1)).get("origin", "human")
    except (json.JSONDecodeError, AttributeError):
        return "human"


def normalise_plane_webhook(
    payload: dict,
    *,
    bus: SteeringEventBus,
    thread_id: str,
) -> Optional[SteeringEvent]:
    """Convert a Plane CE webhook payload to a SteeringEvent and ingest it.

    Parameters
    ----------
    payload:
        The raw Plane CE webhook JSON payload.
    bus:
        The SP-17 SteeringEventBus that deduplicates and routes events.
    thread_id:
        The LangGraph thread_id this Plane board is scoped to.

    Returns
    -------
    The SteeringEvent if it was accepted by the bus (new, human-authored).
    ``None`` if the event was dropped (agent-authored, duplicate, or unsupported
    event type).
    """
    event_type = payload.get("event", "")
    if event_type != "comment.created":
        logger.debug("plane.webhook: ignoring event type %r", event_type)
        return None

    comment: dict = payload.get("data", {})
    comment_id = str(comment.get("id", ""))
    if not comment_id:
        logger.warning("plane.webhook: comment.created payload has no id — dropped")
        return None

    body = comment.get("comment_html", "") or comment.get("comment", "") or ""
    origin = _extract_origin(body)

    event = SteeringEvent(
        thread_id=thread_id,
        channel="board",
        origin_id=comment_id,
        kind="steer",
        verb="APPROVE",
        interrupt_id=None,
        payload={"body": body, "issue_id": str(comment.get("issue", ""))},
        ts=comment.get("created_at", ""),
    )

    accepted = bus.put(event, origin=origin)
    if not accepted:
        logger.debug(
            "plane.webhook: event dropped (agent-authored or deduped) " "origin_id=%s origin=%s",
            comment_id,
            origin,
        )
        return None

    logger.debug(
        "plane.webhook: accepted SteeringEvent channel=board origin_id=%s thread=%s",
        comment_id,
        thread_id,
    )
    return event
