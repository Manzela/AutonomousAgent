# H1 Firecracker — Rollout Plan

**Purpose:** Phased materialization of the Firecracker microVM tier. Each phase has a verifiable exit gate and a rollback path.

**Pre-requisites (do not start P0 until these are true):**
- ✅ Phase 0a GCP migration complete (VM + IAP + Artifact Registry live) — per `audit/2026-05-20-state-of-the-repo-v2/` and project memory
- ✅ `terraform/phase-0a-gcp/model-armor/` applied (or explicitly deferred per F37 disposition)
- ⚠️ A2A spike plan accepted (`audit/2026-05-21-a2a-spike-plan/`) — H1's first real consumer is the A2A peer-exec tool; spinning up Firecracker without a consumer is premature optimization

---

## P0 — Decision gate (1 day)

**Goal:** Lock the design + decision so engineering can proceed without ambiguity.

| Deliverable | Owner | Exit gate |
|---|---|---|
| ADR-0009: "Add Firecracker sandbox tier" | Architect | Merged to `docs/decisions/` |
| Cost model spreadsheet (updates `architecture.md` §6 numbers against real GCP pricing as of decision date) | Operator | Numbers approved by budget owner |
| Decision: separate tier vs replace `cloud_sandbox` | Architect | Recorded in ADR (recommendation: separate, per `architecture.md` §2) |
| Decision: pool size at P1 | Operator | Recorded in ADR (recommendation: 4 warm) |

**Exit:** ADR-0009 merged. **No code or terraform yet.**

**Rollback:** Trivial — un-merge the ADR.

---

## P1 — Foundation (1–2 weeks)

**Goal:** Make `firecracker_sandbox` a real tier that the router knows about, with a working but **un-invoked** microVM pool.

### P1.1 — Router-level support (1 day)

- [ ] Add `firecracker_sandbox` to `config/toolsets.yaml` preamble
- [ ] Add new route mapping `run_python` + `a2a_peer_*` to `firecracker_sandbox` (per `tool-classification.md` §4)
- [ ] Add tests in `tests/unit/test_toolset_router.py` (3 new tests per `tool-classification.md` §7)
- [ ] **Verification:** `pytest tests/unit/test_toolset_router.py -v` 100% pass

### P1.2 — Image build pipeline (3–5 days)

- [ ] Create `deploy/sandboxes/firecracker/rootfs/Dockerfile` — minimal Debian + Python 3.13 + Node 20, no compiler
- [ ] Create `deploy/sandboxes/firecracker/rootfs/build.sh` — Docker → ext4 image conversion
- [ ] Create `deploy/sandboxes/firecracker/kernel/` — pinned vmlinux binary + manifest (kernel version, source SHA)
- [ ] CI job: build rootfs on every push, publish to Artifact Registry tagged with git SHA
- [ ] **Verification:** `make firecracker-rootfs` produces a bootable image; `firecracker --kernel ... --rootfs ...` from a developer laptop boots and returns control in <2s

### P1.3 — GCP provisioning (2–3 days)

- [ ] Add to `terraform/phase-0a-gcp/main.tf`:
  ```hcl
  resource "google_compute_instance" "fc_host" {
    machine_type = "n2-standard-8"
    advanced_machine_features { enable_nested_virtualization = true }
    ...
  }
  ```
- [ ] Add VPC subnet + firewall rules per `architecture.md` §3 (default-deny egress + allowlist via Cloud NAT)
- [ ] Add Cloud Memorystore Redis (`google_redis_instance`, basic tier 1GB)
- [ ] `terraform plan` → review → `terraform apply` (operator gate)
- [ ] **Verification:** SSH to fc-host, run `kvm-ok` → "INFO: /dev/kvm exists"; firecracker binary present + executable

### P1.4 — fc-control service (3–5 days)

- [ ] Implement minimal `services/fc-control/` (Go or Python — language choice in ADR-0009)
- [ ] gRPC API: `Invoke`, `PoolStatus`, `Drain`
- [ ] Pool manager: maintains N warm VMs, replaces on use
- [ ] Deploy to Cloud Run with IAP
- [ ] **Verification:** `grpcurl fc-control.run.app Invoke` from a developer machine (authenticated via IAP) successfully boots a VM, runs `echo hello`, returns stdout

### P1.5 — Pool active, no real invocations

- [ ] Pool runs idle (4 warm VMs)
- [ ] **Verification:** Cloud Monitoring shows `firecracker.pool.warm_count = 4`, `firecracker.invocations.total = 0`, costs match `architecture.md` §6 estimate (±20%)

**Exit:** Pool is live and observable but no real workload is routed to it. **Rollback:** `terraform destroy` of the fc-host + fc-control + Memorystore. Router config change is removable (one PR revert).

---

## P2 — First consumer (2 weeks; concurrent with A2A P1)

**Goal:** A real production tool routes through Firecracker. The consumer is the A2A peer-exec tool from the A2A spike.

### P2.1 — Wire the A2A peer-exec tool

- [ ] Implement `lib/a2a/peer_exec.py` (per A2A spike) — receives `(peer_id, code)` and invokes via fc-control
- [ ] Register `a2a_peer_exec` and `a2a_peer_python` as tools in `hermes-agent/tools/`
- [ ] **Verification:** Integration test: spawn a local A2A peer, send code, assert it executes in Firecracker (verify via OTel span `firecracker.invoke` presence)

### P2.2 — Failure-mode validation

- [ ] Test F23 path: deliberately attempt cgroup breach inside microVM → assert F23 dispatch, halt
- [ ] Test F31 path: attempt egress to non-allowlisted host → assert F31 dispatch, halt
- [ ] Test F2 path: exhaust pool with concurrent invocations → assert queue → assert backoff
- [ ] **Verification:** All three F-codes appear in OTel traces under the corresponding test names

### P2.3 — Operator runbook

- [ ] Write `audit/2026-05-21-h1-firecracker/runbook.md` covering:
  - First-3-minute checklist on F23 / F31 dispatch
  - Pool drain procedure (graceful + emergency)
  - Kill-switch (revoke fc-control IAM, forces all invocations to fail-loud)
  - VM forensics (snapshot a microVM's memory + filesystem before teardown for post-mortem)
- [ ] **Verification:** Operator (different person than implementer) walks through the runbook on a staging F23 simulation; completes successfully without code review

**Exit:** A2A peer-exec is live in production behind Firecracker. **Rollback:** Flip A2A tool registry to "off", traffic drops to zero, pool keeps running idle. Hard rollback: terraform destroy fc-host (keeps a2a registry off).

---

## P3 — Scaling + hardening (ongoing)

**Goal:** Sustain Firecracker as a multi-tenant, multi-region tier as load grows.

### P3.1 — Multi-host pool

- [ ] Promote single fc-host to a managed instance group (autoscale 1–4 hosts based on `firecracker.pool.warm_count` / `firecracker.invocations.total` ratio)
- [ ] fc-control updated to round-robin across hosts
- [ ] **Trigger:** sustained pool utilization > 70% over 1 week

### P3.2 — Kernel-CVE response runbook

- [ ] Document procedure for kernel/firecracker security update:
  - Subscribe to firecracker-microvm/firecracker security advisories
  - Drain pool → rebuild rootfs + vmlinux → boot new pool
  - Time-bound: < 24h from CVE disclosure to deployed patch for critical kernel CVEs

### P3.3 — Multi-region

- [ ] Defer indefinitely. Single-region is fine for current scale. Promotion trigger would be a real cross-region latency requirement (none today).

---

## Verification matrix (across phases)

| Concern | P0 | P1 | P2 | P3 |
|---|----|----|----|----|
| Router routes `run_python` to firecracker | n/a | ✓ unit test | ✓ unit test | ✓ unit test |
| Pool warm count = expected | n/a | ✓ CM metric | ✓ CM metric | ✓ CM metric + alert |
| F23 fires on attempted escape | n/a | n/a | ✓ integration test | ✓ + alert routing verified |
| F31 fires on egress violation | n/a | n/a | ✓ integration test | ✓ + alert routing verified |
| Cost matches forecast | ✓ ADR | ✓ first month bill | ✓ ongoing CM dashboard | ✓ ongoing |
| Operator can perform drain | n/a | n/a | ✓ runbook drill | ✓ quarterly drill |
| Kernel-CVE patch in <24h | n/a | n/a | n/a | ✓ runbook + simulation |

## Anti-patterns to reject

| Anti-pattern | Why wrong | Correct alternative |
|---|---|---|
| Provision Firecracker pool with no consumer (premature scale) | Wastes ~$265/mo, ops attention drift | Wait until A2A peer-exec is ready (P2 trigger) |
| Reuse microVMs across invocations to "save boot time" | State leak across untrusted payloads | Per-invocation fresh VM. Boot tax is mitigated by warm pool, not reuse. |
| Run Firecracker on a shared-tenancy GCP VM | Co-tenant noisy-neighbor + side-channel risk | Dedicated VM (sole-tenant if budget allows) |
| Auto-escalate unknown tools to `firecracker_sandbox` | Cost foot-gun + breaks default-deny intuition | Default tier stays `shell_sandbox`; explicit allowlist for `firecracker_sandbox` |
| Skip jailer ("Firecracker itself is the sandbox") | Loses layered defense; KVM escape CVEs do happen (CVE-2024-XXXX) | Always jailer + cgroup + seccomp + netns on top of KVM |
| Persistent disk on the microVM | Tampering surface; "what's in there" debt | Ephemeral overlay only; persist nothing |
| Allow inbound network to the microVM | Increases attack surface; not needed for code-exec workload | Egress-only via tap; default-deny ingress |

## Done-when

H1 is "done" (P2 exit) when:

- A2A peer-exec is the first production consumer
- All P0/P1/P2 deliverables checked
- Cost is within 20% of `architecture.md` §6 estimate
- One full pool-drain drill executed by an operator who did not write the code
- All four F-code integration tests (F2, F23, F31, F33-on-boot-failure) green for 7 consecutive nightly runs
