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
import threading

logger = logging.getLogger(__name__)

_initialized = False
_provider: object = None
_metrics_initialized = False
_meter_provider: object = None

# Guards against concurrent double-init (TOCTOU on the _initialized flag).
# The OTel SDK is idempotent, so races produce only a cosmetic duplicate-init
# warning; the locks prevent even that noise.
_trace_lock = threading.Lock()
_metrics_lock = threading.Lock()

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
    global _initialized, _provider
    with _trace_lock:
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
    global _metrics_initialized, _meter_provider
    with _metrics_lock:
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

        _meter_provider = provider
        _metrics_initialized = True

    logger.info(
        "OTel metrics initialized: service.name=%s endpoint=%s interval_ms=%d",
        service_name,
        metrics_endpoint,
        export_interval_ms,
    )
    return True


# ---------------------------------------------------------------------------
# O-6 / O-7: Structured JSON logging + ScrubFilter on root logger
# ---------------------------------------------------------------------------

_json_logging_initialized = False
_json_logging_lock = threading.Lock()


class _GcpJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON accepted by Cloud Logging.

    GCP Cloud Logging stores structured logs under ``jsonPayload`` when the
    log line is valid JSON with a ``severity`` (or ``level``) field.  Plain-
    text logs land under ``textPayload`` and cannot be filtered with
    ``jsonPayload.msg=...`` log-based metrics (O-6 finding).

    Fields emitted per record:
      ``severity``   — GCP-canonical severity name (mapped from levelname)
      ``time``       — ISO-8601 timestamp
      ``logger``     — ``record.name``
      ``msg``        — ``record.getMessage()`` (formatted string)
      ``exc``        — exception text (only when ``record.exc_info`` is set)
    """

    _SEVERITY_MAP = {
        "DEBUG": "DEBUG",
        "INFO": "INFO",
        "WARNING": "WARNING",
        "ERROR": "ERROR",
        "CRITICAL": "CRITICAL",
    }

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        import json as _json
        import datetime as _dt

        payload: dict[str, object] = {
            "severity": self._SEVERITY_MAP.get(record.levelname, record.levelname),
            "time": _dt.datetime.fromtimestamp(record.created, tz=_dt.timezone.utc).isoformat(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return _json.dumps(payload, ensure_ascii=False)


def setup_json_logging() -> bool:
    """Replace the root-logger handler formatter with ``_GcpJsonFormatter``.

    Also installs ``lib.scrubber.ScrubFilter`` on the root logger so every
    ``logger.info/warning/error(...)`` call is scrubbed before it reaches
    Cloud Logging (closes O-7).

    Idempotent: safe to call more than once; the second call is a no-op.

    Returns:
        ``True`` when the formatter was installed (or had been on a prior
        call); ``False`` when something prevented installation (logged at
        WARNING so the caller can continue without structured logs).
    """
    global _json_logging_initialized
    with _json_logging_lock:
        if _json_logging_initialized:
            return True

        # Hold the lock for the entire critical section — same pattern as
        # setup_tracing / setup_metrics.  Without this, two concurrent callers
        # both see _json_logging_initialized=False, both proceed outside the
        # lock, and both install duplicate formatters and ScrubFilter instances
        # on the root logger (TOCTOU race).
        try:
            root = logging.getLogger()

            # Install JSON formatter on every existing handler.  If basicConfig
            # hasn't run yet (e.g. unit-test context), attach a StreamHandler.
            if not root.handlers:
                root.addHandler(logging.StreamHandler())

            json_fmt = _GcpJsonFormatter()
            for handler in root.handlers:
                handler.setFormatter(json_fmt)

            # O-7: install ScrubFilter so all Python logger.* calls are scrubbed.
            try:
                from lib.scrubber import ScrubFilter

                root.addFilter(ScrubFilter())
            except Exception as exc:  # noqa: BLE001 — scrubber optional
                logger.warning("setup_json_logging: ScrubFilter unavailable: %s", exc)

            _json_logging_initialized = True

        except Exception as exc:  # noqa: BLE001
            logger.warning("setup_json_logging: failed to install JSON formatter: %s", exc)
            return False

    logger.info("setup_json_logging: GCP JSON formatter + ScrubFilter installed on root logger")
    return True
