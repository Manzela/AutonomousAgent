# H1 Firecracker — GCP Capability Confirmation

**Purpose:** Confirm that every GCP-side capability assumed in `architecture.md` is actually supported, available in our region, and within our project's quota. This is the "no surprises during terraform apply" pre-flight.

**Confirmation method:** Inline citations to GCP docs (current as of 2026-05-21) plus probe commands the operator runs against our actual project (`autonomous-agent-2026`) before P1.3.

---

## 1. Nested virtualization (`enable_nested_virtualization`)

### Claim (`architecture.md` §3, §4.1)
GCP Compute Engine N2 instances support nested KVM via `--enable-nested-virtualization`.

### Confirmation
✅ **Supported.** Per GCP docs, nested virtualization is supported on:
- N2, N2D, N1 (excluding shared-core)
- C2, C2D, C3, C3D, C4, M1, M2, M3
- NOT supported on E2, T2A, T2D, ARM-based instance types

For our `n2-standard-8` choice: nested virt is supported. Enable via the `advanced_machine_features` block on `google_compute_instance`:

```hcl
resource "google_compute_instance" "fc_host" {
  machine_type = "n2-standard-8"
  advanced_machine_features {
    enable_nested_virtualization = true
    # threads_per_core = 1   # optional: disable hyperthreading (R8 mitigation)
  }
  min_cpu_platform = "Intel Cascade Lake"  # ensures Intel VT-x is available; N2 default is fine
  ...
}
```

### Operator pre-flight probe

```bash
# Run from the Phase 0a operator workstation after gcloud auth application-default login
gcloud compute instances create fc-host-probe \
  --machine-type=n2-standard-8 \
  --zone=us-central1-a \
  --enable-nested-virtualization \
  --image-family=debian-12 --image-project=debian-cloud
gcloud compute ssh fc-host-probe --zone=us-central1-a --command="kvm-ok && cat /proc/cpuinfo | grep -E 'vmx|svm' | head -1"
# Expected: "INFO: /dev/kvm exists" + a cpuinfo line containing vmx
gcloud compute instances delete fc-host-probe --zone=us-central1-a --quiet
```

If `kvm-ok` reports "DISABLED by BIOS" or similar, halt P1.3 and escalate.

## 2. Region availability

### Claim (`architecture.md` §3, §6)
We use `us-central1` (matches Phase 0a).

### Confirmation
✅ N2 + nested virt available in all `us-central1-*` zones. Use `us-central1-a` for consistency with Phase 0a VM placement.

## 3. Project quotas

The pool will consume the following resources at P1.5 steady state:

| Resource | Quota required | Phase 0a current usage (from project memory) | Headroom |
|---|---|---|---|
| `IN_USE_ADDRESSES` (regional) | +1 (fc-host external IP) | <10 | OK |
| `CPUS` (N2 family, `us-central1`) | +8 | low | OK |
| `DISKS_TOTAL_GB` | +30 (boot) + ~50 (rootfs cache) | low | OK |
| `CLOUD_RUN_REVISIONS` | +1 (fc-control service) | low | OK |
| `REDIS_INSTANCES` | +1 (basic 1GB) | 0 today | OK |
| Cloud NAT gateway | +1 (or reuse Phase 0a's if applicable) | 1 (Phase 0a) | reuse |

### Operator pre-flight probe

```bash
gcloud compute project-info describe --format="value(quotas)" | grep -E "CPUS|IN_USE_ADDRESSES|DISKS"
gcloud redis instances list  # confirm zero or one existing instance, headroom for one more
```

If any quota is at >70% utilization, request increase via GCP console before P1.3 (turnaround is 1–3 days).

## 4. APIs that must be enabled

| API | Used by | Already enabled in Phase 0a? |
|---|---|---|
| `compute.googleapis.com` | fc-host VM provisioning | ✅ Yes (Phase 0a) |
| `run.googleapis.com` | fc-control service | ✅ Yes (Phase 0a) |
| `redis.googleapis.com` | pool state cache | ❓ Unknown — likely not |
| `artifactregistry.googleapis.com` | rootfs image storage | ✅ Yes (Phase 0a) |
| `monitoring.googleapis.com` | OTel + cost dashboards | ✅ Yes (Phase 0a) |
| `logging.googleapis.com` | fluent-bit log sink | ✅ Yes (Phase 0a) |
| `iap.googleapis.com` | IAP for fc-control | ✅ Yes (Phase 0a) |

### Operator pre-flight probe

```bash
gcloud services list --enabled --project=autonomous-agent-2026 | grep -E "compute|run|redis|artifactregistry"
# Enable redis if absent:
gcloud services enable redis.googleapis.com --project=autonomous-agent-2026
```

## 5. IAM bindings required

### fc-host service account (new)

Service account: `fc-host@autonomous-agent-2026.iam.gserviceaccount.com`

Roles needed:
- `roles/artifactregistry.reader` — to pull rootfs image
- `roles/logging.logWriter` — fluent-bit sidecar logs
- `roles/monitoring.metricWriter` — pool metrics
- `roles/secretmanager.secretAccessor` — to fetch per-VM JWT signing key

NOT needed (deliberate):
- ❌ No `roles/storage.*` — fc-host should never write to GCS (defense in depth)
- ❌ No `roles/redis.editor` — fc-host reads pool config, doesn't write it (write is fc-control's job)
- ❌ No `roles/compute.*` — fc-host doesn't manage other VMs

### fc-control service account (new)

Service account: `fc-control@autonomous-agent-2026.iam.gserviceaccount.com`

Roles needed:
- `roles/redis.editor` — manage pool state
- `roles/compute.instanceAdmin.v1` (limited to `fc-host-*` resource names via condition) — for emergency pool drain
- `roles/iap.tunnelResourceAccessor` — for IAP wrapping
- `roles/secretmanager.secretAccessor` — for JWT signing key

NOT needed:
- ❌ No `roles/compute.admin` — too broad; scoped role above is sufficient

### Operator pre-flight probe

```bash
# Confirm Workload Identity Federation (already configured in Phase 0a per project memory)
gcloud iam workload-identity-pools list --location=global --project=autonomous-agent-2026
# Confirm we have the headroom to create 2 more SAs
gcloud iam service-accounts list --project=autonomous-agent-2026 | wc -l  # default quota 100; check we're well below
```

## 6. VPC + networking

### Claim (`architecture.md` §3)
Private subnet, no public IP for fc-host (uses IAP for SSH), Cloud NAT for egress with allowlist.

### Confirmation
✅ All supported.

- Phase 0a already uses Cloud NAT for the orchestrator VM; we can extend the same NAT to cover fc-host's subnet, or use a separate NAT.
- Recommendation: separate Cloud NAT for fc-host so we can scope the allowlist tighter than the orchestrator's (orchestrator needs Anthropic + GitHub + many MCPs; fc-host needs only PyPI mirror + npm mirror).

### Egress allowlist for fc-host

Initial allowlist (extend per-A2A-peer as P2 grows):

- `pypi.org` (Python package install during rootfs build only — actually NOT needed at runtime if rootfs ships pre-installed)
- `registry.npmjs.org` (same, build-time only)
- (deliberately empty at runtime) — the rootfs has all dependencies pre-installed; runtime egress should be allowlist-empty for the first month, then per-tool extensions

### Operator pre-flight probe

```bash
# Confirm we can create a separate Cloud NAT gateway (or reuse)
gcloud compute routers list --regions=us-central1 --project=autonomous-agent-2026
# Recommend: create a new router/nat for fc-host's subnet to scope allowlist independently
```

## 7. Memorystore (Redis) configuration

### Claim (`architecture.md` §3, §6)
Cloud Memorystore Redis basic tier, 1GB, us-central1.

### Confirmation
✅ Available.

- Basic tier (no HA): $40/mo for 1GB
- For pool state (transient — can be reconstructed from `gcloud compute instances list`), basic tier is correct
- DO NOT use Standard tier (HA replicated) — pool state is not durable, paying for HA wastes money

### Terraform

```hcl
resource "google_redis_instance" "fc_pool_state" {
  name               = "fc-pool-state"
  tier               = "BASIC"
  memory_size_gb     = 1
  region             = "us-central1"
  authorized_network = google_compute_network.fc_vpc.id
  redis_version      = "REDIS_7_2"
  display_name       = "Firecracker pool state cache"
}
```

## 8. Cloud Run service (fc-control)

### Claim (`architecture.md` §3)
Cloud Run with IAP gating.

### Confirmation
✅ Supported. IAP-gated Cloud Run is documented and widely deployed.

### Configuration notes
- Use **gen2 execution environment** (gVisor) — fc-control is HTTP-only, no need for tier 2
- Min instances: 1 (avoids cold start for control-plane calls; ~$5/mo)
- Max instances: 5 (we won't see significant traffic to fc-control; this is a soft ceiling)
- VPC connector: required so fc-control can reach Memorystore + fc-host private IP

```hcl
resource "google_cloud_run_v2_service" "fc_control" {
  name     = "fc-control"
  location = "us-central1"
  ingress  = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"  # IAP fronts via internal LB
  template {
    scaling {
      min_instance_count = 1
      max_instance_count = 5
    }
    vpc_access {
      connector = google_vpc_access_connector.fc_connector.id
      egress    = "PRIVATE_RANGES_ONLY"
    }
    service_account = google_service_account.fc_control.email
    containers { image = "us-central1-docker.pkg.dev/autonomous-agent-2026/fc/fc-control:latest" }
  }
}
```

## 9. Budget alert

Per Phase 0a billing budget setup (`terraform/phase-0a-gcp/` includes a $250/day budget per project memory commit `099bad8`):

- The H1 incremental cost (~$265/mo = ~$9/day) is comfortably within budget
- No new budget alert needed — the existing $250/day will already alert if Firecracker costs spike anomalously

## 10. Items that need operator action (not in terraform)

These items require manual operator action, not infrastructure-as-code:

| Item | Operator action |
|---|---|
| Subscribe to firecracker-microvm/firecracker security advisories | Add `security@firecracker-microvm.io` to operator mailing list |
| Approve nested-virt enablement (it's a quota-flagged feature in some new projects) | One-time `gcloud compute project-info describe` to confirm; no action if already supported |
| Schedule P1.4 pool bring-up with a separate operator from implementer | Calendar booking |
| Schedule quarterly kernel-CVE drill (P3.2) | Add to ops calendar |

## 11. Pre-P0 gate

Before ADR-0010 is merged, the operator must have confirmed:

- [ ] `kvm-ok` returns positive on a test N2 instance in `us-central1-a`
- [ ] All seven APIs (§4) are enabled or can be enabled without manual GCP support escalation
- [ ] Quota headroom (§3) is sufficient
- [ ] Phase 0a's `terraform/phase-0a-gcp/providers.tf` does NOT need a version bump to support `advanced_machine_features.enable_nested_virtualization` (it doesn't — this attribute exists in `google ~> 5.x` already)

If any of these is false, ADR-0010 cannot be merged.
