"""Integration test fixtures: docker compose stack assumed running via docker-compose.test.yml."""

from __future__ import annotations

import os
import time

import httpx
import pytest


HERMES_AGENT_URL = os.environ.get("HERMES_AGENT_URL", "http://localhost:7878")
LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:4000")


@pytest.fixture(scope="session")
def hermes_url() -> str:
    return HERMES_AGENT_URL


@pytest.fixture(scope="session")
def wait_for_stack():
    """Wait until hermes-agent is healthy before running tests."""
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            r = httpx.get(f"{HERMES_AGENT_URL}/health", timeout=3)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError("Stack did not become healthy within 60s")
