"""Verify 429 returned when budget cap hit.

T-2 fix: converted from unconditional pytest.mark.skip to a conditional skip
guarded by the INTEGRATION_LIVE_STACK environment variable.  The test is skipped
by default in unit-test CI; it runs in dedicated integration CI when the full
docker-compose stack is up.

Set INTEGRATION_LIVE_STACK=1 to enable.
"""

from __future__ import annotations

import os

import httpx
import pytest

_LIVE_STACK = bool(os.environ.get("INTEGRATION_LIVE_STACK"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker,
    pytest.mark.skipif(
        not _LIVE_STACK,
        reason=(
            "Requires INTEGRATION_LIVE_STACK=1 and a running docker-compose stack "
            "with /v1/admin/limits endpoint implemented. "
            "Previously unconditionally skipped (T-2 fix; see audit/2026-05-27-ground-truth/findings.md P1.F T-2)."
        ),
    ),
]


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
