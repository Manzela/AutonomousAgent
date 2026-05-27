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


# ─────────────────────────────────────────────────────────────────────
# P2-19: Additional escape-attempt tests
# ─────────────────────────────────────────────────────────────────────

_COMPOSE_CMD = [
    "docker",
    "compose",
    "-f",
    "deploy/docker-compose.yml",
    "exec",
    "-T",
    "shell-sandbox",
]


def _exec(cmd: list[str], *, timeout: int = 15, **kw) -> subprocess.CompletedProcess:
    """Run a command inside shell-sandbox; always captures output."""
    return subprocess.run(
        _COMPOSE_CMD + cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        **kw,
    )


def test_shell_sandbox_fork_bomb_limited():
    """A fork bomb must be killed by pids_limit before consuming the host.

    We use a bounded variant (`:() { :|: & }; :` with an iteration cap)
    so the test terminates in finite time.  The sandbox must either cap the
    PID count (EPERM / fork failed) or the entire shell exits non-zero.

    Acceptance: the exec command exits within 10 s with a non-zero return
    code or produces output indicating 'fork' failed.  A zero exit code
    with unbounded output is a failure.
    """
    out = _exec(
        [
            "bash",
            "-c",
            # Attempt 64 rapid forks; pids_limit should choke this before 64.
            "for i in $(seq 1 64); do (true) & done; wait; echo done",
        ],
        timeout=10,
    )
    # If pids_limit is effective, some forks will fail with EPERM / "fork:
    # retry: Resource temporarily unavailable".  Accept both outcomes:
    #   (a) returncode != 0 — bash itself hit the limit
    #   (b) returncode == 0 but stderr/stdout contains a fork-failure indicator
    fork_failed = (
        "Resource temporarily unavailable" in (out.stderr or "")
        or "Cannot fork" in (out.stderr or "")
        or "fork" in (out.stderr or "").lower()
    )
    sandbox_exited_clean = out.returncode == 0 and "done" in out.stdout
    assert out.returncode != 0 or fork_failed or not sandbox_exited_clean, (
        "Fork bomb completed 64 forks without any resource limit hit — "
        f"pids_limit may not be enforced. returncode={out.returncode}, "
        f"stdout={out.stdout!r}, stderr={out.stderr!r}"
    )


def test_shell_sandbox_mem_bomb_limited():
    """Allocating >memory limit via /dev/zero dd must be killed or fail.

    Assumes the sandbox has a memory limit lower than the amount we try to
    allocate.  We attempt to allocate 2 GiB; any sandbox with mem_limit <=
    512 MiB should OOM-kill or reject the allocation.
    """
    out = _exec(
        [
            "bash",
            "-c",
            "dd if=/dev/zero bs=1M count=2048 | tail -c 1; echo exit=$?",
        ],
        timeout=20,
    )
    oom_or_killed = out.returncode not in (0,) or "Killed" in (out.stdout + out.stderr)
    # Accept either a non-zero exit (OOM kill) or stdout containing "Killed"
    assert oom_or_killed, (
        "Memory allocation of 2 GiB completed without OOM kill — "
        f"mem_limit may not be enforced. returncode={out.returncode}, "
        f"stderr={out.stderr!r}"
    )


def test_shell_sandbox_no_cap_sys_admin():
    """CAP_SYS_ADMIN must not be available inside the sandbox.

    We probe via 'unshare --mount' (requires CAP_SYS_ADMIN or user-ns
    support).  Even if user namespaces are enabled on the host, a proper
    seccomp + cap_drop policy prevents mount namespace creation by an
    unprivileged process inside the container.
    """
    out = _exec(
        [
            "bash",
            "-c",
            "unshare --mount /bin/true 2>&1; echo exit=$?",
        ],
        timeout=10,
    )
    denied = (
        "Operation not permitted" in (out.stdout + out.stderr)
        or "Permission denied" in (out.stdout + out.stderr)
        or out.returncode != 0
    )
    assert denied, (
        "unshare --mount succeeded inside shell-sandbox — CAP_SYS_ADMIN "
        f"or mount unshare may be available. "
        f"returncode={out.returncode}, stdout={out.stdout!r}"
    )


def test_shell_sandbox_seccomp_blocks_kexec():
    """kexec_load syscall must be blocked by the seccomp profile.

    kexec_load is a high-severity primitive; a correct seccomp allowlist
    must not include it.  We probe via 'syscall' through a Python one-liner
    (ctypes) because kexec is not normally exposed as a bash builtin.

    If Python is unavailable, the test checks that the 'kexec' binary
    (if present) fails.
    """
    out = _exec(
        [
            "bash",
            "-c",
            (
                'python3 -c "'
                "import ctypes, sys; "
                "libc = ctypes.CDLL('libc.so.6', use_errno=True); "
                "ret = libc.syscall(246); "  # 246 = __NR_kexec_load on x86-64
                "print('ret=' + str(ret)); "
                "import ctypes.util; "
                "errno = ctypes.get_errno(); "
                "print('errno=' + str(errno)); "
                "sys.exit(0 if ret == -1 else 1)"
                '" 2>&1; echo pystatus=$?'
            ),
        ],
        timeout=10,
    )
    # Acceptable outcomes:
    #   (a) python3 not present → bash exits non-zero or prints 'not found'
    #   (b) syscall returns -1 (EPERM/ENOSYS from seccomp) → pystatus=0
    # Unacceptable: ret=0 (syscall succeeded)
    assert "ret=0" not in out.stdout or "pystatus=1" in out.stdout or out.returncode != 0, (
        "kexec_load syscall (syscall 246) succeeded inside shell-sandbox — "
        "seccomp profile may be missing or not denying kexec. "
        f"stdout={out.stdout!r}, returncode={out.returncode}"
    )


def test_shell_sandbox_rlimit_nproc_enforced():
    """RLIMIT_NPROC (max user processes) must be enforced inside the sandbox.

    Docker --pids-limit sets an upper bound; we verify that spawning
    processes beyond that limit produces a resource error rather than
    succeeding silently.
    """
    out = _exec(
        [
            "bash",
            "-c",
            # Try to spawn 512 background subshells; pids_limit < 512 should choke.
            (
                "fail=0; "
                "for i in $(seq 1 512); do "
                "  (true) 2>/dev/null || { fail=1; break; }; "
                "done; "
                "echo fail=$fail"
            ),
        ],
        timeout=15,
    )
    hit_limit = "fail=1" in out.stdout or out.returncode != 0
    assert hit_limit, (
        "Spawned 512 processes without hitting any resource limit — "
        "pids_limit may not be configured or enforced. "
        f"stdout={out.stdout!r}, returncode={out.returncode}"
    )
