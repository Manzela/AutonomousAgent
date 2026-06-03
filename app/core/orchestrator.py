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
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
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

# SP-05: env keys whose NAME is secret-shaped are NEVER passed into a sandbox child
# (default-deny — the sandbox holds no standing secrets; the lethal-trifecta posture).
_SECRET_KEY_RE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CRED|PRIVATE", re.IGNORECASE)

# SP-05: the only harness env vars a sandbox child inherits (everything else is dropped).
# Absolute `sys.executable` cmds don't strictly need PATH, but keep a minimal POSIX set so
# trivial tools resolve; NONE of these is secret-shaped.
_SAFE_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ", "PWD")


def _scrubbed_sandbox_env(extra: Optional[dict[str, Any]] = None) -> dict[str, str]:
    """Build the sandbox child's env: a minimal allowlist from the harness env plus any
    non-secret ``extra`` overrides. Default-deny — a harness ``*KEY/*TOKEN/*SECRET`` can
    NOT reach the child (SP-05 acceptance(3); the lethal-trifecta exfil guard).

    ``LocalSubprocessSandbox`` passes ``env=`` straight to the child; ``env=None`` would
    leak the full ``os.environ``, so callers MUST pass this scrubbed dict.
    """
    env: dict[str, str] = {k: os.environ[k] for k in _SAFE_ENV_ALLOWLIST if k in os.environ}
    if extra:
        for k, v in extra.items():
            if _SECRET_KEY_RE.search(k):
                continue  # never honor a secret-shaped override
            env[k] = str(v)
    # Defensive: drop anything secret-shaped that slipped through the allowlist/extras.
    return {k: v for k, v in env.items() if not _SECRET_KEY_RE.search(k)}


# SP-05 (F-1) per-phase egress DECISION (default-deny). The PRD posture is TEST=no egress,
# BUILD=SP-00g allowlist — but a LIVE allow-listed egress channel needs a netns/gVisor
# backend (LocalSubprocessSandbox HARD-REFUSES network_allowed=True pre-spawn) and is
# integration-tier/deferred. So the live allowlist here is EMPTY: every phase → no egress.
# Keying on the schema phase Literals (research/draft/refine/verify/ship — NOT an invented
# 'build'/'test') turns the old hardcoded `network_allowed=False` into an auditable,
# default-deny DATA decision; any future widening of this set trips a red test in review.
_EGRESS_ALLOWED_PHASES: frozenset[str] = frozenset()


def _egress_for_phase(phase: str) -> bool:
    """Whether a node in ``phase`` may have sandbox egress. Default-deny: unknown/any
    phase → False until an allow-listed BUILD-egress backend (SP-00g + netns/gVisor) lands."""
    return phase in _EGRESS_ALLOWED_PHASES


def _safe_sandbox_workdir(raw: Any) -> Optional[Path]:
    """Validate a caller-supplied sandbox workdir (the per-node git worktree, SP-05 F-1).

    HARD GUARD: only forward a workdir that resolves UNDER the system tempdir (where
    WorkspaceSession materialises the ephemeral worktree). Anything else — most importantly
    the live repo root — is REFUSED (returns None → run() uses its own tempdir), so a
    malformed constraint can never make the sandbox child run with the harness checkout as
    its writable cwd."""
    if not raw:
        return None
    try:
        wd = Path(str(raw)).resolve()
        tmp_root = Path(tempfile.gettempdir()).resolve()
    except (OSError, ValueError):
        return None
    if wd == tmp_root or tmp_root in wd.parents:
        return wd if wd.is_dir() else None
    return None


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
    sandbox: Optional[AbstractSandbox] = None,
    peer_timeout_s: float = DEFAULT_PEER_TIMEOUT_S,
    local_timeout_s: float = DEFAULT_LOCAL_TIMEOUT_S,
) -> ExecutionResult:
    """Dispatch a task to a capability — peer (A2A), sandboxed-local, or in-process.

    Routing precedence:
      1. ``capability.peer_endpoint`` set → A2A ``send_message`` (unchanged).
      2. ``sandbox`` injected → **SP-05** sandboxed local exec: the node's
         ``constraints["sandbox_cmd"]`` runs in a real ``AbstractSandbox.run()`` child
         (different PID, scrubbed env, no network), NEVER in the harness process.
      3. otherwise → legacy in-process ``capability.invoke()`` (preserved for the
         SP-01/SP-04 spine skeleton when no sandbox is injected).

    Args:
        request: The task to execute.
        capability: The chosen expert's descriptor.
        agent_identity: SA identity for outbound JWT minting (Day 5+).
            Pass ``None`` for the pre-production posture per INTEGRATION.md §P-3
            (fail-open auth).
        sandbox: SP-05 per-node sandbox. When provided (and no peer endpoint), the
            local capability is executed inside it instead of in-process.
        peer_timeout_s: httpx timeout for A2A peer requests.
        local_timeout_s: asyncio timeout for local invoke()/sandbox run.

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
    if sandbox is not None:
        return await _execute_sandboxed(
            request,
            capability,
            sandbox=sandbox,
            timeout_s=local_timeout_s,
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

    if capability.peer_endpoint is None:  # Narrowing for type checker (assert stripped by -O)
        raise RuntimeError("_execute_via_a2a called with no peer_endpoint")

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
                exc.code,  # type: ignore[attr-defined]
                exc,
            )
            error_str = f"a2a_peer_error: code={exc.code} msg={exc}"  # type: ignore[attr-defined]
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
        effective_timeout = (
            min(timeout_s, request.deadline_s) if request.deadline_s > 0.0 else timeout_s
        )
        result = await asyncio.wait_for(
            capability.invoke(request),
            timeout=effective_timeout,
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


async def _execute_sandboxed(
    request: TaskRequest,
    capability: AgentCapability,
    *,
    sandbox: AbstractSandbox,
    timeout_s: float = DEFAULT_LOCAL_TIMEOUT_S,
) -> ExecutionResult:
    """SP-05: run the node's command in a REAL sandbox child — not in-process.

    The command is ``request.constraints["sandbox_cmd"]`` (a ``list[str]``); when the
    execute node provides ``request.constraints["workdir"]`` (the per-node git worktree,
    F-1) it is forwarded as the child's cwd AFTER a hard guard (must resolve under the
    system tempdir — never the live repo). The child runs with a default-deny scrubbed env
    (no harness secrets); egress is a per-phase DECISION (``_egress_for_phase``, default-deny
    — empty live allowlist, so every phase is denied this slice). The real subprocess exit
    code drives the status (anti-drift), never agent-emitted output text.

    Deferred to later SP-05 slices: deriving the command from the external Hermes binary
    (``lib/hermes_bridge.py`` is a host-exec that raises + inherits unscrubbed env — NOT
    wired), per-node cross-isolation for fan-out (SP-05b), per-role tool allowlist, LIVE
    BUILD egress allowlist enforcement (SP-00g + netns/gVisor, integration-tier), kernel
    FS-confinement/EROFS (SP-05c). This node's writable-worktree diff is scope-scored by
    eval_gate (SP-06), NOT kernel-confined.
    """
    t0 = time.monotonic()
    cmd = request.constraints.get("sandbox_cmd")
    if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error="sandbox_exec_missing_or_invalid_cmd",
            duration_s=time.monotonic() - t0,
        )

    env = _scrubbed_sandbox_env(request.constraints.get("sandbox_env"))
    workdir = _safe_sandbox_workdir(request.constraints.get("workdir"))
    effective_timeout = (
        min(timeout_s, request.deadline_s) if request.deadline_s > 0.0 else timeout_s
    )

    try:
        result = await sandbox.run(
            cmd=cmd,
            workdir=workdir,
            env=env,
            timeout_s=effective_timeout,
            # SP-05 F-1: per-phase egress (default-deny; empty live allowlist → False here).
            network_allowed=_egress_for_phase(request.phase),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface any sandbox failure as a task failure
        # logger.exception (not .error) preserves the traceback so an internal sandbox
        # bug (e.g. a broken AbstractSandbox impl) is debuggable; exc_info is scrubbed
        # on the log path (lib/scrubber.py). C9 (gemini-2-5-pro) review.
        logger.exception(
            "sandbox exec error: task_id=%s sandbox=%s",
            request.task_id,
            sandbox.__class__.__name__,
        )
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=capability.agent_id,
            output=None,
            error=f"sandbox_exec_error: {exc!r}",
            duration_s=time.monotonic() - t0,
        )

    ok = result.returncode == 0
    return ExecutionResult(
        task_id=request.task_id,
        status=TaskStatus.COMPLETED if ok else TaskStatus.FAILED,
        agent_id=capability.agent_id,
        output={
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "killed": result.killed,
        },
        error=None if ok else f"sandbox_exit_{result.returncode}",
        duration_s=result.duration_s,
    )


def _map_a2a_status(status: str) -> TaskStatus:
    """Map an A2A Task status string to the orchestrator's TaskStatus enum.

    A2A uses: SUBMITTED, WORKING, INPUT_REQUIRED, COMPLETED, CANCELED, FAILED.
    The orchestrator uses: PENDING, INFLIGHT, INPUT_REQUIRED, COMPLETED, FAILED,
    REFUSED, CANCELED.

    SP-04: INPUT_REQUIRED maps to the NON-TERMINAL TaskStatus.INPUT_REQUIRED — it
    is NOT a hard FAILED. The peer is blocked waiting for a human decision, which
    is exactly what the spine's sign_off interrupt() gate exists to provide: the
    parked task is routable to a human (HITL) and resumable on /approve, never
    discarded as a failure. (Previously this collapsed to FAILED, killing the run;
    that was the SP-04 bug.)
    """
    if status == "INPUT_REQUIRED":
        logger.info(
            "a2a: peer returned INPUT_REQUIRED — task is parked waiting for a human "
            "decision; routable to the sign_off interrupt() gate (non-terminal, "
            "resumable on /approve), NOT failed"
        )
    mapping = {
        "SUBMITTED": TaskStatus.INFLIGHT,
        "WORKING": TaskStatus.INFLIGHT,
        "INPUT_REQUIRED": TaskStatus.INPUT_REQUIRED,
        "COMPLETED": TaskStatus.COMPLETED,
        "CANCELED": TaskStatus.CANCELED,
        "FAILED": TaskStatus.FAILED,
    }
    return mapping.get(status, TaskStatus.FAILED)
