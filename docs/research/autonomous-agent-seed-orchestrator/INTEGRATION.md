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

## GCP-native adapter work items (P-7 .. P-17)

These items implement the hybrid pattern described in
[`04-gcp-native-adapter-plan.md`](./04-gcp-native-adapter-plan.md):
**abstract interfaces in `app/core/`, GCP-native implementations in
`app/adapters/gcp/`, in-memory implementations in `app/adapters/inmemory/`
for tests.** Priority order is in §"Priority order for the GCP-native
swaps" of the adapter plan; the items are listed here in numeric order.

### P-7. Vertex AI Vector Search store (billion-scale tier)

- Subclass `AbstractMemoryStore` as `VertexVectorSearchStore`. Honours the
  same contract as P-2: `search()` rejects empty scopes, `gc_expired()`
  returns row count, tier↔namespace invariant enforced.
- Trigger to activate: project memory tier exceeds ~10M vectors. Below
  that, the P-2 `CloudSqlPgvectorStore` is sufficient.
- **Acceptance.** 100M random vectors across 100 projects; 10K queries;
  zero cross-project leakage; P95 ≤ 50ms for k=10 recall.

### P-8. Cloud Run jobs sandbox tier (spawn-burst optimisation)

- Subclass `AbstractSandbox` as `CloudRunJobSandbox`. Complements P-4
  (Firecracker) — Firecracker for long-lived experts, Cloud Run jobs for
  cheap spawn bursts.
- Cold-start tolerant; not suitable for sub-second-deadline tasks.
- **Acceptance.** 100 concurrent spawns complete within 60s; cold-start
  P95 ≤ 15s; no `network_allowed=True` accepted (same hard refusal as
  `LocalSubprocessSandbox`).

### P-9. Vertex embeddings adapter

- Subclass `AbstractEmbedder` as `VertexEmbeddingsEmbedder`, wrapping the
  current Vertex `text-embedding-005` (or successor) model.
- Batched calls; deterministic on-disk cache keyed by SHA-256 of input.
- Replaces `HashingEmbedder` in production; `HashingEmbedder` stays in
  `app/adapters/inmemory/` for CI.
- **Acceptance.** Recall@10 on a domain-specific eval set improves by
  ≥30% over `HashingEmbedder`; per-embedding cost ≤ $0.0001.

### P-10. Cloud Trace + Cloud Logging + BigQuery telemetry

- Replace the seed's `TelemetrySink` with `CloudTraceTelemetry` that:
  - Emits OTEL spans to Cloud Trace
  - Emits structured logs to Cloud Logging
  - Sinks trajectory rows to BigQuery for judge-calibration analytics
    and PPO trajectory replay
- BigQuery schema: one row per `(task_id, agent_id, judge_id, score,
  calibrated_score, reward, kl_divergence, timestamp)`.
- **Acceptance.** A 24h burn-in run produces a complete trace for every
  task and a queryable BigQuery table of all judge scores. No telemetry
  drop on overflow.

### P-11. Cloud KMS-backed VirtualContextManager

- Subclass `VirtualContextManager` as `CloudKmsVcm`. HMAC operations use
  `MacSign`/`MacVerify` against a KMS HSM-backed key; the master secret
  never materialises in app memory.
- Key rotation policy: every 90 days, dual-key window of 24h.
- Replaces the seed's raw `hmac.new(master_secret, ...)` call.
- **Acceptance.** Master secret is never present in a heap dump of the
  running orchestrator; key rotation completes without dropping any
  in-flight `VirtualContextHandle`.

### P-12. Pub/Sub intake for `Orchestrator.submit()`

- Add a sidecar consumer that pulls from a Pub/Sub topic and invokes
  `orchestrator.submit(request)`. The HTTP layer above publishes to the
  topic instead of calling `submit()` directly.
- Durability, retries with exponential backoff, dead-letter topic after
  N=5 attempts.
- **Acceptance.** Kill the orchestrator mid-task; on restart, the
  in-flight task is re-delivered and completed exactly once
  (idempotency key on `request.task_id`).

### P-13. Cloud Tasks + Cloud Scheduler for background loops

- Replace the orchestrator's three `asyncio.Task` background loops
  (`_eviction_loop`, `_ephemeral_gc_loop`, `_policy_update_loop`) with
  Cloud Scheduler cron triggers that enqueue Cloud Tasks. The orchestrator
  exposes three HTTP handlers; Cloud Tasks invokes them.
- Survives orchestrator restart; no missed eviction cycles.
- **Acceptance.** 7-day continuous operation with N=3 simulated
  orchestrator restarts: zero missed eviction cycles, zero leaked
  ephemeral memory records.

### P-14. Workload Identity Federation

- Eliminate all service-account key files from the runtime environment.
  Pod-level identity via Workload Identity (GKE) or service identity
  (Cloud Run).
- Required before P-12 and P-13 — Pub/Sub and Cloud Tasks need
  identity-bound IAM.
- **Acceptance.** `find / -name '*.json' | xargs grep -l "service_account"`
  inside the running container returns zero results.

### P-15. Artifact Registry + Binary Authorization for sandbox images

- All Firecracker rootfs images and Cloud Run job container images are
  built and stored in Artifact Registry. Binary Authorization enforces
  that only images signed by the project's attestor can be deployed.
- Required for any multi-host sandbox tier (P-4 or P-8).
- **Acceptance.** An unsigned image fails to deploy with a Binary
  Authorization policy violation in the audit log.

### P-16. Three-judge Vertex ensemble (production reward model)

- Wire `VertexAnthropicJudge` (Claude Opus 4.7 via Vertex, already in
  `seed/api_client.py`), `VertexGeminiJudge` (Gemini 3.1 Pro via Vertex
  per `memory/gemini_3_1_pro_preview_quirks.md`), and a third
  alternate-family judge into `JudgeEnsemble`.
- The seed's `HeuristicJudge` stays as a calibration anchor.
- Median aggregation over ≥3 valid responses; OLS calibration against a
  human-labelled hold-out set.
- **Acceptance.** Per `seed/README.md` production checklist item 3: the
  ensemble's calibrated score on a 1K-task hold-out set has Spearman
  rank correlation ≥0.7 with human ground-truth labels.

### P-17. VPC Service Controls perimeter

- VPC SC perimeter around Vertex AI, Cloud SQL, Cloud Storage, Pub/Sub,
  Cloud Tasks, Artifact Registry, and Cloud KMS resources for the
  `autonomous-agent-2026` project.
- Required for production data-plane isolation; defer until the
  data-plane spans multiple GCP resources (i.e., after P-7, P-10, P-11,
  P-12, P-13 land).
- **Acceptance.** An access from outside the perimeter to any in-scope
  resource is logged as a denied request in the Access Context Manager
  audit log.

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
