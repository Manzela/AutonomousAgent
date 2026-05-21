# H1 Firecracker — Risks and Open Questions

**Purpose:** Surface what we do not yet know, what could go sideways, and what decisions are still open. Each item has a recommended disposition; items left as "open" require operator/architect ruling before the corresponding rollout phase.

---

## Risks (ranked by severity × likelihood)

### R1 — Kernel CVE response is slower than industry expectations [HIGH × MED]

**What.** Firecracker depends on a pinned Linux kernel (vmlinux). Critical KVM CVEs (CVE-2024-XXXX class) require rebuilding rootfs + vmlinux and rotating the pool. If our P3.2 runbook says "<24h from CVE disclosure to deployed patch" but our actual mean-time-to-patch is 7 days, we are running known-vulnerable VMs.

**Likelihood.** Medium. Historically firecracker-microvm has ~1 critical advisory per 18 months. Likelihood of *missing one* in our first year is non-trivial because the runbook hasn't been drilled.

**Severity.** High in the limit (sandbox-escape → host compromise → exfiltration of every customer payload that's ever executed). Low in the median (most kernel CVEs require local-only access that microVM isolation already prevents at the privilege-escalation layer).

**Mitigation.**
- P1.5: subscribe to `firecracker-microvm/firecracker` security advisories before pool goes live
- P3.2: drill the patch runbook on first-quarter cadence
- Inverted: until P3.2 is drilled, consider H1 "P2 only" and continue Modal for any high-risk peer code

**Disposition.** Accept the risk for P1/P2. Block P3 productionization on first drill completion.

### R2 — A2A peer code execution introduces multi-party trust [HIGH × MED]

**What.** A2A peers are by definition not under our control. A peer that hands us Python code can probe for our pool's specific Firecracker version, jailer config, egress allowlist. Probing → CVE → escape. The H1 Firecracker tier is the *only* thing between a hostile A2A peer and our infrastructure.

**Likelihood.** Medium, *conditional on H1 launch.* The first 6 months of A2A integration will be with Google + a small set of vetted partners (per the A2A spike plan). Likelihood of hostile-peer activity in that window is low. Likelihood of hostile-peer activity in year 2 (open A2A federation) is much higher.

**Severity.** High — same as R1.

**Mitigation.**
- Per-peer rate limits at fc-control (e.g., 100 invocations/hour/peer in P2)
- Per-peer rootfs (different peers get different rootfs base images so a CVE in one rootfs doesn't auto-compromise another peer's invocations)
- Egress allowlist is per-tool, NOT per-peer — but peer identity is logged in every span so post-incident forensics can attribute
- Quarterly red-team: a security engineer plays "hostile peer", attempts known escape patterns against staging fc-host

**Disposition.** Open question: do we require a peer-vetting process (legal agreement, security questionnaire) before any peer is added to the A2A allowlist? **Recommendation: yes.** Defer the policy decision to the A2A spike's `auth-design.md`.

### R3 — Cost forecast wrong by 3–10× [MED × MED]

**What.** Architecture estimate is ~$265/mo for the 4-warm-VM pool. Real-world hidden costs include: Cloud NAT egress at unexpected volume; Cloud Memorystore vs. cheaper Redis-on-VM; n2-standard-8 actual utilization vs. forecast; sustained-use discount eligibility.

**Likelihood.** Medium. First-time GCP infrastructure cost is consistently underestimated by 1.5–2×; pool-mode infrastructure has fewer surprises than serverless, so floor is ~$300/mo, ceiling realistic ~$600/mo.

**Severity.** Medium. Even at 3× we're at $800/mo for a hardened sandbox tier — comparable to Modal at modest scale. Decision threshold is more "is this worth doing" than "is this affordable."

**Mitigation.**
- P0: cost spreadsheet update with real GCP calculator numbers as of decision date
- P1.5: first month bill is the ground-truth check. If >2× forecast, halt P2 and re-evaluate.

**Disposition.** Accept the uncertainty. Use first-month bill as the gate.

### R4 — Vendor lock-in via fc-control coupling [LOW × HIGH]

**What.** If we write a custom fc-control service with deep Firecracker SDK knowledge, switching to a different microVM tech (gVisor, Kata) becomes a multi-week rewrite. ADR-0003 wisely kept the tier definitions tech-agnostic; we may undo that benefit at the implementation layer.

**Likelihood.** High *that we will couple*; LOW that we will need to switch. Firecracker is broadly adopted (AWS Lambda, Fly.io) and unlikely to be deprecated.

**Severity.** Low — even a 2-week rewrite to switch microVM tech is recoverable.

**Mitigation.**
- fc-control gRPC API is intentionally generic (`Invoke(tool, args)` — no microVM-specific fields exposed)
- Firecracker-specific config (jailer, vmlinux, ext4 rootfs) is in `deploy/sandboxes/firecracker/`, not in the gRPC interface

**Disposition.** Document the abstraction discipline in ADR-0010. Re-evaluate at P3.

### R5 — Operator skill gap on KVM/Firecracker [MED × HIGH]

**What.** This team has not run Firecracker in production. Docker we know; KVM-level debugging (perf, tracing, kernel panics) we do not. When something breaks at the hypervisor layer, mean-time-to-recovery will be hours not minutes.

**Likelihood.** High in year 1; tapers as the team learns.

**Severity.** Medium — outages are bounded by the pool's blast radius (one tier, not the whole agent).

**Mitigation.**
- P1.4: operator (separate from implementer) does the pool bring-up to spread knowledge
- P2.3 runbook explicitly covers KVM-level debugging primitives (`virsh dumpxml`, `firecracker --version`, kernel ring buffer reading via `dmesg | grep KVM`)
- P3.1 multi-host promotion is gated on at least 2 operators having driven a real incident response

**Disposition.** Accept the risk. Pace P3 by skill acquisition.

### R6 — Nested-virtualization performance penalty [LOW × HIGH]

**What.** Nested virt (KVM-inside-KVM, which is what we get when running Firecracker on a GCP VM) carries a 10–30% performance penalty vs. bare-metal Firecracker. For code-exec workloads this is usually negligible; for CPU-bound payloads it matters.

**Likelihood.** Will manifest. Probably won't matter.

**Severity.** Low — workload-dependent.

**Mitigation.**
- Benchmark in P1.4 with realistic workload (Python script that does file I/O + HTTP + light compute) — measure cold-boot, exec wall-clock, vs. baseline
- If the penalty is >50% for the median workload, escalate to operator with: bare-metal option, sole-tenant N2D, or accept

**Disposition.** Measure, then decide.

### R7 — Phase 0a VM is shared between Hermes + fc-host [MED × LOW]

**What.** If P1.3 puts fc-host on the same Compute Engine instance as the Hermes orchestrator (to save money), a kernel exhaustion attack against Firecracker can starve the orchestrator. The defense relies on the cgroup limits in jailer, which are robust but not absolute.

**Likelihood.** Low — cgroup memory.max is hard.

**Severity.** Medium — orchestrator slowdown affects user-visible latency on every other task.

**Mitigation.**
- P1.3 provisions fc-host as a *separate* Compute Engine instance, not shared with the Phase 0a Hermes VM.

**Disposition.** Mitigated by architecture. No additional action needed.

### R8 — Sandbox escape via shared kernel state [HIGH × LOW]

**What.** Even with per-invocation fresh VMs, kernel-level shared state on fc-host (page cache, network buffers, scheduler state) could leak between concurrent microVMs running on the same host. Side-channel CVEs (Spectre, L1TF, MDS) target exactly this.

**Likelihood.** Low — kernel mitigations + microcode updates are kept current on managed GCP infrastructure.

**Severity.** High in worst case (cross-tenant leakage).

**Mitigation.**
- Keep GCP image up to date (auto on managed image families)
- Disable hyperthreading on fc-host (`echo 0 > /sys/devices/system/cpu/cpu1/online` for each SMT sibling) — costs throughput, gains side-channel defense
- For highest-sensitivity peers in year 2, route to dedicated fc-host per peer

**Disposition.** Defer disabling hyperthreading to P3 (cost vs. risk; not warranted for vetted peer set in P2).

---

## Open questions

### Q1 — Separate `firecracker_sandbox` tier vs. replace `cloud_sandbox` in place?

**Recommendation:** Separate tier. See `architecture.md` §2 + ADR-0010. Reason: backward-compatibility for any future Modal-callers, plus honest test naming.

**Decision needed by:** P0 (ADR-0010 merge).

### Q2 — Language for fc-control: Go vs Python?

**Tradeoff:** Go has the official firecracker-go-sdk; Python (firecracker-python or hand-rolled HTTP client over Firecracker's local socket) is closer to the rest of our stack.

**Recommendation:** Go. The firecracker-go-sdk is upstream's reference SDK; Python alternatives are community projects with patchy maintenance. The team is small enough that one Go service is acceptable; the alternative (Python + community SDK) carries multi-year maintenance risk.

**Decision needed by:** P0 (ADR-0010).

### Q3 — Per-peer rootfs (R2 mitigation) vs single rootfs?

**Tradeoff:** Per-peer rootfs prevents one CVE from auto-compromising all peers but multiplies image storage cost and build pipeline complexity by N peers.

**Recommendation:** Single rootfs for P1/P2 (≤5 peers, all vetted); revisit at P3 once the peer set is opened.

**Decision needed by:** P2 (when A2A peer-exec goes live).

### Q4 — Should fc-control be deployed as Cloud Run or as a sidecar on fc-host?

**Tradeoff:** Cloud Run separates control plane from data plane (good for blast radius); sidecar is simpler (one machine to manage).

**Recommendation:** Cloud Run. Separation is worth the $5/mo. Operator complexity is low.

**Decision needed by:** P0 (ADR-0010).

### Q5 — Peer-vetting process: required before any A2A peer is whitelisted?

**See R2.** **Recommendation:** Yes. Defer to A2A spike plan.

**Decision needed by:** A2A spike P0 (sibling audit).

### Q6 — What's the right pool size for steady-state P1?

**Currently:** 4 warm. Based on guess that we'll see ≤10 concurrent invocations.

**Recommendation:** 4 for P1.4, instrument utilization metrics, re-evaluate at P2 exit. Plan headroom up to 16.

**Decision needed by:** P1.3 (pool bring-up).

### Q7 — Do we expose Firecracker as a service to other teams in the org?

**Currently:** No — H1 is single-team.

**Recommendation:** Stay single-team for year 1. Don't conflate "build a sandbox for our agent" with "build a multi-tenant sandbox PaaS"; latter is 10× the work.

**Decision needed by:** Whenever another team asks (no current ask).

### Q8 — Kernel update policy: pinned vs. floating?

**Tradeoff:** Pinned (manual updates) means reproducibility + lag; floating (auto-update) means freshness + occasional surprise.

**Recommendation:** Pinned with a quarterly review + ad-hoc updates for security advisories (the P3.2 runbook).

**Decision needed by:** P0 (ADR-0010).

### Q9 — How do we handle long-running invocations (>60s)?

**Today's architecture** assumes execution is <60s (sized by cgroup CPU + the synchronous gRPC pattern). Some A2A peer payloads (training simulation, large data transform) could exceed.

**Recommendation:** Hard cap at 60s in P2. Long-running workloads should not use the synchronous `Invoke` API — they should use a future `InvokeAsync` that returns a handle and streams logs back. Track as a P3 enhancement.

**Decision needed by:** P3 only (P2 explicitly excludes long jobs).

### Q10 — Disaster recovery: pool corruption / total loss?

**Today:** Stateless. Re-running terraform reconstructs the pool from declarative config.

**Open:** Do we need a snapshot of "last known good rootfs + vmlinux" stored outside the active pool, in case Artifact Registry is corrupted?

**Recommendation:** Mirror rootfs + vmlinux to a cross-region GCS bucket on every build (cents/mo). Defer until P3.

**Decision needed by:** P3.
