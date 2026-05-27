"""Bridge for Hermes CLI invocations.

This module provides the integration point for orchestrating Hermes tasks
with appropriate model routing per W0.7.
"""

from typing import List
import subprocess
import logging

from lib.router.intent_router import resolve_model

logger = logging.getLogger(__name__)


def invoke_hermes_cli(task_intent: str, args: List[str]) -> subprocess.CompletedProcess:
    """Invoke the Hermes CLI with the model selected for the given task_intent.

    Args:
        task_intent: The requested capability tier (e.g., 'orchestrator', 'architect')
        args: Additional CLI arguments to pass to Hermes

    Returns:
        subprocess.CompletedProcess from the CLI invocation
    """
    spec = resolve_model(task_intent)

    # Construct the base hermes command, overriding the model per the router spec
    cmd = ["hermes", "--model", spec.model]

    if spec.api_base:
        cmd.extend(["--api-base", spec.api_base])

    cmd.extend(args)

    logger.info(f"Invoking Hermes CLI with intent '{task_intent}' -> model '{spec.model}'")
    return subprocess.run(cmd, capture_output=True, text=True, check=True)
