# `seed/` — module roles

Quick map of what each file does and how they wire together. For the full
mathematical spec see `../01-phase1-mathematical-spec.md`; for the
self-correction risk analysis see `../02-self-correction-pass.md`; for the
bootstrapping protocol see `../03-phase3-bootstrapping-protocol.md`; for
the mapping onto the existing AutonomousAgent build state see
`../INTEGRATION.md`.

## Module table

| File | Role | Provenance |
|---|---|---|
| `schemas.py` | Pydantic v2 wire contracts: `TaskRequest`, `AgentCapability`, `RoutingAction`, `Reward`, `MemoryRecord`, plus enums. Schema-level invariants are layer-1 of the 5-layer isolation defence. | reconstructed |
| `embedder.py` | `AbstractEmbedder` + `HashingEmbedder` (deterministic, dep-free) + `SentenceTransformerEmbedder` stub + `project_dim()`. | reconstructed |
| `memory_store.py` | `AbstractMemoryStore` + `InMemoryStore` (brute-force cosine) + `EmptyScope` exception. **Layer-3 defence:** `search()` rejects empty scope sets. | reconstructed |
| `virtual_context.py` | `VirtualContextManager` issues per-acquisition `VirtualContextHandle`s. Layers 2 + 4: HMAC namespace token + post-fetch verification that invalidates the handle on contamination. | reconstructed |
| `telemetry.py` | `TelemetrySink` — bounded ring buffer with thread-safe `emit`, `drain`, and `dump_jsonl`. Stand-in for an OTEL exporter. | reconstructed |
| `sandbox.py` | `AbstractSandbox` + `LocalSubprocessSandbox`. POSIX rlimits via `preexec_fn`. **Hard refusal:** `network_allowed=True` raises; refuses to lie about isolation. NOT production-grade. | reconstructed |
| `moe_router.py` | `AbstractMoERouter` + `SoftmaxBilinearRouter`. NumPy PPO with KL trust region + trip-wire revert + Polyak-averaged reference policy. Hot-pluggable expert table. | reconstructed |
| `reward_model.py` | `IntrinsicRewardModel` composing `JudgeEnsemble` (median + OLS calibration) with deterministic R^eff / R^cost / R^div / R^safe heads. Ships `HeuristicJudge` and `AnthropicJudge`. | reconstructed |
| `agent_registry.py` | `AgentRegistry` implementing the Free Agent FSM (SPAWN → PROBATION → ACTIVE → COOL → EVICTED, plus PROMOTED branch). EMA fitness tracker. Listener fan-out outside the lock. | reconstructed |
| `orchestrator.py` | The conductor. `submit()` builds a state vector, routes via the MoE, dispatches the action, computes the decomposed reward, appends to the PPO trajectory buffer, updates fitness EMA, and trips circuit breakers. Three background loops handle eviction, ephemeral GC, and PPO updates. | reconstructed head + **verbatim tail** |
| `api_client.py` | `AnthropicClient` over `AsyncAnthropicVertex` / `AsyncAnthropic`. Ephemeral prompt caching on system blocks ≥1024 chars. Exponential backoff with 4xx vs 5xx-aware retry. `UsageRecord` for cost accounting. | **verbatim** |
| `bootstrap.py` | `META_SYSTEM_PROMPT`, `META_USER_TEMPLATE`, and `make_spawn_callback()`. Extracts source from completion text, scans for banned tokens, sandboxed import, shape validation, smoke test, registers a fresh `AgentCapability`. | **verbatim** |
| `main.py` | Wiring entry-point. Constructs every component and runs the orchestrator. Calls `load_master_secret()`, `default_capability_gap()`, `invoke_from_module()` — each kept as a function so an integrator can override one without monkey-patching. | reconstructed (matches Phase 3 emission shape) |
| `__init__.py` | Public surface. Anything not re-exported here is internal. | reconstructed |

**Provenance reminder.** "Verbatim" = the file is the original session
emission, byte-for-byte. "Reconstructed" = the file preserves every type,
invariant, and behaviour the session described, but its surface text was
regenerated from the spec / summary (the original tokens were compacted
out of the transcript before this folder was written).

## Wiring order (cold start)

```
load_master_secret()            ← path or env var, ≥16 bytes
TelemetrySink()
HashingEmbedder(dim=256)
InMemoryStore(dim=256)
VirtualContextManager(store, master_secret)
LocalSubprocessSandbox()        ← DEV ONLY
HeuristicJudge() + (optional) AnthropicJudge(api_client)
JudgeEnsemble(judges)
IntrinsicRewardModel(judges)
AgentRegistry(RegistryConfig())
OrchestratorConfig(production=…)
SoftmaxBilinearRouter(state_dim, capability_dim, state_proj_dim)
make_spawn_callback(client, sandbox, capability_gap_fn, invoke_from_module)
Orchestrator(config, router, registry, vcm, store, embedder, sandbox,
             reward_model, telemetry, spawn_cb)
await orchestrator.start()      ← spawns 3 background loops
```

## Hot path (one task)

```
orchestrator.submit(request)
  ↓
_check_circuit_breakers           ← fail fast; never queue under load
  ↓
_build_state_vector → encoded()   ← phase | task_emb | proj_emb | budget | history | fingerprint
  ↓
router.act(z, active_ids)         ← bilinear scorer + meta head + temp head
  ↓
_dispatch(action)
   ├─ REFUSE        → _make_refusal
   ├─ SPAWN_EXPERT  → _maybe_spawn → register → _execute
   └─ EXECUTE       → _execute (cap.invoke or A2A peer)
  ↓
reward_model.evaluate(request, result, capability, diversity)
  ↓
trajectory_buffer.append(TrajectoryStep)         ← drained by _policy_update_loop
  ↓
registry.record_fitness(agent, reward.scalar)    ← may transition lifecycle
  ↓
_record_outcome(ok, cost)                        ← breaker windows
```

## Lock ordering

Always acquire in this order (the Orchestrator constructor enforces nothing
at runtime — it's a discipline you keep at call-sites):

```
_spawn_lock  <  _stats_lock  <  _trajectory_lock
```

Listener fan-out (`AgentRegistry`, etc.) always runs OUTSIDE its own lock
so a slow listener cannot block the next mutation. The router's hot-plug
callback embeds the new capability before `add_expert()` returns, so the
state vector built on the very next `submit()` already sees the new expert.

## Production checklist

> **Builder agents:** the recommended integration pattern is **hybrid —
> abstract interfaces in `app/core/`, GCP-native implementations in
> `app/adapters/gcp/`, in-memory implementations in `app/adapters/inmemory/`
> for tests.** See [`../04-gcp-native-adapter-plan.md`](../04-gcp-native-adapter-plan.md)
> for the full adapter table, priority order (P-7..P-17), and acceptance
> criteria. The checklist below is the higher-level summary of the same
> intent.

Before flipping `OrchestratorConfig.production=True`:

1. Swap `LocalSubprocessSandbox` → `FirecrackerSandbox`
   (INTEGRATION.md P-4) for long-lived experts, optionally
   `CloudRunJobSandbox` (P-8) for spawn bursts. The orchestrator
   constructor refuses to start with a non-production sandbox under
   `production=True`.
2. Swap `InMemoryStore` → `CloudSqlPgvectorStore` with HNSW pgvector
   (INTEGRATION.md P-2; Phase 2 Postgres tier in memory
   `phase2_postgres_tier`). If memory tier exceeds ~10M vectors, add
   `VertexVectorSearchStore` (P-7) as the billion-scale tier.
3. Swap `HeuristicJudge` ensemble defaults: keep `HeuristicJudge` as a
   calibration anchor, but the production deploy needs ≥3 LLM judges —
   `VertexAnthropicJudge` + `VertexGeminiJudge` + a third alternate-family
   judge (P-16).
4. Wire `TelemetrySink` → `CloudTraceTelemetry` (P-10): OTEL spans to
   Cloud Trace, structured logs to Cloud Logging, trajectory rows to
   BigQuery for judge-calibration analytics. The seed's sink is bounded
   at 16384 events and will drop on overflow.
5. Swap `VirtualContextManager` → `CloudKmsVcm` (P-11) so the master
   secret never materialises in app memory (HMAC ops done via Cloud KMS
   HSM). Configure `VCM_MASTER_SECRET_PATH` from a sops-managed file or
   GCP Secret Manager for the seed/dev path (never from
   `VCM_MASTER_SECRET_BYTES_DEV`).
6. Enable Model Armor on the J1 sanitisation path
   (memory `model_armor_j1_config` + `persistence_trap_contract`;
   INTEGRATION.md P-5).
7. Add `PubSubIntake` (P-12) in front of `Orchestrator.submit()` for
   durability, and `CloudTasksScheduler` (P-13) for the three background
   loops so they survive orchestrator restart.
8. Review `OrchestratorConfig` spawn rate (`max_spawned_agents_per_hour`)
   and circuit breaker thresholds for the target workload.
9. Establish Workload Identity Federation (P-14), Artifact Registry +
   Binary Authorization (P-15), and VPC Service Controls perimeter (P-17)
   as the deployment surface expands.
