"""Tests for lib/a2a/task_bridge.py — Day 7 bridge acceptance gate."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class _AgentIdentity:
    """Minimal AgentIdentity stand-in — no dependency on auth branch."""

    sub: str
    audience: str
    acting_for: dict[str, str] = field(default_factory=dict)
    expiry: float = 9999999999.0
    jti: str = "stub-jti-000"


_IDENTITY = _AgentIdentity(
    sub="agent-a@autonomous-agent-2026.iam.gserviceaccount.com",
    audience="agent-b@autonomous-agent-2026.iam.gserviceaccount.com",
    acting_for={
        "human_sub": "pseudonym:user-001",
        "human_session_id": "sess-abc",
        "consent_scope": "read:trajectories",
    },
)

_A2A_TASK = {"id": "task-abc123", "status": "SUBMITTED", "metadata": {}}
_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"

import lib.a2a.task_bridge as tb  # noqa: E402


def test_bridge_inbound_creates_taskspec() -> None:
    spec = tb.bridge_inbound_to_taskspec(_A2A_TASK, _IDENTITY)
    assert (
        spec.owner == "pseudonym:user-001"
    ), f"expected owner 'pseudonym:user-001', got {spec.owner!r}"
    raw_meta = (
        spec.metadata if hasattr(spec, "metadata") and isinstance(spec.metadata, dict) else {}
    )
    if not raw_meta:
        try:
            from lib.a2a.task_bridge import get_spec_metadata_for_test

            raw_meta = get_spec_metadata_for_test(spec)
        except ImportError:
            pass
    assert raw_meta.get("a2a_task_id") == "task-abc123", f"metadata missing a2a_task_id: {raw_meta}"
    assert spec.id, "TaskSpec.id must be non-empty"


@pytest.mark.parametrize(
    "spec_status, expected_a2a_state",
    [
        ("draft", "SUBMITTED"),
        ("draft_locked", "WORKING"),
        ("locked", "WORKING"),
        ("superseded", "CANCELED"),
    ],
)
def test_mapping_table_completeness(spec_status: str, expected_a2a_state: str) -> None:
    spec = tb.bridge_inbound_to_taskspec(_A2A_TASK, _IDENTITY)
    if hasattr(spec, "__dataclass_fields__"):
        import dataclasses

        spec = dataclasses.replace(spec, status=spec_status)
    else:
        spec = spec.model_copy(update={"status": spec_status})
    result = tb.bridge_taskspec_status_to_a2a(spec)
    assert (
        result == expected_a2a_state
    ), f"SpecStatus.{spec_status} -> expected {expected_a2a_state!r}, got {result!r}"


def test_bridge_round_trip() -> None:
    spec = tb.bridge_inbound_to_taskspec(_A2A_TASK, _IDENTITY)
    assert spec.status == "draft", f"expected initial status 'draft', got {spec.status!r}"
    a2a_state = tb.bridge_taskspec_status_to_a2a(spec)
    assert a2a_state == "SUBMITTED", f"round-trip: draft should map to SUBMITTED, got {a2a_state!r}"


def test_cancel_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _fake_cancel(raw_args: str) -> str:
        calls.append(raw_args)
        return "cancelled"

    monkeypatch.setattr(tb, "_anchors_cancel", _fake_cancel)
    result = tb.cancel_dispatch("task-abc123")
    assert calls == [
        "task-abc123"
    ], f"cancel_dispatch should pass task_id to _anchors_cancel; calls={calls!r}"
    assert result == "cancelled"


def test_trace_id_in_taskspec_metadata() -> None:
    task_with_trace = {
        **_A2A_TASK,
        "metadata": {"traceparent": f"00-{_TRACE_ID}-00f067aa0ba902b7-01"},
    }
    spec = tb.bridge_inbound_to_taskspec(task_with_trace, _IDENTITY, trace_id=_TRACE_ID)
    raw_meta = (
        spec.metadata if hasattr(spec, "metadata") and isinstance(spec.metadata, dict) else {}
    )
    if not raw_meta:
        try:
            from lib.a2a.task_bridge import get_spec_metadata_for_test

            raw_meta = get_spec_metadata_for_test(spec)
        except ImportError:
            pass
    assert (
        raw_meta.get("a2a_trace_id") == _TRACE_ID
    ), f"expected trace_id in metadata['a2a_trace_id'], got {raw_meta!r}"
