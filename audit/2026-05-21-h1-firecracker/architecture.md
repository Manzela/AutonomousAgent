# H1 Firecracker — Architecture

**Purpose:** Concrete deployment design for a self-hosted Firecracker microVM pool on GCP that backs the `firecracker_sandbox` tier.
**Scope:** Hot path (invocation → boot → exec → teardown), pool management, kernel/rootfs build, jailer config, network posture, observability.
**Non-goals:** ADR drafting (that's `docs/decisions/0009-firecracker-sandbox.md`, future); runbook authoring (future, `audit/2026-05-21-h1-firecracker/runbook.md`).

---

## 1. Decision summary

- **Topology:** Self-managed pool of warm microVMs on a dedicated GCP Compute Engine N2 host with nested virtualization enabled. Cloud Run wrapper for *control-plane* (invoke API), not for the microVM execution itself.
- **Tier mapping:** New `firecracker_sandbox` tier in `config/toolsets.yaml` (per `tool-classification.md`).
- **Pool sizing (P1):** 4 warm VMs, capacity to burst to 16 under queue pressure. ~$120/mo at steady state.
- **Network:** Tap-device into a private VPC subnet with explicit egress allowlist enforced by Cloud NAT + VPC firewall. Default-deny outbound.
- **Storage:** Ephemeral rootfs (overlay on a read-only base), wiped on VM teardown. No persistent volume.
- **Boot strategy:** Pre-baked rootfs + pre-loaded vmlinux + ignition-style config injection (firecracker-go-sdk). Warm pool avoids cold-boot tax (~800ms → ~50ms invocation latency).
- **Identity:** Per-VM short-lived JWT minted by control plane, attested via VM metadata. Allows downstream services to identify the calling tier without trusting the in-VM payload.

## 2. Why a new tier, not "replace cloud_sandbox in place"

Three reasons:

1. **`cloud_sandbox` historically meant Modal/Daytona.** External readers of `config/toolsets.yaml` mentally connect "cloud_sandbox" with outsourced compute. Renaming preserves that intuition for future Modal-callers; introducing a fresh tier name signals the implementation change.
2. **We may want both.** There are workloads where Modal is the right choice (low-frequency, no-credentialed-callout — e.g. one-off Jupyter execution from an operator); Firecracker is the right choice for high-frequency, low-latency, low-credentialed-callout (every A2A peer code call).
3. **Test names become honest.** `test_firecracker_isolation` is more honest than `test_cloud_sandbox_isolation` when the test boots a microVM. The tier names should describe the isolation, not the historical sourcing.

## 3. Topology

```
                                  ┌──────────────────────────────────┐
                                  │  GCP project: i-for-ai (Phase 0a)│
                                  └──────────────────────────────────┘
                                                  │
        ┌─────────────────────────────────────────┼─────────────────────────────────────────┐
        │                                         │                                         │
        ▼                                         ▼                                         ▼
┌──────────────────┐                  ┌──────────────────┐                       ┌──────────────────┐
│ Cloud Run        │                  │ Compute Engine   │                       │ Artifact Reg     │
│ "fc-control"     │  ─── gRPC ────▶  │ N2-standard-8    │  ─── pull at boot ─▶  │  fc-rootfs:      │
│  (control plane) │                  │  + nested-virt   │                       │  latest          │
│  - invoke API    │                  │  + jailer        │                       └──────────────────┘
│  - pool mgr      │                  │  + firecracker   │
│  - JWT minting   │                  │  + 4–16 microVMs │       ┌──────────────────────────────────┐
└──────────────────┘                  └──────────────────┘  ─▶   │ VPC private subnet               │
                                                                 │ - egress allowlist (Cloud NAT)   │
                                                                 │ - default-deny firewall          │
                                                                 │ - per-VM tap device              │
                                                                 └──────────────────────────────────┘
```

### Component responsibilities

- **fc-control (Cloud Run service, gRPC):** Exposes `Invoke(tool_name, args)` to the Hermes agent. Pool-manager logic (warm/cold), JWT minting, lifecycle tracking. **Stateless**; pool state lives in a Cloud Memorystore Redis (read-through cache). Public surface is IAP-gated.
- **fc-host (Compute Engine N2 VM, single host in P1):** Runs the Firecracker process pool. Jailer config restricts each Firecracker process to its own cgroup + chroot. Pool size driven by Redis state from fc-control.
- **fc-rootfs (Artifact Registry image):** Minimal Debian-based rootfs (~200MB), Python 3.13 + Node 20, no compiler. Read-only base; per-invocation overlay is RW scratch.
- **VPC subnet:** Private subnet, no public IP. Cloud NAT for egress with explicit allowlist (Anthropic API, PyPI mirror, npm mirror). VPC firewall: default-deny inbound + outbound; explicit allow rules per egress destination.

## 4. Hot-path invocation sequence

```
Hermes agent                fc-control (Cloud Run)        fc-host (Compute Engine)        microVM
     │                              │                              │                          │
     │── Invoke(run_python, code) ─▶│                              │                          │
     │                              │── reserveFromPool() ───────▶ │                          │
     │                              │                              │── select warm VM ──────▶ │
     │                              │                              │                          │── exec entrypoint
     │                              │                              │                          │   with injected code
     │                              │                              │                          │
     │                              │                              │◀──── stdout + exit_code ─│
     │                              │◀── result + pool release ────│                          │
     │◀── InvokeResponse ───────────│                              │                          │
     │                              │                              │── teardown VM ─────────▶ │── (destroyed)
     │                              │                              │── boot replacement ────▶ │── (new warm VM)
```

### 4.1 Cold-boot reduction

Firecracker boot is ~125ms for a minimal kernel. Tap setup + jailer chroot + cgroup setup add another ~200ms. Total cold start: ~400ms. Add code injection + entrypoint: ~800ms invocation latency.

**Mitigation:** Maintain N warm VMs. fc-control draws from warm pool (50ms invocation), then triggers replacement boot in background. Pool size 4 handles 10 concurrent calls comfortably; bursts to 16 absorb spikes without queue depth growing.

### 4.2 Per-VM lifecycle

| Phase | Duration | Notes |
|---|---|---|
| Boot | 400ms | Jailer + Firecracker + kernel + rootfs mount |
| Warm | up to 1 hr | Idle in pool, ready for invocation |
| Reserved | <100ms | fc-control marks "in-use" in Redis |
| Exec | 0.1–60s | User code runs |
| Teardown | 50ms | Jailer SIGKILL + cgroup cleanup |
| **Total wall-clock per "fresh" VM:** | ~1.5s + exec | Worst case: VM is destroyed after every invocation |

**Per-invocation fresh-VM policy.** Every invocation gets a freshly-booted (or freshly-replaced) VM. **No VM is reused across invocations.** This prevents one untrusted payload from leaking state to the next.

## 5. Jailer + isolation primitives

The Firecracker `jailer` binary is the security boundary on top of KVM. Per-invocation `jailer` invocation sets:

- **Chroot:** dedicated dir under `/srv/jailer/firecracker/<vm-id>/`
- **Cgroup v2:** cpu.max = 1 core, memory.max = 512MB, pids.max = 256
- **Network namespace:** dedicated netns; tap device created in netns, connected to bridge `fcbr0`
- **Seccomp:** Firecracker's default seccomp filter (drops ptrace, kexec, mount, etc.)
- **User:** runs as unprivileged `firecracker` user (UID 1010), `setuid` to that on entry
- **No /dev/kvm pass-through to guest:** guest cannot nest further

This stacks on top of KVM hardware virtualization — even if a guest escapes the VM (catastrophic CVE), jailer's chroot + cgroup + seccomp + netns provide a second defense layer.

## 6. Cost envelope

### 6.1 Steady state (4 warm VMs)

- **Compute Engine N2-standard-8** (8 vCPU, 32GB, hosts ~16 microVMs comfortably): $190/mo (sustained-use discount)
- **fc-control Cloud Run service** (low QPS, scale-to-zero): ~$5/mo
- **Cloud Memorystore Redis basic-tier 1GB:** $40/mo
- **Cloud NAT egress + VPC firewall:** ~$30/mo at expected egress volume
- **Artifact Registry storage** (rootfs image, ~200MB × few versions): <$1/mo
- **Logging + monitoring:** absorbed into Phase 0a observability budget

**Total: ~$265/mo for the pool capacity.**

Compare against Modal at our expected scale:
- 10 invocations/hour × 730 hr/mo × 30s avg × Modal A10G class ($0.000306/sec) = ~$67/mo at 10 inv/hr
- 100 inv/hr → ~$670/mo at Modal vs ~$265/mo Firecracker (Firecracker wins above ~40 inv/hr sustained)

**Breakeven: ~40 invocations/hour sustained.** Below that, Modal is cheaper per-invocation but has 5–10× higher latency (cold start). Above that, Firecracker is both cheaper and faster.

### 6.2 Burst pricing

Pool burst from 4 → 16 VMs costs nothing extra (compute already provisioned). Beyond 16, a queue forms (acceptable for non-interactive batch workloads). 32+ concurrent demand requires a second N2 host (~$190/mo more).

## 7. Observability

All three signals route through the existing OTel infrastructure (`lib/observability/`):

- **Trace:** Each invocation emits a span `firecracker.invoke` with attributes `tool_name`, `pool_size`, `wait_ms`, `exec_ms`, `vm_id`, `cold_boot`. Parent is the Hermes orchestration span.
- **Metrics:** `firecracker.pool.warm_count` gauge, `firecracker.invocations.total` counter, `firecracker.exec_duration_ms` histogram, `firecracker.boot_duration_ms` histogram.
- **Logs:** per-VM stdout/stderr to Cloud Logging via fluent-bit sidecar on fc-host. Log retention 30d.

The histograms feed Phase 3 Governor's cost meter (`audit/2026-05-21-phase3-governor/observability-spec.md` references `tool.duration_ms` as an input signal).

## 8. Failure modes (mapped to F-codes)

| Scenario | F-code | Rationale |
|---|---|---|
| Pool exhausted, queue full, timeout | F2 (Network timeout) | Retry with backoff — pool will replenish |
| fc-control unreachable | F4 (5xx upstream) | Same |
| Firecracker boot fails (kernel mismatch, jailer error) | F33 (unclassified) | Fail-loud — this is an infra bug, not a transient |
| In-VM exec crashes (segfault, OOM) | (returned to caller as `exec_failed` result, not F-coded) | The agent that invoked the tool handles it as a tool failure |
| Suspected escape (jailer reports cgroup breach) | F23 (Sandbox escape attempt) | Fail-loud — immediate halt, alert, snapshot |
| Egress allowlist violation | F31 (Egress allowlist violation) | Fail-loud — same alerting path |

F37 (Model Armor) is unrelated to Firecracker — they're orthogonal defenses.

## 9. Comparison to alternatives (forced to defend the choice)

| Alternative | Why rejected |
|---|---|
| Stay on Modal/Daytona | Cost is OK today but A2A peer code execution at expected scale flips the breakeven; latency for warm pool is 5–10× better with Firecracker. |
| GKE + gVisor | Heavier ops surface (cluster, autoscaler, etc.); gVisor's isolation is weaker than KVM (in-kernel emulation vs hardware boundary); no significant cost win for our scale. |
| Cloud Run jobs (no VM, just container) | Container isolation is too weak for untrusted code; CVE-2024-21626 (runc) is the kind of escape we explicitly want to defend against. |
| Wasmtime (WASM runtime) | Strong isolation but limited stdlib (no easy Python/Node ecosystem). Tracked as a future tier for CPU-bound payloads only. |
| Bare-metal Firecracker on a dedicated server | Cheaper than GCP at scale, but loses the operator-platform integration (Cloud Logging, IAM, IAP). Not worth it for our QPS. |

## 10. Out-of-band concerns addressed in other files

- **Risks** (operator footguns, vendor lock-in, kernel CVE response): `risks-and-open-questions.md`
- **Rollout phasing** (P0 / P1 / P2 / P3): `rollout-plan.md`
- **GCP service confirmation** (nested virt support, supported machine types, region availability): `gcp-confirm.md`
