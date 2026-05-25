"""Shared pytest fixtures for lib/a2a/tests/.

OTel provider is installed once at import time (the SDK only allows one real
provider per process). All test modules that need span inspection use the
shared `otel_exporter` fixture defined here.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

# Install a real TracerProvider once. Subsequent set_tracer_provider calls
# are no-ops (OTel logs a warning and ignores them), so any module that
# tries to install its own provider will silently defer to this one.
_SHARED_EXPORTER = InMemorySpanExporter()
_SHARED_PROVIDER = TracerProvider()
_SHARED_PROVIDER.add_span_processor(SimpleSpanProcessor(_SHARED_EXPORTER))
trace.set_tracer_provider(_SHARED_PROVIDER)


@pytest.fixture()
def otel_exporter() -> InMemorySpanExporter:  # type: ignore[return]
    """Yield the shared in-memory exporter, cleared before and after each test."""
    _SHARED_EXPORTER.clear()
    yield _SHARED_EXPORTER
    _SHARED_EXPORTER.clear()
