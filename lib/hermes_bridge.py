"""Bridge for Hermes CLI invocations.

This module provides the integration point for orchestrating Hermes tasks
with appropriate model routing per W0.7.
"""

from __future__ import annotations

import subprocess
import logging
from typing import List

from lib.router.intent_router import resolve_model

logger = logging.getLogger(__name__)

# Maximum wall-clock seconds for a single Hermes CLI invocation.
# Prevents the bridge from hanging indefinitely on a stalled subprocess.
_SUBPROCESS_TIMEOUT_S = 300


def invoke_hermes_cli(task_intent: str, args: List[str]) -> subprocess.CompletedProcess:
    """Invoke the Hermes CLI with the model selected for the given task_intent.

    Args:
        task_intent: The requested capability tier (e.g., 'orchestrator', 'architect')
        args: Additional CLI arguments to pass to Hermes. Each element must be
            a plain string; no shell metacharacters are interpreted (shell=False).

    Returns:
        subprocess.CompletedProcess from the CLI invocation

    Raises:
        ValueError: if any element of ``args`` is not a str (prevents accidental
            injection via non-string list entries).
        subprocess.CalledProcessError: if Hermes exits non-zero.
        subprocess.TimeoutExpired: if the invocation exceeds ``_SUBPROCESS_TIMEOUT_S``.
    """
    if not isinstance(task_intent, str) or not task_intent:
        raise ValueError("task_intent must be a non-empty string")

    # Validate that every extra arg is a plain string — reject non-str entries
    # that could indicate confused-deputy or injection attempts.
    for i, arg in enumerate(args):
        if not isinstance(arg, str):
            raise ValueError(f"args[{i}] must be a str, got {type(arg).__name__!r}")

    spec = resolve_model(task_intent)

    # Construct the base hermes command, overriding the model per the router spec.
    # shell=False (default) — args are never interpreted by a shell.
    cmd = ["hermes", "--model", spec.model]

    if spec.api_base:
        cmd.extend(["--api-base", spec.api_base])

    cmd.extend(args)

    logger.info(
        "Invoking Hermes CLI with intent %r -> model %r",
        task_intent,
        spec.model,
    )
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        timeout=_SUBPROCESS_TIMEOUT_S,
    )
