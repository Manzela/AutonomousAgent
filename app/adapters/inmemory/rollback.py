"""SP-26 — in-memory rollback test double."""

from __future__ import annotations

import time

from app.core.rollback import AbstractRevisionRollback, RevisionRollbackResult


class RecordingRevisionRollback(AbstractRevisionRollback):
    """Hermetic test double: records rollback calls without hitting Cloud Run.

    Initialise with a list of revision names (newest-first). The first entry
    is treated as the current active revision.
    """

    def __init__(self, revisions: list[str]) -> None:
        if not revisions:
            raise ValueError("revisions must be non-empty")
        self._revisions = list(revisions)
        self._active = revisions[0]
        self.calls: list[str] = []

    async def get_active_revision(self) -> str:
        return self._active

    async def get_revision_history(self, *, limit: int = 10) -> list[str]:
        return self._revisions[:limit]

    async def rollback_to(self, *, revision_name: str) -> RevisionRollbackResult:
        already = revision_name == self._active
        t0 = time.monotonic()
        self.calls.append(revision_name)
        if not already:
            self._active = revision_name
        return RevisionRollbackResult(
            service_name="recording",
            prior_revision=self._revisions[0] if not already else revision_name,
            target_revision=revision_name,
            traffic_percent=100,
            duration_s=0.0 if already else time.monotonic() - t0,
            already_current=already,
        )
