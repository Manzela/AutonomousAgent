#!/usr/bin/env python3
"""Weekly LiteLLM cost summary — invoked by .github/workflows/weekly-cost-summary.yml.

Produces a markdown summary of the previous 7-day LiteLLM spend aggregated
by model and by user/api_key, posts it as a GitHub issue with the
``cost-report`` label, and assigns @Manzela. Matches the FinOps Foundation
weekly cost-review cadence and NIST SP 800-53 AU-6 (audit review).

Operational contract — mirrors ``lib/durability/budget_watchdog.py``:

* **Source of truth.** Tries the LiteLLM proxy ``/spend/logs`` REST
  endpoint first (with LITELLM_PROXY_URL + LITELLM_MASTER_KEY). On 404
  (the endpoint isn't loaded by default, audit finding R5) it falls
  back to direct Postgres on ``LITELLM_DB_URL`` / ``DATABASE_URL`` —
  same pattern PR #84 uses for the daily budget watchdog.

* **Time window.** ``[now - 7d, now)`` in UTC, evaluated at run time so
  the cron drift (workflow scheduler vs the start of the report week)
  shows up in the report header.

* **Output.** Writes the rendered markdown to ``$GITHUB_OUTPUT`` as
  ``body<<EOF\\n...\\nEOF`` and ``title=...`` so the workflow can pass
  them straight to ``gh issue create``. When run outside Actions
  (``--print``), prints the rendering to stdout for local validation.

* **Fail-soft.** If neither the API nor the DB is reachable, the script
  still emits an issue body — it just contains the diagnostic error so
  the operator sees the failure on Monday morning instead of silent
  data loss.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("weekly_cost_summary")

# Query window — last 7 full UTC days (rolling, not calendar week, so a
# Monday cron always covers Mon-prev → Sun-prev once it lands).
WINDOW_DAYS = 7

# Direct-DB fallback. Mirrors lib/durability/budget_watchdog.py — same
# table, same camelCase Prisma columns.
_WEEKLY_SPEND_SQL = (
    'SELECT model, "user", api_key, '
    "COALESCE(SUM(spend), 0.0)::float AS total_spend, "
    "COUNT(*)::int AS call_count "
    'FROM "LiteLLM_SpendLogs" '
    'WHERE "startTime" >= %s AND "startTime" < %s '
    'GROUP BY model, "user", api_key '
    "ORDER BY total_spend DESC"
)


@dataclass(frozen=True)
class SpendRow:
    """One row from the spend aggregation — model × user × api_key."""

    model: str
    user: str
    api_key: str
    total_spend: float
    call_count: int


@dataclass
class SpendReport:
    """Aggregated 7-day spend, ready to render."""

    start: datetime
    end: datetime
    rows: list[SpendRow] = field(default_factory=list)
    source: str = "unknown"  # "api" | "db" | "error"
    error: Optional[str] = None

    @property
    def total_usd(self) -> float:
        return sum(r.total_spend for r in self.rows)

    @property
    def by_model(self) -> dict[str, tuple[float, int]]:
        out: dict[str, tuple[float, int]] = {}
        for r in self.rows:
            spend, calls = out.get(r.model, (0.0, 0))
            out[r.model] = (spend + r.total_spend, calls + r.call_count)
        return out

    @property
    def by_user(self) -> dict[str, tuple[float, int]]:
        out: dict[str, tuple[float, int]] = {}
        for r in self.rows:
            # Coalesce user/api_key — most installs have one of the two
            # set per call; we surface whichever is non-empty.
            label = r.user or _redact_key(r.api_key) or "(unattributed)"
            spend, calls = out.get(label, (0.0, 0))
            out[label] = (spend + r.total_spend, calls + r.call_count)
        return out


def _redact_key(key: str) -> str:
    """Show ``sk-***last4`` so the report doesn't leak the master key.

    Audit finding: cost reports are posted to GitHub issues which are
    archived indefinitely; full keys must never appear there.
    """

    if not key:
        return ""
    if len(key) <= 4:
        return "sk-***"
    return f"{key[:3]}***{key[-4:]}"


# ---------------------------------------------------------------------------
# Source 1 — LiteLLM proxy REST API
# ---------------------------------------------------------------------------


def fetch_via_api(
    proxy_url: str,
    master_key: str,
    start: datetime,
    end: datetime,
) -> tuple[Optional[list[SpendRow]], Optional[str]]:
    """Try the LiteLLM /spend/logs endpoint. Returns (rows, error_msg).

    Returns (rows, None) on success, (None, "...") on any failure so
    the caller can fall back to the direct-DB path. We treat 404 as a
    soft signal that the API extension isn't loaded (mirrors PR #84).
    """

    try:
        import httpx  # type: ignore[import-not-found]
    except ImportError:
        return None, "httpx not installed (install in workflow)"

    url = proxy_url.rstrip("/") + "/spend/logs"
    params = {
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
    }
    headers = {"Authorization": f"Bearer {master_key}"}
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, params=params, headers=headers)
    except Exception as exc:  # noqa: BLE001 — fall back to DB on any error
        return None, f"API request failed: {exc}"

    if resp.status_code == 404:
        return None, "API returned 404 (endpoint not loaded; falling back to DB)"
    if resp.status_code >= 400:
        return None, f"API returned {resp.status_code}: {resp.text[:200]}"

    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        return None, f"API JSON decode failed: {exc}"

    # LiteLLM /spend/logs returns a list of records OR a {"data": [...]}
    # envelope depending on version. Normalise to a list.
    records = payload if isinstance(payload, list) else payload.get("data", [])
    aggregated: dict[tuple[str, str, str], list[float]] = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        key = (
            str(rec.get("model") or ""),
            str(rec.get("user") or rec.get("user_id") or ""),
            str(rec.get("api_key") or ""),
        )
        bucket = aggregated.setdefault(key, [0.0, 0])
        bucket[0] += float(rec.get("spend") or 0.0)
        bucket[1] += 1
    rows = [
        SpendRow(model=m, user=u, api_key=k, total_spend=s, call_count=int(c))
        for (m, u, k), (s, c) in aggregated.items()
    ]
    rows.sort(key=lambda r: r.total_spend, reverse=True)
    return rows, None


# ---------------------------------------------------------------------------
# Source 2 — direct Postgres on LiteLLM_SpendLogs
# ---------------------------------------------------------------------------


def _connect(conn_str: str) -> Any:
    """Lazy psycopg import + connect. Mirrors budget_watchdog._connect."""

    import psycopg  # type: ignore[import-not-found]

    return psycopg.connect(conn_str)


def fetch_via_db(
    conn_str: str,
    start: datetime,
    end: datetime,
) -> tuple[Optional[list[SpendRow]], Optional[str]]:
    """Direct query against LiteLLM_SpendLogs. Returns (rows, error_msg)."""

    try:
        conn = _connect(conn_str)
    except ImportError:
        return None, "psycopg not installed"
    except Exception as exc:  # noqa: BLE001
        return None, f"DB connect failed: {exc}"

    try:
        cur = conn.cursor()
        cur.execute(_WEEKLY_SPEND_SQL, (start, end))
        records = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return None, f"DB query failed: {exc}"
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    rows = [
        SpendRow(
            model=str(r[0] or ""),
            user=str(r[1] or ""),
            api_key=str(r[2] or ""),
            total_spend=float(r[3] or 0.0),
            call_count=int(r[4] or 0),
        )
        for r in records
    ]
    return rows, None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def build_report(now_utc: Optional[datetime] = None) -> SpendReport:
    """Fetch spend rows from the best available source and return a report."""

    end = now_utc or datetime.now(timezone.utc)
    start = end - timedelta(days=WINDOW_DAYS)
    report = SpendReport(start=start, end=end)

    proxy_url = os.environ.get("LITELLM_PROXY_URL", "").strip()
    master_key = os.environ.get("LITELLM_MASTER_KEY", "").strip()

    # API first (cheapest, no DB credentials needed in CI).
    api_err: Optional[str] = None
    if proxy_url and master_key:
        rows, api_err = fetch_via_api(proxy_url, master_key, start, end)
        if rows is not None:
            report.rows = rows
            report.source = "api"
            return report
    else:
        api_err = "LITELLM_PROXY_URL or LITELLM_MASTER_KEY unset"

    # DB fallback.
    db_url = (
        os.environ.get("LITELLM_DB_URL", "").strip() or os.environ.get("DATABASE_URL", "").strip()
    )
    db_err: Optional[str] = None
    if db_url:
        rows, db_err = fetch_via_db(db_url, start, end)
        if rows is not None:
            report.rows = rows
            report.source = "db"
            return report
    else:
        db_err = "LITELLM_DB_URL / DATABASE_URL unset"

    report.source = "error"
    report.error = f"api: {api_err}; db: {db_err}"
    return report


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def render_markdown(report: SpendReport) -> str:
    """Render the report as a GitHub-flavoured markdown issue body."""

    lines: list[str] = []
    lines.append(f"# Weekly LiteLLM cost summary — {report.start:%Y-%m-%d} → {report.end:%Y-%m-%d}")
    lines.append("")
    lines.append(
        f"**Total spend:** `${report.total_usd:.2f} USD` over **{WINDOW_DAYS} days** "
        f"(source: `{report.source}`)."
    )
    lines.append("")

    if report.error:
        lines.append("## Status: degraded")
        lines.append("")
        lines.append("Neither the LiteLLM API nor the direct DB connection returned data.")
        lines.append("")
        lines.append(f"```\n{report.error}\n```")
        lines.append("")
        lines.append(
            "**Operator action required:** verify `LITELLM_PROXY_URL`, "
            "`LITELLM_MASTER_KEY`, and `LITELLM_DB_URL` repository secrets "
            "and the proxy's reachability from GitHub Actions."
        )
        lines.append("")
        return "\n".join(lines)

    if not report.rows:
        lines.append(
            "_No spend recorded in the window. Either the agent was paused, "
            "or the proxy collected no requests._"
        )
        lines.append("")
        return "\n".join(lines)

    # By model
    lines.append("## Spend by model")
    lines.append("")
    lines.append("| Model | Spend (USD) | Calls | Avg cost/call |")
    lines.append("| --- | ---: | ---: | ---: |")
    by_model = sorted(report.by_model.items(), key=lambda kv: kv[1][0], reverse=True)
    for model, (spend, calls) in by_model:
        avg = spend / calls if calls else 0.0
        lines.append(f"| `{model or '(unknown)'}` | ${spend:.2f} | {calls:,} | ${avg:.4f} |")
    lines.append("")

    # By user / api_key
    lines.append("## Spend by user / API key")
    lines.append("")
    lines.append("| Identity | Spend (USD) | Calls |")
    lines.append("| --- | ---: | ---: |")
    by_user = sorted(report.by_user.items(), key=lambda kv: kv[1][0], reverse=True)
    for label, (spend, calls) in by_user:
        lines.append(f"| `{label}` | ${spend:.2f} | {calls:,} |")
    lines.append("")

    # Top line items (so the operator can drill into individual outliers)
    lines.append("## Top 10 line items (model × user × key)")
    lines.append("")
    lines.append("| Model | User | API key | Spend | Calls |")
    lines.append("| --- | --- | --- | ---: | ---: |")
    for r in report.rows[:10]:
        lines.append(
            f"| `{r.model or '?'}` | `{r.user or '-'}` | "
            f"`{_redact_key(r.api_key) or '-'}` | "
            f"${r.total_spend:.2f} | {r.call_count:,} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "Generated by `.github/workflows/weekly-cost-summary.yml` "
        "(FinOps weekly cost review, NIST SP 800-53 AU-6)."
    )
    lines.append("")
    return "\n".join(lines)


def render_title(report: SpendReport) -> str:
    """``Cost summary YYYY-MM-DD — N.NN USD`` — matches the spec exactly."""

    today = report.end.strftime("%Y-%m-%d")
    return f"Cost summary {today} — {report.total_usd:.2f} USD"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_github_output(title: str, body: str) -> None:
    """Stream ``title`` and ``body`` to ``$GITHUB_OUTPUT`` for the workflow."""

    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        logger.warning("GITHUB_OUTPUT unset; outputs not written")
        return
    with open(out_path, "a", encoding="utf-8") as f:
        f.write(f"title={title}\n")
        f.write("body<<COST_REPORT_EOF\n")
        f.write(body)
        if not body.endswith("\n"):
            f.write("\n")
        f.write("COST_REPORT_EOF\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print rendered markdown to stdout (local-dev mode)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    report = build_report()
    title = render_title(report)
    body = render_markdown(report)

    if args.print:
        print(f"# Title\n{title}\n")
        print("# Body")
        print(body)
        return 0

    _write_github_output(title, body)
    logger.info(
        "Cost summary built: source=%s total=$%.2f rows=%d",
        report.source,
        report.total_usd,
        len(report.rows),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
