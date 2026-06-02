"""GCP Firecracker Sandbox Adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.core.sandbox import AbstractSandbox, SandboxResult


class FirecrackerSandbox(AbstractSandbox):
    """GCP Firecracker Sandbox.

    NOT YET IMPLEMENTED — SP-05 scaffolding.  Both __init__ and run() raise
    NotImplementedError until the Firecracker tier is provisioned (see
    docs/architecture/h1-firecracker-provision.md).

    is_production_grade=False until run() is real (H-06).  Setting it True
    on a stub would allow OrchestratorConfig.validate() to falsely accept
    this class in a production environment, then crash on the first .run().
    """

    is_production_grade = False

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
