"""GCP Firecracker Sandbox Adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.core.sandbox import AbstractSandbox, SandboxResult


class FirecrackerSandbox(AbstractSandbox):
    """GCP Firecracker Sandbox."""

    is_production_grade = True

    def __init__(self) -> None:
        raise NotImplementedError(
            "H1: Firecracker tier not yet provisioned — file issue per docs/architecture/h1-firecracker-provision.md"
        )

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
        """Run a command in the sandbox."""
        raise NotImplementedError("Stub")
