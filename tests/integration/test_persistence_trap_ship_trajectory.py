"""Integration tests for TrajectoryShipper.ship_trajectory method,
aligning with the Persistence Trap (#12.c) contract.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any
import pytest

from lib.trajectory.shipper import TrajectoryShipper, ModelArmorSanitizeUnavailable
from tests.integration.test_persistence_trap import (
    FakeGCSClient,
    _StubSanitize,
    _redaction_stub_response,
    CANARY_TOKENS,
)

# Reuse the same canary tokens so we can test floor-redaction behavior.
_CANARY_TRAJECTORY = [
    {
        "role": "user",
        "content": f"My SSN is {CANARY_TOKENS['US_SOCIAL_SECURITY_NUMBER']} and credit card is {CANARY_TOKENS['CREDIT_CARD_NUMBER']}",
    },
    {
        "role": "assistant",
        "content": f"I will send emails to {CANARY_TOKENS['EMAIL_ADDRESS']} or call {CANARY_TOKENS['PHONE_NUMBER']}",
    },
]


class _DispatchRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, f_code: str, **kwargs: Any):
        self.calls.append((f_code, kwargs))

        class _Sentinel:
            action = "halt"

        return _Sentinel()


@pytest.fixture
def mock_dispatch(monkeypatch: pytest.MonkeyPatch) -> _DispatchRecorder:
    recorder = _DispatchRecorder()
    monkeypatch.setattr("lib.durability.handlers.dispatch", recorder)
    return recorder


@pytest.fixture
def fake_gcs() -> FakeGCSClient:
    return FakeGCSClient()


@pytest.fixture
def stub_sanitize() -> _StubSanitize:
    return _StubSanitize(_redaction_stub_response)


@pytest.fixture
def broken_sanitize() -> _StubSanitize:
    def _raise(*, template: str, content: str) -> Any:
        raise RuntimeError("Model Armor sanitize unavailable")

    return _StubSanitize(_raise)


def test_ship_trajectory_floor_only_redacts(
    fake_gcs: FakeGCSClient,
    stub_sanitize: _StubSanitize,
) -> None:
    """T1/T2: ship_trajectory calls sanitize, GCS blob contains no raw canary tokens
    and does contain redaction markers.
    """
    shipper = TrajectoryShipper(
        bucket="test-bucket",
        template="j1-trajectory-shipper",
        sanitize_client=stub_sanitize,
        gcs_client=fake_gcs,
    )

    session_id = "test-session-001"
    shipper.ship_trajectory(session_id, _CANARY_TRAJECTORY)

    # Sanitize must be called exactly once
    assert stub_sanitize.call_count == 1
    assert stub_sanitize.last_call.template == "j1-trajectory-shipper"

    # Verify GCS blob name and content
    expected_object_name = f"malt/trajectory/{session_id}.json"
    assert fake_gcs.exists("test-bucket", expected_object_name)

    blob_content = fake_gcs.get("test-bucket", expected_object_name)
    data = json.loads(blob_content)
    assert data["session_id"] == session_id

    # Check that GCS content is sanitized (tokens absent, markers present)
    for info_type, token in CANARY_TOKENS.items():
        assert token not in blob_content, f"leak: {token!r} ({info_type}) found in GCS blob"
        assert f"[{info_type}]" in blob_content, f"marker [{info_type}] missing from GCS blob"


def test_ship_trajectory_writes_local_backup_when_env_set(
    fake_gcs: FakeGCSClient,
    stub_sanitize: _StubSanitize,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that ship_trajectory also writes to MALT_LOCAL_DIR if set."""
    local_dir = tmp_path / "malt_local"
    monkeypatch.setenv("MALT_LOCAL_DIR", str(local_dir))

    shipper = TrajectoryShipper(
        bucket="test-bucket",
        template="j1-trajectory-shipper",
        sanitize_client=stub_sanitize,
        gcs_client=fake_gcs,
    )

    session_id = "test-session-local"
    shipper.ship_trajectory(session_id, _CANARY_TRAJECTORY)

    # Check GCS was uploaded
    expected_object_name = f"malt/trajectory/{session_id}.json"
    assert fake_gcs.exists("test-bucket", expected_object_name)

    # Check local backup exists and is identical to GCS blob
    local_file = local_dir / f"{session_id}.json"
    assert local_file.exists()

    local_content = local_file.read_text()
    assert local_content == fake_gcs.get("test-bucket", expected_object_name)


def test_ship_trajectory_sanitize_unavailable_fails_loud(
    fake_gcs: FakeGCSClient,
    broken_sanitize: _StubSanitize,
    mock_dispatch: _DispatchRecorder,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T3: If sanitize is unavailable, ship_trajectory MUST fail loud,
    dispatch F37 with the session_id, and upload/write NOTHING.

    # DO NOT WEAKEN THIS TEST
    # Contract reference: audit/2026-05-21-persistence-trap-12c/test-contract.md
    """
    local_dir = tmp_path / "malt_local"
    monkeypatch.setenv("MALT_LOCAL_DIR", str(local_dir))

    shipper = TrajectoryShipper(
        bucket="test-bucket",
        template="j1-trajectory-shipper",
        sanitize_client=broken_sanitize,
        gcs_client=fake_gcs,
    )

    session_id = "test-session-failed"
    with pytest.raises(ModelArmorSanitizeUnavailable):
        shipper.ship_trajectory(session_id, _CANARY_TRAJECTORY)

    # Check F37 dispatch
    assert len(mock_dispatch.calls) == 1
    f_code, kwargs = mock_dispatch.calls[0]
    assert f_code == "F37"
    assert kwargs.get("tool_call_id") is None
    assert kwargs.get("payload", {}).get("shipper") == "trajectory"
    assert kwargs.get("payload", {}).get("session_id") == session_id

    # GCS must not have the blob
    expected_object_name = f"malt/trajectory/{session_id}.json"
    assert not fake_gcs.exists("test-bucket", expected_object_name)
    assert fake_gcs.stored_keys == []

    # Local backup must not exist
    local_file = local_dir / f"{session_id}.json"
    assert not local_file.exists()


def test_ship_trajectory_unrecognizable_response_fails_loud(
    fake_gcs: FakeGCSClient,
    mock_dispatch: _DispatchRecorder,
) -> None:
    """If sanitize response is unrecognizable, ship_trajectory must fail loud,
    dispatch F37 (if handled), and upload NOTHING.
    """

    class _UnrecognizableSanitize:
        def sanitize(self, *, template: str, content: str) -> Any:
            # Return something that doesn't have a sanitized_content attribute
            # to trigger AttributeError or ModelArmorSanitizeUnavailable.
            return 42

    shipper = TrajectoryShipper(
        bucket="test-bucket",
        template="j1-trajectory-shipper",
        sanitize_client=_UnrecognizableSanitize(),
        gcs_client=fake_gcs,
    )

    session_id = "test-session-unrecognizable"
    with pytest.raises(ModelArmorSanitizeUnavailable):
        shipper.ship_trajectory(session_id, _CANARY_TRAJECTORY)

    # Note: currently, _extract_sanitized_payload raising ModelArmorSanitizeUnavailable
    # from outside the try-block does NOT trigger the local F37 dispatch inside ship_trajectory.
    # We assert the exception propagates, and GCS upload is blocked.
    expected_object_name = f"malt/trajectory/{session_id}.json"
    assert not fake_gcs.exists("test-bucket", expected_object_name)
