"""Verify shell-sandbox isolation: no host network, no host FS escape.

Tests in this module exec into the live `deploy/docker-compose.yml` stack
via `docker compose exec`, so they hard-require a running docker daemon
AND the Compose v2 plugin. We apply `@pytest.mark.docker` + a lazy
`_docker_available()` probe so the suite cleanly SKIPS (does not FAIL)
on hosts without docker — mirroring the pattern proven in
`tests/integration/test_hermes_plugin_loader_smoke.py:166`.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

_DOCKER_AVAILABLE_CACHE: bool | None = None


def _docker_available() -> bool:
    """True iff `docker info` succeeds AND `docker compose version` succeeds.

    Lazy + process-cached: collection touches this once per process, not
    once per test, keeping `pytest --collect-only` fast on docker-less
    hosts.
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


def test_docker_skip_guard_fires_when_docker_absent(monkeypatch):
    """The skip-guard MUST cause Docker tests to skip (not fail) when docker is unreachable."""
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "docker" else "/usr/bin/" + name
    )
    import tests.integration.test_sandbox_isolation as mod

    mod._DOCKER_AVAILABLE_CACHE = None
    assert mod._docker_available() is False
    # Reset cache so subsequent live runs re-probe
    mod._DOCKER_AVAILABLE_CACHE = None


def test_shell_sandbox_no_network():
    """`curl example.com` from inside shell-sandbox must fail."""
    out = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "deploy/docker-compose.yml",
            "exec",
            "-T",
            "shell-sandbox",
            "curl",
            "-fsS",
            "--max-time",
            "3",
            "https://example.com",
        ],
        capture_output=True,
    )
    assert out.returncode != 0, "shell-sandbox should NOT have internet access"


def test_shell_sandbox_no_root_fs_write():
    """Writing to / from inside shell-sandbox must fail (read-only)."""
    out = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "deploy/docker-compose.yml",
            "exec",
            "-T",
            "shell-sandbox",
            "bash",
            "-c",
            "echo test > /etc/should-not-write 2>&1; echo $?",
        ],
        capture_output=True,
        text=True,
    )
    assert "1" in out.stdout or "Permission denied" in out.stdout or "Read-only" in out.stdout
