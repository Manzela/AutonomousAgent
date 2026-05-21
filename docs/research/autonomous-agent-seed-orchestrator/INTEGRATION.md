# Integration into the AutonomousAgent build plan

This document maps the seed orchestrator's modules onto the existing
AutonomousAgent build state and identifies the work items needed to fold
this research artefact into the production scope.

## Where this fits in the current state of the repo

Per `memory/project_state_2026-05-20.md`:

> *main HEAD now `f67b577` (audit v2 squash-merged 2026-05-20 16:26Z);
> Wave 3 (audit P2) 7/8 shipped; cli-config + supply-chain + hermes-submodule
> state of record*

This seed orchestrator is **net-new scope** — it is not a refactor of
existing modules. It would slot in as:

```
lib/seed_orchestrator/        ← new package (currently empty)
    schemas.py
    embedder.py
    memory_store.py
    virtual_context.py
    moe_router.py
    reward_model.py
    agent_registry.py
    sandbox.py
    telemetry.py
    orchestrator.py
    api_client.py
    bootstrap.py
    main.py
tests/seed_orchestrator/      ← new test tree
```

The `hermes-agent/` submodule and the existing `lib/` layout remain
untouched. This is a **parallel research branch** that, if accepted,
becomes the spine of Phase 2+ work.

## Dependency on currently locked strategic decisions

Per `memory/project_strategic_dispositions_2026-05-20.md` ("all 8 ADR-0008
Qs locked"), the following decisions directly govern how this seed becomes
production:

| ADR-0008 disposition | Effect on seed orchestrator |
|---|---|
| **$5K GPU + Unsloth-only** | The PPO update loop in `orchestrator._policy_update_loop` is currently NumPy. Production update must run on the $5K GPU under Unsloth; the bilinear `W_r` and policy heads must move to PyTorch. **Work item: P-1** |
| **Postgres Phase 2** (Cloud SQL `db-custom-16-64000` + HNSW pgvector) | `AbstractMemoryStore` has one implementation (`InMemoryStore`). Production requires `PostgresStore` honouring the same contract, with HNSW (`m=16, ef=64`) per `memory/phase2_postgres_tier.md`. **Work item: P-2** |
| **A2A PRIORITY** | The orchestrator's `_execute()` currently dispatches to local sandbox via `agent_module.run(request, ctx)`. A2A peer-execution requires `_execute()` to route across the A2A boundary when the chosen expert lives on a peer node. **Work item: P-3** |
| **Firecracker H1** | `sandbox.py` ships `LocalSubprocessSandbox` only. Production requires `FirecrackerSandbox` (separate tier per `memory/h1_firecracker_scope.md`). **Work item: P-4** |
| **J1 + Model Armor** | Spawn-generated modules are persisted to `module_store_dir` in `bootstrap.py`. Per `memory/persistence_trap_contract.md`, the J3 shipper MUST call Model Armor sanitize before any GCS upload. The bootstrap's `module_store_dir.write_text(source)` is local-only; the J3 shipper layer must wrap it. **Work item: P-5** |
| **Governor Phase 3** | Per `memory/phase3_governor_design.md`, the governor is a standalone service that gates A2A traffic. The seed orchestrator's `_check_circuit_breakers` is a per-process breaker — the governor will subsume this once Q4 2026 trigger fires. **Work item: P-6 (deferred, not blocking)** |

## Concrete work items

The following are sized to roughly match the granularity of the existing
audit-plan task list. They are **not** filed as audit tasks yet — they're
queued for prioritisation against the post-audit roadmap.

### P-1. Port the policy network to PyTorch / Unsloth

- Replace `SoftmaxBilinearRouter`'s NumPy `ppo_update` with a PyTorch
  implementation runnable under Unsloth.
- Keep the bilinear contract — `e_k` shape and `W_r` invariance to expert
  reordering — so `add_expert`/`remove_expert` semantics survive.
- Mirror the `snapshot()` / `restore()` interface so the operator
  workflow is unchanged.
- **Acceptance.** A 24h burn-in run on the $5K GPU completes 1K PPO
  updates with cumulative KL drift < 0.5 to the initial reference policy.

### P-2. Implement `PostgresStore` (HNSW pgvector)

- Subclass `AbstractMemoryStore` with the same contract:
  - `search()` rejects empty scopes
  - `gc_expired(MemoryTier.EPHEMERAL, before_ts)` returns row count
  - the `tier_namespace_invariant` is enforced as a DB CHECK constraint as
    well as a Python validator
- Cloud SQL: `db-custom-16-64000` with `cloudsql-postgres-iam-auth` (Cloud
  SQL Auth Proxy, not PgBouncer — per `memory/phase2_postgres_tier.md`).
- HNSW index: `m=16, ef=64` over the `embedding` column.
- **Acceptance.** Property test: 10 projects × 1K records each, 100K random
  queries, zero cross-project leakage. Latency P95 ≤ 30ms for k=10 recall.

### P-3. A2A peer-execution dispatch

- Extend `AgentCapability` with a `peer_endpoint: str | None` field. When
  set, the orchestrator's `_execute()` routes the task across the A2A
  boundary instead of in-process.
- The peer node's orchestrator validates the inbound request against the
  capability's `source_sha256` to ensure the peer is running the same
  module bytes.
- **Acceptance.** Two-node test: project P with 3 agents, one local, two
  remote. Routing distribution roughly matches the local case after the
  same number of trajectories.

### P-4. Firecracker sandbox tier

- Implement `FirecrackerSandbox` per `memory/h1_firecracker_scope.md`:
  separate tier, not a `LocalSubprocessSandbox` replacement.
- GCP N2 nested-virt hosts; ~$265/month per the memory note.
- **Acceptance.** A2A peer-exec calls land in Firecracker microVM, not
  on the host kernel.

### P-5. J3 shipper integration in bootstrap

- Wrap `bootstrap.py`'s `out_path.write_text(source)` with a call to the
  Model Armor sanitize endpoint per the Persistence Trap contract:
  `model_armor.sanitize(source, mode=INSPECT_AND_REDACT)` →
  GCS upload of the sanitized bytes → local cache write of the original.
- Honour canary tokens + halt-LOUD posture per
  `memory/model_armor_j1_config.md`.
- **Acceptance.** A canary string injected into a generated module
  triggers `halt.loud` telemetry and the module is NOT persisted to GCS.

### P-6. Governor service rationalisation (DEFERRED)

- The per-process `_check_circuit_breakers` is fine for single-node
  operation. Once Q4 2026 A2A traffic gates fire, the standalone Governor
  service per `memory/phase3_governor_design.md` subsumes this and the
  orchestrator's breakers become local soft-limits only.
- **Trigger:** Q4 2026 A2A traffic threshold per the strategic disposition.

## What is NOT changed by this seed

- **CI gate set.** The audit-plan P0-P2 CI gates remain authoritative.
  This package would land behind the same gates.
- **Branch / commit conventions.** Conventional commits, squash-only,
  branch-name regex — unchanged.
- **Secrets layout.** The seed's `master_secret` for the VCM must be
  added to `secrets/` under sops encryption per existing convention.
  Naming proposal: `secrets/vcm_master.env.sops` containing
  `VCM_MASTER_SECRET=<hex>`.
- **PR #112 / Phase 0a state.** Unaffected. This research artefact lands
  on `main` (via the present branch) and PR #112 continues independently.

## Open questions for scope decision

1. **Is this folded into Wave 4 of the audit-plan, or does it become a
   separate Phase 1.5 initiative?** The work items above are roughly six
   audit-plan tasks; if absorbed as Wave 4 they would compete with the
   single deferred P2-5 item plus whatever Wave-4 was already planned to
   contain.
2. **Production model identity for the bootstrap loop.** The seed defaults
   to `claude-opus-4-7`. Per cost projections, sustaining the bootstrap at
   12 spawns/hour with Opus is materially expensive (~$1.50/hr at typical
   token volumes). Sonnet 4.6 cuts that to ~$0.30/hr at some quality cost.
   Recommend pinning to Opus during development and re-evaluating after
   the first 1K real generations have a fitness distribution to compare.
3. **Reward function `(α, β, γ, δ, ε)` retuning cadence.** Defaults are
   `(1.0, 0.3, 0.1, 0.5, 10.0)`. These need to be retuned after the first
   real workload. Propose: snapshot trajectory data at 1K, 10K, 100K
   episodes and re-fit by grid search on a hold-out set.

These questions are flagged for the next scope-review session; nothing
in the seed pre-empts them.
