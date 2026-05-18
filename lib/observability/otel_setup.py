"""Initialize the OpenTelemetry SDK once at plugin register time.

Hermes itself does not configure a ``TracerProvider`` — setting
``OTEL_SERVICE_NAME`` / ``OTEL_EXPORTER_OTLP_ENDPOINT`` env vars alone is
not enough; the SDK still has to be wired with an explicit provider +
exporter or every ``tracer.start_span()`` is a NoOp.

This module installs that wiring on first import. It is idempotent via the
``_initialized`` module-level flag so re-imports (or accidental double-
register) leave the existing provider in place.

The collector receivers (``deploy/otel/collector.dev.yaml``) accept both
gRPC (4317) and HTTP (4318). We use the HTTP exporter (matches the
``OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318`` env var the
hermes container ships with).

An ``atexit`` handler force-flushes pending spans on interpreter shutdown
so short-lived ``hermes -z`` invocations (and the test suite) don't drop
the spans they emitted in the last 5 s before exit.
"""

from __future__ import annotations

import atexit
import logging
import os

logger = logging.getLogger(__name__)

_initialized = False
_provider: object = None


def setup_tracing(service_name: str = "hermes-agent") -> bool:
    """Install a global ``TracerProvider`` exporting to the OTLP HTTP collector.

    Args:
        service_name: Value written to the ``service.name`` resource
            attribute. Defaults to ``hermes-agent`` so spans line up with
            the Phase 1 acceptance runbook step 4.

    Returns:
        ``True`` when the SDK was initialized (or had been initialized on
        a prior call); ``False`` when the OpenTelemetry packages are not
        importable. The plugin's ``register()`` uses this signal to decide
        whether to wire its span-emitting hooks.
    """
    global _initialized
    if _initialized:
        return True

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:  # pragma: no cover — verified present in container
        logger.warning(
            "OTel SDK not importable (%s); observability plugin will be inert.",
            exc,
        )
        return False

    # OTEL_SERVICE_NAME env var (set to "hermes" in deploy/docker-compose.yml)
    # is intentionally overridden here so spans show up at the
    # service.name=hermes-agent the runbook expects.
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    # The OTLP HTTP exporter requires the /v1/traces path. Append it if
    # the env var is a bare base URL (which is the case in our compose
    # file). Don't double-append if the caller already pointed at the
    # full path.
    base = endpoint.rstrip("/")
    if not base.endswith("/v1/traces"):
        endpoint = base + "/v1/traces"
    else:
        endpoint = base

    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Force-flush pending spans on interpreter shutdown so short-lived
    # CLI invocations (`hermes -z ...`) don't drop the last batch.
    # BatchSpanProcessor's default schedule_delay_millis is 5000 — without
    # this hook our final-turn spans would never reach the collector.
    def _flush_on_exit() -> None:
        try:
            provider.shutdown()
        except Exception:  # pragma: no cover
            pass

    atexit.register(_flush_on_exit)

    global _provider
    _provider = provider
    _initialized = True
    logger.info(
        "OTel tracing initialized: service.name=%s endpoint=%s",
        service_name,
        endpoint,
    )
    return True
