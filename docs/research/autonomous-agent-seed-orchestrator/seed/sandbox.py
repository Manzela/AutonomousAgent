"""
Sandbox ABC + a POSIX-rlimit subprocess implementation.

`LocalSubprocessSandbox` is **dev/CI only**. It is NOT a security boundary —
it provides resource isolation via POSIX rlimits (RLIMIT_CPU, RLIMIT_AS,
RLIMIT_NOFILE) so a runaway expert can't burn the host, but a hostile module
can still touch the filesystem and the network from inside its workdir.

Production deploys MUST use `FirecrackerSandbox` (see INTEGRATION.md P-4 and
memory note `h1_firecracker_scope`). The orchestrator config refuses to
start with `LocalSubprocessSandbox` if `OrchestratorConfig.production=True`.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(slots=True, frozen=True)
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    killed: bool


class AbstractSandbox(ABC):
    """Run a subprocess under resource caps; return its output."""

    @abstractmethod
    async def run(
        self,
        *,
        cmd: list[str],
        workdir: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        stdin: Optional[str] = None,
        timeout_s: float = 60.0,
        cpu_seconds: int = 30,
        memory_mb: int = 512,
        max_files: int = 256,
        network_allowed: bool = False,
    ) -> SandboxResult: ...


class LocalSubprocessSandbox(AbstractSandbox):
    """POSIX-rlimit subprocess sandbox. Dev/CI grade.

    Hard refusal: `network_allowed=True` raises immediately. This sandbox
    cannot enforce network isolation (it has no netns); accepting that flag
    would silently lie to callers about isolation properties.
    """

    is_production_grade: bool = False

    async def run(
        self,
        *,
        cmd: list[str],
        workdir: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        stdin: Optional[str] = None,
        timeout_s: float = 60.0,
        cpu_seconds: int = 30,
        memory_mb: int = 512,
        max_files: int = 256,
        network_allowed: bool = False,
    ) -> SandboxResult:
        if network_allowed:
            raise PermissionError(
                "LocalSubprocessSandbox cannot enforce network isolation. "
                "Use FirecrackerSandbox for network_allowed=True."
            )
        if sys.platform not in ("linux", "darwin"):
            raise RuntimeError(f"LocalSubprocessSandbox is POSIX-only; platform={sys.platform}")

        with tempfile.TemporaryDirectory(prefix="sandbox-") as td:
            cwd = workdir or Path(td)
            cwd.mkdir(parents=True, exist_ok=True)
            preexec = _make_preexec(
                cpu_seconds=cpu_seconds, memory_mb=memory_mb, max_files=max_files
            )
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cwd),
                env=env or {},
                stdin=asyncio.subprocess.PIPE if stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=preexec,
                start_new_session=True,  # for killpg cleanup
            )
            t0 = _now()
            killed = False
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(stdin.encode("utf-8") if stdin else None),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                killed = True
                # Kill the process group so we get any forked children.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    stdout_b, stderr_b = await proc.communicate()
                except Exception:
                    stdout_b, stderr_b = b"", b""
            duration = _now() - t0
            rc = proc.returncode if proc.returncode is not None else -1
            return SandboxResult(
                returncode=rc,
                stdout=stdout_b.decode("utf-8", errors="replace"),
                stderr=stderr_b.decode("utf-8", errors="replace"),
                duration_s=duration,
                killed=killed,
            )


def _make_preexec(*, cpu_seconds: int, memory_mb: int, max_files: int):
    """Build a preexec_fn that applies POSIX rlimits to the child.

    Returns None on non-POSIX (preexec_fn isn't supported on Windows anyway).
    """

    def preexec() -> None:
        import resource  # POSIX-only; safe inside the preexec closure

        # CPU seconds (SIGXCPU at soft limit, SIGKILL at hard).
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        # Address space (bytes). Affects malloc/mmap and most heap growth.
        as_bytes = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
        # Max open files.
        resource.setrlimit(resource.RLIMIT_NOFILE, (max_files, max_files))
        # Detach from parent's stdio session (start_new_session already does
        # this when set on subprocess.create), but make doubly sure.
        try:
            os.setsid()
        except OSError:
            pass

    return preexec


def _now() -> float:
    import time as _t

    return _t.monotonic()
