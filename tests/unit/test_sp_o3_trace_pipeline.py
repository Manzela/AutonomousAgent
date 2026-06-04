"""SP-O3: hermetic tests for the trace-to-eval regression pipeline.

Uses a mock Langfuse client (monkeypatched) so no network calls are made.
Proves:
  1. The extraction→YAML path works with mock traces
  2. Graceful fallback when Langfuse SDK is missing
  3. Graceful fallback when credentials are missing
  4. Empty traces produce an empty regression suite

Oracle map:
  test_traces_to_eval_cases_converts     — mock traces → eval cases
  test_write_regression_yaml             — cases → valid YAML file
  test_generate_pipeline_with_mock       — full pipeline with mock client
  test_graceful_no_credentials           — no creds → empty result, no crash
  test_graceful_no_sdk                   — SDK import fails → empty result
  test_empty_traces                      — no failed traces → empty suite
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml

from evals.trace_to_eval_pipeline import (
    LangfuseTraceClient,
    generate_regression_evals_from_traces,
    traces_to_eval_cases,
    write_regression_yaml,
)


# ── Unit: traces_to_eval_cases ───────────────────────────────────────────────


def test_traces_to_eval_cases_converts():
    """Mock traces should be converted to Promptfoo-compatible eval cases."""
    traces = [
        {
            "trace_id": "t-1",
            "input": "Deploy the web service",
            "output": "I am unable to do that.",
            "error": "Insufficient privileges",
        },
        {
            "trace_id": "t-2",
            "input": "Run the migration",
            "output": "",
            "error": "Timeout",
        },
    ]
    cases = traces_to_eval_cases(traces)
    assert len(cases) == 2
    assert cases[0]["vars"]["input"] == "Deploy the web service"
    assert cases[0]["assert"][0]["type"] == "not-contains"
    assert "t-1" in cases[0]["description"]
    assert "Insufficient privileges" in cases[0]["description"]


def test_traces_to_eval_cases_skips_empty_input():
    """Traces with empty input should be skipped."""
    traces = [
        {"trace_id": "t-skip", "input": "", "output": "x", "error": ""},
        {"trace_id": "t-keep", "input": "valid input", "output": "y", "error": ""},
    ]
    cases = traces_to_eval_cases(traces)
    assert len(cases) == 1
    assert cases[0]["vars"]["input"] == "valid input"


# ── Unit: write_regression_yaml ──────────────────────────────────────────────


def test_write_regression_yaml(tmp_path):
    """Write cases to YAML and verify structure."""
    cases = [{"vars": {"input": "test"}, "assert": [{"type": "not-contains", "value": "err"}]}]
    out = write_regression_yaml(cases, tmp_path / "reg.yaml")
    assert out.exists()
    data = yaml.safe_load(out.read_text())
    assert data["description"] == "Auto-Generated Prod Regressions (SP-O3)"
    assert len(data["tests"]) == 1


# ── Integration: full pipeline with mock Langfuse ────────────────────────────


def _mock_langfuse_traces():
    """Build mock Langfuse trace objects."""
    t1 = SimpleNamespace(
        id="trace-abc",
        input="Deploy the service",
        output="Unable to deploy",
        metadata={"error": "auth failure"},
    )
    t2 = SimpleNamespace(
        id="trace-def",
        input="Run tests",
        output="All failed",
        metadata={"error": "timeout"},
    )
    return SimpleNamespace(data=[t1, t2])


def test_generate_pipeline_with_mock(tmp_path, monkeypatch):
    """Full pipeline with a mock Langfuse client produces a valid YAML."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    mock_langfuse_cls = MagicMock()
    mock_instance = MagicMock()
    mock_instance.fetch_traces.return_value = _mock_langfuse_traces()
    mock_langfuse_cls.return_value = mock_instance

    with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=mock_langfuse_cls)}):
        cases = generate_regression_evals_from_traces(
            output_path=tmp_path / "auto_reg.yaml",
        )

    assert len(cases) == 2
    out = tmp_path / "auto_reg.yaml"
    assert out.exists()
    data = yaml.safe_load(out.read_text())
    assert len(data["tests"]) == 2


# ── Graceful fallbacks ───────────────────────────────────────────────────────


def test_graceful_no_credentials(monkeypatch):
    """Missing credentials → empty result, no crash."""
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    client = LangfuseTraceClient()
    traces = client.fetch_failed_traces()
    assert traces == []


def test_graceful_no_sdk(monkeypatch):
    """Langfuse SDK not installed → empty result, no crash."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    # Simulate ImportError when trying to import langfuse
    with patch.dict("sys.modules", {"langfuse": None}):
        client = LangfuseTraceClient(public_key="pk", secret_key="sk")
        traces = client.fetch_failed_traces()
    assert traces == []


def test_empty_traces(tmp_path, monkeypatch):
    """No failed traces → empty regression suite."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")

    mock_langfuse_cls = MagicMock()
    mock_instance = MagicMock()
    mock_instance.fetch_traces.return_value = SimpleNamespace(data=[])
    mock_langfuse_cls.return_value = mock_instance

    with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=mock_langfuse_cls)}):
        cases = generate_regression_evals_from_traces(
            output_path=tmp_path / "empty.yaml",
        )
    assert cases == []
