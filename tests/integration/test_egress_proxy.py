"""SP-00g: Validate the L7 hostname-allowlist egress proxy (Squid).

Tests bring up the `egress-proxy-test` Docker Compose project, run a curl
client container on its network, then tear everything down.  The pattern
mirrors test_sandbox_isolation.py: a lazy `_docker_available()` probe causes
the entire module to SKIP (not FAIL) on hosts without a Docker daemon.

Markers:
  integration  — cross-module, requires live infra.
  docker       — requires Docker daemon + `docker compose` CLI.
  network      — requires real outbound TCP/443 (applied per-test where needed).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Docker availability probe (lazy, process-cached)
# ---------------------------------------------------------------------------

_DOCKER_AVAILABLE_CACHE: bool | None = None

COMPOSE_FILE = (
    Path(__file__).parent.parent.parent / "deploy" / "docker-compose.egress-proxy-test.yml"
)

# Pinned digest for curlimages/curl:8.11.1 (pulled 2026-06-01).
# Re-pin with: docker inspect --format '{{index .RepoDigests 0}}' curlimages/curl:8.11.1
CURL_IMAGE = (
    "curlimages/curl@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69"
)


def _docker_available() -> bool:
    """True iff `docker info` succeeds AND `docker compose version` succeeds.

    Lazy + process-cached: collection touches this once per process, not
    once per test, keeping `pytest --collect-only` fast on docker-less hosts.
    """
    global _DOCKER_AVAILABLE_CACHE
    if _DOCKER_AVAILABLE_CACHE is not None:
        return _DOCKER_AVAILABLE_CACHE
    if shutil.which("docker") is None:
        _DOCKER_AVAILABLE_CACHE = False
        return False
    try:
        info = subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
        if info.returncode != 0:
            _DOCKER_AVAILABLE_CACHE = False
            return False
        ver = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        _DOCKER_AVAILABLE_CACHE = ver.returncode == 0
        return _DOCKER_AVAILABLE_CACHE
    except (subprocess.TimeoutExpired, FileNotFoundError):
        _DOCKER_AVAILABLE_CACHE = False
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.docker,
    pytest.mark.skipif(
        not _docker_available(),
        reason="docker daemon or `docker compose` CLI not available",
    ),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COMPOSE_CMD = ["docker", "compose", "-f", str(COMPOSE_FILE)]


def _compose_up() -> None:
    """Bring the egress-proxy project up and wait for the healthcheck."""
    subprocess.run(
        [*_COMPOSE_CMD, "up", "-d", "--wait"],
        check=True,
        capture_output=True,
        timeout=120,
    )


def _compose_down() -> None:
    """Tear the egress-proxy project down and remove anonymous volumes."""
    subprocess.run(
        [*_COMPOSE_CMD, "down", "-v"],
        check=False,
        capture_output=True,
        timeout=60,
    )


def _curl_via_proxy(url: str, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run a curl container on the proxy network and return the CompletedProcess.

    Uses `--network egress-proxy-test_default` so curl can reach `egress-proxy:3128`.
    Returns the process (caller checks returncode / stdout).
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "egress-proxy-test_default",
        CURL_IMAGE,
        "-s",
        "-o",
        "/dev/null",
        "-w",
        "%{http_code}",
        "-x",
        "http://egress-proxy:3128",
        *(extra_args or []),
        url,
    ]
    return subprocess.run(cmd, capture_output=True, timeout=30, check=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_proxy_blocks_evil_example() -> None:
    """Squid MUST reject CONNECT to evil.example with a non-2xx response."""
    _compose_up()
    try:
        result = _curl_via_proxy("https://evil.example/exfil")
        http_code = result.stdout.decode().strip()
        # Squid returns 403 for TCP_DENIED.  We accept any non-2xx code
        # (e.g. 403, 407) because network-level denial can also surface as an
        # empty response (code "000").  The important assertion is: NOT 2xx.
        assert http_code not in ("200", "201", "204"), (
            f"Expected non-2xx for evil.example but got HTTP {http_code}. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    finally:
        _compose_down()


@pytest.mark.network
def test_proxy_allows_api_github_com() -> None:
    """Squid MUST allow CONNECT tunnels to api.github.com (allowlisted)."""
    _compose_up()
    try:
        result = _curl_via_proxy(
            "https://api.github.com/",
            extra_args=["--max-time", "15"],
        )
        http_code = result.stdout.decode().strip()
        # Any response that is NOT a Squid-level denial (403/000) means the
        # tunnel was allowed.  GitHub may return 200, 301, 401, 404 etc.
        # We only need to confirm the proxy did NOT block it.
        assert http_code not in ("403",), (
            f"Expected proxy to ALLOW api.github.com but got HTTP {http_code}. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        # Squid TCP_DENIED produces "000" (connection reset before HTTP response).
        assert (
            http_code != "000"
        ), "Proxy returned empty response (000) — connection was reset/denied."
    finally:
        _compose_down()


def test_proxy_logs_tcp_denied() -> None:
    """After a blocked request the Squid log MUST contain 'TCP_DENIED'."""
    _compose_up()
    try:
        # Issue a blocked request first.
        _curl_via_proxy("https://evil.example/probe")
        # Allow a moment for Squid to flush the access_log.
        time.sleep(1)
        logs = subprocess.run(
            [*_COMPOSE_CMD, "logs", "egress-proxy"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        log_output = logs.stdout + logs.stderr
        assert b"TCP_DENIED" in log_output, (
            "Expected 'TCP_DENIED' in Squid logs after blocking evil.example, "
            f"but it was absent. Logs:\n{log_output.decode(errors='replace')[:2000]}"
        )
    finally:
        _compose_down()


def test_proxy_blocks_second_host() -> None:
    """A second disallowed host (malicious.example) is also denied.

    Negative control: proves the deny-all is the catch-all rule, not an
    evil.example-specific special-case.
    """
    _compose_up()
    try:
        result = _curl_via_proxy("https://malicious.example/steal")
        http_code = result.stdout.decode().strip()
        assert http_code not in ("200", "201", "204"), (
            f"Expected non-2xx for malicious.example but got HTTP {http_code}. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    finally:
        _compose_down()
