"""
Phase-3 bootstrapping protocol.

Exposes:
  - META_SYSTEM_PROMPT, META_USER_TEMPLATE — verbatim prompts used to drive
    the Anthropic API into producing new expert modules.
  - spawn_via_api(...)                     — the callable wired into the
    Orchestrator as `spawn_callback`. Generates → validates → smoke-tests
    → registers a new AgentCapability. Returns None on any failure.
  - make_spawn_callback(...)                — factory that closes over an
    AnthropicClient + capability registrar and returns a properly-typed
    spawn_cb suitable for OrchestratorConfig.spawn_callback.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Awaitable, Callable

from .api_client import AnthropicClient
from .sandbox import AbstractSandbox
from .schemas import (
    AgentCapability,
    AgentID,
    ExecutionResult,
    TaskRequest,
)
from .telemetry import TelemetrySink


# ─────────────────────────────────────────────────────────────────────────
# Meta-prompts.
#
# The system prompt is INTENTIONALLY STATIC: this lets prompt-caching kick
# in on the second invocation onward. The user prompt is per-request.
# ─────────────────────────────────────────────────────────────────────────

META_SYSTEM_PROMPT = """\
You are an Expert Module Generator inside a recursive, self-evolving agentic system.
Your job: produce a single self-contained Python 3.11+ module that implements
ONE new expert agent which the host system can hot-plug into its Mixture-of-Experts
fleet.

# Hard contract (any violation → module is rejected and discarded)

1. Emit EXACTLY ONE Python file. No prose, no markdown fences other than the
   final ```python ... ``` block. No explanatory text after the block.
2. The file MUST define a top-level coroutine function with this exact signature:
       async def run(request: dict, ctx: dict) -> dict
   - `request` is a JSON-serializable dict (the TaskRequest)
   - `ctx` is a JSON-serializable dict with read-only access helpers
   - Return a JSON-serializable dict with keys: status, output, error, artifacts
       status ∈ {"completed", "failed", "refused"}
       output: any JSON value (or null)
       error:  string or null
       artifacts: list of {"name": str, "uri": str} (may be empty)
3. The file MUST define the following module-level constants:
       AGENT_ID: str          # globally-unique slug, kebab-case, ≤ 64 chars
       AGENT_VERSION: str     # semver, e.g. "0.1.0"
       PHASE: str             # one of: "research", "draft", "refine", "verify", "ship"
       CAPABILITY_DESCRIPTION: str  # one paragraph, ≤ 400 chars
       CAPABILITY_TAGS: list[str]   # ≤ 12 lowercase tokens, alnum + underscore
       ESTIMATED_COST_USD: float    # per-invocation expectation, ≥ 0.0
       ESTIMATED_LATENCY_S: float   # per-invocation expectation, ≥ 0.0
4. Imports allowed: stdlib only, plus { "math", "json", "re", "time",
   "asyncio", "dataclasses", "typing", "hashlib", "statistics", "itertools",
   "collections", "functools", "datetime", "pathlib", "uuid" }. No network,
   no filesystem writes outside `ctx["workdir"]`, no subprocess, no env
   manipulation, no eval/exec, no dynamic imports.
5. Be DETERMINISTIC under fixed inputs. Do not call random without a seed
   derived from request. Do not call time.time() to influence outputs
   (logging is fine).
6. The `run` coroutine MUST complete within 60 seconds on a normal input.
7. The module MUST be self-sufficient. Do NOT reference any sibling module.

# Failure modes

If the requested capability is ill-posed, ambiguous, or would require
disallowed imports, set status="refused" inside `run` and explain in `error`.
Do NOT refuse at module-generation time — always emit a syntactically valid
module that can refuse politely at runtime.

# Style

- Type hint every function and method.
- No print(); no logging configuration. Return data instead.
- One short module docstring explaining the capability.

# Output format

Respond with EXACTLY ONE fenced code block, language `python`:

```python
# <module source here>
```
"""


META_USER_TEMPLATE = """\
<context>
<phase_in_pipeline>{phase}</phase_in_pipeline>

<existing_fleet>
The following expert agents are already active. Generate a NEW, NON-OVERLAPPING
capability that addresses the gap below.
{fleet_summary}
</existing_fleet>

<capability_gap>
{capability_gap}
</capability_gap>

<task_that_triggered_spawn>
<task_id>{task_id}</task_id>
<task_summary>{task_summary}</task_summary>
<project_id>{project_id}</project_id>
</task_that_triggered_spawn>

<budget>
<max_estimated_cost_usd>{max_cost_usd}</max_estimated_cost_usd>
<max_estimated_latency_s>{max_latency_s}</max_estimated_latency_s>
</budget>
</context>

Produce ONE new expert module per the system contract. The module's
CAPABILITY_DESCRIPTION must explicitly state how it differs from each
existing fleet member.
"""


# ─────────────────────────────────────────────────────────────────────────
# Validation & module loading.
# ─────────────────────────────────────────────────────────────────────────

_FENCED_PY_RE = re.compile(
    r"```(?:python|py)?\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)

_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")

# Imports the generated module is allowed to use. Anything else → reject.
_ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        "math",
        "json",
        "re",
        "time",
        "asyncio",
        "dataclasses",
        "typing",
        "hashlib",
        "statistics",
        "itertools",
        "collections",
        "functools",
        "datetime",
        "pathlib",
        "uuid",
        "__future__",
    }
)

# Banned identifiers anywhere in the generated source.
_BANNED_TOKENS: tuple[str, ...] = (
    "eval(",
    "exec(",
    "compile(",
    "__import__(",
    "subprocess",
    "os.system",
    "os.popen",
    "os.environ",
    "socket",
    "urllib",
    "requests",
    "httpx",
    "aiohttp",
    "open(",  # forces use of ctx["workdir"] indirection
)


class ModuleRejection(Exception):
    """Raised when a generated module fails validation or smoke-test."""


@dataclass(slots=True)
class GeneratedModule:
    agent_id: str
    agent_version: str
    phase: str
    description: str
    tags: tuple[str, ...]
    est_cost_usd: float
    est_latency_s: float
    source: str
    source_sha256: str


def _extract_source(text: str) -> str:
    m = _FENCED_PY_RE.search(text)
    if m is None:
        raise ModuleRejection("no fenced python code block found in response")
    return m.group("body").strip()


def _scan_for_banned(source: str) -> None:
    for tok in _BANNED_TOKENS:
        if tok in source:
            raise ModuleRejection(f"banned token in source: {tok!r}")


def _scan_imports(source: str) -> None:
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ModuleRejection(f"syntax error: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in _ALLOWED_IMPORTS:
                    raise ModuleRejection(f"disallowed import: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root and root not in _ALLOWED_IMPORTS:
                raise ModuleRejection(f"disallowed import: from {node.module}")


def _load_module(source: str, mod_name: str) -> ModuleType:
    # We compile + exec into a fresh module object so the generated code
    # never touches the importer cache for sibling modules.
    code = compile(source, filename=f"<expert:{mod_name}>", mode="exec")
    module = ModuleType(mod_name)
    module.__file__ = f"<expert:{mod_name}>"
    module.__dict__["__builtins__"] = __builtins__
    exec(code, module.__dict__)
    return module


def _validate_module_shape(module: ModuleType) -> GeneratedModule:
    required: dict[str, type] = {
        "AGENT_ID": str,
        "AGENT_VERSION": str,
        "PHASE": str,
        "CAPABILITY_DESCRIPTION": str,
        "CAPABILITY_TAGS": list,
        "ESTIMATED_COST_USD": float,
        "ESTIMATED_LATENCY_S": float,
    }
    for name, typ in required.items():
        if not hasattr(module, name):
            raise ModuleRejection(f"missing required constant: {name}")
        val = getattr(module, name)
        if not isinstance(val, typ):
            raise ModuleRejection(f"{name} must be {typ.__name__}, got {type(val).__name__}")

    run = getattr(module, "run", None)
    if run is None or not callable(run) or not asyncio.iscoroutinefunction(run):
        raise ModuleRejection("module must define `async def run(request, ctx)`")
    sig = inspect.signature(run)
    if len(sig.parameters) != 2:
        raise ModuleRejection("run() must accept exactly (request, ctx)")

    agent_id = module.AGENT_ID
    if not _AGENT_ID_RE.match(agent_id):
        raise ModuleRejection(f"AGENT_ID does not match required pattern: {agent_id!r}")

    phase = module.PHASE
    if phase not in {"research", "draft", "refine", "verify", "ship"}:
        raise ModuleRejection(f"PHASE must be one of the allowed values, got {phase!r}")

    tags = module.CAPABILITY_TAGS
    if len(tags) > 12 or not all(
        isinstance(t, str) and re.fullmatch(r"[a-z0-9_]{1,32}", t) for t in tags
    ):
        raise ModuleRejection("CAPABILITY_TAGS malformed (≤12 lowercase tokens)")

    if module.ESTIMATED_COST_USD < 0.0 or module.ESTIMATED_LATENCY_S < 0.0:
        raise ModuleRejection("estimates must be non-negative")

    return GeneratedModule(
        agent_id=agent_id,
        agent_version=module.AGENT_VERSION,
        phase=phase,
        description=module.CAPABILITY_DESCRIPTION,
        tags=tuple(tags),
        est_cost_usd=float(module.ESTIMATED_COST_USD),
        est_latency_s=float(module.ESTIMATED_LATENCY_S),
        source="",  # filled in by caller
        source_sha256="",
    )


async def _smoke_test(module: ModuleType, workdir: Path) -> None:
    """Invoke run() with a canary input. Reject if it raises, times out, or
    returns a malformed result."""
    canary_request = {
        "task_id": "canary-" + uuid.uuid4().hex[:8],
        "project_id": None,
        "summary": "self-test: respond with status=refused and an explanation.",
        "constraints": {},
    }
    ctx = {
        "workdir": str(workdir),
        "canary": True,
    }
    try:
        result = await asyncio.wait_for(module.run(canary_request, ctx), timeout=10.0)
    except asyncio.TimeoutError as e:
        raise ModuleRejection("smoke-test timed out (10s)") from e
    except Exception as e:
        raise ModuleRejection(f"smoke-test raised: {e!r}") from e

    if not isinstance(result, dict):
        raise ModuleRejection(f"smoke-test returned non-dict: {type(result).__name__}")
    for k in ("status", "output", "error", "artifacts"):
        if k not in result:
            raise ModuleRejection(f"smoke-test result missing key: {k}")
    if result["status"] not in {"completed", "failed", "refused"}:
        raise ModuleRejection(f"smoke-test status invalid: {result['status']!r}")
    if not isinstance(result["artifacts"], list):
        raise ModuleRejection("smoke-test artifacts must be a list")


# ─────────────────────────────────────────────────────────────────────────
# The spawn entry-point. Wired into Orchestrator as spawn_callback.
# ─────────────────────────────────────────────────────────────────────────


def make_spawn_callback(
    *,
    api: AnthropicClient,
    sandbox: AbstractSandbox,
    telemetry: TelemetrySink,
    fleet_snapshot: Callable[[], list[AgentCapability]],
    capability_gap_fn: Callable[[TaskRequest, list[AgentCapability]], str],
    module_store_dir: Path,
    invoke_capability_factory: Callable[[ModuleType], Callable[..., Awaitable[ExecutionResult]]],
    max_cost_usd: float = 0.05,
    max_latency_s: float = 30.0,
) -> Callable[[TaskRequest, str], Awaitable[AgentCapability | None]]:
    """Build a spawn-callback closure.

    Parameters
    ----------
    api
        Shared AnthropicClient.
    sandbox
        Sandbox used for the smoke test workdir (the module itself runs
        in-process; the sandbox provides an ephemeral workdir).
    telemetry
        Sink for spawn lifecycle events.
    fleet_snapshot
        Callable that returns the current active capability list (for the
        prompt's fleet_summary).
    capability_gap_fn
        Pure function: (request, fleet) → free-text description of the
        functional gap the new expert should fill. Implementation-defined.
    module_store_dir
        Persistent dir where validated modules are written (one file per
        agent_id). Created if missing.
    invoke_capability_factory
        Factory that turns a loaded module into an async invoke() callable
        compatible with the runtime expected by the orchestrator's executor.
    max_cost_usd / max_latency_s
        Per-invocation budget hints written into the meta-prompt.
    """
    module_store_dir.mkdir(parents=True, exist_ok=True)

    async def spawn(request: TaskRequest, hint: str) -> AgentCapability | None:
        spawn_id = uuid.uuid4().hex[:12]
        t0 = time.monotonic()
        telemetry.emit(
            "bootstrap.start", {"spawn_id": spawn_id, "task_id": request.task_id, "hint": hint}
        )
        fleet = fleet_snapshot()
        fleet_summary = _format_fleet(fleet)
        gap = capability_gap_fn(request, fleet)

        user_prompt = META_USER_TEMPLATE.format(
            phase=getattr(request, "phase", "draft"),
            fleet_summary=fleet_summary,
            capability_gap=gap,
            task_id=request.task_id,
            task_summary=getattr(request, "summary", "")[:1024],
            project_id=getattr(request, "project_id", "") or "(consensus)",
            max_cost_usd=f"{max_cost_usd:.4f}",
            max_latency_s=f"{max_latency_s:.2f}",
        )

        # 1. API call
        try:
            completion = await api.complete(
                system=META_SYSTEM_PROMPT,
                user=user_prompt,
                cache_system=True,
                temperature=0.4,
            )
        except Exception as e:
            telemetry.emit("bootstrap.api_error", {"spawn_id": spawn_id, "error": repr(e)})
            return None

        # 2. Extract & validate
        try:
            source = _extract_source(completion.text)
            _scan_for_banned(source)
            _scan_imports(source)
        except ModuleRejection as e:
            telemetry.emit(
                "bootstrap.rejected",
                {
                    "spawn_id": spawn_id,
                    "stage": "static",
                    "reason": str(e),
                    "cost_usd": completion.usage.cost_usd,
                },
            )
            return None

        # 3. Load
        sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        mod_name = f"expert_{sha[:12]}"
        try:
            module = _load_module(source, mod_name)
        except Exception as e:
            telemetry.emit(
                "bootstrap.rejected", {"spawn_id": spawn_id, "stage": "exec", "reason": repr(e)}
            )
            return None

        # 4. Shape check
        try:
            gen = _validate_module_shape(module)
        except ModuleRejection as e:
            telemetry.emit(
                "bootstrap.rejected", {"spawn_id": spawn_id, "stage": "shape", "reason": str(e)}
            )
            return None

        # Budget gates
        if gen.est_cost_usd > max_cost_usd * 4:
            telemetry.emit(
                "bootstrap.rejected",
                {
                    "spawn_id": spawn_id,
                    "stage": "budget_cost",
                    "estimate": gen.est_cost_usd,
                    "cap": max_cost_usd * 4,
                },
            )
            return None
        if gen.est_latency_s > max_latency_s * 4:
            telemetry.emit(
                "bootstrap.rejected",
                {
                    "spawn_id": spawn_id,
                    "stage": "budget_latency",
                    "estimate": gen.est_latency_s,
                    "cap": max_latency_s * 4,
                },
            )
            return None

        # 5. Smoke test in isolated workdir
        with tempfile.TemporaryDirectory(prefix=f"smoke-{gen.agent_id}-") as td:
            workdir = Path(td)
            try:
                await _smoke_test(module, workdir)
            except ModuleRejection as e:
                telemetry.emit(
                    "bootstrap.rejected", {"spawn_id": spawn_id, "stage": "smoke", "reason": str(e)}
                )
                return None

        # 6. Persist source for replayability / audit
        out_path = module_store_dir / f"{gen.agent_id}.py"
        out_path.write_text(source, encoding="utf-8")
        gen = GeneratedModule(
            agent_id=gen.agent_id,
            agent_version=gen.agent_version,
            phase=gen.phase,
            description=gen.description,
            tags=gen.tags,
            est_cost_usd=gen.est_cost_usd,
            est_latency_s=gen.est_latency_s,
            source=source,
            source_sha256=sha,
        )

        # 7. Build AgentCapability and return for registry insertion
        invoke = invoke_capability_factory(module)
        cap = AgentCapability(
            agent_id=AgentID(gen.agent_id),
            version=gen.agent_version,
            phase=gen.phase,
            description=gen.description,
            tags=gen.tags,
            est_cost_usd=gen.est_cost_usd,
            est_latency_s=gen.est_latency_s,
            lifecycle="probation",  # FSM: PROBATION until fitness EMA crosses high-watermark
            invoke=invoke,
            source_sha256=sha,
            spawned_at=time.time(),
        )
        latency_s = time.monotonic() - t0
        telemetry.emit(
            "bootstrap.ok",
            {
                "spawn_id": spawn_id,
                "agent_id": cap.agent_id,
                "cost_usd": completion.usage.cost_usd,
                "tokens_in": completion.usage.tokens_in,
                "tokens_out": completion.usage.tokens_out,
                "latency_s": latency_s,
            },
        )
        return cap

    return spawn


def _format_fleet(fleet: list[AgentCapability]) -> str:
    if not fleet:
        return "  (no active experts; you are spawning the first one)"
    lines: list[str] = []
    for c in fleet:
        tags = ",".join(c.tags) if c.tags else "-"
        lines.append(
            f"  - id={c.agent_id} phase={c.phase} tags=[{tags}] "
            f"est_cost=${c.est_cost_usd:.4f} est_lat={c.est_latency_s:.1f}s "
            f"lifecycle={c.lifecycle}"
        )
    return "\n".join(lines)
