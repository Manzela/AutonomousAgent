"""Day 6 OTel traceparent propagation tests.

Uses the shared `otel_exporter` fixture from conftest.py (one TracerProvider
per process — the SDK ignores subsequent set_tracer_provider calls).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from lib.a2a.server import app

client = TestClient(app)


def test_jsonrpc_dispatch_extracts_traceparent(otel_exporter) -> None:
    trace_id_hex = "4bf92f3577b34da6a3ce929d0e0e4736"
    traceparent = f"00-{trace_id_hex}-00f067aa0ba902b7-01"
    resp = client.post(
        "/",
        headers={"traceparent": traceparent},
        json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "message/send",
            "params": {"message": {"role": "USER", "parts": [{"text": "otel"}]}},
        },
    )
    assert resp.status_code == 200
    spans = otel_exporter.get_finished_spans()
    assert len(spans) >= 1
    expected_trace_id = int(trace_id_hex, 16)
    assert any(
        s.context.trace_id == expected_trace_id for s in spans
    ), f"no span with trace_id {expected_trace_id:#034x}"


def test_stream_route_extracts_traceparent(otel_exporter) -> None:
    trace_id_hex = "1234567890abcdef1234567890abcdef"  # pragma: allowlist secret
    parent_span_hex = "fedcba0987654321"  # pragma: allowlist secret
    traceparent = f"00-{trace_id_hex}-{parent_span_hex}-01"
    with client.stream(
        "POST",
        "/stream",
        headers={"traceparent": traceparent},
        json={"message": {"role": "USER", "parts": [{"text": "hi"}]}},
    ) as resp:
        resp.read()
    assert resp.status_code == 200
    spans = otel_exporter.get_finished_spans()
    assert len(spans) >= 1
    expected_trace_id = int(trace_id_hex, 16)
    assert any(s.context.trace_id == expected_trace_id for s in spans)
