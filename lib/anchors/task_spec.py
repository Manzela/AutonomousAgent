"""Immutable TaskSpec — the anchor every P1-2 judge scores against.

Schema is versioned via `schema_version`. Spec edits create a new spec
with `parent_spec_sha` pointing back; the old spec gets `status='superseded'`.
No in-place mutation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class Scope(BaseModel):
    """In-scope and out-of-scope items for the task."""

    in_scope: list[str] = Field(min_length=1)
    out_of_scope: list[str] = Field(min_length=1)


IntentCategory = Literal["coding", "audit", "research", "writing", "ops", "data", "unknown"]
SpecStatus = Literal["draft", "draft_locked", "locked", "superseded"]


class TaskSpec(BaseModel):
    """Locked task contract — immutable post-`status='locked'`.

    The 6 mandatory fields are what every judge in P1-2 scores against.
    Operational fields (budget, deadline, etc.) are optional.
    """

    # --- Mandatory (clarification loop must populate all 6) ---
    title: str = Field(min_length=1)
    intent: str = Field(min_length=1)  # 1-3 sentences; the "why"
    acceptance_criteria: list[str] = Field(min_length=1)
    scope: Scope
    success_metrics: list[str] = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)

    # --- Optional (defaults when user doesn't specify) ---
    budget_usd_cap: Optional[float] = None
    deadline_utc: Optional[datetime] = None
    escalation_h: int = 24
    owner_telegram_id: Optional[int] = None
    parent_spec_sha: Optional[str] = None  # for spec versioning

    # --- Auto-populated metadata (set by spec_store, not user) ---
    spec_id: UUID
    spec_sha: str  # sha256 of normalized JSON; computed by spec_store
    created_at: datetime
    created_by: int  # telegram user_id
    schema_version: Literal["1"] = "1"
    status: SpecStatus = "draft"
    superseded_by: Optional[str] = None
    intent_category: IntentCategory = "unknown"
