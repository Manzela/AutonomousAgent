"""Days 4-7 integration test — all four wires active simultaneously.

Uses the shared `otel_exporter` fixture from conftest.py (one TracerProvider
per process — the SDK ignores subsequent set_tracer_provider calls).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from lib.a2a.server import app

client = TestClient(app)


@dataclass
class _FakeSpec:
    id: str = "integration-spec-001"


def test_full_message_send_with_jwt_otel_bridge(otel_exporter) -> None:
    trace_id_hex = "aabbccddeeff00112233445566778899"
    traceparent = f"00-{trace_id_hex}-0011223344556677-01"
    fake_identity = MagicMock()
    fake_identity.sub = "agent-canary@autonomous-agent-2026.iam.gserviceaccount.com"
    with (
        patch("lib.a2a.server.verify_token", new=AsyncMock(return_value=fake_identity)),
        patch("lib.a2a.server.bridge_inbound_to_taskspec", return_value=_FakeSpec()) as mock_bridge,
        patch("lib.a2a.server.bridge_taskspec_status_to_a2a", return_value="SUBMITTED"),
    ):
        resp = client.post(
            "/",
            headers={"Authorization": "Bearer fake.jwt", "traceparent": traceparent},
            json={
                "jsonrpc": "2.0",
                "id": 99,
                "method": "message/send",
                "params": {"message": {"role": "USER", "parts": [{"text": "integration"}]}},
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "error" not in body
    assert body["result"]["id"] == "integration-spec-001"
    call_identity = (
        mock_bridge.call_args.args[1]
        if len(mock_bridge.call_args.args) > 1
        else mock_bridge.call_args.kwargs.get("identity")
    )
    assert call_identity is fake_identity
    spans = otel_exporter.get_finished_spans()
    assert len(spans) >= 1
    expected_trace_id = int(trace_id_hex, 16)
    assert any(s.context.trace_id == expected_trace_id for s in spans)


def test_sse_stream_with_traceparent_and_three_events(otel_exporter) -> None:
    trace_id_hex = "cafebabe00000000cafebabe00000000"
    traceparent = f"00-{trace_id_hex}-cafebabe00000001-01"
    with client.stream(
        "POST",
        "/stream",
        headers={"traceparent": traceparent},
        json={"message": {"role": "USER", "parts": [{"text": "stream integration"}]}},
    ) as resp:
        body = resp.read()
    assert resp.status_code == 200
    events = [
        json.loads(c.strip()[len(b"data: ") :])
        for c in body.split(b"\n\n")
        if c.strip().startswith(b"data: ")
    ]
    assert len(events) == 3
    assert events[0] == {"status": "WORKING"}
    assert events[2] == {"status": "COMPLETED"}
    spans = otel_exporter.get_finished_spans()
    assert len(spans) >= 1
    expected_trace_id = int(trace_id_hex, 16)
    assert any(s.context.trace_id == expected_trace_id for s in spans)
