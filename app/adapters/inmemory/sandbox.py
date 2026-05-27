from __future__ import annotations
import asyncio
import os
import signal
import sys
import tempfile
from pathlib import Path
from typing import Optional

from app.core.sandbox import AbstractSandbox, SandboxResult


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
                env=env if env is not None else None,
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

    KNOWN RISK (P3-11): preexec_fn is unsafe in multi-threaded Python processes
    because the child is created via fork() while other threads may hold locks
    that are never released in the forked child (fork-deadlock).  This is
    accepted for the CI / test-only context where LocalSubprocessSandbox runs.
    Production deployments MUST use FirecrackerSandbox, which uses a
    process-manager sidecar instead of fork+exec (no preexec_fn).
    """

    def preexec() -> None:
        import resource  # POSIX-only; safe inside the preexec closure

        # CPU seconds (SIGXCPU at soft limit, SIGKILL at hard).
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 5))
        # Address space (bytes). Affects malloc/mmap and most heap growth.
        # Fail-CLOSED: if the OS rejects the rlimit (ValueError = bad value;
        # OSError/EPERM = container hard-limit prevents the request), abort the
        # child process before exec rather than running without memory isolation.
        # The parent sees this as SubprocessError from create_subprocess_exec.
        as_bytes = memory_mb * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
        except (ValueError, OSError) as exc:
            sys.stderr.write(
                f"sandbox: RLIMIT_AS ({as_bytes // (1024 * 1024)}MB) rejected by OS: {exc!r}; "
                f"aborting child (fail-closed — no memory isolation)\n"
            )
            raise  # Abort child process before exec; parent gets SubprocessError.
        # Max open files. Same fail-closed policy.
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (max_files, max_files))
        except (ValueError, OSError) as exc:
            sys.stderr.write(
                f"sandbox: RLIMIT_NOFILE ({max_files}) rejected by OS: {exc!r}; "
                f"aborting child (fail-closed — no file-descriptor isolation)\n"
            )
            raise  # Abort child process before exec.
        # Detach from parent's stdio session (start_new_session already does
        # this when set on subprocess.create), but make doubly sure.
        try:
            os.setsid()
        except OSError:
            pass  # already a session leader — expected when start_new_session=True was set

    return preexec


def _now() -> float:
    import time as _t

    return _t.monotonic()
