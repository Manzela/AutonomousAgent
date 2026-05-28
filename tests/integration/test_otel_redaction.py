"""Integration test for OpenTelemetry redaction processor.

Dynamically extracts the redaction configuration from deploy/otel/collector.prod.yaml,
spins up an isolated OTel collector container (v0.153.0) with that config,
sends test traces/logs/metrics containing PII and normal attributes,
and asserts that the output JSON files are redacted correctly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import pytest
import yaml

# Resolve paths
REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COLLECTOR_YAML = REPO_ROOT / "deploy" / "otel" / "collector.prod.yaml"
OTEL_COLLECTOR_IMAGE = "otel/opentelemetry-collector-contrib:0.153.0"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker,
]


def _docker_available() -> bool:
    """Check if docker daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    try:
        info = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return info.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


@pytest.fixture
def docker_collector():
    """Fixture to start the OTel collector container with extracted redaction config."""
    if not _docker_available():
        pytest.skip("Docker daemon is not available.")

    # 1. Read production config
    with open(PROD_COLLECTOR_YAML) as f:
        prod_config = yaml.safe_load(f)

    redaction_config = prod_config.get("processors", {}).get("redaction")
    assert redaction_config is not None, "redaction processor config not found in prod YAML"

    # 2. Build test config
    test_config = {
        "receivers": {
            "otlp": {
                "protocols": {
                    "grpc": {"endpoint": "0.0.0.0:4317"},
                    "http": {"endpoint": "0.0.0.0:4318"},
                }
            }
        },
        "processors": {
            "redaction": redaction_config,
            "batch": {
                "timeout": "100ms",
                "send_batch_size": 1,
            },
        },
        "exporters": {
            "file/traces": {"path": "/etc/otelcol-contrib/traces.json"},
            "file/metrics": {"path": "/etc/otelcol-contrib/metrics.json"},
            "file/logs": {"path": "/etc/otelcol-contrib/logs.json"},
        },
        "service": {
            "pipelines": {
                "traces": {
                    "receivers": ["otlp"],
                    "processors": ["redaction", "batch"],
                    "exporters": ["file/traces"],
                },
                "metrics": {
                    "receivers": ["otlp"],
                    "processors": ["redaction", "batch"],
                    "exporters": ["file/metrics"],
                },
                "logs": {
                    "receivers": ["otlp"],
                    "processors": ["redaction", "batch"],
                    "exporters": ["file/logs"],
                },
            }
        },
    }

    # 3. Create temp dir and write config
    temp_dir = tempfile.mkdtemp(prefix="otel-redaction-test-")
    # Grant full permissions so the container's non-root user can write files
    os.chmod(temp_dir, 0o777)

    config_path = Path(temp_dir) / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(test_config, f)

    # 4. Start container
    container_name = f"otel-collector-redaction-test-{os.getpid()}"
    run_cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        container_name,
        "-v",
        f"{temp_dir}:/etc/otelcol-contrib",
        "-P",  # Publish all exposed ports to random ports
        OTEL_COLLECTOR_IMAGE,
        "--config",
        "/etc/otelcol-contrib/config.yaml",
    ]

    proc = subprocess.run(run_cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        shutil.rmtree(temp_dir)
        pytest.fail(f"Failed to start OTel collector container: {proc.stderr}")

    # Helper to resolve mapped port
    def get_port(internal_port: str) -> int:
        res = subprocess.run(
            ["docker", "port", container_name, f"{internal_port}/tcp"],
            capture_output=True,
            text=True,
            check=True,
        )
        return int(res.stdout.strip().split(":")[-1])

    # Wait for collector startup
    deadline = time.time() + 10
    started = False
    mapped_http_port = None
    while time.time() < deadline:
        try:
            mapped_http_port = get_port("4318")
            # Probe OTLP endpoint
            r = httpx.get(f"http://localhost:{mapped_http_port}", timeout=1)
            # The HTTP receiver returns 404 on root, but if it responds, it's alive
            if r.status_code in (404, 200, 405):
                started = True
                break
        except Exception:
            pass
        time.sleep(0.5)

    if not started:
        # Fetch logs before teardown
        logs = subprocess.run(
            ["docker", "logs", container_name], capture_output=True, text=True, check=False
        )
        # Cleanup
        subprocess.run(["docker", "rm", "-f", container_name], check=False)
        shutil.rmtree(temp_dir)
        pytest.fail(
            f"Collector container did not start healthy.\nLogs:\n{logs.stdout}\n{logs.stderr}"
        )

    yield mapped_http_port, temp_dir

    # Cleanup container and temp files
    subprocess.run(["docker", "rm", "-f", container_name], check=False)
    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass


def test_otel_collector_redacts_pii(docker_collector):
    """Verify that OTel collector redacts trace/log/metric sensitive values and keys."""
    http_port, temp_dir = docker_collector
    client = httpx.Client(base_url=f"http://localhost:{http_port}")

    # --- 1. Emit Traces ---
    trace_payload = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "test-service"}}
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "test-scope"},
                        "spans": [
                            {
                                "traceId": "4bf92f3577b34da6a3ce929d0e0e4736",  # pragma: allowlist secret
                                "spanId": "00f067aa0ba902b7",
                                "name": "test-span",
                                "kind": 1,
                                "startTimeUnixNano": "1581452773000000000",
                                "endTimeUnixNano": "1581452774000000000",
                                "attributes": [
                                    {"key": "http.status_code", "value": {"intValue": 200}},
                                    {"key": "error", "value": {"stringValue": "true"}},
                                    {"key": "user_id", "value": {"stringValue": "user-12345"}},
                                    {
                                        "key": "customer_email",
                                        "value": {"stringValue": "sensitive@example.com"},
                                    },
                                    {
                                        "key": "api_key",
                                        "value": {
                                            "stringValue": "sk-proj-abcdefghijklmnopqrst"  # pragma: allowlist secret
                                        },
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    r = client.post("/v1/traces", json=trace_payload)
    assert r.status_code == 200

    # --- 2. Emit Logs ---
    log_payload = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "test-service"}}
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "test-scope"},
                        "logRecords": [
                            {
                                "timeUnixNano": "1581452773000000000",
                                "body": {
                                    "stringValue": "User password is supersecret123"  # pragma: allowlist secret
                                },
                                "attributes": [
                                    {"key": "email", "value": {"stringValue": "user@test.com"}},
                                    {"key": "ssn", "value": {"stringValue": "000-12-3456"}},
                                    {"key": "normal_attr", "value": {"stringValue": "keep-me"}},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    r = client.post("/v1/logs", json=log_payload)
    assert r.status_code == 200

    # --- 3. Emit Metrics ---
    metric_payload = {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "test-service"}}
                    ]
                },
                "scopeMetrics": [
                    {
                        "scope": {"name": "test-scope"},
                        "metrics": [
                            {
                                "name": "llm.call.cost",
                                "description": "Estimated cost",
                                "unit": "USD",
                                "sum": {
                                    "dataPoints": [
                                        {
                                            "startTimeUnixNano": "1581452773000000000",
                                            "timeUnixNano": "1581452774000000000",
                                            "asDouble": 0.05,
                                            "attributes": [
                                                {
                                                    "key": "gen_ai.request.model",
                                                    "value": {"stringValue": "claude-3-5"},
                                                },
                                                {
                                                    "key": "user_id",
                                                    "value": {"stringValue": "user-123"},
                                                },
                                                {
                                                    "key": "customer_email",
                                                    "value": {"stringValue": "test@sensitive.com"},
                                                },
                                            ],
                                        }
                                    ],
                                    "aggregationTemporality": 1,
                                    "isMonotonic": True,
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }
    r = client.post("/v1/metrics", json=metric_payload)
    assert r.status_code == 200

    # Wait for the files to write
    traces_file = Path(temp_dir) / "traces.json"
    logs_file = Path(temp_dir) / "logs.json"
    metrics_file = Path(temp_dir) / "metrics.json"

    deadline = time.time() + 10
    while time.time() < deadline:
        if traces_file.exists() and logs_file.exists() and metrics_file.exists():
            # Check sizes are non-zero
            if (
                traces_file.stat().st_size > 0
                and logs_file.stat().st_size > 0
                and metrics_file.stat().st_size > 0
            ):
                break
        time.sleep(0.5)

    assert traces_file.exists(), "Traces file was not written"
    assert logs_file.exists(), "Logs file was not written"
    assert metrics_file.exists(), "Metrics file was not written"

    # --- 4. Assert Trace Redaction ---
    with open(traces_file) as f:
        traces_data = json.loads(f.read().strip())

    span = traces_data["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attrs = {attr["key"]: attr["value"] for attr in span.get("attributes", [])}

    # Allowed non-PII keys must be preserved
    assert "http.status_code" in attrs
    assert attrs["http.status_code"] in ({"intValue": 200}, {"intValue": "200"})
    assert "error" in attrs
    assert attrs["error"] == {"stringValue": "true"}

    # Sensitive keys and matching values must be masked
    # Key-pattern block: customer_email, user_id, api_key must be masked to ****
    assert attrs["customer_email"] == {"stringValue": "****"}
    assert attrs["user_id"] == {"stringValue": "****"}
    assert attrs["api_key"] == {"stringValue": "****"}

    # --- 5. Assert Log Redaction ---
    with open(logs_file) as f:
        logs_data = json.loads(f.read().strip())

    log_record = logs_data["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    log_attrs = {attr["key"]: attr["value"] for attr in log_record.get("attributes", [])}

    # Normal attributes must be preserved
    assert "normal_attr" in log_attrs
    assert log_attrs["normal_attr"] == {"stringValue": "keep-me"}

    # Sensitive attributes (email, ssn) must be masked to ****
    assert log_attrs["email"] == {"stringValue": "****"}
    assert log_attrs["ssn"] == {"stringValue": "****"}

    # Body value (contains "password" blocked value pattern) must be masked
    body_val = log_record["body"]["stringValue"]
    assert "supersecret123" not in body_val
    assert "****" in body_val

    # --- 6. Assert Metrics Redaction ---
    with open(metrics_file) as f:
        metrics_data = json.loads(f.read().strip())

    metric_dp = metrics_data["resourceMetrics"][0]["scopeMetrics"][0]["metrics"][0]["sum"][
        "dataPoints"
    ][0]
    metric_attrs = {attr["key"]: attr["value"] for attr in metric_dp.get("attributes", [])}

    # Non-sensitive metric attributes preserved
    assert "gen_ai.request.model" in metric_attrs
    assert metric_attrs["gen_ai.request.model"] == {"stringValue": "claude-3-5"}

    # Sensitive metric attributes masked to ****
    assert metric_attrs["user_id"] == {"stringValue": "****"}
    assert metric_attrs["customer_email"] == {"stringValue": "****"}
