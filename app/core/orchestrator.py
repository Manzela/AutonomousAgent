"""A2A peer-execution dispatch layer (P-3).

**Scope:** This module implements the A2A routing layer for P-3 only —
the ``execute()`` entry point that branches between local and peer dispatch.
It does NOT contain the full seed ``Orchestrator`` class (router, registry,
policy update loops, etc.). Those components are ported in subsequent
work items (P-1, P-12, P-13 per INTEGRATION.md).

Per 04-gcp-native-adapter-plan.md: the dispatch logic lives here as a
module-level function (not a class method) because the full orchestrator
class has not yet been ported to app/core/. When P-12/P-13 are implemented,
``execute()`` should be moved into ``Orchestrator._execute()`` and this module
becomes the implementation of that method.

See INTEGRATION.md §P-3 for acceptance criteria.

Collision boundary: this module calls ``lib.a2a.client.send_message`` but
does NOT modify ``lib/a2a/`` — that is Claude Code territory.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from app.core.sandbox import AbstractSandbox
from app.core.embedder import AbstractEmbedder

from app.core.schemas import (
    AgentCapability,
    ExecutionResult,
    TaskRequest,
    TaskStatus,
)

logger = logging.getLogger(__name__)

# Default timeout for A2A peer requests (seconds).
DEFAULT_PEER_TIMEOUT_S = 30.0

# Default task timeout for local dispatch (seconds).
DEFAULT_LOCAL_TIMEOUT_S = 60.0


@dataclass
class OrchestratorConfig:
    """Configuration for the orchestrator."""

    sandbox: Optional[AbstractSandbox] = None
    embedder: Optional[AbstractEmbedder] = None
    environment: str = "development"

    def __post_init__(self) -> None:
        # Auto-enforce the production sandbox gate on construction (P3-4).
        # validate() is also callable explicitly for pre-flight checks.
        if self.environment == "production":
            self.validate()

    def validate(self) -> None:
        """Validate configuration for the given environment.

        Called automatically at construction when environment='production'.
        Safe to call explicitly at any time for pre-flight checks.
        """
        sandbox_cls = self.sandbox.__class__.__name__ if self.sandbox else "None"
        embedder_cls = self.embedder.__class__.__name__ if self.embedder else "None"

        logger.info(
            "Orchestrator starting. Environment: %s | Sandbox: %s | Embedder: %s",
            self.environment,
            sandbox_cls,
            embedder_cls,
        )

        if (
            self.environment == "production"
            and self.sandbox
            and not getattr(self.sandbox, "is_production_grade", False)
        ):
            raise RuntimeError(
                f"Cannot use non-production sandbox {sandbox_cls} in production environment. "
                f"sandbox.is_production_grade=False."
            )


async def execute(
    request: TaskRequest,
    capability: AgentCapability,
    *,
    agent_identity: Any = None,
    peer_timeout_s: float = DEFAULT_PEER_TIMEOUT_S,
    local_timeout_s: float = DEFAULT_LOCAL_TIMEOUT_S,
) -> ExecutionResult:
    """Dispatch a task to a capability — local or across the A2A boundary.

    If ``capability.peer_endpoint`` is set, routes via A2A ``send_message``.
    Otherwise executes the agent module in-process via ``cap.invoke()``.

    Args:
        request: The task to execute.
        capability: The chosen expert's descriptor.
        agent_identity: SA identity for outbound JWT minting (Day 5+).
            Pass ``None`` for the pre-production posture per INTEGRATION.md §P-3
            (fail-open auth).
        peer_timeout_s: httpx timeout for A2A peer requests.
        local_timeout_s: asyncio timeout for local invoke().

    Returns:
        ``ExecutionResult`` with proper status, timing, and cost accounting.
    """
    if capability.peer_endpoint is not None:
        return await _execute_via_a2a(
            request,
            capability,
            agent_identity=agent_identity,
            timeout_s=peer_timeout_s,
        )
    return await _execute_local(
        request,
        capability,
        timeout_s=local_timeout_s,
    )


async def _execute_via_a2a(
    request: TaskRequest,
    capability: AgentCapability,
    *,
    agent_identity: Any = None,
    timeout_s: float = DEFAULT_PEER_TIMEOUT_S,
) -> ExecutionResult:
    """Route task to peer agent via A2A JSON-RPC ``send_message``.

    Constructs an A2A Message from the orchestrator's ``TaskRequest``,
    sends to ``capability.peer_endpoint``, and wraps the peer's Task
    response into an ``ExecutionResult``.

    The peer's SA email is looked up from ``config/a2a/peers.yaml`` by URL;
    auth is fail-open if the peer is not configured — pre-production posture
    per INTEGRATION.md §P-3.
    """
    # Lazy import to avoid circular dependencies and to respect the collision
    # boundary: lib.a2a.client is Claude Code territory — we call it, never modify.
    from lib.a2a.client import send_message  # type: ignore[import-untyped]

    assert capability.peer_endpoint is not None  # Narrowing for type checker

    # Build the A2A Message from the orchestrator request.
    # Spec §7.6.1 requires at least `parts` with one text entry.
    intent_text = request.summary or f"task:{request.task_id}"
    message: dict[str, Any] = {
        "role": "USER",
        "parts": [{"text": intent_text}],
        "metadata": {
            "orchestrator_task_id": request.task_id,
            "phase": request.phase,
            "project_id": str(request.project_id) if request.project_id else None,
        },
    }

    t0 = time.monotonic()
    try:
        task = await send_message(
            capability.peer_endpoint,
            message,
            agent_identity=agent_identity,
            timeout=timeout_s,
        )
        duration = time.monotonic() - t0

        logger.info(
            "a2a peer dispatch: task_id=%s peer=%s status=%s duration=%.3fs",
            request.task_id,
            capability.peer_endpoint,
            task.get("status", "UNKNOWN"),
            duration,
        )

        return ExecutionResult(
            task_id=request.task_id,
            status=_map_a2a_status(task.get("status", "UNKNOWN")),
            agent_id=capability.agent_id,
            output=task,
            error=None,
            duration_s=duration,
            cost_usd=0.0,  # Peer costs tracked on the peer side
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )

    except asyncio.TimeoutError:
        duration = time.monotonic() - t0
        logger.warning(
            "a2a peer dispatch timeout: task_id=%s peer=%s timeout=%.1fs",
            request.task_id,
            capability.peer_endpoint,
            timeout_s,
        )
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error=f"a2a_peer_timeout: {capability.peer_endpoint} after {timeout_s}s",
            duration_s=duration,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )

    except asyncio.CancelledError:
        raise  # Never swallow cancellation

    except Exception as exc:
        duration = time.monotonic() - t0
        # Detect A2AError subclasses to surface the error code for operators.
        try:
            from lib.a2a.client import A2AError  # type: ignore[import-untyped]

            is_a2a = isinstance(exc, A2AError)
        except ImportError:
            is_a2a = False

        if is_a2a:
            logger.error(
                "a2a peer dispatch protocol error: task_id=%s peer=%s code=%d msg=%s",
                request.task_id,
                capability.peer_endpoint,
                exc.code,  # type: ignore[union-attr]
                exc,
            )
            error_str = f"a2a_peer_error: code={exc.code} msg={exc}"  # type: ignore[union-attr]
        else:
            logger.error(
                "a2a peer dispatch error: task_id=%s peer=%s error=%r",
                request.task_id,
                capability.peer_endpoint,
                exc,
            )
            error_str = f"a2a_peer_error: {exc!r}"

        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error=error_str,
            duration_s=duration,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )


async def _execute_local(
    request: TaskRequest,
    capability: AgentCapability,
    *,
    timeout_s: float = DEFAULT_LOCAL_TIMEOUT_S,
) -> ExecutionResult:
    """Execute task via the capability's in-process ``invoke`` coroutine.

    This is the unchanged local dispatch path from the seed orchestrator.
    If ``invoke`` is None and ``peer_endpoint`` is also None, the capability
    is unusable — we return FAILED.
    """
    t0 = time.monotonic()

    if capability.invoke is None:
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error="capability_has_no_invoke_and_no_peer",
            duration_s=0.0,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )

    try:
        result = await asyncio.wait_for(
            capability.invoke(request),
            timeout=min(timeout_s, request.deadline_s or 60.0),
        )
        if not isinstance(result, ExecutionResult):
            return ExecutionResult(
                task_id=request.task_id,
                status=TaskStatus.FAILED,
                agent_id=capability.agent_id,
                output=None,
                error="invoke_returned_non_execution_result",
                duration_s=time.monotonic() - t0,
                cost_usd=0.0,
                tokens_in=0,
                tokens_out=0,
                artifacts=(),
            )
        return result

    except asyncio.TimeoutError:
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error="task_timeout",
            duration_s=time.monotonic() - t0,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )

    except asyncio.CancelledError:
        raise

    except Exception as exc:
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error=f"invoke_exception: {exc!r}",
            duration_s=time.monotonic() - t0,
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            artifacts=(),
        )


def _map_a2a_status(status: str) -> TaskStatus:
    """Map an A2A Task status string to the orchestrator's TaskStatus enum.

    A2A uses: SUBMITTED, WORKING, INPUT_REQUIRED, COMPLETED, CANCELED, FAILED.
    The orchestrator uses: PENDING, INFLIGHT, COMPLETED, FAILED, REFUSED.

    INPUT_REQUIRED maps to FAILED because the peer is blocked waiting for
    human input that the orchestrator cannot provide.  Operators are warned
    via a log message so the blockage is surfaced rather than silently
    treated as in-progress work.
    """
    if status == "INPUT_REQUIRED":
        logger.warning(
            "a2a: peer returned INPUT_REQUIRED — task is blocked waiting for human input; "
            "orchestrator has no mechanism to unblock this task (treating as FAILED)"
        )
    mapping = {
        "SUBMITTED": TaskStatus.INFLIGHT,
        "WORKING": TaskStatus.INFLIGHT,
        "INPUT_REQUIRED": TaskStatus.FAILED,
        "COMPLETED": TaskStatus.COMPLETED,
        "CANCELED": TaskStatus.CANCELED,
        "FAILED": TaskStatus.FAILED,
    }
    return mapping.get(status, TaskStatus.FAILED)
