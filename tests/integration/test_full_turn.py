"""Full turn round-trip via mocked LLM.

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
            "Requires INTEGRATION_LIVE_STACK=1 and a running docker-compose stack. "
            "Previously unconditionally skipped (T-2 fix; see audit/2026-05-27-ground-truth/findings.md P1.F T-2)."
        ),
    ),
]


def test_health_endpoint_responds(hermes_url, wait_for_stack):
    r = httpx.get(f"{hermes_url}/health", timeout=5)
    assert r.status_code == 200


def test_full_turn_via_admin_api(hermes_url, wait_for_stack):
    """POST a synthetic user turn; assert we get a structured response back."""
    r = httpx.post(
        f"{hermes_url}/v1/turn",
        json={"session_id": "test-full-turn-001", "message": "ping"},
        timeout=30,
    )
    assert r.status_code == 200
    data = r.json()
    assert "response" in data
    # The mocked LLM returns "Mocked response: pong"
    assert "pong" in data["response"].lower()
