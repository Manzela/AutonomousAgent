"""SP-05c hermetic unit tests for CloudRunJobSandbox.

All tests run without GCP credentials or the google-cloud-run wheel
(CI installs dev extras only).  The google.cloud.run_v2 module is mocked
via unittest.mock.patch so every test remains hermetic.

Oracles:
  1. network_allowed=True raises PermissionError (hard refusal)
  2. CloudRunJobSandbox is an AbstractSandbox subclass
  3. is_production_grade is True
  4. empty project_id raises ValueError at construction
  5. empty region raises ValueError at construction
  6. empty job_name raises ValueError at construction
  7. _build_run_job_request sets args=cmd on the container override
  8. _build_run_job_request env vars forwarded correctly
  9. _build_run_job_request empty env → no EnvVar objects
  10. _execution_succeeded returns True for succeeded condition (state=1)
  11. _execution_succeeded returns False for failed condition
  12. _execution_succeeded fallback: completion_time set + failed_count=0 → True
  13. _execution_succeeded fallback: completion_time None → False
  14. successful execution returns returncode=0, killed=False
  15. timed-out execution returns killed=True, returncode=1
  16. ImportError from lazy import propagates with install hint
  17. job FQN is constructed from project/region/job_name
  18. _collect_execution_logs deferred stub returns ("", "")
"""

from __future__ import annotations

import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.gcp.cloud_run_sandbox import (
    CloudRunJobSandbox,
    _build_run_job_request,
    _collect_execution_logs,
    _execution_succeeded,
)
from app.core.sandbox import AbstractSandbox


# ── Fixtures ──────────────────────────────────────────────────────────────────


class _FakeEnvVar:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


class _FakeContainerOverride:
    def __init__(self, args=None, env=None) -> None:
        self.args = args or []
        self.env = env or []


class _FakeOverrides:
    ContainerOverride = _FakeContainerOverride

    def __init__(self, container_overrides=None, task_count=1, timeout=None) -> None:
        self.container_overrides = container_overrides or []
        self.task_count = task_count
        self.timeout = timeout


class _FakeRunJobRequest:
    Overrides = _FakeOverrides

    def __init__(self, name: str, overrides: Any = None) -> None:
        self.name = name
        self.overrides = overrides


def _make_fake_run_v2():
    """Build a minimal fake google.cloud.run_v2 module."""
    mod = types.ModuleType("google.cloud.run_v2")
    mod.EnvVar = _FakeEnvVar
    mod.RunJobRequest = _FakeRunJobRequest
    mod.JobsClient = MagicMock
    return mod


def _succeeded_execution() -> MagicMock:
    cond = MagicMock()
    cond.type_ = "CONDITION_SUCCEEDED"
    cond.state = 1
    exc = MagicMock()
    exc.conditions = [cond]
    exc.name = "projects/p/locations/r/jobs/j/executions/exec-1"
    exc.completion_time = "2026-06-04T00:00:00Z"
    exc.failed_count = 0
    return exc


def _failed_execution() -> MagicMock:
    cond = MagicMock()
    cond.type_ = "CONDITION_FAILED"
    cond.state = 1
    exc = MagicMock()
    exc.conditions = [cond]
    exc.name = "projects/p/locations/r/jobs/j/executions/exec-2"
    exc.completion_time = "2026-06-04T00:00:01Z"
    exc.failed_count = 1
    return exc


def _make_sandbox(*, client: Any = None) -> CloudRunJobSandbox:
    return CloudRunJobSandbox(
        project_id="autonomous-agent-2026",
        region="us-central1",
        job_name="aa-sandbox",
        client=client or MagicMock(),
    )


# ── Oracle 1 — network_allowed=True hard refusal ──────────────────────────────


@pytest.mark.asyncio
async def test_network_allowed_raises():
    sb = _make_sandbox()
    with pytest.raises(PermissionError, match="network isolation"):
        await sb.run(cmd=["echo", "hi"], network_allowed=True)


# ── Oracle 2 — AbstractSandbox subclass ───────────────────────────────────────


def test_is_abstract_sandbox_subclass():
    assert issubclass(CloudRunJobSandbox, AbstractSandbox)


# ── Oracle 3 — is_production_grade ───────────────────────────────────────────


def test_is_production_grade():
    assert CloudRunJobSandbox.is_production_grade is True


# ── Oracles 4-6 — constructor validation ─────────────────────────────────────


def test_empty_project_id_raises():
    with pytest.raises(ValueError, match="project_id"):
        CloudRunJobSandbox(project_id="", region="us-central1", job_name="j")


def test_empty_region_raises():
    with pytest.raises(ValueError, match="region"):
        CloudRunJobSandbox(project_id="p", region="", job_name="j")


def test_empty_job_name_raises():
    with pytest.raises(ValueError, match="job_name"):
        CloudRunJobSandbox(project_id="p", region="us-central1", job_name="")


# ── Oracle 7 — _build_run_job_request sets args ───────────────────────────────


def test_build_run_job_request_args():
    fake = _make_fake_run_v2()
    cmd = ["python", "-c", "print('hi')"]
    req = _build_run_job_request(
        job_name="projects/p/locations/r/jobs/j",
        cmd=cmd,
        env=None,
        timeout_s=30.0,
        memory_mb=512,
        cpu_seconds=30,
        run_v2_mod=fake,
    )
    override = req.overrides.container_overrides[0]
    assert override.args == cmd


# ── Oracle 8 — _build_run_job_request forwards env vars ──────────────────────


def test_build_run_job_request_env_vars():
    fake = _make_fake_run_v2()
    env = {"FOO": "bar", "HELLO": "world"}
    req = _build_run_job_request(
        job_name="projects/p/locations/r/jobs/j",
        cmd=["echo"],
        env=env,
        timeout_s=30.0,
        memory_mb=512,
        cpu_seconds=30,
        run_v2_mod=fake,
    )
    override = req.overrides.container_overrides[0]
    env_dict = {e.name: e.value for e in override.env}
    assert env_dict == env


# ── Oracle 9 — empty env → no EnvVar objects ────────────────────────────────


def test_build_run_job_request_no_env():
    fake = _make_fake_run_v2()
    req = _build_run_job_request(
        job_name="projects/p/locations/r/jobs/j",
        cmd=["ls"],
        env=None,
        timeout_s=10.0,
        memory_mb=256,
        cpu_seconds=10,
        run_v2_mod=fake,
    )
    override = req.overrides.container_overrides[0]
    assert override.env == []


# ── Oracle 10 — _execution_succeeded: CONDITION_SUCCEEDED state=1 ─────────────


def test_execution_succeeded_true():
    assert _execution_succeeded(_succeeded_execution()) is True


# ── Oracle 11 — _execution_succeeded: CONDITION_FAILED → False ───────────────


def test_execution_succeeded_false_on_failed():
    exc = _failed_execution()
    # Override condition so CONDITION_SUCCEEDED is not present
    cond = MagicMock()
    cond.type_ = "CONDITION_FAILED"
    cond.state = 1
    exc.conditions = [cond]
    exc.failed_count = 1
    assert _execution_succeeded(exc) is False


# ── Oracle 12 — _execution_succeeded fallback: completion_time + failed=0 ────


def test_execution_succeeded_fallback_completion_time():
    exc = MagicMock()
    exc.conditions = []
    exc.completion_time = "2026-06-04T00:00:00Z"
    exc.failed_count = 0
    assert _execution_succeeded(exc) is True


# ── Oracle 13 — _execution_succeeded fallback: no completion_time → False ─────


def test_execution_succeeded_fallback_no_completion():
    exc = MagicMock()
    exc.conditions = []
    exc.completion_time = None
    exc.failed_count = 0
    assert _execution_succeeded(exc) is False


# ── Oracle 14 — successful execution → rc=0, killed=False ────────────────────


@pytest.mark.asyncio
async def test_successful_execution_result():
    fake = _make_fake_run_v2()
    execution = _succeeded_execution()

    lro = MagicMock()
    lro.result.return_value = execution

    client = MagicMock()
    client.run_job.return_value = lro

    sb = _make_sandbox(client=client)

    with patch("app.adapters.gcp.cloud_run_sandbox._lazy_run_v2", return_value=fake):
        result = await sb.run(cmd=["echo", "hello"])

    assert result.returncode == 0
    assert result.killed is False
    assert result.duration_s >= 0.0


# ── Oracle 15 — timeout → killed=True, returncode=1 ─────────────────────────


@pytest.mark.asyncio
async def test_timeout_sets_killed():
    fake = _make_fake_run_v2()

    def slow_result():
        import time

        time.sleep(10)

    lro = MagicMock()
    lro.result.side_effect = slow_result

    client = MagicMock()
    client.run_job.return_value = lro

    sb = _make_sandbox(client=client)

    with patch("app.adapters.gcp.cloud_run_sandbox._lazy_run_v2", return_value=fake):
        result = await sb.run(cmd=["sleep", "100"], timeout_s=0.05)

    assert result.killed is True
    assert result.returncode == 1


# ── Oracle 16 — ImportError propagates with install hint ─────────────────────


def test_lazy_import_error_has_hint():
    with patch.dict("sys.modules", {"google.cloud.run_v2": None}):
        from app.adapters.gcp.cloud_run_sandbox import _lazy_run_v2

        with pytest.raises(ImportError, match="uv sync --extra gcp"):
            _lazy_run_v2()


# ── Oracle 17 — job FQN construction ────────────────────────────────────────


def test_job_fqn_construction():
    sb = CloudRunJobSandbox(
        project_id="my-project",
        region="europe-west1",
        job_name="my-job",
        client=MagicMock(),
    )
    assert sb._job_fqn == "projects/my-project/locations/europe-west1/jobs/my-job"


# ── Oracle 18 — deferred log collection returns empty strings ────────────────


@pytest.mark.asyncio
async def test_collect_logs_deferred_stub():
    stdout, stderr = await _collect_execution_logs(
        "projects/p/locations/r/jobs/j/executions/exec-1"
    )
    assert stdout == ""
    assert stderr == ""
