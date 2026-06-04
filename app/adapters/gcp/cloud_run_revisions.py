"""SP-26 — Cloud Run revision rollback adapter.

Rolls back a Cloud Run Service to a specific revision by redirecting 100%
of traffic. Uses the Cloud Run Admin API (google.cloud.run_v2.ServicesClient),
lazy-imported so CI runs without the ``gcp`` optional-dep installed.

Design:
  - Operator-only: the caller is responsible for authentication/authorisation.
    ``CloudRunRevisionRollback`` does not embed auth; it delegates to ADC
    (Application Default Credentials) or an injected ``client``.
  - Hard idempotency: if the target revision already serves 100% of traffic,
    returns ``RevisionRollbackResult(already_current=True, duration_s=0.0)``.
  - Lazy imports: ``google.cloud.run_v2`` raised with an install hint if absent.
  - ``_build_traffic_split_request`` is a pure function — unit-testable without GCP.

GCP resource naming:
  Service FQN: ``projects/{project}/locations/{region}/services/{service}``
  Revision FQN: ``projects/{project}/locations/{region}/services/{service}/revisions/{name}``
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.core.rollback import AbstractRevisionRollback, RevisionRollbackResult

logger = logging.getLogger(__name__)


def _lazy_run_v2():
    """Return google.cloud.run_v2, raising ImportError with install hint."""
    try:
        from google.cloud import run_v2  # type: ignore[import-not-found]

        return run_v2
    except ImportError as exc:
        raise ImportError(
            "google-cloud-run is required for CloudRunRevisionRollback. "
            "Install with: uv sync --extra gcp"
        ) from exc


def _build_traffic_split_request(
    *,
    service_name: str,
    revision_name: str,
    run_v2_mod: Any,
) -> Any:
    """Build an UpdateServiceRequest that routes 100% traffic to ``revision_name``.

    Pure function — no I/O, fully unit-testable without a GCP project.

    Traffic is set as a single TRAFFIC_TARGET_REVISION allocation of 100%
    pointing at the specified revision, replacing any current split.
    """
    traffic = run_v2_mod.TrafficTarget(
        type_=run_v2_mod.TrafficTargetAllocationType.TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION,
        revision=revision_name,
        percent=100,
    )
    service = run_v2_mod.Service(
        name=service_name,
        traffic=[traffic],
    )
    return run_v2_mod.UpdateServiceRequest(service=service)


class CloudRunRevisionRollback(AbstractRevisionRollback):
    """Roll back a Cloud Run Service to a prior revision via the Admin API.

    Args:
        project_id: GCP project ID (e.g. ``autonomous-agent-2026``).
        region: Cloud Run region (e.g. ``us-central1``).
        service_name: Cloud Run Service short name (e.g. ``hermes``).
        client: Optional injected ``ServicesClient`` — production leaves this
            None (default ADC client); tests inject a fake.
    """

    is_production_grade: bool = True

    def __init__(
        self,
        *,
        project_id: str,
        region: str,
        service_name: str,
        client: Optional[Any] = None,
    ) -> None:
        if not project_id:
            raise ValueError("project_id must be non-empty")
        if not region:
            raise ValueError("region must be non-empty")
        if not service_name:
            raise ValueError("service_name must be non-empty")
        self._project_id = project_id
        self._region = region
        self._service_name = service_name
        self._service_fqn = f"projects/{project_id}/locations/{region}/services/{service_name}"
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            run_v2 = _lazy_run_v2()
            self._client = run_v2.ServicesClient()
        return self._client

    async def get_active_revision(self) -> str:
        """Return the revision currently handling 100% of traffic."""
        import asyncio

        client = self._get_client()
        service = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.get_service(name=self._service_fqn)
        )
        for traffic_item in service.traffic_statuses:
            if getattr(traffic_item, "percent", 0) == 100:
                return traffic_item.revision
        # Fallback: return the first traffic entry's revision
        if service.traffic_statuses:
            return service.traffic_statuses[0].revision
        return ""

    async def get_revision_history(self, *, limit: int = 10) -> list[str]:
        """Return up to ``limit`` revision FQNs, newest-first."""
        import asyncio

        run_v2 = _lazy_run_v2()
        client = self._get_client()
        revisions_client = run_v2.RevisionsClient(credentials=getattr(client, "_credentials", None))
        request = run_v2.ListRevisionsRequest(parent=self._service_fqn)
        page = await asyncio.get_event_loop().run_in_executor(
            None, lambda: revisions_client.list_revisions(request=request)
        )
        revisions = []
        for rev in page:
            revisions.append(rev.name)
            if len(revisions) >= limit:
                break
        return revisions

    async def rollback_to(self, *, revision_name: str) -> RevisionRollbackResult:
        """Redirect 100% of traffic to ``revision_name``.

        Idempotent: if the revision already handles all traffic, returns
        ``already_current=True`` without an API call.
        """
        import asyncio

        run_v2 = _lazy_run_v2()
        client = self._get_client()

        current = await self.get_active_revision()
        if current == revision_name:
            logger.info("cloud_run_rollback: revision %s already active — no-op", revision_name)
            return RevisionRollbackResult(
                service_name=self._service_fqn,
                prior_revision=current,
                target_revision=revision_name,
                traffic_percent=100,
                duration_s=0.0,
                already_current=True,
            )

        t0 = time.monotonic()
        request = _build_traffic_split_request(
            service_name=self._service_fqn,
            revision_name=revision_name,
            run_v2_mod=run_v2,
        )
        logger.info(
            "cloud_run_rollback: routing 100%% traffic to revision=%s (was %s)",
            revision_name,
            current,
        )
        lro = client.update_service(request=request)
        await asyncio.get_event_loop().run_in_executor(None, lro.result)
        duration_s = time.monotonic() - t0
        logger.info(
            "cloud_run_rollback: done revision=%s duration=%.2fs", revision_name, duration_s
        )
        return RevisionRollbackResult(
            service_name=self._service_fqn,
            prior_revision=current,
            target_revision=revision_name,
            traffic_percent=100,
            duration_s=duration_s,
            already_current=False,
        )
