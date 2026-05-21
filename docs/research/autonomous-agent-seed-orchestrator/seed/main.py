"""
Wiring entry-point for the seed orchestrator.

This module composes every other module into a runnable process. It is
intentionally explicit — every dependency is constructed here, in one
place, so a deploy can be audited end-to-end by reading a single file.

Run as:
    python -m seed.main

Required env vars:
    ANTHROPIC_API_KEY          # if running without Vertex AI
    ANTHROPIC_VERTEX_PROJECT_ID # if running with Vertex AI
    CLOUD_ML_REGION            # Vertex AI region (e.g., us-east5)
    VCM_MASTER_SECRET_PATH     # path to a file containing ≥16 random bytes

The defaults wire `LocalSubprocessSandbox` — DEV ONLY. For production set
`OrchestratorConfig.production=True` AND swap in `FirecrackerSandbox`
(see INTEGRATION.md work item P-4).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional


from .agent_registry import AgentRegistry, RegistryConfig
from .api_client import AnthropicClient, AnthropicClientConfig
from .bootstrap import make_spawn_callback
from .embedder import HashingEmbedder
from .memory_store import InMemoryStore
from .moe_router import SoftmaxBilinearRouter
from .orchestrator import Orchestrator, OrchestratorConfig
from .reward_model import (
    AnthropicJudge,
    HeuristicJudge,
    IntrinsicRewardModel,
    JudgeEnsemble,
)
from .sandbox import LocalSubprocessSandbox
from .schemas import AgentCapability, TaskRequest
from .telemetry import TelemetrySink
from .virtual_context import VirtualContextManager


# ─────────────────────────────────────────────────────────────────────────
# Secrets and capability-gap helpers (kept as functions so an integrator
# can override one without monkey-patching the orchestrator).
# ─────────────────────────────────────────────────────────────────────────


def load_master_secret(env_var: str = "VCM_MASTER_SECRET_PATH") -> bytes:
    """Read the VCM master secret from a path or fall back to env var bytes.

    For dev, you can set `VCM_MASTER_SECRET_BYTES_DEV=<utf-8>` to inline
    bytes (NOT recommended for production — the path-based form is the
    only one that integrates with sops / GCP Secret Manager cleanly).
    """
    path = os.environ.get(env_var)
    if path:
        data = Path(path).read_bytes().strip()
        if len(data) < 16:
            raise ValueError(f"{env_var}={path} contains <16 bytes; refusing to construct VCM")
        return data
    dev = os.environ.get("VCM_MASTER_SECRET_BYTES_DEV")
    if dev:
        if len(dev.encode()) < 16:
            raise ValueError("VCM_MASTER_SECRET_BYTES_DEV is shorter than 16 bytes")
        return dev.encode("utf-8")
    raise RuntimeError(
        f"No master secret available: set {env_var} or " "VCM_MASTER_SECRET_BYTES_DEV (dev only)"
    )


def default_capability_gap(request: TaskRequest, registry: AgentRegistry) -> str:
    """Heuristic capability-gap descriptor passed to META_USER_TEMPLATE.

    Inspects the current fleet's phase coverage and tag bag and returns a
    free-form sentence describing what capability the task seems to need
    that isn't well-served. The router has already decided that SPAWN_EXPERT
    is the right meta-action; this string is the *reason* to give to the
    Anthropic generator.
    """
    have = registry.snapshot()
    phases_covered = {
        meta["phase"] for meta in have.values() if meta["lifecycle"] in ("active", "probation")
    }
    if request.phase not in phases_covered:
        return (
            f"No active expert covers phase={request.phase!r}. "
            f"Generate an expert specialised for this phase given task "
            f"summary: {request.summary[:200]!r}."
        )
    tags_seen: set[str] = set()
    for meta in have.values():
        tags_seen.update(meta.get("tags") or [])
    constraint_tags = {str(k) for k in request.constraints.keys() if isinstance(k, str)}
    missing = constraint_tags - tags_seen
    if missing:
        return (
            f"Active fleet covers phase={request.phase} but no expert holds "
            f"the constraint tags {sorted(missing)}. "
            f"Generate an expert specialised for these constraints."
        )
    return (
        f"Fleet has surface coverage for phase={request.phase} but the "
        f"router emitted SPAWN_EXPERT; assume the existing experts are "
        f"saturated or down-weighted. Task summary: {request.summary[:200]!r}."
    )


async def invoke_from_module(
    module,
    cap: AgentCapability,
    request: TaskRequest,
    *,
    sandbox: LocalSubprocessSandbox,
    timeout_s: float,
):
    """Translate a generated module's `invoke(request)` into an ExecutionResult.

    Generated experts have signature `async def invoke(request) -> dict | str`.
    We wrap any non-ExecutionResult return in a COMPLETED ExecutionResult
    with the agent's own est_cost/est_latency as accounting placeholders;
    the real cost (token spend, etc.) is recorded inside `api_client`.
    """
    import time as _time
    from .schemas import ExecutionResult, TaskStatus

    invoke = getattr(module, "invoke", None)
    if invoke is None:
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=cap.agent_id,
            error="module_missing_invoke",
            duration_s=0.0,
        )
    t0 = _time.monotonic()
    try:
        out = await asyncio.wait_for(invoke(request), timeout=timeout_s)
    except asyncio.TimeoutError:
        return ExecutionResult(
            task_id=request.task_id,
            status=TaskStatus.FAILED,
            agent_id=cap.agent_id,
            error="invoke_timeout",
            duration_s=_time.monotonic() - t0,
        )
    duration = _time.monotonic() - t0
    if isinstance(out, ExecutionResult):
        return out
    return ExecutionResult(
        task_id=request.task_id,
        status=TaskStatus.COMPLETED,
        agent_id=cap.agent_id,
        output=out,
        duration_s=duration,
        cost_usd=float(cap.est_cost_usd),
    )


# ─────────────────────────────────────────────────────────────────────────
# Wiring.
# ─────────────────────────────────────────────────────────────────────────


def build(
    *,
    production: bool = False,
    embedder_dim: int = 256,
) -> tuple[Orchestrator, TelemetrySink]:
    """Construct a fully-wired orchestrator and return (orchestrator, telemetry).

    Caller is responsible for `await orchestrator.start()` and `stop()`.
    """
    telemetry = TelemetrySink()

    # Core stores and helpers.
    embedder = HashingEmbedder(dim=embedder_dim)
    memory_store = InMemoryStore(dim=embedder_dim)
    vcm = VirtualContextManager(store=memory_store, master_secret=load_master_secret())

    # Sandbox: DEV/CI only by default. Refused at orchestrator-construction
    # time if `production=True` and the sandbox is not production-grade.
    sandbox = LocalSubprocessSandbox()

    # Reward model: heuristic + (optional) Anthropic judge.
    judges: list = [HeuristicJudge()]
    api_client: Optional[AnthropicClient] = None
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID"):
        api_client = AnthropicClient(
            config=AnthropicClientConfig(
                use_vertex=bool(os.environ.get("CLAUDE_CODE_USE_VERTEX")),
                vertex_project_id=os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID"),
                vertex_region=os.environ.get("CLOUD_ML_REGION", "us-east5"),
            )
        )
        judges.append(AnthropicJudge(api_client))
    ensemble = JudgeEnsemble(judges=judges)
    reward_model = IntrinsicRewardModel(judges=ensemble)

    # Registry + router.
    registry = AgentRegistry(config=RegistryConfig())
    config = OrchestratorConfig(
        production=production,
        capability_dim=embedder_dim,
        state_proj_dim=embedder_dim,
    )
    router = SoftmaxBilinearRouter(
        state_dim=config.state_dim,
        capability_dim=embedder_dim,
        state_proj_dim=embedder_dim,
    )

    # Spawn callback wires bootstrap → registry. The callback returns an
    # AgentCapability bound to a sandbox-backed `invoke` closure.
    spawn_cb = make_spawn_callback(
        client=api_client,
        sandbox=sandbox,
        capability_gap_fn=lambda req: default_capability_gap(req, registry),
        invoke_from_module=lambda mod, cap, req: invoke_from_module(
            mod,
            cap,
            req,
            sandbox=sandbox,
            timeout_s=config.default_task_timeout_s,
        ),
    )

    orchestrator = Orchestrator(
        config=config,
        router=router,
        registry=registry,
        vcm=vcm,
        memory_store=memory_store,
        embedder=embedder,
        sandbox=sandbox,
        reward_model=reward_model,
        telemetry=telemetry,
        spawn_callback=spawn_cb,
    )
    return orchestrator, telemetry


async def _main() -> int:
    if "--production" in sys.argv:
        production = True
    else:
        production = False
    orchestrator, telemetry = build(production=production)
    await orchestrator.start()
    try:
        # Daemon mode: caller drives `orchestrator.submit(request)` from
        # another process or from an HTTP layer above this module.
        await asyncio.Event().wait()
    finally:
        await orchestrator.stop()
        telemetry.dump_jsonl(Path("./telemetry-final.jsonl"), clear=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
