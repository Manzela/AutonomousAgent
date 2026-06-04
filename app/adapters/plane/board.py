"""PlaneBoard — AbstractBoard adapter for Plane CE REST API (SP-21).

Projects the agent's DAG onto a Plane CE workspace as Work Items (issues):
  - parent goal card → a top-level Plane issue
  - child node cards → sub-issues (parent= the goal issue id)

The board is OUTBOUND by default (mirrors AbstractBoard semantics): a graph node
MUST NOT read board state directly; board comments reach the graph only via the
SP-17 SteeringEventBus after the PlaneWebhookNormaliser strips agent-authored ones.

Origin-tag protocol (C15 no-ping-pong):
  - Agent-authored comments embed <!-- plane-origin: {"origin": "agent"} --> in
    the comment body so the webhook normaliser can call bus.put(..., origin="agent")
    and have the event dropped at ingest.
  - Human comments carry no such tag (or origin="human"); they become SteeringEvents.

"Blocked on you" filter (SP-17 criterion 6 / U-3):
  - list_blocked_cards() returns cards in status="blocked" — these are threads
    awaiting operator input (interrupt()-paused or SP-05-parked). The filter
    reuses the board's own in-memory store; no new persistence layer is added.

C14 invariant is inherited from AbstractBoard's concrete public methods (create_card /
set_status / mark_done) — NOT overridden here. Only the 4 storage primitives are
implemented by this adapter.

Security (C9 review, round 1):
  - workspace_slug and project_id are validated against a strict slug charset
    (alphanumeric + hyphen/underscore, max 256 chars) at construction time so
    path-traversal segments (../../admin) are rejected before they reach any URL.
  - _create_plane_issue rolls back _store on REST failure (orphan-card prevention).
  - get_comments branches on the response type before calling .get() to avoid
    AttributeError when Plane returns an un-enveloped list.

Plane CE REST API base paths (relative to base_url):
  POST   /api/v1/workspaces/{ws}/projects/{proj}/issues/
  PATCH  /api/v1/workspaces/{ws}/projects/{proj}/issues/{issue_id}/
  GET    /api/v1/workspaces/{ws}/projects/{proj}/issues/{issue_id}/
  POST   /api/v1/workspaces/{ws}/projects/{proj}/issues/{issue_id}/comments/
  GET    /api/v1/workspaces/{ws}/projects/{proj}/issues/{issue_id}/comments/

DEFERRED (named, NOT silently dropped):
  * real GitHub two-way sync (Plane-Pro-only feature; not implemented in CE);
  * live state-name resolution (mapping CardStatus → Plane project state UUID);
  * first-party Plane MCP server integration;
  * Board poll / webhook subscription setup.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

from app.core.board import AbstractBoard, BoardError, Card

logger = logging.getLogger(__name__)

# C9-P1: validate workspace/project path components to prevent path traversal.
# Only alphanumeric, hyphen, underscore, and dot — no slashes or percent-encoded sequences.
_SLUG_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,256}\Z")

# HTML comment sentinel embedded in every agent-authored board comment so the
# SP-17 webhook normaliser can detect and drop them at ingest (no-ping-pong, C15).
_ORIGIN_TAG_TPL = "<!-- plane-origin: {payload} -->"
_ORIGIN_PREFIX = "<!-- plane-origin: "


class PlaneBoard(AbstractBoard):
    """AbstractBoard over Plane CE REST API.

    Parameters
    ----------
    workspace_slug:
        The Plane workspace slug (e.g. ``"my-workspace"``).
    project_id:
        The Plane project UUID.
    api_token:
        Plane CE API token (X-Api-Token header).  Ignored when *client* is provided.
    base_url:
        Plane CE base URL (default ``"http://localhost"``).  Ignored when *client*
        is provided.
    client:
        Optional pre-built ``httpx.Client`` — inject for hermetic tests.  When
        None a new client is created from *base_url* + *api_token*.
    """

    def __init__(
        self,
        workspace_slug: str,
        project_id: str,
        *,
        api_token: str = "",
        base_url: str = "http://localhost",
        client: Optional[httpx.Client] = None,
    ) -> None:
        # C9-P1: reject path-traversal slugs before they reach any URL construction.
        if not _SLUG_RE.match(workspace_slug):
            raise ValueError(
                f"workspace_slug {workspace_slug!r} is not a valid Plane slug "
                "(allowed: A-Z a-z 0-9 _ . - ; max 256 chars)"
            )
        if not _SLUG_RE.match(project_id):
            raise ValueError(
                f"project_id {project_id!r} is not a valid Plane id "
                "(allowed: A-Z a-z 0-9 _ . - ; max 256 chars)"
            )
        self._workspace = workspace_slug
        self._project = project_id
        self._client: httpx.Client = client or httpx.Client(
            base_url=base_url,
            headers={"X-Api-Token": api_token, "Content-Type": "application/json"},
        )
        self._store: dict[str, Card] = {}
        self._seq = 0
        # local card id → Plane issue UUID  (set after a successful POST)
        self._plane_ids: dict[str, str] = {}

    # ── storage primitives ────────────────────────────────────────────────────

    def _new_id(self) -> str:
        self._seq += 1
        return f"card-{self._seq}"

    def _put(self, card: Card) -> None:
        """Write card to local store and sync to Plane REST API."""
        self._store[card.id] = card
        plane_id = self._plane_ids.get(card.id)
        if plane_id is None:
            self._create_plane_issue(card)
        else:
            self._update_plane_issue(plane_id, card)

    def get_card(self, card_id: str) -> Card:
        try:
            return self._store[card_id]
        except KeyError:
            raise BoardError(f"no such card: {card_id!r}") from None

    def list_cards(self, *, thread_id: Optional[str] = None) -> list[Card]:
        cards = list(self._store.values())
        if thread_id is None:
            return cards
        return [c for c in cards if c.thread_id == thread_id]

    # ── Plane API helpers ─────────────────────────────────────────────────────

    def _issues_url(self) -> str:
        return f"/api/v1/workspaces/{self._workspace}/projects/{self._project}/issues/"

    def _issue_url(self, plane_id: str) -> str:
        return f"/api/v1/workspaces/{self._workspace}/projects/{self._project}/issues/{plane_id}/"

    def _comments_url(self, plane_id: str) -> str:
        return f"/api/v1/workspaces/{self._workspace}/projects/{self._project}/issues/{plane_id}/comments/"

    def _card_to_payload(self, card: Card) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": card.title,
            "description_html": card.body or "",
            "metadata": {
                "thread_id": card.thread_id,
                "card_id": card.id,
                "node_id": card.node_id,
                "status": card.status,
            },
        }
        parent_plane_id = self._plane_ids.get(card.parent_id) if card.parent_id else None
        if parent_plane_id:
            payload["parent"] = parent_plane_id
        return payload

    def _create_plane_issue(self, card: Card) -> None:
        # C9-P2: do NOT permanently write to _store before a successful REST create.
        # On failure, roll back the _store entry so the card does not become a zombie
        # (visible via get_card/list_cards but with no backing Plane issue).
        try:
            resp = self._client.post(self._issues_url(), json=self._card_to_payload(card))
            resp.raise_for_status()
        except Exception:
            self._store.pop(card.id, None)
            raise
        plane_id = resp.json().get("id", card.id)
        self._plane_ids[card.id] = plane_id

    def _update_plane_issue(self, plane_id: str, card: Card) -> None:
        resp = self._client.patch(
            self._issue_url(plane_id),
            json={
                "name": card.title,
                "metadata": {
                    "thread_id": card.thread_id,
                    "card_id": card.id,
                    "node_id": card.node_id,
                    "status": card.status,
                },
            },
        )
        resp.raise_for_status()

    # ── Comment API (origin-tag protocol) ────────────────────────────────────

    def add_comment(self, card_id: str, body: str, *, origin: str) -> dict[str, Any]:
        """Post a comment to the matching Plane Work Item.

        The *origin* value (``"agent"`` or ``"human"``) is embedded as a JSON
        HTML-comment tag in the body so the SP-17 webhook normaliser can detect
        agent-authored comments and drop them at bus.put() time (C15 no-ping-pong).

        Returns the Plane comment payload ``{"id": ..., "origin": origin}``.

        Raises ``BoardError`` if the card has no backing Plane issue yet (i.e. it
        was never written to Plane).
        """
        plane_id = self._plane_ids.get(card_id)
        if plane_id is None:
            raise BoardError(f"no Plane issue for card {card_id!r} — card must be pushed first")
        tag = _ORIGIN_TAG_TPL.format(payload=json.dumps({"origin": origin}))
        tagged_body = f"{body}\n{tag}"
        resp = self._client.post(
            self._comments_url(plane_id),
            json={"comment_html": tagged_body},
        )
        resp.raise_for_status()
        comment_id = resp.json().get("id", f"cmt-{card_id}-1")
        logger.debug(
            "plane.add_comment: card=%s plane=%s origin=%s comment_id=%s",
            card_id,
            plane_id,
            origin,
            comment_id,
        )
        return {"id": comment_id, "origin": origin}

    def get_comments(self, card_id: str) -> list[dict[str, Any]]:
        """Return comments on the Plane Work Item backing *card_id*."""
        plane_id = self._plane_ids.get(card_id)
        if plane_id is None:
            raise BoardError(f"no Plane issue for card {card_id!r}")
        resp = self._client.get(self._comments_url(plane_id))
        resp.raise_for_status()
        # C9-P1: parse once, branch on type to avoid AttributeError when Plane
        # returns an un-enveloped list (list.get does not exist).
        data = resp.json()
        if isinstance(data, dict):
            return data.get("results", [])
        if isinstance(data, list):
            return data
        return []

    # ── "Blocked on you" filter (SP-17 criterion 6 / U-3) ───────────────────

    def list_blocked_cards(self, *, thread_id: Optional[str] = None) -> list[Card]:
        """Return cards in ``status="blocked"`` — the operator "blocked on you" view.

        These are threads whose graph is interrupt()-paused or SP-05-parked and
        waiting for operator input.  No new persistence store is added; this filters
        the board's in-memory card store (reuses the SP-17 query contract).
        """
        return [c for c in self.list_cards(thread_id=thread_id) if c.status == "blocked"]
