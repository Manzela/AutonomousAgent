"""SP-26 — Deployment rollback ABC + result types.

AbstractRevisionRollback defines the contract for rolling back a Cloud Run
Service to a prior revision by redirecting 100% of traffic.

Hybrid adapter pattern (CLAUDE.md / 04-gcp-native-adapter-plan.md):
  - app/core/rollback.py               — AbstractRevisionRollback ABC + types (this file).
  - app/adapters/gcp/cloud_run_revisions.py — CloudRunRevisionRollback (GCP, lazy-import).
  - app/adapters/inmemory/rollback.py  — RecordingRevisionRollback (hermetic test double).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class RevisionRollbackResult:
    """Result of a rollback operation."""

    service_name: str
    prior_revision: str
    target_revision: str
    traffic_percent: int
    duration_s: float
    already_current: bool


class AbstractRevisionRollback(ABC):
    """Roll back a Cloud Run Service to a specific (or prior) revision."""

    @abstractmethod
    async def get_active_revision(self) -> str:
        """Return the currently-active revision name."""
        raise NotImplementedError

    @abstractmethod
    async def get_revision_history(self, *, limit: int = 10) -> list[str]:
        """Return up to ``limit`` revision names, newest-first."""
        raise NotImplementedError

    @abstractmethod
    async def rollback_to(self, *, revision_name: str) -> RevisionRollbackResult:
        """Redirect 100% of traffic to ``revision_name``.

        If ``revision_name`` already carries 100% of traffic, returns a result with
        ``already_current=True`` and ``duration_s=0.0`` (idempotent).
        """
        raise NotImplementedError
