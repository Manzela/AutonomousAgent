"""Stop chroma; assert agent continues with vector-memory degradation."""

from __future__ import annotations

import subprocess
import time

import httpx


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
