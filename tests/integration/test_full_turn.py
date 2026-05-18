"""Full turn round-trip via mocked LLM."""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.skip(
    reason="P2 — requires HTTP gateway (de-scoped in P1; original two-service architecture collapsed into single hermes service per commit 408459e); see docs/superpowers/specs/2026-05-18-phase1-completion-coordination-design.md §6.1 triage matrix"
)


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
