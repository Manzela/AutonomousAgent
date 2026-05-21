# Architectural Self-Correction Pass

The original user prompt closed with:

> *"Analyze this entire prompt, perform a self-correction pass to eliminate
> architectural bottlenecks or single points of failure, and begin generation."*

This document enumerates the **8 risks** that the self-correction pass
identified, the **mitigation** that was incorporated into the seed
implementation, and the **residual risk** that remains for the integration
team to think about.

---

## R1. MoE router as single point of failure

**Risk.** The router is on every task's critical path. If its weights
diverge (catastrophic PPO step), every task fails-bad simultaneously.

**Mitigation.**

1. **Deterministic fallback.** `AbstractMoERouter` exposes
   `act_deterministic(state)` which uses `argmax` over the reference policy
   `π_ref` instead of sampling from `π_θ`. The orchestrator calls this when
   the live policy's KL divergence to `π_ref` exceeds the configured
   threshold (default 0.05).
2. **Checkpointed reference policy.** `π_ref` is Polyak-averaged at
   `blend = 0.99` and saved to disk via `router.snapshot()` after every
   update cycle that passes `ppo_kl_bless_threshold`. The router can
   hot-swap to a known-good snapshot without restart.
3. **Trip-wire revert.** If a single PPO update produces `KL_after > 0.05`,
   `W_r` is rolled back to its pre-update values and the trajectory batch
   is dropped. The system logs `policy.update_reverted` and continues.

**Residual risk.** A correlated batch of *all* judges being compromised
(reward hacking) can still walk the reference policy somewhere bad over
many bless cycles. Addressed at R3.

---

## R2. Cross-project memory contamination

**Risk.** A bug in the memory store, the embedder, or any single agent
implementation leaks one project's data into another's recall window.
For a system handling N projects, the failure mode is N²
information-leakage events.

**Mitigation.** Five-layer defence-in-depth (full detail in
`01-phase1-mathematical-spec.md` §2.3):

1. **Schema invariant** at construction time (`MemoryRecord` Pydantic
   validator: `CONSENSUS ⟺ project_id is None`).
2. **HMAC namespace token** derived from a master secret. Forgeable only
   with the master secret.
3. **Read-side scope filter** in the store — empty scopes are rejected.
4. **Defence-in-depth assertion** in the VCM on every returned record;
   handle is invalidated on violation.
5. **Physical sharding** by `hash(project_id) mod N` for EPISODIC tier in
   production.

**Residual risk.** A compromise of the master secret defeats layer 2 (and
through it, layer 4). The master secret must live in the secrets store
(`secrets/`) with sops-encryption per the existing project convention and
be rotated on the project's documented cadence.

---

## R3. Reward hacking

**Risk.** Intrinsic rewards are notoriously easy to game. An expert that
learns to produce outputs the judge specifically likes (rather than
outputs that are actually good) can drag the entire fleet toward a
degenerate local optimum.

**Mitigation.**

1. **Decomposed reward**:
   `R = α·R^out + β·R^eff + γ·R^div − δ·R^cost − ε·R^safe`. No single
   source can saturate the scalar; gaming `R^out` is offset by
   `−ε·R^safe` (with `ε = 10`).
2. **Judge quorum.** ≥ 3 judges, median aggregation. A single jail-broken
   judge cannot move the median.
3. **KL constraint.** Even a high-reward direction cannot be exploited
   faster than the trust-region width allows. Pathological policy shifts
   trip the wire (R1) and revert.
4. **Periodic OLS calibration.** Judge scores are linearly calibrated
   against a small human-labelled set; sustained drift in `(scale, bias)`
   is visible in telemetry and operationally alerts.

**Residual risk.** All defences assume the judges are independent. If the
judges come from the same model family with the same training data, they
share blind spots. Production should use a heterogeneous judge ensemble
(at minimum: different model families; ideally: a programmatic verifier
for the subset of tasks where one exists).

---

## R4. Cold start for newly-spawned experts

**Risk.** A freshly-generated expert has no fitness history. Greedy
routing would never select it; ε-greedy alone selects it at random and
penalises the policy for the exploration cost.

**Mitigation.**

1. **Capability-vector warm start.** The new expert's `description + tags`
   are embedded to produce `e_new`. Initial probability mass comes from
   the bilinear scorer using `e_new` — i.e., the expert's *announced*
   capability already steers some routing probability toward it without
   any history needed.
2. **PROBATION lifecycle.** New experts land in `PROBATION` and have
   their logit multiplied by `m ∈ [0.2, 0.5]`. They are *selectable* but
   at reduced probability, so the fleet doesn't bet the farm on a
   capability that hasn't been validated.
3. **LoRA-style adaptation (optional, deferred).** If a generated expert
   shares ≥80% of its capability vector with an existing high-fitness
   expert, its initial fitness EMA can be seeded from that neighbour
   (the implementation slot is reserved; not enabled by default to keep
   priors honest).

**Residual risk.** If the embedder is bad (poor separation between
capability vectors), warm-start does not help and PROBATION period
becomes a pure cost. The 256-dim hashing embedder is adequate for
hundreds of agents; once the fleet grows past ~500 active agents, replace
with `SentenceTransformerEmbedder` (the swap is one constructor change).

---

## R5. Sandbox escape

**Risk.** Bootstrap-generated agents run arbitrary Python. A
sandbox escape — even by accident — lets an agent touch the orchestrator
process, write outside its workdir, or talk to the network.

**Mitigation.**

1. **Firecracker microVMs for production.** Each invocation runs in a
   throwaway microVM with a read-only rootfs, an ephemeral workdir, no
   network egress by default, and a syscall denylist
   (see memory note `h1_firecracker_scope`).
2. **Capability-scoped FS access.** The agent receives `ctx["workdir"]`
   and only that path is writable. The bootstrap contract bans `open()`
   to force this discipline at the source-code level.
3. **Default no-net.** The Firecracker network namespace has zero
   egress routes unless the orchestrator explicitly opens them per-call
   via a TUN/TAP pair with `iptables` filtering.
4. **Static scan at generation time.** `bootstrap._scan_for_banned`
   rejects any module whose source contains `eval(`, `exec(`,
   `subprocess`, `socket`, `urllib`, `requests`, `os.system`, etc., before
   it ever runs.

**Residual risk.** `LocalSubprocessSandbox` (the only sandbox shipped in
this seed) is **not** production-grade. It uses POSIX `rlimits` which
provide isolation but not security. The seed module's class docstring
explicitly marks it as dev/CI only and the orchestrator config must
refuse to start in production mode if `sandbox is LocalSubprocessSandbox`.

---

## R6. Runaway self-evolution

**Risk.** A bug or adversarial prompt makes the spawn callback fire on
every task. The fleet inflates, the cost balloon pops the budget, and
the policy thrashes.

**Mitigation.**

1. **Spawn rate limiter.** `max_spawned_agents_per_hour = 12` by default,
   enforced under `_spawn_lock` in the orchestrator hot path.
2. **Fleet cap.** `max_active_agents = 64` by default; once reached, no
   more spawns until evictions happen.
3. **Cost circuit breaker.** Rolling-window cumulative spend > budget →
   breaker trips and refuses new work until cooldown.
4. **Fitness-gated promotion.** Spawning a new expert does not automatically
   promote it. It must clear the high watermark in PROBATION for `ACTIVE`,
   and the *meta-evaluator* in CONSENSUS for `PROMOTED`.
5. **Meta-evaluator.** A separate judge (not in the per-task ensemble)
   reviews the trajectory of every promotion to CONSENSUS. Drift is
   visible there.

**Residual risk.** None of the rate limits help if a misconfigured
production deploy starts with `max_spawned_agents_per_hour = 100000`.
Config schema uses Pydantic v2 `extra="forbid"` + explicit bounds via
`Annotated[int, Field(le=10_000)]` so obvious typos fail at startup.

---

## R7. Async deadlock

**Risk.** Many concurrent tasks, multiple locks (`spawn`, `stats`,
`trajectory`), background loops, listener callbacks — the classic async
deadlock recipe.

**Mitigation.**

1. **Structured concurrency.** Background loops are managed under
   `asyncio.TaskGroup` (Python 3.11+); cancel propagates cleanly.
2. **Lock ordering.** Every code path acquires at most one lock at a time.
   When two are needed (rare), the order is fixed:
   `_spawn_lock < _stats_lock < _trajectory_lock`.
3. **Listener fan-out outside locks.** Registry mutations release the
   lock *before* firing listeners. A listener that calls back into the
   registry will not deadlock.
4. **Single trajectory buffer.** Producers append under `_trajectory_lock`,
   the policy update loop drains under the same lock. No multi-producer
   multi-consumer queue with hidden ordering.

**Residual risk.** Custom listener registered by external code that
re-enters the orchestrator with a blocking call would still hang. The
registry's docstring requires listener callables to be coroutine-safe
and non-blocking; this is a contract, not an enforcement.

---

## R8. Vector index contamination

**Risk.** Even with all the schema/HMAC defences, the underlying vector
index could leak rows across namespaces — e.g., an HNSW build that doesn't
honour scope tags. Index-level bugs are the hardest to catch because they
look like correct retrievals.

**Mitigation.**

1. **Per-namespace logical scopes.** The store's `search()` takes an
   explicit scope set. Implementations must filter inside the index call,
   not after.
2. **Defence-in-depth assertion** at the VCM boundary (layer 4 of the
   isolation stack) catches index bugs. A row returned from the store
   whose `project_id` is outside the handle's allowed set raises
   `NamespaceContamination` and invalidates the handle.
3. **Adversarial property test.** The integration plan should ship a
   property test that constructs N projects, writes 1K records per project
   with deliberately overlapping embeddings, and asserts no cross-project
   recall over 10K random queries. (Not included in this seed; tracked
   in `INTEGRATION.md`.)

**Residual risk.** A bug that *occasionally* leaks (e.g., on every Nth
query) might evade the property test if N is small. Recommend keeping
the assertion at layer 4 active in production, not stripping it for
performance.
