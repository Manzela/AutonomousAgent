"""Stop chroma; assert agent continues with vector-memory degradation.

T-2 fix: converted from unconditional pytest.mark.skip to a conditional skip
guarded by the INTEGRATION_LIVE_STACK environment variable.  The test is skipped
by default in unit-test CI; it runs in dedicated integration CI when the full
docker-compose stack is up.

Set INTEGRATION_LIVE_STACK=1 to enable.
"""

from __future__ import annotations

import os
import subprocess
import time

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


def test_chroma_outage_degrades_gracefully(hermes_url, wait_for_stack):
    subprocess.run(
        ["docker", "compose", "-f", "deploy/docker-compose.yml", "stop", "chroma"], check=True
    )
    try:
        time.sleep(3)
        r = httpx.post(
            f"{hermes_url}/v1/turn",
            json={"session_id": "test-chroma-out-001", "message": "ping with no vector memory"},
            timeout=20,
        )
        assert r.status_code == 200
        body = r.json()
        assert "response" in body
        assert any(d.get("name") == "vector_memory" for d in body.get("degraded", []))
    finally:
        subprocess.run(
            ["docker", "compose", "-f", "deploy/docker-compose.yml", "start", "chroma"], check=True
        )
