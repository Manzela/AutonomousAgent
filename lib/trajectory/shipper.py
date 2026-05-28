"""J3 trajectory shipper. Tails judge-events JSONL, sanitizes via Model
Armor, uploads to GCS. Persistence Trap (#12.c) MUST hold: every record
uploaded MUST have passed templates.sanitize. If sanitize is unavailable,
dispatch F37 and HALT — do NOT fall back to local-log of the un-redacted
record. See audit/2026-05-21-persistence-trap-12c/test-contract.md.

The contract is enforced at three layers:

1. **Per-record sanitize** — `ship_one` calls `templates.sanitize` for a
   single record. Per-batch sanitize is rejected (anti-pattern §) because
   a single-record failure could be misinterpreted as "skip this batch"
   rather than "halt all writes."
2. **Strict response parsing** — `_extract_sanitized_payload` raises
   `ModelArmorSanitizeUnavailable` if the sanitize response shape is
   unrecognizable. Falling back to the original payload would defeat the
   trap when the SDK is upgraded with an incompatible response shape.
3. **F37 dispatch + re-raise** — `ship_batch` catches
   `ModelArmorSanitizeUnavailable` exactly once, dispatches F37 (which
   routes to ``halt_alert_snapshot``: checkpoint, Telegram alert, BLOCKED
   state), then RE-RAISES so the caller's loop stops and no further records
   are shipped under a known-unavailable sanitize endpoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ModelArmorSanitizeUnavailable(Exception):
    """Raised when ``templates.sanitize`` fails, times out, or returns an
    unrecognizable response. Triggers F37 dispatch via
    ``lib.durability.handlers.dispatch("F37", ...)``.

    Do NOT catch this exception inside the sanitize try-block. Do NOT
    suppress it in ``ship_batch``. The only legitimate catch site is the
    outermost orchestrator loop, which has already received the F37
    dispatch and is responsible for the halt-and-restart cycle.
    """


def _default_sanitize_client() -> Any:
    """Lazy import of the Google Cloud Model Armor client.

    Kept as a thin indirection so the unit-test layer can inject a stub
    without importing the SDK at module load time. Mirrors the pattern in
    ``lib.snapshots.gcs_snapshot._gcs_client``.
    """

    from google.cloud import modelarmor_v1  # type: ignore[import-not-found]

    return modelarmor_v1.ModelArmorClient()


def _default_gcs_client() -> Any:
    """Lazy import of the Google Cloud Storage client. Same pattern as
    ``_default_sanitize_client``."""

    from google.cloud import storage  # type: ignore[import-not-found]

    return storage.Client()


def _extract_sanitized_payload(response: Any) -> str:
    """Strict extractor for the Model Armor sanitize response.

    Accepts the response in any of the following shapes and returns the
    sanitized string:

    * a bare string (test stubs frequently use this);
    * an object with one of ``sanitized_content`` / ``content`` / ``text``
      attributes set to a string (real Model Armor SDK responses);
    * a dict with the same keys (older / wrapped SDK responses).

    Raises ``ModelArmorSanitizeUnavailable`` if none of those shapes
    apply. The persistence-trap contract requires that an unrecognizable
    response is treated as sanitize-unavailable, NOT as "ship the
    original payload." This is the load-bearing strictness behind the
    "DO NOT WEAKEN THIS TEST" assertion in the test contract.
    """

    if isinstance(response, str):
        return response

    for attr in ("sanitized_content", "content", "text"):
        candidate = getattr(response, attr, None)
        if isinstance(candidate, str):
            return candidate

    if isinstance(response, dict):
        for key in ("sanitized_content", "content", "text"):
            candidate = response.get(key)
            if isinstance(candidate, str):
                return candidate

    raise ModelArmorSanitizeUnavailable(
        "templates.sanitize returned an unrecognizable response shape; "
        "refusing to ship un-verified payload"
    )


def _object_name_for(verdict: dict) -> str:
    """Deterministic GCS object name from verdict.

    Format: ``trajectory/{date}/{tool_call_id}.json``. Date is the
    ``YYYY-MM-DD`` prefix of the verdict's ``timestamp`` field (ISO 8601);
    falls back to ``undated`` if no timestamp is present. The
    ``tool_call_id`` is required — a verdict without one is shipper-level
    invalid; we still produce a name (``unknown.json``) so the shipper
    fails downstream with a clear "missing tool_call_id" trace rather than
    a KeyError.
    """

    tool_call_id = verdict.get("tool_call_id", "unknown")
    timestamp = verdict.get("timestamp")
    date = timestamp[:10] if isinstance(timestamp, str) and len(timestamp) >= 10 else "undated"
    return f"trajectory/{date}/{tool_call_id}.json"


class TrajectoryShipper:
    """Per-record sanitize + GCS upload for J1 judge verdicts.

    Construction is dependency-injected: ``sanitize_client`` and
    ``gcs_client`` default to None and are lazily replaced with the real
    Google Cloud clients on first use. Tests pass stubs and never trigger
    the lazy default.

    Per the Persistence Trap (#12.c) contract:

    * ``ship_one`` calls Model Armor ``templates.sanitize`` exactly once
      per record and uploads the sanitized payload. Sanitize failure
      raises :class:`ModelArmorSanitizeUnavailable`.
    * ``ship_batch`` iterates ``ship_one`` and, on
      :class:`ModelArmorSanitizeUnavailable`, dispatches F37 (which fires
      ``halt_alert_snapshot``) and RE-RAISES so the caller's loop stops
      under a known-unavailable sanitize endpoint.
    """

    def __init__(
        self,
        bucket: str,
        template: str,
        sanitize_client: Any = None,
        gcs_client: Any = None,
    ) -> None:
        if not bucket:
            raise ValueError("bucket is required (no production binding without it)")
        if not template:
            raise ValueError("template is required (no implicit Floor-Setting-only ship)")
        self.bucket = bucket
        self.template = template
        self._sanitize_client = sanitize_client
        self._gcs_client = gcs_client

    @property
    def sanitize_client(self) -> Any:
        if self._sanitize_client is None:
            self._sanitize_client = _default_sanitize_client()
        return self._sanitize_client

    @property
    def gcs_client(self) -> Any:
        if self._gcs_client is None:
            self._gcs_client = _default_gcs_client()
        return self._gcs_client

    def ship_one(self, verdict: dict) -> None:
        """Sanitize a single verdict via Model Armor, upload sanitized
        payload to GCS.

        **Sync-only contract** (P2-26): both the Model Armor ``sanitize``
        call and the GCS ``upload_from_string`` are synchronous blocking
        I/O. Calling this method directly from an async context blocks the
        event loop. Use :meth:`ship_one_async` from async callers instead.

        Raises:
            ModelArmorSanitizeUnavailable: if ``templates.sanitize`` fails,
                times out, or returns an unrecognizable response shape.

        Side effects:
            - One call to ``sanitize_client.sanitize``.
            - One GCS PUT (only if sanitize succeeded).
        """

        record_payload = json.dumps(verdict, separators=(",", ":"), sort_keys=True)

        try:
            response = self.sanitize_client.sanitize(
                template=self.template,
                content=record_payload,
            )
        except ModelArmorSanitizeUnavailable:
            # _extract_sanitized_payload-equivalent raised inside a custom
            # stub: re-raise unchanged.
            raise
        except Exception as exc:  # noqa: BLE001 — contract requires loud surface
            raise ModelArmorSanitizeUnavailable(
                f"templates.sanitize failed for tool_call_id="
                f"{verdict.get('tool_call_id', '<unknown>')}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        sanitized_payload = _extract_sanitized_payload(response)

        object_name = _object_name_for(verdict)
        bucket_handle = self.gcs_client.bucket(self.bucket)
        blob = bucket_handle.blob(object_name)
        blob.upload_from_string(sanitized_payload, content_type="application/json")

    async def ship_one_async(self, verdict: dict) -> None:
        """Async wrapper around :meth:`ship_one` for use from async callers.

        Delegates to :func:`asyncio.to_thread` so the synchronous Model Armor
        sanitize call and the GCS PUT do not block the event loop.

        Raises:
            ModelArmorSanitizeUnavailable: propagated from :meth:`ship_one`
                unchanged — the persistence-trap contract requires the
                caller's loop to stop on sanitize unavailability.
        """
        await asyncio.to_thread(self.ship_one, verdict)

    def ship_batch(self, verdicts: list[dict]) -> None:
        """Per-record sanitize + upload. A single
        :class:`ModelArmorSanitizeUnavailable` halts the batch.

        On halt: dispatches F37 with the failing record's ``tool_call_id``
        and a payload identifying the shipper, then re-raises. The
        remaining records are NOT shipped under a known-unavailable
        sanitize endpoint.
        """

        for record in verdicts:
            try:
                self.ship_one(record)
            except ModelArmorSanitizeUnavailable as exc:
                # Inline import: the durability handler registry is
                # mutated by `lib.durability.handlers` at import-time, so
                # the import side-effect of registering F37 must happen
                # exactly when we need to dispatch — not earlier (avoids a
                # circular dep at lib.trajectory import time).
                from lib.durability.handlers import dispatch

                dispatch(
                    "F37",
                    error=exc,
                    tool_call_id=record.get("tool_call_id"),
                    payload={
                        "shipper": "trajectory",
                        "verdict_id": record.get("tool_call_id"),
                    },
                )
                raise

    def ship_trajectory(self, session_id: str, trajectory: list[dict]) -> None:
        """MALT logging (Phase 1.3): Ship a full structured trajectory for evaluation.

        A trajectory is a chronological list of turns (inputs -> tool calls -> reasoning -> outputs).
        """
        payload = {"session_id": session_id, "trajectory": trajectory, "type": "malt_trajectory_v1"}
        record_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True)

        try:
            response = self.sanitize_client.sanitize(
                template=self.template,
                content=record_payload,
            )
        except ModelArmorSanitizeUnavailable as exc:
            from lib.durability.handlers import dispatch

            dispatch(
                "F37",
                error=exc,
                tool_call_id=None,
                payload={
                    "shipper": "trajectory",
                    "session_id": session_id,
                },
            )
            raise
        except Exception as exc:
            wrapped_exc = ModelArmorSanitizeUnavailable(
                f"templates.sanitize failed for MALT trajectory session_id={session_id}: "
                f"{type(exc).__name__}: {exc}"
            )
            from lib.durability.handlers import dispatch

            dispatch(
                "F37",
                error=wrapped_exc,
                tool_call_id=None,
                payload={
                    "shipper": "trajectory",
                    "session_id": session_id,
                },
            )
            raise wrapped_exc from exc

        sanitized_payload = _extract_sanitized_payload(response)

        # CI artifact integration (Phase 1.3)
        import os

        local_dir = os.environ.get("MALT_LOCAL_DIR")
        if local_dir:
            import pathlib

            path = pathlib.Path(local_dir)
            path.mkdir(parents=True, exist_ok=True)
            (path / f"{session_id}.json").write_text(sanitized_payload)

        object_name = f"malt/trajectory/{session_id}.json"
        bucket_handle = self.gcs_client.bucket(self.bucket)
        blob = bucket_handle.blob(object_name)
        blob.upload_from_string(sanitized_payload, content_type="application/json")

    def replay_trajectory(self, session_id: str) -> list[dict]:
        """MALT replay (Phase 1.3): Reload exact state from a failed trajectory.

        Fetches the trajectory from GCS and parses it back into a list of structured turns
        so the orchestrator can re-hydrate memory and retry deterministically.
        """
        object_name = f"malt/trajectory/{session_id}.json"
        bucket_handle = self.gcs_client.bucket(self.bucket)
        blob = bucket_handle.blob(object_name)

        if not blob.exists():
            raise FileNotFoundError(f"No MALT trajectory found for session_id={session_id}")

        payload_bytes = blob.download_as_string()
        data = json.loads(payload_bytes)

        return data.get("trajectory", [])

    @classmethod
    def delete_user_blobs(cls, user_id: str) -> dict[str, Any]:
        """GDPR deletion stub for GCS trajectories."""
        raise NotImplementedError("GDPR deletion stub for GCS trajectories")
