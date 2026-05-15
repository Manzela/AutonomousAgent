"""Verify shell-sandbox isolation: no host network, no host FS escape."""

from __future__ import annotations

import subprocess


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
