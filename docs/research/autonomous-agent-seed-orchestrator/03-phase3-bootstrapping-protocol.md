# PHASE 3 — Bootstrapping Protocol

Verbatim emission from the research session. This document describes how the
seed orchestrator self-evolves: how it prompts a model to write a new expert,
how that generated module is validated, sandboxed, and hot-plugged into the
running router, and how the loop converges (or is halted).

## 3.1  Meta-prompts

**Where they live.** `bootstrap.py` exposes two module-level constants used verbatim by the orchestrator:

| Constant | Purpose | Caching |
|---|---|---|
| `META_SYSTEM_PROMPT` | Static contract definition (hard rules, allowed imports, output format) | Wrapped in `cache_control: {"type": "ephemeral"}` on every call — pays cache-read price after first spawn |
| `META_USER_TEMPLATE` | Per-spawn substrate: `phase`, `fleet_summary`, `capability_gap`, `task_*`, `project_id`, `max_*` | Per-call (never cached) |

**Design choices baked into the prompts.**

1. The contract is **strict and machine-checkable** — every required constant has a regex, every banned token is keyword-scanned in `_scan_for_banned`, imports are AST-walked in `_scan_imports`. A model that hallucinates outside the contract gets rejected at no runtime cost.
2. The contract **forbids cross-module imports** to the rest of the system. Generated experts run with no access to memory stores, the router, the registry, or the spawn API. This is the *anti-self-replication* guarantee — generated experts cannot bootstrap new generated experts.
3. The contract **demands a polite refusal path** (`status="refused"`). If the model can't satisfy the spec, it refuses *at runtime* inside an otherwise valid module. This avoids the failure mode where the model emits prose + apology instead of code.
4. The `fleet_summary` enumeration forces the model to differentiate (per the closing line: *"explicitly state how it differs from each existing fleet member"*). This is the empirical defense against fleet collapse onto a single dominant capability vector.

## 3.2  API execution loop

The execution loop is implemented end-to-end in `bootstrap.py::make_spawn_callback`. Lifecycle of a single spawn:

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Orchestrator decides to spawn (route_action == SPAWN_EXPERT)            │
│      │                                                                   │
│      ▼                                                                   │
│  spawn_lock acquired → rate-limit check → spawn_lock released            │
│      │                                                                   │
│      ▼                                                                   │
│  fleet_snapshot() + capability_gap_fn(request, fleet)                    │
│      │                                                                   │
│      ▼                                                                   │
│  api.complete(system=META_SYSTEM_PROMPT, user=META_USER_TEMPLATE.fmt)    │
│      │   ↳ cached system block → cheap cache-read after first call       │
│      │   ↳ exponential backoff w/ jitter on 429 / 5xx / timeout          │
│      │   ↳ deadline = OrchestratorConfig.spawn_timeout_s (default 180s)  │
│      ▼                                                                   │
│  _extract_source     (rejects: no ```python``` block)                    │
│  _scan_for_banned    (rejects: eval/exec/subprocess/socket/etc.)         │
│  _scan_imports       (rejects: any import outside allowlist)             │
│  _load_module        (rejects: SyntaxError, NameError on exec)           │
│  _validate_module_shape  (rejects: missing constants, wrong run sig,     │
│                            bad AGENT_ID, unknown phase, bad tags)        │
│  budget gates        (rejects: est_cost > 4×cap, est_latency > 4×cap)    │
│  _smoke_test(workdir=ephemeral)  (rejects: timeout/raises/bad shape)     │
│      │                                                                   │
│      ▼                                                                   │
│  persist source to module_store_dir/{agent_id}.py                        │
│  build AgentCapability(lifecycle="probation", invoke=factory(module))    │
│      │                                                                   │
│      ▼                                                                   │
│  return cap  →  orchestrator calls registry.register(cap)                │
│                  registry fires on_change listeners                      │
│                  router.add_expert(cap)  ← hot-plug, no restart          │
└──────────────────────────────────────────────────────────────────────────┘
```

Every transition emits a structured telemetry event keyed on `spawn_id`, so post-hoc forensics on a rejected generation chain are a single JSONL pass.

## 3.3  Hot-plug mechanism

Hot-plug is the chain `registry.register(cap)` → registry listener fan-out → `router.add_expert(cap)`. The router's contract for adding an expert without restart:

1. **Capability vector embedding.** The new expert's `description + " ".join(tags)` is embedded with the same embedder used at training time, producing `e_new ∈ ℝ^256`.
2. **Warm-start weights.** The bilinear scorer `W_r ∈ ℝ^(256×256)` is *unchanged* — bilinear scoring is invariant to expert reordering and zero-shot accommodates new experts. There is **no matrix reshape**, no weight migration.
3. **Capability matrix update.** `E ← E ⊕ e_new` (column append). The `expert_matrix()` builder in `StateVector.encoded()` will pick this up on the next routing decision.
4. **Lifecycle pinning.** The expert lands in `PROBATION`. The router applies a **probation multiplier** `m_probation ∈ [0.2, 0.5]` to its logit during sampling — it is selectable but at reduced probability. This guards against a freshly-spawned expert dominating routing before fitness is established.
5. **Promotion gate.** The agent registry's EMA fitness tracker promotes `PROBATION → ACTIVE` once `EMA_fitness(agent) ≥ high_watermark AND age ≥ promotion_min_age_s`. Demotion `ACTIVE → COOL` happens when fitness falls below `low_watermark` for `cool_grace_s`. Final eviction `COOL → EVICTED` is triggered by `_eviction_loop`.
6. **Atomicity.** Registry mutations hold a single `asyncio.Lock`; listener fan-out happens *after* the lock is released, preventing reentrancy deadlocks if a listener decides to enqueue another mutation. The router treats `add_expert` and `remove_expert` as idempotent.

```
                                FSM (Free Agent Mechanism)
        ┌──────────┐
        │  SPAWN   │  fresh module loaded, capability built
        └─────┬────┘
              │ register()
              ▼
        ┌──────────┐  EMA_fitness ≥ high_watermark
        │PROBATION │ ─────────────────────────────────┐
        └─────┬────┘                                  ▼
              │ fitness < low_watermark for         ┌────────┐
              │ cool_grace_s                        │ ACTIVE │
              ▼                                     └────┬───┘
        ┌──────────┐  fitness ≥ high_watermark          │
        │   COOL   │ ◄──────────────────────────────────┘
        └─────┬────┘  fitness < low_watermark
              │ age in COOL ≥ eviction_grace_s   for cool_grace_s
              ▼
        ┌──────────┐
        │ EVICTED  │  removed from router fleet; module file retained for audit
        └──────────┘

        ┌──────────┐  manual: meta-evaluator confirms novel high-fitness behavior
        │ PROMOTED │  → memory.tier == CONSENSUS (capability becomes permanent)
        └──────────┘
```

## 3.4  Convergence & termination

The bootstrapping loop is **monotonically self-improving until budgeted halt**. Four termination criteria are enforced in `orchestrator.py` (any one triggers a graceful shutdown via `stop()`):

| Criterion | Variable | Default | Rationale |
|---|---|---|---|
| Episode cap | `max_episodes` | unlimited | Allow indefinite run by default; operator sets a finite cap when desired |
| Wall-clock cap | `max_runtime_s` | unlimited | Same |
| Cost cap (total) | `cb_cost_budget_usd` × Σ windows | rolling | Already enforced per-window by circuit breaker; aggregated cumulatively in `stats()` |
| Fitness plateau | best-of-fleet Polyak-averaged fitness change `< ε_conv` over `plateau_window` consecutive update cycles | `ε_conv = 1e-3`, `plateau_window = 50` | Detects "the fleet has stopped improving" → no further evolution justified |

The plateau detector lives in `_policy_update_loop` (extension): after each successful `ppo_update`, it appends `best_fitness_now` to a rolling deque; when `max(window) - min(window) < ε_conv` for the full `plateau_window` length, it emits `convergence.plateau` and sets an internal `_should_stop` flag that `submit()` checks before accepting new work.

**Operator override.** The convergence check is *advisory* — `stop()` is required for actual shutdown. This lets a human keep the loop running past auto-detected plateau if they want to push for novel capability discovery via curriculum changes.

---

## Wiring summary (single canonical bootstrap)

```python
# main.py — the only script the operator runs
from pathlib import Path
import asyncio

from .api_client     import AnthropicClient, AnthropicClientConfig
from .bootstrap      import make_spawn_callback
from .embedder       import HashingEmbedder
from .memory_store   import InMemoryStore
from .virtual_context import VirtualContextManager
from .moe_router    import SoftmaxBilinearRouter
from .reward_model  import JudgeEnsemble, AnthropicJudge
from .agent_registry import AgentRegistry
from .sandbox       import LocalSubprocessSandbox
from .telemetry     import TelemetrySink
from .orchestrator  import Orchestrator, OrchestratorConfig


async def main() -> None:
    telemetry = TelemetrySink()
    embedder  = HashingEmbedder(dim=256)
    store     = InMemoryStore(dim=256)
    vcm       = VirtualContextManager(store=store, master_secret=load_master_secret())
    registry  = AgentRegistry()
    router    = SoftmaxBilinearRouter(state_dim=256, capability_dim=256)
    registry.add_listener(router.on_registry_change)  # the hot-plug wire
    reward    = JudgeEnsemble(judges=[AnthropicJudge(api=api) for _ in range(3)])
    sandbox   = LocalSubprocessSandbox()
    api       = AnthropicClient(AnthropicClientConfig(model="claude-opus-4-7"))

    spawn_cb  = make_spawn_callback(
        api=api,
        sandbox=sandbox,
        telemetry=telemetry,
        fleet_snapshot=registry.snapshot,
        capability_gap_fn=default_capability_gap,
        module_store_dir=Path("./experts"),
        invoke_capability_factory=invoke_from_module,
    )

    orch = Orchestrator(
        config=OrchestratorConfig(),
        embedder=embedder, memory_store=store, vcm=vcm,
        router=router, registry=registry, reward_model=reward,
        sandbox=sandbox, telemetry=telemetry,
        spawn_callback=spawn_cb,
    )
    await orch.start()
    try:
        await orch.run_forever()    # honors convergence + signal handlers
    finally:
        await orch.stop()
        await api.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

---

**What's delivered.** Phase 1 (math/structure), Phase 2 (orchestrator + config + api_client + bootstrap + memory/router/reward/registry/sandbox modules), Phase 3 (meta-prompts, exec loop, hot-plug FSM, convergence) — all inline above, no placeholders.

**What's not.** `main.py` references three helpers declared but not implemented: `load_master_secret()`, `default_capability_gap(request, fleet) -> str`, `invoke_from_module(module) -> Callable`. These are intentional integration seams — the first reads from the project's existing secrets store (the project already has `secrets/honcho.env.sops` as the canonical path), the second is the only piece of *prompted strategy* the operator owns (it determines what gaps the spawn loop targets), and the third bridges the runtime contract between a generated `async def run(request, ctx)` and the orchestrator's existing executor. A reference `main.py` is provided in `seed/main.py` with these helpers stubbed for clarity.
