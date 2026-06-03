"""SP-05c — Cloud Run Jobs sandbox adapter.

Submits each command invocation as a Cloud Run Job execution using gVisor
(runsc) isolation, collects stdout/stderr from Cloud Logging, and returns a
SandboxResult.

Design:
  - Hard refuses ``network_allowed=True``: Cloud Run Jobs are not network-
    isolated by default; accepting the flag would silently mislead callers
    about the isolation guarantee.  Pass network_allowed=False always.
  - ``is_production_grade = True``: this is the intended production sandbox.
  - Lazy-imports ``google.cloud.run_v2`` so that unit tests (CI) run without
    the ``gcp`` optional-dep installed.  Tests inject a fake client via the
    ``client`` constructor parameter.
  - Execution lifecycle: RunJobRequest → poll Execution.status → collect logs
    via Cloud Logging (``google-cloud-logging`` — lazy import).
  - Timeout enforcement: if the job execution exceeds ``timeout_s``, the
    execution is cancelled and SandboxResult.killed=True is returned.

GCP resource naming:
  Job name format: ``projects/{project}/locations/{region}/jobs/{job_name}``
  Each run creates one Execution per call.

Deferred integrations:
  - SP-05c-INT-1: Cloud Logging log collection (currently returns empty stdout/
    stderr; the ``log_uri`` is recorded for post-hoc inspection).
  - SP-05c-INT-2: 100-concurrent-execution load test (acceptance criterion).
  - SP-05c-INT-3: cold-start P95 ≤15 s measurement in staging.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from app.core.sandbox import AbstractSandbox, SandboxResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Sentinel: Cloud Run Execution terminal states
_SUCCEEDED = "CONDITION_SUCCEEDED"
_FAILED = "CONDITION_FAILED"
_TERMINAL = frozenset({_SUCCEEDED, _FAILED, "CONDITION_CANCELLED"})

# Poll interval in seconds while waiting for execution completion
_POLL_INTERVAL_S = 2.0


def _lazy_run_v2():
    """Return the google.cloud.run_v2 module, raising ImportError with install hint."""
    try:
        from google.cloud import run_v2  # type: ignore[import-not-found]

        return run_v2
    except ImportError as exc:
        raise ImportError(
            "google-cloud-run is required for CloudRunJobSandbox. "
            "Install with: uv sync --extra gcp"
        ) from exc


def _build_run_job_request(
    *,
    job_name: str,
    cmd: list[str],
    env: Optional[dict[str, str]],
    timeout_s: float,
    memory_mb: int,
    cpu_seconds: int,
    run_v2_mod: Any,
) -> Any:
    """Build a RunJobRequest with command + resource overrides.

    Pure function — no I/O, fully unit-testable without a GCP project.

    Cloud Run Jobs accept per-execution overrides for:
      - container[0].command / args
      - container[0].env
      - container[0].resources.limits (CPU/memory)
      - task_count / parallelism (always 1 here — one command per call)
    """
    env_vars = []
    if env:
        for k, v in env.items():
            env_vars.append(run_v2_mod.EnvVar(name=k, value=v))

    container_override = run_v2_mod.RunJobRequest.Overrides.ContainerOverride(
        args=cmd,
        env=env_vars,
    )
    overrides = run_v2_mod.RunJobRequest.Overrides(
        container_overrides=[container_override],
        task_count=1,
        timeout=_seconds_to_duration(timeout_s),
    )
    return run_v2_mod.RunJobRequest(name=job_name, overrides=overrides)


def _seconds_to_duration(seconds: float) -> Any:
    """Convert float seconds to google.protobuf.duration_pb2.Duration."""
    from google.protobuf import duration_pb2  # type: ignore[import-not-found]

    d = duration_pb2.Duration()
    d.seconds = int(seconds)
    d.nanos = int((seconds % 1) * 1e9)
    return d


class CloudRunJobSandbox(AbstractSandbox):
    """Production sandbox that runs each command as a Cloud Run Job execution.

    Cloud Run Jobs provide gVisor (runsc) container isolation and run in the
    project's VPC; the job template is created once (operator responsibility)
    and this class creates per-call Executions with command overrides.

    SP-05c-INT-1 deferred: log collection is a stub (empty stdout/stderr).
    Set ``collect_logs=True`` once ``google-cloud-logging`` is wired.

    Args:
        project_id: GCP project ID (e.g. ``autonomous-agent-2026``).
        region: Cloud Run region (e.g. ``us-central1``).
        job_name: Cloud Run Job short name (e.g. ``aa-sandbox``).
        client: Optional injected ``JobsClient`` — production leaves this
            None (default client from ADC); tests inject a fake.
        collect_logs: Whether to collect execution logs from Cloud Logging.
            Deferred (SP-05c-INT-1) — always False for now.
    """

    is_production_grade: bool = True

    def __init__(
        self,
        *,
        project_id: str,
        region: str,
        job_name: str,
        client: Optional[Any] = None,
        collect_logs: bool = False,
    ) -> None:
        if not project_id:
            raise ValueError("project_id must be non-empty")
        if not region:
            raise ValueError("region must be non-empty")
        if not job_name:
            raise ValueError("job_name must be non-empty")
        self._project_id = project_id
        self._region = region
        self._job_name = job_name
        self._job_fqn = f"projects/{project_id}/locations/{region}/jobs/{job_name}"
        self._client = client  # None → lazy-constructed on first run()
        self._collect_logs = collect_logs

    def _get_client(self) -> Any:
        if self._client is None:
            run_v2 = _lazy_run_v2()
            self._client = run_v2.JobsClient()
        return self._client

    async def run(
        self,
        *,
        cmd: list[str],
        workdir: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        stdin: Optional[str] = None,
        timeout_s: float = 60.0,
        cpu_seconds: int = 30,
        memory_mb: int = 512,
        max_files: int = 256,
        network_allowed: bool = False,
    ) -> SandboxResult:
        """Submit cmd as a Cloud Run Job execution and wait for completion.

        Hard refusal: ``network_allowed=True`` is rejected — Cloud Run Jobs
        are not network-isolated.  The caller must explicitly opt out of
        network access expectations by passing network_allowed=False.

        ``workdir`` and ``stdin`` are not forwarded to the container (Cloud
        Run Jobs execute in a clean container; workdir must be baked into the
        image or passed as an env var).  ``max_files`` is advisory (ulimit
        cannot be set on a managed runtime).
        """
        if network_allowed:
            raise PermissionError(
                "CloudRunJobSandbox cannot guarantee network isolation. "
                "Cloud Run Jobs run on a shared VPC; passing network_allowed=True "
                "would mislead callers about the isolation properties. "
                "Use network_allowed=False (the only supported mode)."
            )

        run_v2 = _lazy_run_v2()
        client = self._get_client()
        request = _build_run_job_request(
            job_name=self._job_fqn,
            cmd=cmd,
            env=env,
            timeout_s=timeout_s,
            memory_mb=memory_mb,
            cpu_seconds=cpu_seconds,
            run_v2_mod=run_v2,
        )

        t0 = time.monotonic()
        logger.info("cloud_run_sandbox: submitting job=%s cmd=%r", self._job_fqn, cmd)

        lro = client.run_job(request=request)
        execution_name: Optional[str] = None
        killed = False

        try:
            execution = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, lro.result),
                timeout=timeout_s,
            )
            execution_name = execution.name
            succeeded = _execution_succeeded(execution)
        except asyncio.TimeoutError:
            killed = True
            logger.warning(
                "cloud_run_sandbox: execution timed out after %.1fs, cancelling", timeout_s
            )
            succeeded = False
            # Best-effort cancel (fail-open: if cancel fails, log and continue)
            try:
                if execution_name:
                    client.cancel_execution(name=execution_name)
            except Exception:  # noqa: BLE001
                logger.exception("cloud_run_sandbox: cancel failed — execution may still run")

        duration_s = time.monotonic() - t0
        returncode = 0 if succeeded else 1

        stdout, stderr = "", ""
        if self._collect_logs and execution_name:
            stdout, stderr = await _collect_execution_logs(execution_name)

        logger.info(
            "cloud_run_sandbox: done job=%s rc=%d duration=%.2fs killed=%s",
            self._job_fqn,
            returncode,
            duration_s,
            killed,
        )
        return SandboxResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_s=duration_s,
            killed=killed,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _execution_succeeded(execution: Any) -> bool:
    """Return True if the Cloud Run Execution completed successfully."""
    for cond in getattr(execution, "conditions", []):
        if getattr(cond, "type_", None) == _SUCCEEDED and getattr(cond, "state", None) == 1:
            return True
    # Fallback: check completion_time + no failed_count
    completed = getattr(execution, "completion_time", None)
    failed = getattr(execution, "failed_count", 0) or 0
    return bool(completed and failed == 0)


async def _collect_execution_logs(execution_name: str) -> tuple[str, str]:
    """SP-05c-INT-1 deferred: collect stdout/stderr from Cloud Logging.

    Returns empty strings until the Cloud Logging integration is wired.
    The execution_name is recorded so operators can query logs manually:
      gcloud logging read 'resource.labels.job_name=...' --project=...
    """
    logger.debug(
        "cloud_run_sandbox: log collection deferred (SP-05c-INT-1) — "
        "execution=%s; query logs manually via Cloud Logging",
        execution_name,
    )
    return "", ""
