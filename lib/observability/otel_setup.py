"""Initialize the OpenTelemetry SDK once at plugin register time.

Hermes itself does not configure a ``TracerProvider`` or ``MeterProvider``
— setting ``OTEL_SERVICE_NAME`` / ``OTEL_EXPORTER_OTLP_ENDPOINT`` env vars
alone is not enough; the SDK still has to be wired with an explicit
provider + exporter or every ``tracer.start_span()`` / ``meter.create_*``
call is a NoOp.

This module installs that wiring on first import. Both ``setup_tracing``
and ``setup_metrics`` are idempotent via module-level flags so re-imports
(or accidental double-register) leave the existing providers in place.

The collector receivers (``deploy/otel/collector.dev.yaml``) accept both
gRPC (4317) and HTTP (4318) for traces, metrics, and logs. We use the HTTP
exporters (matches the ``OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318``
env var the hermes container ships with). Both collectors (dev + prod)
already declare a metrics pipeline (``deploy/otel/collector.{dev,prod}.yaml``
``service.pipelines.metrics``); see audit/2026-05-21-summary.md J9 row.

An ``atexit`` handler force-flushes pending spans + metrics on interpreter
shutdown so short-lived ``hermes -z`` invocations (and the test suite)
don't drop the data emitted in the last few seconds before exit.
"""

from __future__ import annotations

import atexit
import logging
import os

logger = logging.getLogger(__name__)

_initialized = False
_provider: object = None
_metrics_initialized = False
_meter_provider: object = None

# Default metric export interval. 30 s matches the OTel Python default;
# kept explicit so the value is grep-able and tunable per-deployment.
DEFAULT_METRIC_EXPORT_INTERVAL_MS = 30_000


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


def setup_metrics(
    service_name: str = "hermes-agent",
    export_interval_ms: int = DEFAULT_METRIC_EXPORT_INTERVAL_MS,
) -> bool:
    """Install a global ``MeterProvider`` exporting to the OTLP HTTP collector.

    Mirrors :func:`setup_tracing` — same endpoint env var
    (``OTEL_EXPORTER_OTLP_ENDPOINT``), same idempotency pattern, same
    atexit force-flush. Splits out into its own function so the trace
    plumbing remains unaffected when metrics SDK packages are missing
    (separate ImportError surfaces).

    The OTLP HTTP metric exporter posts to ``{endpoint}/v1/metrics`` —
    we append the path the same way ``setup_tracing`` does for
    ``/v1/traces``, so the env var still expresses a single base URL.

    Args:
        service_name: ``service.name`` resource attribute. Defaults to
            ``hermes-agent`` to match the trace pipeline.
        export_interval_ms: ``PeriodicExportingMetricReader`` collection
            interval. Defaults to 30 s (OTel Python default); kept as a
            kwarg so test fixtures can choose a shorter interval to
            shorten round-trip assertions.

    Returns:
        ``True`` when the MeterProvider was installed (or had been on a
        prior call); ``False`` when the OpenTelemetry metrics packages
        are not importable. Callers should treat ``False`` as
        "metric instruments degrade to no-op" — NOT as a fatal error.

    Why a sync ``Gauge`` instead of an ``ObservableGauge``?
        Our values arrive event-driven from ``ContextUsageDetector.record_usage``,
        not on a poll. A sync ``Gauge`` (OTel API >= 1.27, pinned in
        ``deploy/Dockerfile.hermes``) lets the detector call ``.set(ratio, attrs)``
        the moment a new reading arrives. An ``ObservableGauge`` would
        require a callback that reads the last snapshot, adding a polling
        delay and an extra round-trip per session.
    """
    global _metrics_initialized
    if _metrics_initialized:
        return True

    try:
        from opentelemetry import metrics
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource
    except ImportError as exc:  # pragma: no cover — verified present in container
        logger.warning(
            "OTel metrics SDK not importable (%s); metric instruments will be inert.",
            exc,
        )
        return False

    resource = Resource.create({"service.name": service_name})

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
    base = endpoint.rstrip("/")
    if not base.endswith("/v1/metrics"):
        metrics_endpoint = base + "/v1/metrics"
    else:
        metrics_endpoint = base

    exporter = OTLPMetricExporter(endpoint=metrics_endpoint)
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=export_interval_ms,
    )
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)

    def _flush_metrics_on_exit() -> None:
        try:
            provider.shutdown()
        except Exception:  # pragma: no cover
            pass

    atexit.register(_flush_metrics_on_exit)

    global _meter_provider
    _meter_provider = provider
    _metrics_initialized = True
    logger.info(
        "OTel metrics initialized: service.name=%s endpoint=%s interval_ms=%d",
        service_name,
        metrics_endpoint,
        export_interval_ms,
    )
    return True
