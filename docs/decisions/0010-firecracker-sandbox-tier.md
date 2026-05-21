# 0010. Separate `firecracker_sandbox` tier for high-frequency untrusted code execution

**Status:** Accepted
**Date:** 2026-05-21
**Decision-makers:** Daniel Manzela (+ Claude Opus 4.7)

## Context

ADR-0003 (`docs/decisions/0003-tiered-sandboxing-strategy.md`) established a
five-tier sandbox routing table in `config/toolsets.yaml`, with `cloud_sandbox`
nominally pointing at Modal/Daytona ephemeral microVMs for arbitrary code
execution. Three things have shifted since 0003 landed:

1. **The `cloud_sandbox` slot has zero callers in HEAD.** The defensive route
   (`run_python`, `run_javascript`, `exec_code`, `code_interpreter`) exists so
   that the router does not silently fall through to `shell_sandbox` if the
   model emits those names. `grep -rln "run_python\|exec_code\|code_interpreter"
   lib/ hermes-agent/` returns empty. The tier is a placeholder, not a live
   subsystem.

2. **A2A peer-exec is the realistic forcing function.** The A2A integration
   spike (`audit/2026-05-21-a2a-integration/spike-plan.md`) defines a
   `a2a_peer_*` tool family in which the peer hands us Python to execute on
   our infrastructure. Those calls are (a) high-frequency in the steady state,
   (b) come from a party we do not control, and (c) need <100ms invocation
   latency at the 95th percentile. Modal's cold-boot tax (~800ms) and
   per-call pricing both fail this brief above ~40 invocations per hour.

3. **The 2026-05-20 architecture-research gap analysis surfaced the tier
   distinction explicitly.** `audit/2026-05-20-architecture-research-gap-analysis/findings.md`
   §Component 9 noted that the research doc described "Firecracker / gVisor /
   WASM" as if they were interchangeable, when in practice they have
   materially different cost, latency, and trust profiles. The decision was
   deferred at the time pending a concrete deployment design.

That concrete design now exists in `audit/2026-05-21-h1-firecracker/` (six
artifacts: `findings.md`, `tool-classification.md`, `architecture.md`,
`rollout-plan.md`, `risks-and-open-questions.md`, `gcp-confirm.md`).

The question this ADR resolves: *how should that design be wired into the
existing tier taxonomy* — as a replacement of `cloud_sandbox`, or as a new
tier alongside it?

## Decision

We will introduce a new `firecracker_sandbox` tier in `config/toolsets.yaml`
*alongside* the existing `cloud_sandbox` tier, not as an in-place
replacement. Specifically:

1. **A new tier `firecracker_sandbox` is added to the taxonomy**, backed by
   a self-managed pool of warm microVMs on a dedicated GCP Compute Engine
   N2 host with nested virtualization enabled (`enable_nested_virtualization`).
   Control plane runs on Cloud Run (`fc-control`, IAP-gated); execution
   runs on the N2 host (`fc-host`); a Cloud Memorystore Redis basic 1GB
   instance backs the pool-state ledger. Pool sizing P1 is 4 warm VMs with
   burst to 16 (~$265/mo steady-state). Architecture details:
   `audit/2026-05-21-h1-firecracker/architecture.md`.

2. **The existing `cloud_sandbox` tier remains in place** as the route for
   any caller that wants Modal/Daytona semantics (low-frequency, no
   credentialed callout, one-off Jupyter execution from an operator).
   `cloud_sandbox` is not deprecated and not deleted.

3. **Tool-routing decisions follow trust × side-effect classification**,
   not provider preference:
   - Untrusted + any side-effect → `firecracker_sandbox`
   - Trusted code (Anthropic-written, repo-resident) → lighter tiers per
     existing rules
   - Default unknown tool → stays `shell_sandbox` (NOT firecracker; auto-
     escalation would be a cost foot-gun at ~$265/mo per always-on pool)

4. **A2A peer-exec (`a2a_peer_*`) is the first real consumer.** Firecracker
   P1 (foundation: terraform + warm pool + control-plane API) can ship
   independently. P2 (first consumer wiring) gates on A2A P1 ship per the
   A2A spike plan dependency chain.

5. **Kernel CVE response is treated as a launch-gating capability**, not
   a post-launch nice-to-have. P3 productionization is blocked on a drilled
   patch runbook (`audit/2026-05-21-h1-firecracker/rollout-plan.md` §P3.2)
   with a <24h MTTR target from CVE disclosure to deployed patch.

## Consequences

### Positive

- **A real isolation boundary for untrusted code-exec.** Firecracker microVMs
  + jailer + private VPC + egress allowlist + per-invocation rootfs wipe is
  defense-in-depth where today there is nothing live behind `cloud_sandbox`.
- **Honest test names.** `test_firecracker_isolation` describes the
  boundary the test exercises (microVM); `test_cloud_sandbox_isolation`
  would have described the historical sourcing (Modal). Tier names now
  describe isolation, not provenance.
- **Dual-use future preserved.** Modal stays available for the workloads
  it suits (low-frequency, one-shot, operator-initiated). We are not
  forced to migrate use-cases to Firecracker that do not benefit from
  the latency/cost profile.
- **A2A unblocked at the infrastructure layer.** A2A peer-exec without a
  microVM tier would either (a) ship a security regression, or (b) be
  forced onto Modal at a sustained cost premium. Neither is acceptable.
- **Tier routing stays data-driven.** `lib/toolset_router.py:44-49` first-
  match-wins glob matching already supports new tiers without code
  changes — adding `firecracker_sandbox` is a `config/toolsets.yaml` edit
  + the GCP infrastructure, not a refactor.

### Negative

- **Operational burden of a self-hosted microVM pool.** Kernel pinning,
  vmlinux rebuilds, jailer config audits, warm-pool replenishment,
  nested-virt quirks on GCP — all of these become a recurring ops surface
  that did not exist when `cloud_sandbox` was a Modal-shaped placeholder.
- **Kernel CVE response is now load-bearing.** A missed advisory is a
  sandbox-escape risk. The P3.2 runbook drill is mandatory before any
  hostile-peer workload (open A2A federation in year 2) is routed here.
- **Cost floor of ~$265/mo even at zero traffic.** Warm pools mean
  always-on infrastructure; a busy A2A integration that pushes us above
  ~40 invocations/hour amortizes this comfortably, but a slow rollout
  pays the full cost without proportionate benefit. P1 capacity sizing
  errs small (4 warm VMs) to reduce this exposure.
- **Two tier names to teach.** Onboarding documentation and routing
  audits now have to explain `cloud_sandbox` vs `firecracker_sandbox` and
  when each fits. `audit/2026-05-21-h1-firecracker/tool-classification.md`
  is the canonical reference; ADR-0003 will be cross-linked to point here.

### Neutral

- **No code-execution surface is *added* by this ADR alone.** The
  `run_python` / `exec_code` route currently has zero callers; this ADR
  rewires *where* such calls would land if they appeared. Actual
  code-exec tools land with A2A P1 per the spike plan dependency chain.
- **Existing sandbox tier observability already covers Firecracker.**
  OTel spans emit `sandbox.tier=...` per ADR-0003; the `firecracker_sandbox`
  value drops in without a tracing schema change.
- **Trust × side-effect routing is now the canonical model.** Future tier
  decisions (gVisor, WASM, gVisor-on-Firecracker hybrids) should
  position themselves on the same axes rather than re-arguing "should
  we use X or Y."

## Alternatives considered

### Option A: Stay on Modal/Daytona for all untrusted code-exec

- Pros: No infrastructure to operate; Modal absorbs kernel-CVE response;
  zero fixed cost at zero traffic.
- Cons: Cold-boot latency (~800ms) blows the A2A peer-exec latency
  budget at the 95th percentile; per-call pricing inverts above
  ~40 invocations/hour; outage of Modal blocks A2A peer-exec entirely;
  cross-org trust boundary widens (Modal now sees every untrusted
  payload).
- Why rejected: A2A peer-exec is the workload that forces the decision,
  and it fails on every axis Modal optimizes for. Keeping Modal as a
  fallback (Option C of `architecture.md` §6) preserves the upside
  without the lock-in.

### Option B: Replace `cloud_sandbox` in place with Firecracker

- Pros: One tier to teach; no naming drift; routing config stays small.
- Cons: Loses the dual-use intuition (`cloud_sandbox` historically meant
  "outsourced compute" — readers of `config/toolsets.yaml` will mis-model
  the new semantics); deletes a useful escape hatch for workloads where
  Modal genuinely is the right answer; test names become misleading
  (`test_cloud_sandbox_isolation` boots a microVM).
- Why rejected: The cost of one extra tier name is one paragraph in
  `tool-classification.md`. The cost of conflating two materially
  different boundaries is recurring mis-routing and audit confusion.

### Option C: gVisor-only (no Firecracker, no Modal)

- Pros: Single isolation mechanism; lighter than full microVM; runs on
  existing infrastructure without nested-virt requirement.
- Cons: gVisor's syscall-emulation surface has a different (smaller but
  non-empty) CVE history; the Google-maintained release cadence is
  slower than Firecracker's; for the A2A high-frequency workload we
  would still want a warm-pool layer, which negates most of the
  "lighter" claim.
- Why rejected: gVisor is a viable secondary tier (and may be added
  later as `gvisor_sandbox` for a different workload), but the H1
  forcing function is A2A peer-exec, where the Firecracker performance
  + isolation profile is the better fit. This ADR does not preclude a
  future gVisor tier.

## References

- **Audit packet:** `audit/2026-05-21-h1-firecracker/` (6 artifacts)
  - `findings.md` — current state of `cloud_sandbox` (zero callers,
    defensive placeholder); existing tier router design
  - `tool-classification.md` — trust × side-effect routing table
  - `architecture.md` — GCP topology, pool sizing, network posture,
    boot strategy, identity scheme
  - `rollout-plan.md` — P1/P2/P3 phasing + the P3.2 kernel-CVE
    response runbook
  - `risks-and-open-questions.md` — R1 (kernel-CVE response speed),
    R2 (A2A multi-party trust), open questions
  - `gcp-confirm.md` — nested-virt pre-flight operator probes
- **Parent ADR:** `docs/decisions/0003-tiered-sandboxing-strategy.md`
  (tier taxonomy this ADR extends)
- **Related ADRs:**
  - `docs/decisions/0005-self-rl-pipeline-architecture.md` —
    trajectory-shipper handoff (A2A peer-exec traces feed the same
    pipeline)
  - `docs/decisions/0009-judge-panel-as-rlaif.md` — RLAIF substrate
    (peer-exec verdicts will be judge-evaluated)
- **Config + code:**
  - `config/toolsets.yaml:6-12` — `cloud_sandbox` route (preserved);
    `firecracker_sandbox` route added in P1
  - `lib/toolset_router.py:44-49` — first-match-wins router (no code
    change required)
  - `docs/architecture/sandbox-tiers.md` — five-tier taxonomy doc;
    will be updated to a six-tier taxonomy in P1
- **Forcing function:** `audit/2026-05-21-a2a-integration/spike-plan.md`
  (A2A peer-exec P1 dependency on `firecracker_sandbox` P1)
- **Memory pointer:** [[h1_firecracker_scope]] in project memory
