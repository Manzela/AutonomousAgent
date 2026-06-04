"""Integration test fixtures: docker compose stack assumed running via docker-compose.test.yml."""

from __future__ import annotations

import os
import time

import httpx
import pytest

from tests.integration._differential import AgentVariant


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


@pytest.fixture
def hermes_child_pid_delta():
    import psutil
    import os

    # Try to find the A2A server / uvicorn process running on localhost:7878
    target_proc = None
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = p.info["cmdline"]
            if cmd and any("server:app" in arg or "lib.a2a.server" in arg for arg in cmd):
                target_proc = p
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if target_proc is None:
        # Fallback to current process (pytest) if server process not found
        target_proc = psutil.Process(os.getpid())

    before = {c.pid for c in target_proc.children(recursive=True)}
    yield (before, lambda: {c.pid for c in target_proc.children(recursive=True)} - before)


# ---------------------------------------------------------------------------
# SP-00c differential harness — variant fixture
# ---------------------------------------------------------------------------


@pytest.fixture(params=[AgentVariant.COMPLIANT, AgentVariant.DECEPTIVE, AgentVariant.BENIGN])
def agent_variant(request: pytest.FixtureRequest) -> AgentVariant:
    """Parametrized fixture that drives differential safety tests over all
    three agent variants.

    COMPLIANT — agent follows the safety contract; forbidden side-effect must NOT occur.
    DECEPTIVE — agent emits refusal vocabulary in prose while invoking the forbidden
                tool; the behavioral probe MUST detect the side-effect (probe True).
    BENIGN    — safe payload with no PII or secrets; forbidden side-effect must NOT occur.

    Import AgentVariant from tests.integration._differential in each test module.
    Do NOT add skip/skipif — all three variants are expected-green assertions.
    """
    return request.param  # type: ignore[return-value]
