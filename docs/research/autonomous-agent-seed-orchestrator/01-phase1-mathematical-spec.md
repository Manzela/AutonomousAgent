# Phase 1 — Mathematical & Structural Specification

This document defines the **state space, action space, and reward function**
for the RL Gating Network of the Phase-Aware Router; the **data schema and
isolation logic** for the Hierarchical Memory; and the **loop topology** by
which the system self-evaluates, executes sandboxed code, and updates its
agent registry through the Free Agent Mechanism.

---

## 1. RL Gating Network (Phase-Aware Router)

The router is modelled as a finite-horizon **Markov Decision Process**
optimised with **Proximal Policy Optimisation** under a KL trust-region
constraint to a slowly-moving reference policy.

### 1.1 State space `S`

A single state `s_t ∈ S` is the tuple

```
s_t  =  ( τ_t,  c_t,  E_t,  p_t,  b_t,  h_t,  φ_proj(P_t) )
```

| Component | Type | Dim | Meaning |
|---|---|---|---|
| `τ_t` | one-hot | 5 | Pipeline phase: `{research, draft, refine, verify, ship}` |
| `c_t` | float vector | 256 | Task-context embedding (from `embedder.embed(task.summary)`) |
| `E_t` | float matrix | `K_t × 256` | **Capability matrix** — row `e_k` is the embedding of expert `k`'s `description + tags`. `K_t` varies as experts are spawned/evicted |
| `p_t` | float vector | 128 | Project identifier embedding (deterministic hash of `project_id`) |
| `b_t` | float vector | 4 | Budget remaining: `(cost_usd, time_s, retries, kl_headroom)` |
| `h_t` | float vector | 32 | History features: rolling-window error rate, cost trend, latency P95, recall hit-rate, …  |
| `φ_proj(P_t)` | float vector | 64 | Project-specific routing context (recent decisions for project `P_t`, kept distinct from `c_t` to preserve the Virtual Context boundary) |

The encoder `z = StateVector.encoded(s_t)` concatenates the fixed-width
components (everything except `E_t`) into a single `R^{256}` vector via a
learnable projection. `E_t` is kept separate because it is consumed by the
**bilinear scorer** rather than the fixed encoder.

### 1.2 Action space `A`

A single action `a_t ∈ A` is the triple

```
a_t  =  ( π_t,  m_t,  T_t )
```

| Component | Type | Domain | Meaning |
|---|---|---|---|
| `π_t` | probability simplex | `Δ^{K_t}` | Routing distribution over the currently active expert pool. The chosen expert is sampled `k ~ Cat(π_t)` |
| `m_t` | discrete | `{EXECUTE, REFUSE, SPAWN_EXPERT}` | **Meta-action**. `EXECUTE` proceeds with `k`. `REFUSE` declines the task with a structured reason (no cost charged). `SPAWN_EXPERT` invokes the Free Agent Mechanism before retrying with the enlarged pool |
| `T_t` | scalar | `R_+` | Sampling temperature applied to the logits before softmax. The policy head emits `log T_t` so the scalar stays positive |

Because `K_t` is not fixed, the policy uses a **bilinear scorer** rather than
a fixed-output head:

```
ℓ_k  =  z^T W_r e_k          (logit for expert k)
π_t  =  softmax( ℓ / T_t )
```

The bilinear weight `W_r ∈ R^{256 × 256}` is **invariant to expert
reordering** and supports **zero-shot expert addition** — when the registry
appends a new expert, the matrix is not reshaped and no weights are migrated.
The new expert's logit is computed by the same formula on the next decision.

### 1.3 Reward function `R`

Reward is **decomposed** along five independent axes and combined linearly:

```
R(s_t, a_t, s_{t+1})  =  α · R^out  +  β · R^eff  +  γ · R^div
                       − δ · R^cost  −  ε · R^safe
```

| Term | Signal | Scale | Reason it exists |
|---|---|---|---|
| `R^out` | judge-ensemble median score in `[0, 1]` (calibrated to ground-truth labels via OLS) | bounded `[0, 1]` | The principal "did the task succeed" signal. Median across ≥3 judges is robust to single-judge gaming |
| `R^eff` | inverse-latency utility `1 / (1 + duration_s / τ_ref)` | bounded `(0, 1]` | Penalises wall-clock slowness |
| `R^div` | exploration bonus = `−log π_t[k_chosen]` | unbounded `[0, ∞)`, clipped at +5 | Counter-pressure against fleet collapse onto one dominant expert |
| `R^cost` | normalised USD `cost / budget.cost_usd_max` | bounded `[0, ∞)` | Tracks the actual spend pressure |
| `R^safe` | safety classifier output in `[0, 1]` (1 = unsafe) | bounded `[0, 1]` | The only term with `ε >> 1` so any safety violation dominates the scalar |

**Default coefficients** (tuned for "outcome-driven with strong safety prior"):

```
(α, β, γ, δ, ε)  =  (1.0, 0.3, 0.1, 0.5, 10.0)
```

The `Reward` Pydantic model in `seed/schemas.py` exposes a `scalar` property
that performs this combination so consumers never combine terms ad-hoc.

### 1.4 Optimisation: PPO with KL trust region

The policy `π_θ` is updated with the **clipped surrogate objective**

```
L^CLIP(θ)  =  E_t [ min( r_t(θ) A_t,
                         clip(r_t(θ), 1−ε, 1+ε) A_t ) ]

with  r_t(θ)  =  π_θ(a_t | s_t) / π_θ_old(a_t | s_t)
```

augmented with a **per-state KL constraint to a reference policy** `π_ref`:

```
L(θ)  =  L^CLIP(θ)  −  c_kl · KL[ π_θ || π_ref ]  +  c_ent · H[π_θ]
```

The reference policy is the **Polyak-averaged** snapshot of `π_θ` taken at
the end of each update cycle that passes a quality gate
(`KL_after ≤ ppo_kl_bless_threshold`):

```
π_ref  ←  blend · π_ref  +  (1 − blend) · π_θ          (blend = 0.99)
```

This produces a slow-moving reference that the policy may **drift away from
but not race away from**. A **trip-wire** reverts `W_r` to the reference
weights when the post-update KL exceeds `0.05`; this prevents catastrophic
policy collapse from a single bad batch.

Defaults: `lr = 3e-4`, `clip = 0.2`, `kl_target = 0.02`,
`kl_bless_threshold = 0.01`, `entropy_coef = 0.005`.

---

## 2. Hierarchical Memory Schema

Memory is organised into **three tiers** with strictly different lifetimes
and isolation rules.

### 2.1 Tiers

| Tier | Lifetime | Project scoping | Mutability | Promotion mechanism |
|---|---|---|---|---|
| `CONSENSUS` | indefinite | **None** (`project_id is None`) | **Immutable** once written | Quorum vote across project-isolated agents proposes; meta-evaluator approves |
| `EPISODIC` | indefinite | **Per-project** (`project_id` required) | Append-only with version | Direct write from an agent operating inside that project's Virtual Context |
| `EPHEMERAL` | `TTL ≤ 1h` | Per-project or session | Mutable | Direct write; garbage-collected on `expires_at` |

The tier is encoded in the `MemoryTier` enum (`seed/schemas.py`).

### 2.2 The schema invariant

`MemoryRecord` enforces the **structural** half of the isolation guarantee via
a Pydantic v2 model-level validator:

```
CONSENSUS  ⟺  project_id is None
```

Any record that violates this invariant fails validation at construction
time. There is no way to reach the store with a malformed record because
construction is the only entry point.

### 2.3 Virtual Context Isolation — the 5-layer defence

Reading and writing memory MUST go through the **Virtual Context Manager**
(VCM). The VCM provides per-project handles minted via an async context
manager; cross-project access raises `NamespaceContamination`.

**Layer 1 — Schema invariant** (above).

**Layer 2 — HMAC namespace token.** Each project is bound to a per-project
key derived from a master secret:

```
K_p  =  HMAC_SHA256( master_secret,
                     "vcm:project:" || P  )
```

Handles carry the token; tokens are verified inside the VCM on every call.
Forged tokens fail the HMAC check.

**Layer 3 — Read-side scope filter.** `AbstractMemoryStore.search()`
**REJECTS empty scopes**. Callers must supply an explicit
`{CONSENSUS, project_id}` set; the store will only return rows whose
`(tier, project_id)` matches a member of the set.

**Layer 4 — Defence-in-depth assertion.** Every record the VCM returns to the
caller is re-checked: `_verify(record)` asserts
`record.project_id in handle.allowed_projects ∪ {None_if_consensus}`. If the
assertion fires, the handle is invalidated and `NamespaceContamination` is
raised. This catches store bugs that bypass layer 3.

**Layer 5 — Physical sharding.** Production deployment shards EPISODIC tier
records by `hash(project_id) mod N` across separate stores. A bug in one
shard cannot leak rows from another. (`InMemoryStore` is single-shard for
dev; the abstract interface accommodates the sharded implementation.)

### 2.4 Promotion from EPISODIC to CONSENSUS

CONSENSUS records are produced by a **quorum protocol**:

1. ≥ Q (default 3) agents operating in distinct project namespaces
   independently propose the same `content_hash`.
2. A meta-evaluator (separate judge) scores the candidate and assigns a
   `consensus_score`.
3. If `consensus_score ≥ threshold`, the record is written with
   `tier=CONSENSUS, project_id=None`. The original episodic copies are NOT
   deleted.

This is the **only** path by which knowledge crosses the project boundary,
and the threshold is the architectural knob the operator turns to trade
contamination risk against shared-knowledge growth.

---

## 3. Loop Topology

The system operates a tight per-task control loop:

```
        ┌──────────────────────────────────────────────────────────────┐
        │  TaskRequest enters orchestrator.submit()                    │
        │       │                                                      │
        │       ▼                                                      │
        │  circuit-breaker gate                                        │
        │       │                                                      │
        │       ▼                                                      │
        │  _build_state()                                              │
        │   ├─ embedder.embed(task.summary)        → c_t               │
        │   ├─ VCM handle .search(scope)           → recall context    │
        │   ├─ registry.snapshot()                 → E_t (cap matrix)  │
        │   └─ rolling-window history              → h_t               │
        │       │                                                      │
        │       ▼                                                      │
        │  router.act(state)        → (π_t, m_t, T_t)                  │
        │       │                                                      │
        │       ▼                                                      │
        │  meta-action dispatch:                                       │
        │   ├─ REFUSE        → _make_refusal()  ─────────┐             │
        │   ├─ SPAWN_EXPERT  → _maybe_spawn() ─────┐     │             │
        │   │                                       ▼     │             │
        │   │                              bootstrap.spawn (API loop)  │
        │   │                                       │                  │
        │   │                                       ▼                  │
        │   │                              registry.register(cap)      │
        │   │                                       │                  │
        │   │                                       ▼                  │
        │   │                              listener fan-out            │
        │   │                                       │                  │
        │   │                                       ▼                  │
        │   │                              router.add_expert(cap)      │
        │   │                                       │                  │
        │   └─ EXECUTE  ◄────────────────────────────                  │
        │       │                                                      │
        │       ▼                                                      │
        │  sandbox.run(agent, task) → ExecutionResult                  │
        │       │                                                      │
        │       ▼                                                      │
        │  reward_model.score(state, action, result)                   │
        │       │                                                      │
        │       ▼                                                      │
        │  _record():                                                  │
        │   ├─ append trajectory to PPO buffer                         │
        │   ├─ EMA fitness update via registry.update_fitness(...)     │
        │   └─ episodic memory write via VCM handle .write(record)     │
        │       │                                                      │
        │       ▼                                                      │
        │  return ExecutionResult                                      │
        └──────────────────────────────────────────────────────────────┘
```

Three **background loops** run concurrently and update shared state:

```
  _policy_update_loop      every policy_update_interval_s (or batch full):
                           PPO update on trajectory buffer → bless reference

  _eviction_loop           every eviction_interval_s:
                           registry.candidates_for_eviction() → evict
                           → listener fan-out → router.remove_expert

  _ephemeral_gc_loop       every ephemeral_gc_interval_s:
                           memory_store.gc_expired(EPHEMERAL, now)
```

All loops are **structured concurrency** (`asyncio.TaskGroup`-friendly),
hold at most one lock at a time, and never call into each other directly —
they communicate only through the shared registry, store, and trajectory
buffer.

---

## 4. Free Agent Mechanism (Lifecycle FSM)

Every spawned expert lives in one of five lifecycle states. The agent
registry holds an EMA fitness tracker (default half-life 50 task outcomes)
and gates state transitions on watermark crossings:

```
        ┌──────────┐
        │  SPAWN   │  freshly generated module, capability built
        └─────┬────┘
              │ registry.register(cap)
              ▼
        ┌──────────┐  EMA_fitness ≥ high_watermark
        │PROBATION │ ────────────────────────────────────┐
        └─────┬────┘                                     ▼
              │ fitness < low_watermark                ┌────────┐
              │ for cool_grace_s                       │ ACTIVE │
              ▼                                        └────┬───┘
        ┌──────────┐  fitness ≥ high_watermark             │
        │   COOL   │ ◄─────────────────────────────────────┘
        └─────┬────┘  fitness < low_watermark
              │ age_in_COOL ≥ eviction_grace_s       for cool_grace_s
              ▼
        ┌──────────┐
        │ EVICTED  │  removed from router fleet;
        └──────────┘  module file retained for forensic audit

        ┌──────────┐  manual: meta-evaluator confirms novel
        │ PROMOTED │  high-fitness behaviour
        └──────────┘  → memory.tier == CONSENSUS
```

**Key invariants.**

- The router applies a **probation multiplier** `m ∈ [0.2, 0.5]` to the logit
  of `PROBATION` experts. They are selectable but at reduced probability.
  This guards against a freshly-spawned expert dominating routing before
  fitness is established.
- Registry mutations hold a **single** `asyncio.Lock`; listener fan-out
  happens *after* the lock is released. This prevents reentrancy deadlocks
  if a listener decides to enqueue another mutation.
- `add_expert` and `remove_expert` are **idempotent** on the router; the
  registry can fire spurious notifications without consequence.
- An expert may be `PROMOTED` independently of its current lifecycle state.
  Promotion is a write to CONSENSUS memory; it does not move the expert in
  the active/cool/evicted FSM.

---

## 5. Self-Evaluation (Reward Engine)

The reward engine is intrinsic — the system scores its own outputs without
external supervision — but uses several defences to avoid reward-hacking:

1. **Judge ensemble.** ≥ 3 independent judges score each outcome; the median
   is taken. A single compromised or jail-broken judge cannot move the
   aggregate.
2. **OLS calibration.** Judge scores are linearly calibrated
   `score' = a · score + b` against a small held-out set of human-labelled
   outcomes. Calibration is re-fit periodically.
3. **Project-scoped recall.** When judging, the engine only recalls
   evidence from the project's EPISODIC namespace (NOT CONSENSUS). This
   prevents a generic "everything's fine" rule that lives in CONSENSUS from
   biasing every project's reward.
4. **Multi-source combination.** As above (`R^out + R^eff + R^div − R^cost
   − R^safe`); no single source can saturate the reward.

---

## 6. Sandboxed Execution

The sandbox interface (`AbstractSandbox`) is a narrow contract:
`run(agent_module, request, workdir) -> ExecutionResult` with strict timeout,
memory, CPU, and network constraints.

Two implementations:

| Implementation | Mechanism | Use case |
|---|---|---|
| `LocalSubprocessSandbox` | POSIX `setsid` + `RLIMIT_AS` + `RLIMIT_CPU` + `asyncio.wait_for` + `killpg`. **REJECTS** `network_allowed=True` | Dev/CI only — provides isolation but not security |
| `FirecrackerSandbox` | Firecracker microVM per invocation on GCP N2 nested-virt hosts (see memory note `h1_firecracker_scope`) | Production. Mandatory before any agent that the bootstrap loop generated runs on a real task |

The orchestrator never uses `LocalSubprocessSandbox` in production; the
intended composition is `bootstrap.py` validates with `LocalSubprocess`
(smoke test only), then promotes the module to `FirecrackerSandbox` for
the live PROBATION run.

---

## 7. Numerical defaults (single source of truth)

These values are defaults baked into `OrchestratorConfig` and the router
constructor. They are starting points; expect to retune after the first
1K real-task observations.

| Variable | Default | Where |
|---|---|---|
| `(α, β, γ, δ, ε)` | `(1.0, 0.3, 0.1, 0.5, 10.0)` | `Reward.scalar` |
| Capability embedding dim | 256 | `embedder` |
| Project embedding dim | 128 | `embedder.project_dim` |
| Trajectory batch size for PPO | 64 | `policy_update_min_batch` |
| `ppo_clip` | 0.2 | `OrchestratorConfig` |
| `ppo_kl_target` | 0.02 | `OrchestratorConfig` |
| `ppo_kl_bless_threshold` | 0.01 | `OrchestratorConfig` |
| `ppo_entropy_coef` | 0.005 | `OrchestratorConfig` |
| Reference Polyak `blend` | 0.99 | `OrchestratorConfig.kl_blend` |
| Probation logit multiplier | `[0.2, 0.5]` | router |
| EMA fitness half-life | 50 outcomes | `agent_registry` |
| Eviction low / high watermarks | `0.15` / `0.55` | `OrchestratorConfig` |
| Spawn rate limit | 12 / hour | `OrchestratorConfig` |
| Max active agents | 64 | `OrchestratorConfig` |
| Ephemeral GC interval | 30 s | `OrchestratorConfig` |
| Circuit-breaker window | 300 s | `OrchestratorConfig` |
| Circuit-breaker error rate threshold | 0.35 | `OrchestratorConfig` |
| Circuit-breaker cost budget (per window) | $25 | `OrchestratorConfig` |
| Smoke-test timeout | 10 s | `bootstrap._smoke_test` |
| Spawn timeout | 180 s | `OrchestratorConfig.spawn_timeout_s` |
