"""Sandbox ABC + result dataclass.

``LocalSubprocessSandbox`` is **dev/CI only** (see ``adapters/inmemory/sandbox.py``).
Production deploys MUST use ``FirecrackerSandbox`` (see ``INTEGRATION.md`` P-4).
"""

from __future__ import annotations

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
    ) -> SandboxResult:
        raise NotImplementedError(f"{self.__class__.__name__}.run() must be implemented")
