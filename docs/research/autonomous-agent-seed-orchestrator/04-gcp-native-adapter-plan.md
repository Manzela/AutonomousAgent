# Phase 4 — GCP-Native Adapter Plan

**Date.** 2026-05-21
**Status.** Recommendation locked. Builder agents MUST apply this pattern
when lifting the seed into production code under `app/` or `lib/`.

## TL;DR for the builder agent

When you port a seed module from `docs/research/autonomous-agent-seed-orchestrator/seed/`
into the live codebase, do not collapse the abstract base class. Keep the
seed's `AbstractMemoryStore`, `AbstractSandbox`, `AbstractMoERouter`,
`AbstractEmbedder`, `Judge` Protocol, and `AbstractIntrinsicRewardModel`
exactly as written. Then add a **GCP-native subclass** as a sibling under
`app/adapters/gcp/` (or `lib/adapters/gcp/` — match the surrounding
convention). Tests run against the in-memory defaults; staging and prod run
against the GCP adapters.

Concretely:

```
app/
  core/
    schemas.py              ← copied from seed/schemas.py
    orchestrator.py         ← copied from seed/orchestrator.py
    moe_router.py           ← copied from seed/moe_router.py
    reward_model.py         ← copied from seed/reward_model.py
    agent_registry.py       ← copied from seed/agent_registry.py
    virtual_context.py      ← copied from seed/virtual_context.py
  adapters/
    inmemory/               ← copied from seed/memory_store.py, telemetry.py,
                              sandbox.py, embedder.py — for tests
    gcp/                    ← NEW — see "Adapter table" below
      vector_search_store.py
      cloud_sql_store.py
      firecracker_sandbox.py
      cloud_run_sandbox.py
      cloud_trace_telemetry.py
      kms_vcm.py
      pubsub_intake.py
      cloud_tasks_scheduler.py
      artifact_registry_bootstrap.py
      vertex_anthropic_judge.py
      vertex_gemini_judge.py
```

## Why hybrid (and not "everything GCP-native")

The seed exists in two roles simultaneously:

1. **Research artefact / specification.** Other teams (internal or external)
   may read it for architectural reference. A native dependency on Cloud
   KMS or Pub/Sub in the seed itself would make the artefact unreadable for
   anyone not on GCP.
2. **Local-test substrate.** CI runs `pytest` without provisioning a Cloud
   SQL instance, Vertex AI Vector Search index, or Firecracker host. The
   in-memory defaults are what makes the test suite fast and offline.

Going fully GCP-native at the seed layer would sacrifice both. Going fully
portable in production would sacrifice operational simplicity, vendor
optionality, and security primitives we have already locked in
(`secrets/`-managed sops + Model Armor + Cloud SQL + Vertex AI).

The pattern is therefore: **abstract interface in `core/`, GCP-native
implementation in `adapters/gcp/`, in-memory implementation in
`adapters/inmemory/`.** This adds ~10% boilerplate and pays back in:

- Local testability (no GCP credentials required for `pytest`)
- Vendor optionality (swap `VertexGeminiJudge` for `BedrockClaudeJudge`)
- Re-usability as a research artefact (other teams can adopt pieces)
- Clear separation between contract and infrastructure

## Adapter table

This is the canonical mapping. Each row is a separate work item; cross-
reference with `INTEGRATION.md` for the P-N work-item identifiers.

| Seed abstract | In-memory adapter (CI/test) | GCP-native adapter (production) | Work item |
|---|---|---|---|
| `AbstractMemoryStore` | `InMemoryStore` (brute-force cosine) | **`VertexVectorSearchStore`** (Matching Engine) for billion-scale tier; **`CloudSqlPgvectorStore`** (HNSW `m=16, ef=64`) for Phase 2 mid-scale per `memory/phase2_postgres_tier.md` | P-2 (mid-scale, already filed), **P-7 (Matching Engine, new)** |
| `AbstractSandbox` | `LocalSubprocessSandbox` (rlimits) | **`FirecrackerGCEStore`** (N2 nested-virt, ~$265/mo per `memory/h1_firecracker_scope.md`) for long-lived experts; **`CloudRunJobSandbox`** for spawn bursts (cheaper, cold-start tolerant) | P-4 (Firecracker, already filed), **P-8 (Cloud Run jobs, new)** |
| `AbstractEmbedder` | `HashingEmbedder` (SHA-256 token-hash, dep-free) | **`VertexEmbeddingsEmbedder`** wrapping `text-embedding-005` (or successor); deterministic, batched, with on-disk cache | **P-9 (new)** |
| `TelemetrySink` (concrete class) | ring buffer (16384 events) | **`CloudTraceTelemetry`** — emits OTEL spans to Cloud Trace, structured logs to Cloud Logging, and trajectory rows to **BigQuery** for judge-calibration analytics | **P-10 (new)** |
| `VirtualContextManager` (raw HMAC) | `master_secret: bytes` in memory | **`CloudKmsVcm`** — HMAC operations done via Cloud KMS HSM-backed key (`MacSign`/`MacVerify`); master secret never materialises in app memory | **P-11 (new)** |
| `Orchestrator.submit()` (in-process) | direct call | **`PubSubIntake`** in front of `submit()` — durability, retries, dead-letter, fan-out; sidecar consumer pulls and invokes `submit()` | **P-12 (new)** |
| 3 background loops (`asyncio.Task`) | in-process | **`CloudTasksScheduler` + `CloudScheduler`** for eviction loop, ephemeral GC loop, PPO update loop; survives orchestrator restart | **P-13 (new)** |
| Service-account keys | none required | **Workload Identity Federation** — pod-level identity, no key files on disk | **P-14 (new)** |
| Sandbox image distribution | local Docker | **Artifact Registry** + **Binary Authorization** (only signed images can run) | **P-15 (new)** |
| `Judge` Protocol | `HeuristicJudge` | **`VertexAnthropicJudge`** (Claude via Vertex), **`VertexGeminiJudge`** (Gemini 3.1 Pro via Vertex per `memory/gemini_3_1_pro_preview_quirks.md`), and a third alternate-family judge | P-2 (mentioned), **P-16 (new — wire all three)** |
| Network egress / data-plane isolation | none | **VPC Service Controls** perimeter around Vertex AI + Cloud SQL + GCS + Pub/Sub + Cloud Tasks resources | **P-17 (new)** |

## What NOT to GCP-native (keep portable even in production)

These pieces must stay platform-agnostic so the orchestrator core remains
testable locally and the project retains cloud-exit optionality:

- `core/orchestrator.py` — the conductor logic. Must run against in-memory
  adapters for unit tests; GCP adapters injected at startup.
- `core/moe_router.py` — pure NumPy math (and the PPO update logic, even
  after the P-1 PyTorch port). No cloud calls in the hot path.
- `core/reward_model.py` — the reward composition (`α·R^out + β·R^eff +
  γ·R^div − δ·R^cost − ε·R^safe`) is pure functions over scores. Judge
  *implementations* go in `adapters/gcp/`; the *protocol* and the
  *composition* stay portable.
- `core/schemas.py` — Pydantic models. Vendor-neutral by definition.
- `core/agent_registry.py` — Free Agent FSM. In-memory + thread-safe; if
  multi-process state is ever needed, a separate `RedisRegistryBackend` (or
  Memorystore) adapter is added rather than changing the registry.
- `Judge` Protocol — so you can A/B Anthropic vs Gemini vs self-hosted
  Llama (per the `$5K GPU + Unsloth-only` strategic disposition).

## Dependency chain for the builder

If the builder agent is starting from a clean `app/` tree, the suggested
ordering is:

```
1. core/schemas.py             ← copy seed/schemas.py verbatim
2. core/virtual_context.py     ← copy seed/virtual_context.py
3. adapters/inmemory/           ← copy seed/{memory_store, sandbox, embedder, telemetry}.py
4. core/moe_router.py          ← copy seed/moe_router.py (NumPy first; P-1 PyTorch later)
5. core/reward_model.py        ← copy seed/reward_model.py
6. core/agent_registry.py      ← copy seed/agent_registry.py
7. core/orchestrator.py        ← copy seed/orchestrator.py
8. core/api_client.py          ← copy seed/api_client.py (already Vertex-aware)
9. core/bootstrap.py           ← copy seed/bootstrap.py
10. main.py                    ← copy seed/main.py; wire DI at the bottom
11. tests/                     ← write tests against adapters/inmemory/*
12. adapters/gcp/              ← P-7..P-17 work items, in priority order
```

Steps 1–11 ship the system on in-memory adapters. Step 12 swaps
implementations one at a time, gated by acceptance tests that mirror the
seed's contracts.

## Priority order for the GCP-native swaps

Not all P-7..P-17 items are equally urgent. Suggested ordering:

| Order | Work item | Reason |
|---|---|---|
| 1 | **P-11 (Cloud KMS VCM)** | Security boundary. Master secret in app memory is a P0 concern once any production traffic flows. |
| 2 | **P-9 (Vertex embeddings)** | `HashingEmbedder` is fine for tests but its recall ceiling caps the whole system's effectiveness in production. Cheap win. |
| 3 | **P-2 (Cloud SQL pgvector)** | Already filed and locked per Phase 2 disposition. |
| 4 | **P-10 (Cloud Trace + BigQuery telemetry)** | Observability is a precondition for diagnosing the next P-N item. |
| 5 | **P-16 (3-judge Vertex ensemble)** | Per `seed/README.md` production checklist; required before flipping `production=True`. |
| 6 | **P-12 (Pub/Sub intake)** | Durability for the hot path. Required once external clients submit tasks. |
| 7 | **P-13 (Cloud Tasks scheduler)** | Durability for the background loops. Required for >24h continuous operation. |
| 8 | **P-4 (Firecracker)** | Already filed and locked per H1 disposition. |
| 9 | **P-8 (Cloud Run jobs)** | Spawn-burst optimisation. Defer until spawn rate exceeds Firecracker's steady-state capacity. |
| 10 | **P-7 (Vertex Matching Engine)** | Only needed when memory tier exceeds ~10M vectors. pgvector HNSW handles everything below that. |
| 11 | **P-14 (Workload Identity Federation)** | Required for any GKE/Cloud Run deploy. Defer if still on a single VM. |
| 12 | **P-15 (Artifact Registry + Binary Authorization)** | Required for any multi-host sandbox tier. Defer until P-4 or P-8 lands. |
| 13 | **P-17 (VPC Service Controls)** | Required for production data-plane isolation. Defer until the data-plane has multiple GCP resources to perimeter. |

## Cost rough-order-of-magnitude

For a single-tenant deploy serving ~1K tasks/day, the GCP-native adapters
add roughly:

| Service | Monthly cost (rough) | Notes |
|---|---|---|
| Cloud SQL `db-custom-16-64000` | $1,580 | Already in Phase 2 scope (`memory/phase2_postgres_tier.md`) |
| Firecracker GCE N2 (1 host, 24/7) | $265 | Already in H1 scope (`memory/h1_firecracker_scope.md`) |
| Vertex AI Vector Search (if used) | $200–$2000 | Scales with index size; pgvector covers most needs |
| Vertex embeddings | $20 | At 1K tasks/day × ~5 embeddings each |
| Cloud KMS HSM key + ops | $5 | Per-key cost negligible at this scale |
| Pub/Sub | $40 | At 1K tasks/day |
| Cloud Tasks + Cloud Scheduler | $5 | Negligible |
| Cloud Trace + Cloud Logging + BigQuery | $50–$200 | Scales with telemetry volume |
| Artifact Registry | $5 | Per-GB storage; sandbox image is small |
| **GCP-native overhead (above Phase 2 + H1)** | **~$325–$2,275/mo** | Highly workload-dependent |

The bulk (Cloud SQL + Firecracker) is already in the locked scope. The
incremental cost of the full GCP-native pattern is ~$325–$2,275/mo
depending on whether Matching Engine and aggressive BigQuery sinking are
turned on.

## Acceptance criteria for "this codebase is GCP-native"

The builder agent has finished the GCP-native pass when:

1. Every concrete class in `app/adapters/gcp/` inherits from a corresponding
   abstract in `app/core/`.
2. `pytest` runs end-to-end against `adapters/inmemory/` with zero GCP
   credentials configured.
3. A second test suite under `tests/integration/gcp/` runs against real
   GCP resources, gated behind an env flag and a per-PR opt-in label.
4. The production deploy can flip `OrchestratorConfig.production=True` and
   the constructor's `_require_production_grade()` checks pass for all
   adapters (sandbox, store, judge ensemble, telemetry sink, secrets).
5. `INTEGRATION.md`'s P-7 through P-17 are either checked off or
   explicitly deferred with a recorded reason.
6. No `i-for-ai` references remain in active code paths (per the GCP
   migration constraint in `CLAUDE.md`).

## Cross-references

- `INTEGRATION.md` — work-item identifiers P-1..P-17 and per-item
  acceptance criteria
- `seed/README.md` — production checklist (7 swaps) that lists the same
  intent at a higher level
- `memory/phase2_postgres_tier.md` — pgvector HNSW config
- `memory/h1_firecracker_scope.md` — Firecracker tier sizing
- `memory/model_armor_j1_config.md` + `memory/persistence_trap_contract.md`
  — Model Armor / J1 sanitisation (already wired into P-5)
- `memory/gemini_3_1_pro_preview_quirks.md` — Gemini 3.1 Pro model-id
  quirks (relevant to P-16)
- `memory/gcp_migration_2026-05-21.md` — `i-for-ai` → `autonomous-agent-2026`
  migration; all P-7..P-17 must target `autonomous-agent-2026` directly
