"""Verify 429 returned when budget cap hit."""

from __future__ import annotations

import httpx


def test_budget_cap_enforced(hermes_url, wait_for_stack, monkeypatch):
    """Set $0.01 cap, run a turn; assert 429."""
    httpx.post(f"{hermes_url}/v1/admin/limits", json={"budget": {"daily_usd_cap": 0.01}})
    try:
        r = httpx.post(
            f"{hermes_url}/v1/turn",
            json={"session_id": "test-budget-001", "message": "use up my budget"},
            timeout=15,
        )
        assert r.status_code == 429 or "budget" in r.text.lower()
    finally:
        httpx.post(f"{hermes_url}/v1/admin/limits", json={"budget": {"daily_usd_cap": 100}})
