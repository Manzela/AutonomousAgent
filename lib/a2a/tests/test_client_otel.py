"""Tests for OTel traceparent injection in lib/a2a/client.py — Day 6."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from opentelemetry.context import attach, detach
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from lib.a2a.client import send_message

_BASE = "http://testserver/"
_MSG = {"role": "USER", "parts": [{"text": "hi from otel test"}]}
_SUBMITTED_RESPONSE = {
    "jsonrpc": "2.0",
    "id": "test-id",
    "result": {"id": "task-otel-001", "status": "SUBMITTED"},
}


def _make_provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


@pytest.mark.asyncio
async def test_traceparent_injected_in_send_message() -> None:
    """When an OTel span is active, traceparent appears in the outbound headers."""
    provider, _ = _make_provider()
    tracer = provider.get_tracer("test")
    captured_headers: dict[str, str] = {}

    async def _capturing_post(
        url: str, *, json: dict, timeout: float, headers: dict | None = None, **kwargs: Any
    ) -> httpx.Response:
        captured_headers.update(headers or {})
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _SUBMITTED_RESPONSE
        return mock_response

    with tracer.start_as_current_span("test-span") as span:
        trace_id_hex = format(span.get_span_context().trace_id, "032x")
        span_id_hex = format(span.get_span_context().span_id, "016x")
        with patch("httpx.AsyncClient.post", side_effect=_capturing_post):
            await send_message(_BASE, _MSG)

    assert (
        "traceparent" in captured_headers
    ), f"traceparent missing. headers: {list(captured_headers)}"
    parts = captured_headers["traceparent"].split("-")
    assert len(parts) == 4
    assert parts[0] == "00"
    assert parts[1] == trace_id_hex
    assert parts[2] == span_id_hex


@pytest.mark.asyncio
async def test_no_traceparent_when_no_active_span() -> None:
    """When no span is active, traceparent must NOT be injected."""
    captured_headers: dict[str, str] = {}

    async def _capturing_post(
        url: str, *, json: dict, timeout: float, headers: dict | None = None, **kwargs: Any
    ) -> httpx.Response:
        captured_headers.update(headers or {})
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _SUBMITTED_RESPONSE
        return mock_response

    # Detach any active context
    from opentelemetry.context import attach, detach, Context

    token = attach(Context())
    try:
        with patch("httpx.AsyncClient.post", side_effect=_capturing_post):
            await send_message(_BASE, _MSG)
    finally:
        detach(token)

    assert (
        "traceparent" not in captured_headers
    ), f"traceparent must not be injected with no active span. headers: {captured_headers}"


@pytest.mark.asyncio
async def test_tracestate_passed_through() -> None:
    """When tracestate is present in context, it passes through."""
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    provider, _ = _make_provider()
    tracer = provider.get_tracer("test-ts")
    captured_headers: dict[str, str] = {}

    async def _capturing_post(
        url: str, *, json: dict, timeout: float, headers: dict | None = None, **kwargs: Any
    ) -> httpx.Response:
        captured_headers.update(headers or {})
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _SUBMITTED_RESPONSE
        return mock_response

    propagator = TraceContextTextMapPropagator()
    carrier = {
        "traceparent": "00-abcdef1234567890abcdef1234567890-1234567890abcdef-01",
        "tracestate": "vendorname=opaquevalue",
    }
    ctx = propagator.extract(carrier)
    with tracer.start_as_current_span("ts-span", context=ctx):
        with patch("httpx.AsyncClient.post", side_effect=_capturing_post):
            await send_message(_BASE, _MSG)

    assert (
        "tracestate" in captured_headers
    ), f"tracestate must be forwarded. headers: {captured_headers}"
    assert "vendorname=opaquevalue" in captured_headers["tracestate"]


@pytest.mark.asyncio
async def test_sampled_bit_respected() -> None:
    """An active but unsampled span must produce traceparent with flags '00' or no traceparent at all.

    Uses a NonRecordingSpan with an explicit valid SpanContext carrying TraceFlags(0x00)
    so the is_valid guard passes but trace_flags.sampled is False.
    """
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
    from opentelemetry import trace as _trace

    # Build a valid (non-zero trace_id, non-zero span_id) but unsampled SpanContext.
    unsampled_ctx = SpanContext(
        trace_id=0x000102030405060708090A0B0C0D0E0F,
        span_id=0x0102030405060708,
        is_remote=True,
        trace_flags=TraceFlags(0x00),  # NOT sampled
    )
    unsampled_span = NonRecordingSpan(unsampled_ctx)

    captured_headers: dict[str, str] = {}

    async def _capturing_post(
        url: str,
        *,
        json: dict,
        timeout: float,
        headers: dict | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        captured_headers.update(headers or {})
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _SUBMITTED_RESPONSE
        return mock_response

    # Attach the unsampled span as the current span for this task
    ctx = _trace.set_span_in_context(unsampled_span)
    token = attach(ctx)
    try:
        with patch("httpx.AsyncClient.post", side_effect=_capturing_post):
            await send_message(_BASE, _MSG)
    finally:
        detach(token)

    # The sampled bit is 0x00 — implementation should either:
    #   (a) not inject traceparent at all, OR
    #   (b) inject with flags "00"
    # Both are correct per W3C spec. What must NOT happen is flags "01".
    tp = captured_headers.get("traceparent", "")
    if tp:
        parts = tp.split("-")
        assert len(parts) == 4, f"malformed traceparent: {tp!r}"
        assert (
            parts[-1] == "00"
        ), f"unsampled span must produce flags '00', got {parts[-1]!r} in {tp!r}"
    # If no traceparent header, that's also valid — the implementation chose not to propagate.
    # The important assertion is that we reached this point without flags "01".
