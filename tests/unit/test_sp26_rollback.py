"""SP-26 hermetic unit tests for deployment rollback.

All tests run without GCP credentials or google-cloud-run wheel.
The google.cloud.run_v2 module is mocked via unittest.mock.patch.

Oracles:
  1.  AbstractRevisionRollback is an ABC (cannot be instantiated directly).
  2.  CloudRunRevisionRollback is an AbstractRevisionRollback subclass.
  3.  is_production_grade is True on CloudRunRevisionRollback.
  4.  empty project_id raises ValueError at construction.
  5.  empty region raises ValueError at construction.
  6.  empty service_name raises ValueError at construction.
  7.  service FQN constructed as projects/{p}/locations/{r}/services/{s}.
  8.  _build_traffic_split_request targets the correct revision at 100%.
  9.  rollback to already-active revision returns already_current=True, duration_s=0.0.
  10. rollback to a different revision calls update_service and returns already_current=False.
  11. ImportError from _lazy_run_v2 propagates with install hint.
  12. RecordingRevisionRollback.rollback_to records call.
  13. RecordingRevisionRollback.rollback_to is idempotent.
  14. RecordingRevisionRollback.get_active_revision returns initial active.
  15. RecordingRevisionRollback.get_revision_history respects limit.
  16. empty revisions list raises ValueError in RecordingRevisionRollback.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.gcp.cloud_run_revisions import (
    CloudRunRevisionRollback,
    _build_traffic_split_request,
)
from app.adapters.inmemory.rollback import RecordingRevisionRollback
from app.core.rollback import AbstractRevisionRollback


# ── Fake run_v2 module ────────────────────────────────────────────────────────


class _FakeTrafficTarget:
    def __init__(self, type_=None, revision=None, percent=0) -> None:
        self.type_ = type_
        self.revision = revision
        self.percent = percent


class _FakeService:
    def __init__(self, name: str, traffic=None) -> None:
        self.name = name
        self.traffic = traffic or []
        self.traffic_statuses = traffic or []


class _FakeUpdateServiceRequest:
    def __init__(self, service=None) -> None:
        self.service = service


class _FakeTrafficTargetAllocationType:
    TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION = "REVISION"


def _make_fake_run_v2():
    mod = types.ModuleType("google.cloud.run_v2")
    mod.TrafficTarget = _FakeTrafficTarget
    mod.Service = _FakeService
    mod.UpdateServiceRequest = _FakeUpdateServiceRequest
    mod.TrafficTargetAllocationType = _FakeTrafficTargetAllocationType
    mod.ServicesClient = MagicMock
    mod.RevisionsClient = MagicMock
    mod.ListRevisionsRequest = MagicMock
    return mod


def _make_rollback(*, client=None) -> CloudRunRevisionRollback:
    return CloudRunRevisionRollback(
        project_id="autonomous-agent-2026",
        region="us-central1",
        service_name="hermes",
        client=client or MagicMock(),
    )


# ── Oracle 1 — ABC cannot be instantiated ────────────────────────────────────


def test_abstract_revision_rollback_is_abc():
    with pytest.raises(TypeError):
        AbstractRevisionRollback()


# ── Oracle 2 — CloudRunRevisionRollback is AbstractRevisionRollback subclass ──


def test_is_abstract_rollback_subclass():
    assert issubclass(CloudRunRevisionRollback, AbstractRevisionRollback)


# ── Oracle 3 — is_production_grade ───────────────────────────────────────────


def test_is_production_grade():
    assert CloudRunRevisionRollback.is_production_grade is True


# ── Oracles 4-6 — constructor validation ─────────────────────────────────────


def test_empty_project_id_raises():
    with pytest.raises(ValueError, match="project_id"):
        CloudRunRevisionRollback(project_id="", region="us-central1", service_name="s")


def test_empty_region_raises():
    with pytest.raises(ValueError, match="region"):
        CloudRunRevisionRollback(project_id="p", region="", service_name="s")


def test_empty_service_name_raises():
    with pytest.raises(ValueError, match="service_name"):
        CloudRunRevisionRollback(project_id="p", region="us-central1", service_name="")


# ── Oracle 7 — service FQN construction ─────────────────────────────────────


def test_service_fqn_construction():
    rb = _make_rollback()
    assert rb._service_fqn == (
        "projects/autonomous-agent-2026/locations/us-central1/services/hermes"
    )


# ── Oracle 8 — _build_traffic_split_request ──────────────────────────────────


def test_build_traffic_split_request():
    fake = _make_fake_run_v2()
    req = _build_traffic_split_request(
        service_name="projects/p/locations/r/services/s",
        revision_name="projects/p/locations/r/services/s/revisions/rev-abc",
        run_v2_mod=fake,
    )
    assert req.service.name == "projects/p/locations/r/services/s"
    assert len(req.service.traffic) == 1
    assert req.service.traffic[0].revision == "projects/p/locations/r/services/s/revisions/rev-abc"
    assert req.service.traffic[0].percent == 100


# ── Oracle 9 — rollback to already-active → already_current=True ─────────────


@pytest.mark.asyncio
async def test_rollback_idempotent_already_active():
    fake = _make_fake_run_v2()

    # Mock get_active_revision to return "rev-abc"
    rb = _make_rollback()
    target_rev = "projects/p/locations/r/services/s/revisions/rev-abc"
    rb.get_active_revision = lambda: _async_return(target_rev)

    with patch("app.adapters.gcp.cloud_run_revisions._lazy_run_v2", return_value=fake):
        result = await rb.rollback_to(revision_name=target_rev)

    assert result.already_current is True
    assert result.duration_s == 0.0
    assert result.target_revision == target_rev


# ── Oracle 10 — rollback to different revision → already_current=False ────────


@pytest.mark.asyncio
async def test_rollback_to_prior_revision():
    fake = _make_fake_run_v2()

    lro = MagicMock()
    lro.result.return_value = MagicMock()

    client = MagicMock()
    client.update_service.return_value = lro

    rb = _make_rollback(client=client)
    current_rev = "projects/p/locations/r/services/s/revisions/rev-current"
    prior_rev = "projects/p/locations/r/services/s/revisions/rev-prior"
    rb.get_active_revision = lambda: _async_return(current_rev)

    with patch("app.adapters.gcp.cloud_run_revisions._lazy_run_v2", return_value=fake):
        result = await rb.rollback_to(revision_name=prior_rev)

    assert result.already_current is False
    assert result.target_revision == prior_rev
    assert result.prior_revision == current_rev
    assert result.traffic_percent == 100
    assert client.update_service.called


# ── Oracle 11 — ImportError propagates with install hint ────────────────────


def test_lazy_import_error_has_hint():
    with patch.dict("sys.modules", {"google.cloud.run_v2": None}):
        from app.adapters.gcp.cloud_run_revisions import _lazy_run_v2

        with pytest.raises(ImportError, match="uv sync --extra gcp"):
            _lazy_run_v2()


# ── Oracle 12 — RecordingRevisionRollback records call ───────────────────────


@pytest.mark.asyncio
async def test_recording_rollback_records_call():
    rb = RecordingRevisionRollback(["rev-new", "rev-old"])
    result = await rb.rollback_to(revision_name="rev-old")
    assert "rev-old" in rb.calls
    assert result.already_current is False
    assert result.target_revision == "rev-old"


# ── Oracle 13 — RecordingRevisionRollback idempotent ─────────────────────────


@pytest.mark.asyncio
async def test_recording_rollback_idempotent():
    rb = RecordingRevisionRollback(["rev-new", "rev-old"])
    await rb.rollback_to(revision_name="rev-new")  # already active
    result = await rb.rollback_to(revision_name="rev-new")
    assert result.already_current is True
    assert result.duration_s == 0.0


# ── Oracle 14 — RecordingRevisionRollback.get_active_revision ────────────────


@pytest.mark.asyncio
async def test_recording_get_active_revision():
    rb = RecordingRevisionRollback(["rev-abc", "rev-xyz"])
    rev = await rb.get_active_revision()
    assert rev == "rev-abc"


# ── Oracle 15 — RecordingRevisionRollback.get_revision_history respects limit ─


@pytest.mark.asyncio
async def test_recording_get_revision_history_limit():
    revs = [f"rev-{i}" for i in range(20)]
    rb = RecordingRevisionRollback(revs)
    history = await rb.get_revision_history(limit=5)
    assert history == revs[:5]
    assert len(history) == 5


# ── Oracle 16 — empty revisions list raises ValueError ───────────────────────


def test_recording_empty_revisions_raises():
    with pytest.raises(ValueError, match="revisions"):
        RecordingRevisionRollback([])


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _async_return(val):
    return val
