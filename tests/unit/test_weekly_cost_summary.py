"""Unit tests for scripts/weekly_cost_summary.py.

The script lives in ``scripts/`` (a runnable, not a library) so we
import it via importlib + path-injection rather than the usual
``from lib.foo`` pattern. That mirrors the test-loading approach used
elsewhere for one-shot scripts that don't ship as Python packages.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest


def _load_module():
    """Import scripts/weekly_cost_summary.py without packaging it."""

    path = Path(__file__).resolve().parents[2] / "scripts" / "weekly_cost_summary.py"
    spec = importlib.util.spec_from_file_location("weekly_cost_summary", path)
    assert spec and spec.loader, "loader missing"
    module = importlib.util.module_from_spec(spec)
    sys.modules["weekly_cost_summary"] = module
    spec.loader.exec_module(module)
    return module


wcs = _load_module()


# ---------------------------------------------------------------------------
# _redact_key — make sure we never leak secrets into a public issue
# ---------------------------------------------------------------------------


def test_redact_key_short():
    assert wcs._redact_key("") == ""
    assert wcs._redact_key("ab") == "sk-***"


def test_redact_key_long_keeps_only_prefix_and_suffix():
    redacted = wcs._redact_key("sk-12345abcdefXYZ9")  # pragma: allowlist secret
    assert redacted == "sk-***XYZ9"
    # The mid-section of the key must not appear in the redaction.
    assert "12345abcdef" not in redacted  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# Aggregation properties on SpendReport
# ---------------------------------------------------------------------------


def _row(model="m", user="u", api_key="k", spend=1.0, calls=1):
    return wcs.SpendRow(
        model=model, user=user, api_key=api_key, total_spend=spend, call_count=calls
    )


def test_total_usd_sums_rows():
    report = wcs.SpendReport(
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
        rows=[_row(spend=1.5), _row(spend=2.5)],
    )
    assert report.total_usd == pytest.approx(4.0)


def test_by_model_aggregates_across_users():
    report = wcs.SpendReport(
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
        rows=[
            _row(model="opus", user="a", spend=1.0, calls=1),
            _row(model="opus", user="b", spend=2.0, calls=3),
            _row(model="haiku", user="a", spend=0.5, calls=10),
        ],
    )
    by_model = report.by_model
    assert by_model["opus"] == (pytest.approx(3.0), 4)
    assert by_model["haiku"] == (pytest.approx(0.5), 10)


def test_by_user_falls_back_to_redacted_key_when_user_empty():
    # Fake key fixtures — never real credentials, kept on one line so
    # `# pragma: allowlist secret` stays adjacent through ruff-format wraps.
    fake_key_with_tail = "sk-fooBARbaz1234"  # pragma: allowlist secret
    fake_key_short = "sk-XXX"  # pragma: allowlist secret
    report = wcs.SpendReport(
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
        rows=[
            _row(user="", api_key=fake_key_with_tail, spend=3.0, calls=2),
            _row(user="alice", api_key=fake_key_short, spend=1.0, calls=1),
            _row(user="", api_key="", spend=0.25, calls=1),
        ],
    )
    by_user = report.by_user
    assert by_user["alice"] == (pytest.approx(1.0), 1)
    assert by_user["sk-***1234"] == (pytest.approx(3.0), 2)
    assert by_user["(unattributed)"] == (pytest.approx(0.25), 1)


# ---------------------------------------------------------------------------
# Title / body rendering
# ---------------------------------------------------------------------------


def test_render_title_matches_spec_format():
    report = wcs.SpendReport(
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
        rows=[_row(spend=12.34)],
    )
    assert wcs.render_title(report) == "Cost summary 2026-05-20 — 12.34 USD"


def test_render_markdown_includes_all_sections():
    report = wcs.SpendReport(
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
        rows=[
            _row(model="opus", user="alice", spend=10.0, calls=20),
            _row(model="haiku", user="bob", spend=1.0, calls=100),
        ],
        source="api",
    )
    body = wcs.render_markdown(report)
    assert "Weekly LiteLLM cost summary" in body
    assert "2026-05-13" in body and "2026-05-20" in body
    assert "$11.00 USD" in body
    assert "## Spend by model" in body
    assert "## Spend by user / API key" in body
    assert "## Top 10 line items" in body
    assert "`opus`" in body and "`haiku`" in body
    assert "FinOps weekly cost review" in body


def test_render_markdown_degraded_when_error_set():
    report = wcs.SpendReport(
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
        source="error",
        error="api: 500 server; db: connection refused",
    )
    body = wcs.render_markdown(report)
    assert "## Status: degraded" in body
    assert "api: 500 server" in body
    assert "Operator action required" in body


def test_render_markdown_empty_rows_explains_zero_spend():
    report = wcs.SpendReport(
        start=datetime(2026, 5, 13, tzinfo=timezone.utc),
        end=datetime(2026, 5, 20, tzinfo=timezone.utc),
        source="api",
    )
    body = wcs.render_markdown(report)
    assert "No spend recorded" in body


# ---------------------------------------------------------------------------
# build_report — source selection (api → db → error)
# ---------------------------------------------------------------------------


def test_build_report_uses_api_when_credentials_set(monkeypatch):
    monkeypatch.setenv("LITELLM_PROXY_URL", "https://proxy.example")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.delenv("LITELLM_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    fake_rows = [_row(model="opus", spend=5.0, calls=2)]
    with mock.patch.object(wcs, "fetch_via_api", return_value=(fake_rows, None)) as api:
        with mock.patch.object(wcs, "fetch_via_db") as db:
            report = wcs.build_report(now_utc=datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc))

    api.assert_called_once()
    db.assert_not_called()
    assert report.source == "api"
    assert report.rows == fake_rows


def test_build_report_falls_back_to_db_on_api_404(monkeypatch):
    monkeypatch.setenv("LITELLM_PROXY_URL", "https://proxy.example")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("LITELLM_DB_URL", "postgresql://x")

    db_rows = [_row(model="opus", spend=7.0, calls=4)]
    with mock.patch.object(wcs, "fetch_via_api", return_value=(None, "404")):
        with mock.patch.object(wcs, "fetch_via_db", return_value=(db_rows, None)):
            report = wcs.build_report(now_utc=datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc))

    assert report.source == "db"
    assert report.rows == db_rows


def test_build_report_records_error_when_both_sources_fail(monkeypatch):
    monkeypatch.delenv("LITELLM_PROXY_URL", raising=False)
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    monkeypatch.delenv("LITELLM_DB_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    report = wcs.build_report(now_utc=datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc))
    assert report.source == "error"
    assert report.error is not None
    assert "LITELLM_PROXY_URL or LITELLM_MASTER_KEY unset" in report.error
    assert "LITELLM_DB_URL / DATABASE_URL unset" in report.error


def test_build_report_window_is_seven_days(monkeypatch):
    monkeypatch.delenv("LITELLM_PROXY_URL", raising=False)
    monkeypatch.delenv("LITELLM_DB_URL", raising=False)
    end = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    report = wcs.build_report(now_utc=end)
    assert (report.end - report.start).days == 7
    assert report.end == end


# ---------------------------------------------------------------------------
# fetch_via_api — JSON envelope handling
# ---------------------------------------------------------------------------


def test_fetch_via_api_handles_list_envelope(monkeypatch):
    """LiteLLM /spend/logs returns a flat list — must aggregate correctly."""

    monkeypatch.setenv("LITELLM_PROXY_URL", "https://proxy.example")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")  # pragma: allowlist secret

    # Fake api_key fixtures pinned to single-line locals so the allow-list
    # comment stays adjacent to the literal through ruff-format wraps.
    opus_key = "sk-1"  # pragma: allowlist secret
    haiku_key = "sk-2"  # pragma: allowlist secret

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return [
                {"model": "opus", "user": "alice", "api_key": opus_key, "spend": 1.5},
                {"model": "opus", "user": "alice", "api_key": opus_key, "spend": 2.5},
                {"model": "haiku", "user": "bob", "api_key": haiku_key, "spend": 0.25},
            ]

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params, headers):
            return _Resp()

    with mock.patch.dict(sys.modules, {"httpx": mock.MagicMock(Client=_Client)}):
        rows, err = wcs.fetch_via_api(
            "https://proxy.example",
            "sk-test",
            datetime(2026, 5, 13, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )

    assert err is None
    assert rows is not None
    # opus row aggregates 1.5 + 2.5 = 4.0; haiku stays 0.25
    by_model = {r.model: r.total_spend for r in rows}
    assert by_model["opus"] == pytest.approx(4.0)
    assert by_model["haiku"] == pytest.approx(0.25)


def test_fetch_via_api_404_returns_fallback_signal(monkeypatch):
    class _Resp:
        status_code = 404
        text = "endpoint not loaded"

        def json(self):  # pragma: no cover — not called on non-200
            return {}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params, headers):
            return _Resp()

    with mock.patch.dict(sys.modules, {"httpx": mock.MagicMock(Client=_Client)}):
        rows, err = wcs.fetch_via_api(
            "https://proxy.example",
            "sk-test",
            datetime(2026, 5, 13, tzinfo=timezone.utc),
            datetime(2026, 5, 20, tzinfo=timezone.utc),
        )

    assert rows is None
    assert err is not None and "404" in err


# ---------------------------------------------------------------------------
# _write_github_output — workflow contract
# ---------------------------------------------------------------------------


def test_write_github_output_renders_heredoc(tmp_path, monkeypatch):
    out_file = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))

    wcs._write_github_output("My title", "line one\nline two\n")
    contents = out_file.read_text()
    assert "title=My title" in contents
    assert "body<<COST_REPORT_EOF" in contents
    assert "line one\nline two" in contents
    assert contents.rstrip().endswith("COST_REPORT_EOF")


def test_write_github_output_adds_trailing_newline_if_missing(tmp_path, monkeypatch):
    out_file = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    wcs._write_github_output("t", "no-trailing-nl")
    contents = out_file.read_text()
    # The heredoc terminator must sit on its own line.
    assert "no-trailing-nl\nCOST_REPORT_EOF" in contents
