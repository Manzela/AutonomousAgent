"""A2A <-> Hermes TaskSpec bridge (Day 7).

Public API
----------
bridge_inbound_to_taskspec(a2a_task, agent_identity, *, trace_id=None) -> TaskSpec
bridge_taskspec_status_to_a2a(spec) -> str
cancel_dispatch(task_id) -> str
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

# Re-export SpecStatus from lib.anchors when available (for type consumers).
# The bridge always returns its own TaskSpec dataclass regardless, because
# lib.anchors.task_spec.TaskSpec is a Pydantic model with a different schema
# (no owner/metadata fields) and is not the right shape for bridge outputs.
try:
    from lib.anchors.task_spec import SpecStatus  # type: ignore[import]
except Exception:
    SpecStatus = str  # type: ignore[assignment,misc]


@dataclass
class TaskSpec:
    """Bridge-internal TaskSpec — owns owner, metadata, status, and id.

    Intentionally separate from lib.anchors.task_spec.TaskSpec (which is a
    Pydantic model for the spec-store persistence layer). The bridge creates
    this lightweight object and callers can promote it to an anchors TaskSpec
    via spec_store.create() when ready.
    """

    id: str
    owner: str
    metadata: dict = field(default_factory=dict)
    status: str = "draft"


_SPEC_STATUS_TO_A2A: dict[str, str] = {
    "draft": "SUBMITTED",
    "draft_locked": "WORKING",
    "locked": "WORKING",
    "superseded": "CANCELED",
}


def get_spec_metadata_for_test(spec: Any) -> dict[str, Any]:
    """Test helper — retrieve bridge metadata from a TaskSpec's metadata dict."""
    return getattr(spec, "metadata", {}) or {}


def _anchors_cancel(raw_args: str) -> str:
    try:
        from lib.anchors import _slash_cancel  # type: ignore[import]

        return _slash_cancel(raw_args)
    except Exception as exc:
        return f"cancel stub: {raw_args} ({exc})"


def bridge_inbound_to_taskspec(
    a2a_task: dict[str, Any],
    agent_identity: Any,
    *,
    trace_id: str | None = None,
) -> TaskSpec:
    """Convert an inbound A2A task dict + AgentIdentity into a bridge TaskSpec."""
    owner: str = (agent_identity.acting_for or {}).get("human_sub", "unknown")
    a2a_task_id: str = a2a_task.get("id", "")
    metadata: dict[str, Any] = {"a2a_task_id": a2a_task_id}
    if trace_id is not None:
        metadata["a2a_trace_id"] = trace_id
    spec_id = str(uuid.uuid4())
    return TaskSpec(id=spec_id, owner=owner, metadata=metadata, status="draft")


def bridge_taskspec_status_to_a2a(spec: Any) -> str:
    """Map SpecStatus -> A2A TaskState string. Raises KeyError for unknown statuses."""
    status: str = getattr(spec, "status", "draft")
    try:
        return _SPEC_STATUS_TO_A2A[status]
    except KeyError:
        raise KeyError(
            f"Unknown SpecStatus {status!r}. Expected: {list(_SPEC_STATUS_TO_A2A)}"
        ) from None


def cancel_dispatch(task_id: str) -> str:
    """Dispatch a cancel request to the /cancel slash command path."""
    return _anchors_cancel(task_id)
