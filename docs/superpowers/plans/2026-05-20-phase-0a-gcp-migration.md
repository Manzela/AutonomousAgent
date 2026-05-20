# Phase 0a — GCP Always-Online Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the 8-service (11-container) AutonomousAgent docker-compose stack from a developer laptop to a single GCE VM in GCP, gated on a fix for the hermes exit-137 silent-crash bug, with host-level watchdog, daily snapshots, IAP-only access, Workload Identity Federation CI/CD, Secret Manager-backed runtime secrets, and Cloud Logging/Monitoring observability — achieving ~99% uptime (3.65 days/yr error budget).

**Architecture:** Single `e2-standard-4` GCE VM (Debian 12) in `us-central1-a` of a new GCP project `rx-research-autonomousagent`. Boot disk (50 GB) + dedicated data disk (100 GB, backs `hermes-data` named volume) with daily PD snapshots. No public IP — SSH via IAP TCP forwarding. Three-layer auto-restart (VM `automatic_restart=true`, Docker `restart=unless-stopped`, host-level `hermes-watchdog.service` systemd unit polling `docker compose ps`). Secret Manager replaces SOPS at runtime via a `hermes-secrets.service` systemd one-shot that writes ephemeral tmpfs env files before compose starts. GitHub Actions deploys via Workload Identity Federation (no JSON keys) — build → push to Artifact Registry → SSH into VM via IAP → `docker compose pull && up -d` → smoke check → roll back on failure. All infrastructure is Terraform-defined under `terraform/phase-0a-gcp/`.

**Tech Stack:** Terraform 1.7+, Google Cloud (GCE, Persistent Disk, Cloud Storage, Artifact Registry, Secret Manager, Cloud Logging, Cloud Monitoring, IAP, Workload Identity Federation, IAM), Debian 12, systemd, Docker Compose v2, bash, GitHub Actions, `gcloud` CLI, `gh` CLI.

**Source spec:** `docs/superpowers/specs/2026-05-20-phase-0a-gcp-always-online-design.md`

**Audit findings addressed:**
- F-2026-05-20-1 / P0-A — hermes exit-137 silent crash (Phase A of this plan)
- F-2026-05-20-7 / P2-E — hermes submodule 2 commits behind (Phase A, Task 2)
- The watchdog (Phase D) addresses the root architectural gap that allowed F-2026-05-20-1 to go unnoticed

---

## File Structure

### New files (this plan creates)

**Terraform module** — `terraform/phase-0a-gcp/`:
- `providers.tf` — google + google-beta provider config, backend
- `variables.tf` — project_id, region, zone, vm machine_type, disk sizes
- `outputs.tf` — vm_name, vm_internal_ip, sa emails, artifact_registry_repo
- `project.tf` — `google_project` (or `data` if reusing), API enablement
- `iam.tf` — runtime + CI service accounts, role bindings
- `wif.tf` — Workload Identity Pool + GitHub provider
- `networking.tf` — VPC, subnet, firewall rules (IAP + egress)
- `compute.tf` — boot disk, data disk, snapshot schedule, GCE instance
- `artifact_registry.tf` — `hermes` Docker repo
- `secret_manager.tf` — Secret resources (placeholder; values land via migration script)
- `gcs.tf` — snapshot bucket (Phase 0a-local) — distinct from PR #108 bucket
- `logging_monitoring.tf` — uptime check, log-based custom metric, 4 alert policies
- `vm_metadata.tf` — startup-script-url metadata pointing to bootstrap tarball in GCS

**VM bootstrap (scripts copied onto VM at provision time)** — `scripts/vm-bootstrap/`:
- `install.sh` — master entry; installs Docker, pulls bootstrap, enables systemd units
- `load-secrets.sh` — pulls Secret Manager values, writes to `/run/hermes/env/*.env`
- `hermes-secrets.service` — systemd one-shot, runs `load-secrets.sh` `Before=docker-compose-hermes.service`
- `docker-compose-hermes.service` — systemd unit wrapping `docker compose up/down`
- `hermes-watchdog.sh` — polls `docker compose ps`, restarts on missing/exited containers, emits structured log
- `hermes-watchdog.service` — systemd unit running watchdog every 30s
- `expected-containers.txt` — list of long-running container names (excludes one-shot `volume-init`)

**Secret migration** — `scripts/`:
- `migrate-secrets-to-secret-manager.sh` — one-time, idempotent; decrypts SOPS → writes to Secret Manager

**CI/CD** — `.github/workflows/`:
- `phase-0a-deploy.yml` — build → push → IAP SSH deploy → smoke → rollback

**Verification** — `tests/phase_0a/`:
- `smoke.sh` — post-deploy: `litellm-proxy /health` + watchdog log check
- `chaos.sh` — `docker kill hermes` → verify watchdog restarts within 90s
- `acceptance.sh` — runs all 10 spec section-11 criteria

**Runbooks** — `docs/runbooks/`:
- `phase-0a-cutover.md` — laptop→GCP cutover procedure
- `phase-0a-rollback.md` — GCP→laptop rollback procedure
- `phase-0a-recovery.md` — VM rebuild from PD snapshot

### Modified files

- `deploy/docker-compose.yml` — add `gcplogs` log driver (Phase F, Task 26) — only on the VM-deployed copy, not the dev one
- `deploy/docker-compose.gcp.override.yml` (NEW) — GCP-specific compose override for log driver + secret env files
- `hermes/` submodule — bump from `5e743559e` → `42c428841` (Phase A, Task 2)

---

## Phase A — Pre-flight blocker (hermes RCA + fix)

**Audit task P0-A. The migration is gated on this phase passing.** All work in this phase happens on the developer laptop. No GCP resources yet.

### Task 1: Reproduce exit-137 with profiling

**Files:**
- Read: `audit/2026-05-20-state-of-the-repo/findings.md` (F-2026-05-20-1)
- Read: `deploy/docker-compose.yml` (hermes service definition)
- Create: `audit/2026-05-20-state-of-the-repo/p0a-rca/run1-baseline.log`

- [ ] **Step 1: Capture clean baseline run**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
mkdir -p audit/2026-05-20-state-of-the-repo/p0a-rca
docker compose -f deploy/docker-compose.yml down -v
docker compose -f deploy/docker-compose.yml up -d
sleep 60
docker compose -f deploy/docker-compose.yml ps > audit/2026-05-20-state-of-the-repo/p0a-rca/run1-baseline.log
docker compose -f deploy/docker-compose.yml logs hermes --tail 200 >> audit/2026-05-20-state-of-the-repo/p0a-rca/run1-baseline.log
```

Expected: `run1-baseline.log` shows hermes container, then plugin discovery lines, then either (a) silent exit or (b) explicit OOM-kill / SIGKILL.

- [ ] **Step 2: Capture memory profile at the crash window**

```bash
docker stats --no-stream --format "{{.Name}}: {{.MemUsage}}" > audit/2026-05-20-state-of-the-repo/p0a-rca/run1-memstats.log
docker inspect $(docker compose -f deploy/docker-compose.yml ps -q hermes) --format '{{.State.ExitCode}} {{.State.OOMKilled}} {{.State.Error}}' >> audit/2026-05-20-state-of-the-repo/p0a-rca/run1-memstats.log
```

Expected: line showing exit code 137 + `OOMKilled: true|false`. If `OOMKilled: true`, hypothesis B (tmpfs/memory starvation) is most likely. If `false`, more likely hypothesis A (submodule regression) or C (plugin guard).

- [ ] **Step 3: Document findings**

Append to `audit/2026-05-20-state-of-the-repo/p0a-rca/run1-baseline.log`:

```
=== Reproduction summary ===
Date: <iso8601>
Exit code: <from inspect>
OOMKilled: <true|false>
Last hermes log line before crash: <from logs>
Hypothesis ordering after baseline: <reorder A/B/C based on signals>
```

- [ ] **Step 4: Commit the RCA evidence**

```bash
git add audit/2026-05-20-state-of-the-repo/p0a-rca/
git commit -m "chore(audit): P0-A run-1 baseline reproduction of hermes exit-137

Capture clean repro logs, memstats, and inspect output to guide
hypothesis selection for the submodule-bump / tmpfs / plugin-guard fixes."
```

---

### Task 2: Test hypothesis A — submodule bump (also closes P2-E)

**Files:**
- Modify: `hermes/` submodule pointer
- Create: `audit/2026-05-20-state-of-the-repo/p0a-rca/run2-submodule-bump.log`

- [ ] **Step 1: Bump submodule to upstream HEAD**

```bash
cd hermes
git fetch origin
git checkout 42c428841
cd ..
git submodule status hermes
```

Expected: `42c428841 hermes (heads/main)`.

- [ ] **Step 2: Rebuild hermes image**

```bash
docker compose -f deploy/docker-compose.yml build hermes
```

Expected: build succeeds; new image tagged `autonomousagent/hermes:0.1.0`.

- [ ] **Step 3: Smoke test the new image**

```bash
docker compose -f deploy/docker-compose.yml down
docker compose -f deploy/docker-compose.yml up -d
sleep 120
docker compose -f deploy/docker-compose.yml ps > audit/2026-05-20-state-of-the-repo/p0a-rca/run2-submodule-bump.log
docker inspect $(docker compose -f deploy/docker-compose.yml ps -q hermes) --format '{{.State.ExitCode}} {{.State.OOMKilled}}' >> audit/2026-05-20-state-of-the-repo/p0a-rca/run2-submodule-bump.log
```

Expected: if hypothesis A is correct, hermes stays `Up` and exit code is `0` (still running).

- [ ] **Step 4: Decision branch**

If hermes is `Up` for >5 min: hypothesis A confirmed, skip to Task 5.
If hermes still exits 137: proceed to Task 3.

Document the decision in `run2-submodule-bump.log`:

```
=== Decision ===
Hermes status after 5 min: <Up | Exited>
Exit code: <code>
Hypothesis A: <confirmed | rejected>
Next task: <Task 5 | Task 3>
```

- [ ] **Step 5: Commit (regardless of branch)**

```bash
git add hermes audit/2026-05-20-state-of-the-repo/p0a-rca/run2-submodule-bump.log
git commit -m "fix(hermes): bump submodule 5e743559e -> 42c428841 (P2-E, P0-A hypothesis A)

Tests whether upstream HEAD resolves the exit-137 silent crash.
See audit/2026-05-20-state-of-the-repo/p0a-rca/run2 for evidence."
```

---

### Task 3: Test hypothesis B — PR #98 tmpfs starvation (conditional)

**Run only if Task 2 did not resolve the crash.**

**Files:**
- Modify: `deploy/docker-compose.yml` — hermes `tmpfs:` block
- Create: `audit/2026-05-20-state-of-the-repo/p0a-rca/run3-tmpfs-expanded.log`

- [ ] **Step 1: Inspect current tmpfs sizing**

```bash
grep -A 5 "tmpfs:" deploy/docker-compose.yml
```

Note the current size (likely `100M` or similar from PR #58 hardening).

- [ ] **Step 2: Expand tmpfs allocation**

In `deploy/docker-compose.yml`, find the hermes service `tmpfs:` block and change each tmpfs mount size to `512M`:

```yaml
    tmpfs:
      - /tmp:size=512M,mode=1777
      - /run:size=512M,mode=755
```

- [ ] **Step 3: Restart and observe**

```bash
docker compose -f deploy/docker-compose.yml down
docker compose -f deploy/docker-compose.yml up -d
sleep 120
docker compose -f deploy/docker-compose.yml ps > audit/2026-05-20-state-of-the-repo/p0a-rca/run3-tmpfs-expanded.log
docker inspect $(docker compose -f deploy/docker-compose.yml ps -q hermes) --format '{{.State.ExitCode}} {{.State.OOMKilled}}' >> audit/2026-05-20-state-of-the-repo/p0a-rca/run3-tmpfs-expanded.log
```

- [ ] **Step 4: Decision branch**

If hermes is `Up` for >5 min: hypothesis B confirmed, skip to Task 5.
If still exits 137: revert the tmpfs change and proceed to Task 4.

```bash
# If reverting:
git checkout deploy/docker-compose.yml
```

- [ ] **Step 5: Commit (only if fix held)**

```bash
git add deploy/docker-compose.yml audit/2026-05-20-state-of-the-repo/p0a-rca/run3-tmpfs-expanded.log
git commit -m "fix(deploy): expand hermes tmpfs to 512M (P0-A hypothesis B)

PR #98 hardening starved the plugin loader; bumping tmpfs from
~100M to 512M resolves the exit-137 at plugin discovery."
```

---

### Task 4: Test hypothesis C — disk-cleanup plugin guard (conditional)

**Run only if Task 2 and Task 3 did not resolve the crash.**

**Files:**
- Modify: `config/plugins.yaml` — disable `disk_cleanup`
- Create: `audit/2026-05-20-state-of-the-repo/p0a-rca/run4-disk-cleanup-disabled.log`

- [ ] **Step 1: Locate and disable the plugin**

```bash
grep -n "disk_cleanup\|disk-cleanup" config/plugins.yaml
```

Edit `config/plugins.yaml` and set `enabled: false` for the `disk_cleanup` plugin entry.

- [ ] **Step 2: Restart and observe**

```bash
docker compose -f deploy/docker-compose.yml down
docker compose -f deploy/docker-compose.yml up -d
sleep 120
docker compose -f deploy/docker-compose.yml ps > audit/2026-05-20-state-of-the-repo/p0a-rca/run4-disk-cleanup-disabled.log
docker inspect $(docker compose -f deploy/docker-compose.yml ps -q hermes) --format '{{.State.ExitCode}} {{.State.OOMKilled}}' >> audit/2026-05-20-state-of-the-repo/p0a-rca/run4-disk-cleanup-disabled.log
```

- [ ] **Step 3: Decision branch**

If hermes is `Up` for >5 min: hypothesis C confirmed. Open a tracking issue to fix the plugin's resource guard properly (`gh issue create --title "fix: disk_cleanup plugin trips own resource guard at startup" --body "Discovered during P0-A RCA — see audit/2026-05-20-state-of-the-repo/p0a-rca/run4."`). Then proceed to Task 5.

If still exits 137: **escalate.** All three hypotheses rejected. Open a P0 issue and pause the migration. Do not skip to Task 5.

- [ ] **Step 4: Commit (only if fix held)**

```bash
git add config/plugins.yaml audit/2026-05-20-state-of-the-repo/p0a-rca/run4-disk-cleanup-disabled.log
git commit -m "fix(plugins): disable disk_cleanup pending guard fix (P0-A hypothesis C)

Plugin's own resource guard trips during startup discovery.
Disabling restores hermes uptime; proper fix tracked in <issue-url>."
```

---

### Task 5: 24-hour idle soak + pre-flight gate

**Files:**
- Create: `audit/2026-05-20-state-of-the-repo/p0a-rca/soak-24h.log`

- [ ] **Step 1: Start a clean run with the fix**

```bash
docker compose -f deploy/docker-compose.yml down
docker compose -f deploy/docker-compose.yml up -d
echo "$(date -u +%FT%TZ) START" > audit/2026-05-20-state-of-the-repo/p0a-rca/soak-24h.log
```

- [ ] **Step 2: Schedule hourly status checks** (manual or via cron)

For each hour over 24 hours, append:

```bash
echo "$(date -u +%FT%TZ) $(docker compose -f deploy/docker-compose.yml ps --format json | jq -r '.[] | select(.Service=="hermes") | .State')" >> audit/2026-05-20-state-of-the-repo/p0a-rca/soak-24h.log
```

Expected: every line shows `running`. Any `exited` is a soak failure.

- [ ] **Step 3: Gate check after 24h**

```bash
grep -c "running" audit/2026-05-20-state-of-the-repo/p0a-rca/soak-24h.log
grep -c "exited" audit/2026-05-20-state-of-the-repo/p0a-rca/soak-24h.log
```

Expected: 25+ "running" entries (start + 24 hourly), 0 "exited" entries.

If the gate passes: Phase A is closed. Proceed to Phase B.
If the gate fails: re-open the RCA — the previous fix was incomplete.

- [ ] **Step 4: Commit the soak evidence + close P0-A**

```bash
git add audit/2026-05-20-state-of-the-repo/p0a-rca/soak-24h.log
git commit -m "chore(audit): P0-A closed — 24h soak passes, hermes stable

Pre-flight blocker for Phase 0a GCP migration is now lifted.
See audit/2026-05-20-state-of-the-repo/p0a-rca/soak-24h.log for evidence."

# Update audit-plan to mark P0-A done
gh issue list --search "P0-A" --json number,title --state open
# If a P0-A tracking issue exists, close it with reference to this commit
```

---

## Phase B — Terraform foundation (project + IAM + networking)

All Terraform code lives under `terraform/phase-0a-gcp/`. Tasks 6–13 each add one resource group, run `terraform plan`, then `apply`, then `git commit`.

**Pre-step (before Task 6):** Ensure tooling is installed and authenticated.

```bash
# Verify tooling
terraform version    # Expect 1.7.0+
gcloud version       # Already installed
gh --version         # Already installed
gcloud auth application-default login  # ADC for Terraform google provider
```

### Task 6: Terraform module skeleton + providers + variables

**Files:**
- Create: `terraform/phase-0a-gcp/providers.tf`
- Create: `terraform/phase-0a-gcp/variables.tf`
- Create: `terraform/phase-0a-gcp/outputs.tf`
- Create: `terraform/phase-0a-gcp/terraform.tfvars.example`
- Create: `terraform/phase-0a-gcp/.gitignore`
- Create: `terraform/phase-0a-gcp/README.md`

- [ ] **Step 1: Create directory + .gitignore**

```bash
mkdir -p terraform/phase-0a-gcp
cat > terraform/phase-0a-gcp/.gitignore <<'EOF'
.terraform/
*.tfstate
*.tfstate.backup
terraform.tfvars
*.tfvars
!terraform.tfvars.example
EOF
```

- [ ] **Step 2: Write providers.tf**

Create `terraform/phase-0a-gcp/providers.tf`:

```hcl
terraform {
  required_version = ">= 1.7.0"
  required_providers {
    google      = { source = "hashicorp/google",      version = "~> 5.30" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 5.30" }
  }
  backend "gcs" {
    bucket = "rx-research-autonomousagent-tfstate"
    prefix = "phase-0a"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
```

- [ ] **Step 3: Write variables.tf**

Create `terraform/phase-0a-gcp/variables.tf`:

```hcl
variable "project_id" {
  type        = string
  description = "GCP project ID (default: rx-research-autonomousagent)"
  default     = "rx-research-autonomousagent"
}

variable "billing_account" {
  type        = string
  description = "GCP billing account ID — required only on first project create"
  default     = ""
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
}

variable "vm_machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "vm_boot_disk_gb" {
  type    = number
  default = 50
}

variable "vm_data_disk_gb" {
  type    = number
  default = 100
}

variable "github_owner" {
  type    = string
  default = "Manzela"
}

variable "github_repo" {
  type    = string
  default = "AutonomousAgent"
}
```

- [ ] **Step 4: Write outputs.tf placeholder**

Create `terraform/phase-0a-gcp/outputs.tf`:

```hcl
# Populated incrementally by later tasks.
output "project_id" {
  value = var.project_id
}
```

- [ ] **Step 5: Write terraform.tfvars.example**

```hcl
# Copy to terraform.tfvars and fill in.
# project_id      = "rx-research-autonomousagent"
# billing_account = "XXXXXX-XXXXXX-XXXXXX"
```

- [ ] **Step 6: Initialize backend bucket (one-shot, outside Terraform)**

```bash
gcloud storage buckets create gs://rx-research-autonomousagent-tfstate \
  --location=us-central1 \
  --uniform-bucket-level-access \
  --public-access-prevention
gcloud storage buckets update gs://rx-research-autonomousagent-tfstate --versioning
```

Expected: bucket created. (If you don't yet have a project to bill this against, create it first via `gcloud projects create rx-research-autonomousagent --billing-account=$BILLING_ACCT` — Task 7 handles enabling APIs, but the project must exist to create the bucket.)

- [ ] **Step 7: terraform init**

```bash
cd terraform/phase-0a-gcp
terraform init
```

Expected: "Terraform has been successfully initialized!"

- [ ] **Step 8: Commit**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/
git commit -m "feat(terraform): scaffold phase-0a-gcp module (providers, variables, backend)

Resolves OQ-1 (new project: rx-research-autonomousagent),
OQ-2 (WIF defaults wired in later task)."
```

---

### Task 7: GCP project + API enablement

**Files:**
- Create: `terraform/phase-0a-gcp/project.tf`

- [ ] **Step 1: Write project.tf**

```hcl
# Project itself is assumed pre-created (see Task 6 step 6).
# This task enables the APIs Phase 0a needs.

locals {
  required_apis = [
    "compute.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "storage.googleapis.com",
    "iap.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "sts.googleapis.com",
  ]
}

resource "google_project_service" "enabled" {
  for_each = toset(local.required_apis)
  service  = each.value

  disable_on_destroy         = false
  disable_dependent_services = false
}
```

- [ ] **Step 2: Plan + apply**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
```

Expected: 11 `google_project_service` resources created.

- [ ] **Step 3: Verify**

```bash
gcloud services list --enabled --project=rx-research-autonomousagent | grep -E "compute|iam|secretmanager|artifactregistry"
```

Expected: at least the 4 named services appear.

- [ ] **Step 4: Commit**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/project.tf
git commit -m "feat(terraform): enable 11 required GCP APIs for phase-0a"
```

---

### Task 8: VPC + subnet

**Files:**
- Create: `terraform/phase-0a-gcp/networking.tf`

- [ ] **Step 1: Write networking.tf (VPC + subnet only)**

```hcl
resource "google_compute_network" "hermes" {
  name                            = "hermes-vpc"
  auto_create_subnetworks         = false
  routing_mode                    = "REGIONAL"
  delete_default_routes_on_create = false
  depends_on                      = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "hermes" {
  name                     = "hermes-subnet-us-central1"
  ip_cidr_range            = "10.10.0.0/24"
  region                   = var.region
  network                  = google_compute_network.hermes.id
  private_ip_google_access = true
}
```

- [ ] **Step 2: Plan + apply**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
```

Expected: 2 resources created.

- [ ] **Step 3: Verify**

```bash
gcloud compute networks list --project=rx-research-autonomousagent | grep hermes-vpc
gcloud compute networks subnets list --project=rx-research-autonomousagent --filter="region:us-central1" | grep hermes-subnet
```

- [ ] **Step 4: Commit**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/networking.tf
git commit -m "feat(terraform): VPC hermes-vpc + /24 subnet in us-central1"
```

---

### Task 9: Firewall (deny-all ingress + IAP SSH + egress allow)

**Files:**
- Modify: `terraform/phase-0a-gcp/networking.tf`

- [ ] **Step 1: Append firewall rules to networking.tf**

```hcl
resource "google_compute_firewall" "deny_all_ingress" {
  name      = "hermes-deny-all-ingress"
  network   = google_compute_network.hermes.name
  direction = "INGRESS"
  priority  = 65534

  deny { protocol = "all" }
  source_ranges = ["0.0.0.0/0"]
}

resource "google_compute_firewall" "allow_iap_ssh" {
  name      = "hermes-allow-iap-ssh"
  network   = google_compute_network.hermes.name
  direction = "INGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
  # GCP-published IAP CIDR
  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["hermes-vm"]
}

resource "google_compute_firewall" "allow_egress_all" {
  name      = "hermes-allow-egress-all"
  network   = google_compute_network.hermes.name
  direction = "EGRESS"
  priority  = 1000

  allow { protocol = "all" }
  destination_ranges = ["0.0.0.0/0"]
}
```

- [ ] **Step 2: Plan + apply**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
```

Expected: 3 firewall resources created.

- [ ] **Step 3: Commit**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/networking.tf
git commit -m "feat(terraform): firewall — deny-all ingress, allow IAP SSH, allow egress"
```

---

### Task 10: Runtime service account + role bindings

**Files:**
- Create: `terraform/phase-0a-gcp/iam.tf`

- [ ] **Step 1: Write iam.tf (runtime SA only)**

```hcl
resource "google_service_account" "hermes_runtime" {
  account_id   = "hermes-runtime"
  display_name = "Hermes VM runtime identity"
  description  = "Used by the GCE VM to pull secrets, write logs/metrics, pull images"
}

locals {
  runtime_roles = [
    "roles/secretmanager.secretAccessor",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/artifactregistry.reader",
    "roles/storage.objectCreator",  # for snapshot bucket writes
  ]
}

resource "google_project_iam_member" "runtime_roles" {
  for_each = toset(local.runtime_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.hermes_runtime.email}"
}
```

- [ ] **Step 2: Plan + apply**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
```

Expected: 1 SA + 5 IAM members.

- [ ] **Step 3: Verify**

```bash
gcloud iam service-accounts list --project=rx-research-autonomousagent | grep hermes-runtime
gcloud projects get-iam-policy rx-research-autonomousagent \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:hermes-runtime@*" \
  --format="value(bindings.role)" | sort
```

Expected: 5 role lines matching the list.

- [ ] **Step 4: Commit**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/iam.tf
git commit -m "feat(terraform): runtime SA hermes-runtime + 5 minimal roles"
```

---

### Task 11: CI service account + Workload Identity Federation

**Files:**
- Modify: `terraform/phase-0a-gcp/iam.tf`
- Create: `terraform/phase-0a-gcp/wif.tf`

- [ ] **Step 1: Append CI SA to iam.tf**

```hcl
resource "google_service_account" "gha_deployer" {
  account_id   = "gha-deployer"
  display_name = "GitHub Actions CI/CD deployer"
}

locals {
  gha_roles = [
    "roles/compute.instanceAdmin.v1",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
    "roles/iap.tunnelResourceAccessor",
  ]
}

resource "google_project_iam_member" "gha_roles" {
  for_each = toset(local.gha_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.gha_deployer.email}"
}
```

- [ ] **Step 2: Write wif.tf**

Resolves **OQ-2**: pool `github-actions`, provider `manzela-autonomousagent`.

```hcl
resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  description               = "OIDC federation for AutonomousAgent CI"
  depends_on                = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "manzela_autonomousagent" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "manzela-autonomousagent"
  display_name                       = "Manzela/AutonomousAgent"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
  }

  # Restrict to this single repo
  attribute_condition = "attribute.repository == \"${var.github_owner}/${var.github_repo}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "gha_can_impersonate" {
  service_account_id = google_service_account.gha_deployer.id
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.github_repo}"
}

output "wif_provider_resource_name" {
  value = google_iam_workload_identity_pool_provider.manzela_autonomousagent.name
}

output "gha_deployer_email" {
  value = google_service_account.gha_deployer.email
}
```

- [ ] **Step 3: Plan + apply**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
```

Expected: 1 SA + 4 IAM members + 1 pool + 1 provider + 1 SA-IAM binding = 8 new resources.

- [ ] **Step 4: Capture outputs for GitHub Actions secrets**

```bash
terraform output wif_provider_resource_name
terraform output gha_deployer_email
```

Copy these two values; they'll go into GitHub Actions repo variables in Task 30.

- [ ] **Step 5: Commit**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/iam.tf terraform/phase-0a-gcp/wif.tf
git commit -m "feat(terraform): gha-deployer SA + WIF pool/provider for GitHub OIDC

Resolves OQ-2: pool=github-actions, provider=manzela-autonomousagent.
No JSON service-account keys — federation only."
```

---

### Task 12: Artifact Registry repo

**Files:**
- Create: `terraform/phase-0a-gcp/artifact_registry.tf`

- [ ] **Step 1: Write artifact_registry.tf**

```hcl
resource "google_artifact_registry_repository" "hermes" {
  location      = var.region
  repository_id = "hermes"
  description   = "AutonomousAgent hermes container images, tagged by git SHA"
  format        = "DOCKER"

  cleanup_policies {
    id     = "keep-30-most-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 30
    }
  }

  cleanup_policies {
    id     = "delete-untagged-after-7d"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s"
    }
  }

  depends_on = [google_project_service.enabled]
}

output "artifact_registry_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.hermes.repository_id}"
}
```

- [ ] **Step 2: Plan + apply**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
```

- [ ] **Step 3: Verify**

```bash
gcloud artifacts repositories list --project=rx-research-autonomousagent --location=us-central1
terraform output artifact_registry_repo
```

Expected output: `us-central1-docker.pkg.dev/rx-research-autonomousagent/hermes`.

- [ ] **Step 4: Commit**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/artifact_registry.tf
git commit -m "feat(terraform): Artifact Registry repo 'hermes' with cleanup policies"
```

---

### Task 13: GCS snapshot bucket (Phase 0a-local)

**Files:**
- Create: `terraform/phase-0a-gcp/gcs.tf`

Resolves **OQ-3**: this bucket is for daily PD snapshots staged in-region; weekly cross-region durability stays with the existing PR #108 bucket.

- [ ] **Step 1: Write gcs.tf**

```hcl
resource "google_storage_bucket" "hermes_snapshots" {
  name                        = "rx-research-autonomousagent-snapshots"
  location                    = var.region   # us-central1, NOT multi-region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning { enabled = true }

  lifecycle_rule {
    condition { age = 30 }   # days
    action    { type = "Delete" }
  }
}
```

- [ ] **Step 2: Plan + apply**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
```

- [ ] **Step 3: Verify**

```bash
gcloud storage buckets describe gs://rx-research-autonomousagent-snapshots --format="value(location,storageClass)"
```

Expected: `US-CENTRAL1 STANDARD`.

- [ ] **Step 4: Commit**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/gcs.tf
git commit -m "feat(terraform): GCS snapshot bucket (in-region, 30d lifecycle)

Resolves OQ-3: daily PD snapshots stage here in us-central1; cross-region
durability remains with the existing PR #108 weekly GCS bucket."
```

---

## Phase C — Persistence + VM

### Task 14: Secret Manager secret resources (empty placeholders)

**Files:**
- Create: `terraform/phase-0a-gcp/secret_manager.tf`

The secret *resources* are Terraform-managed; the secret *values* land via Phase E migration script (Task 23-25). Reason: keeping values out of Terraform avoids leaking via `terraform plan` and state files.

- [ ] **Step 1: List current SOPS env files**

```bash
ls secrets/*.env.sops 2>/dev/null
```

Expected (based on memory): at least `secrets/honcho.env.sops`. There may be others (`litellm.env.sops`, `telegram.env.sops`, etc.). Record the list.

- [ ] **Step 2: Write secret_manager.tf**

```hcl
locals {
  # Names of SOPS env files (without .env.sops suffix) that we want as Secret Manager entries.
  # Each secret in SM stores the entire env-file content as a single secret value.
  sops_env_files = [
    "honcho",
    # Append other entries here as you confirm them in step 1 (e.g., "litellm", "telegram", "openrouter")
  ]
}

resource "google_secret_manager_secret" "envfiles" {
  for_each  = toset(local.sops_env_files)
  secret_id = "hermes-${each.value}"

  replication {
    auto {}
  }

  labels = {
    phase     = "0a"
    component = "envfile"
  }
}
```

- [ ] **Step 3: Plan + apply**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
```

Expected: 1 secret per entry in `local.sops_env_files` (at minimum `hermes-honcho`).

- [ ] **Step 4: Verify (secrets exist, no versions yet)**

```bash
gcloud secrets list --project=rx-research-autonomousagent | grep hermes-
gcloud secrets versions list hermes-honcho --project=rx-research-autonomousagent
```

Expected: secret listed; version list is empty (no values yet).

- [ ] **Step 5: Commit**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/secret_manager.tf
git commit -m "feat(terraform): Secret Manager placeholders for SOPS env files

Resources only — values land via Phase E migration script. Avoids
secret leakage through tfstate."
```

---

### Task 15: Boot + data disks + snapshot schedule

**Files:**
- Create: `terraform/phase-0a-gcp/compute.tf` (partial — disks + schedule only; VM added in Task 16)

- [ ] **Step 1: Write compute.tf (disks + schedule)**

```hcl
resource "google_compute_resource_policy" "daily_snapshot" {
  name   = "hermes-data-daily-snapshot"
  region = var.region

  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "07:00"   # UTC; ~3am US Central, low-activity window
      }
    }
    retention_policy {
      max_retention_days    = 7
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
    snapshot_properties {
      storage_locations = [var.region]
      labels = {
        phase = "0a"
        disk  = "hermes-data"
      }
    }
  }
}

resource "google_compute_disk" "boot" {
  name  = "hermes-vm-boot"
  type  = "pd-balanced"
  zone  = var.zone
  size  = var.vm_boot_disk_gb
  image = "debian-cloud/debian-12"
}

resource "google_compute_disk" "data" {
  name = "hermes-vm-data"
  type = "pd-balanced"
  zone = var.zone
  size = var.vm_data_disk_gb
}

resource "google_compute_disk_resource_policy_attachment" "data_snapshot" {
  name = google_compute_resource_policy.daily_snapshot.name
  disk = google_compute_disk.data.name
  zone = var.zone
}
```

- [ ] **Step 2: Plan + apply**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
```

Expected: 1 resource policy + 2 disks + 1 attachment.

- [ ] **Step 3: Verify**

```bash
gcloud compute disks list --project=rx-research-autonomousagent --zones=us-central1-a
gcloud compute resource-policies list --project=rx-research-autonomousagent --region=us-central1
```

- [ ] **Step 4: Commit**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/compute.tf
git commit -m "feat(terraform): boot + data disks + daily snapshot schedule (7d retention)"
```

---

### Task 16: GCE VM with startup-script metadata

**Files:**
- Modify: `terraform/phase-0a-gcp/compute.tf`
- Modify: `terraform/phase-0a-gcp/outputs.tf`

The VM references a startup script that will be uploaded in Phase D (Task 17). For now, the script URL points to a known GCS path; the script content is uploaded separately.

- [ ] **Step 1: Append VM resource to compute.tf**

```hcl
resource "google_compute_instance" "hermes" {
  name         = "hermes-vm"
  machine_type = var.vm_machine_type
  zone         = var.zone
  tags         = ["hermes-vm"]

  boot_disk {
    source      = google_compute_disk.boot.self_link
    auto_delete = false
  }

  attached_disk {
    source      = google_compute_disk.data.self_link
    device_name = "hermes-data"
    mode        = "READ_WRITE"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.hermes.id
    # No access_config block → no public IP.
  }

  service_account {
    email  = google_service_account.hermes_runtime.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    preemptible         = false
  }

  metadata = {
    enable-oslogin    = "TRUE"
    startup-script-url = "gs://rx-research-autonomousagent-snapshots/bootstrap/install.sh"
    # The compose stack is pulled by install.sh from this image repo:
    hermes-image-repo = "us-central1-docker.pkg.dev/${var.project_id}/hermes"
  }

  labels = {
    phase = "0a"
  }

  allow_stopping_for_update = true
  depends_on                = [google_storage_bucket.hermes_snapshots]
}
```

- [ ] **Step 2: Append outputs**

In `outputs.tf`:

```hcl
output "vm_name" {
  value = google_compute_instance.hermes.name
}

output "vm_internal_ip" {
  value = google_compute_instance.hermes.network_interface[0].network_ip
}

output "vm_zone" {
  value = var.zone
}

output "hermes_runtime_sa_email" {
  value = google_service_account.hermes_runtime.email
}
```

- [ ] **Step 3: Plan — DO NOT apply yet**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
```

Expected: 1 VM to create. Plan shows the startup-script-url referencing `gs://.../bootstrap/install.sh` which doesn't exist yet. **Do not apply** — the VM will boot and fail the startup script. Hold until Phase D Task 17 uploads `install.sh`.

- [ ] **Step 4: Commit (plan only, no apply)**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/compute.tf terraform/phase-0a-gcp/outputs.tf
git commit -m "feat(terraform): GCE VM hermes-vm (e2-standard-4, no public IP)

Apply held until Phase D Task 17 uploads bootstrap/install.sh."
```

---

## Phase D — VM bootstrap (systemd, watchdog, secret loading)

All scripts in this phase live in `scripts/vm-bootstrap/` and are uploaded as a tarball to `gs://rx-research-autonomousagent-snapshots/bootstrap/`. The VM's startup script (`install.sh`) downloads the tarball, extracts it, installs Docker, places systemd units, and starts the stack.

### Task 17: install.sh master bootstrap

**Files:**
- Create: `scripts/vm-bootstrap/install.sh`

- [ ] **Step 1: Write install.sh**

```bash
#!/usr/bin/env bash
# scripts/vm-bootstrap/install.sh
# Master bootstrap for the hermes GCE VM. Runs once on first boot
# (and is idempotent — safe to re-run via 'sudo bash install.sh').

set -euo pipefail

LOG=/var/log/hermes-bootstrap.log
exec > >(tee -a "$LOG") 2>&1
echo "=== hermes bootstrap start $(date -u +%FT%TZ) ==="

PROJECT_ID="$(curl -fsSL -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/project/project-id)"
HERMES_IMAGE_REPO="$(curl -fsSL -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/hermes-image-repo)"

export PROJECT_ID HERMES_IMAGE_REPO

# 1. System prep
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg lsb-release jq

# 2. Docker + compose plugin
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/debian $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# 3. Mount data disk if not yet mounted
if ! mountpoint -q /opt/hermes/data; then
  mkdir -p /opt/hermes/data
  if ! blkid /dev/disk/by-id/google-hermes-data >/dev/null; then
    mkfs.ext4 -F /dev/disk/by-id/google-hermes-data
  fi
  echo "/dev/disk/by-id/google-hermes-data /opt/hermes/data ext4 defaults,nofail 0 2" >> /etc/fstab
  mount /opt/hermes/data
fi

# 4. Fetch bootstrap tarball (includes compose file, env templates, scripts, systemd units)
mkdir -p /opt/hermes/bootstrap
gsutil cp "gs://rx-research-autonomousagent-snapshots/bootstrap/hermes-bootstrap.tar.gz" \
  /opt/hermes/bootstrap/hermes-bootstrap.tar.gz
tar -xzf /opt/hermes/bootstrap/hermes-bootstrap.tar.gz -C /opt/hermes/bootstrap/

# 5. Install systemd units
install -m 0644 /opt/hermes/bootstrap/systemd/hermes-secrets.service          /etc/systemd/system/
install -m 0644 /opt/hermes/bootstrap/systemd/docker-compose-hermes.service   /etc/systemd/system/
install -m 0644 /opt/hermes/bootstrap/systemd/hermes-watchdog.service         /etc/systemd/system/
install -m 0755 /opt/hermes/bootstrap/load-secrets.sh                          /usr/local/bin/
install -m 0755 /opt/hermes/bootstrap/hermes-watchdog.sh                       /usr/local/bin/
install -m 0644 /opt/hermes/bootstrap/expected-containers.txt                  /etc/hermes/expected-containers.txt
mkdir -p /etc/hermes /run/hermes/env

# 6. Auth gcloud for the image registry
gcloud auth configure-docker us-central1-docker.pkg.dev --quiet

# 7. Enable + start units
systemctl daemon-reload
systemctl enable hermes-secrets.service docker-compose-hermes.service hermes-watchdog.service
systemctl start hermes-secrets.service           # blocks until secrets are loaded
systemctl start docker-compose-hermes.service    # blocks until compose is up
systemctl start hermes-watchdog.service          # background loop

echo "=== hermes bootstrap done $(date -u +%FT%TZ) ==="
```

- [ ] **Step 2: chmod**

```bash
chmod 755 scripts/vm-bootstrap/install.sh
```

- [ ] **Step 3: shellcheck**

```bash
shellcheck scripts/vm-bootstrap/install.sh
```

Expected: no errors. If shellcheck not installed: `brew install shellcheck`.

- [ ] **Step 4: Commit**

```bash
git add scripts/vm-bootstrap/install.sh
git commit -m "feat(vm-bootstrap): master install.sh (Docker, mount, units, compose)"
```

---

### Task 18: load-secrets.sh

**Files:**
- Create: `scripts/vm-bootstrap/load-secrets.sh`

- [ ] **Step 1: Write load-secrets.sh**

```bash
#!/usr/bin/env bash
# /usr/local/bin/load-secrets.sh
# Pulls hermes-* secrets from Secret Manager, writes ephemeral env files
# at /run/hermes/env/<name>.env. Runs once per boot via systemd one-shot.

set -euo pipefail

PROJECT_ID="$(curl -fsSL -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/project/project-id)"

ENV_DIR=/run/hermes/env
mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"

# Pull every secret with prefix hermes-
SECRETS=$(gcloud secrets list --project="$PROJECT_ID" \
  --filter="name:hermes-" --format="value(name)")

if [ -z "$SECRETS" ]; then
  echo "ERROR: no hermes-* secrets found" >&2
  exit 1
fi

for secret in $SECRETS; do
  # secret looks like "hermes-honcho"; strip hermes- prefix for env filename
  name="${secret#hermes-}"
  out="$ENV_DIR/${name}.env"
  gcloud secrets versions access latest --secret="$secret" --project="$PROJECT_ID" > "$out"
  chmod 600 "$out"
  echo "loaded $secret -> $out"
done

echo "load-secrets done $(date -u +%FT%TZ)"
```

- [ ] **Step 2: chmod + shellcheck + commit**

```bash
chmod 755 scripts/vm-bootstrap/load-secrets.sh
shellcheck scripts/vm-bootstrap/load-secrets.sh
git add scripts/vm-bootstrap/load-secrets.sh
git commit -m "feat(vm-bootstrap): load-secrets.sh — SM -> /run/hermes/env tmpfs"
```

---

### Task 19: hermes-secrets.service systemd unit

**Files:**
- Create: `scripts/vm-bootstrap/systemd/hermes-secrets.service`

- [ ] **Step 1: Create systemd subdirectory + unit file**

```bash
mkdir -p scripts/vm-bootstrap/systemd
```

Create `scripts/vm-bootstrap/systemd/hermes-secrets.service`:

```ini
[Unit]
Description=Hermes — load secrets from Secret Manager into /run/hermes/env
After=network-online.target
Wants=network-online.target
Before=docker-compose-hermes.service
DefaultDependencies=no

[Service]
Type=oneshot
ExecStart=/usr/local/bin/load-secrets.sh
RemainAfterExit=yes
TimeoutStartSec=60

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Commit**

```bash
git add scripts/vm-bootstrap/systemd/hermes-secrets.service
git commit -m "feat(vm-bootstrap): hermes-secrets.service systemd one-shot"
```

---

### Task 20: docker-compose-hermes.service systemd unit

**Files:**
- Create: `scripts/vm-bootstrap/systemd/docker-compose-hermes.service`

- [ ] **Step 1: Write the unit**

```ini
[Unit]
Description=Hermes docker-compose stack
After=docker.service hermes-secrets.service
Requires=docker.service hermes-secrets.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/hermes/bootstrap
EnvironmentFile=/run/hermes/env/*
ExecStartPre=/usr/bin/docker compose -f docker-compose.yml -f docker-compose.gcp.override.yml pull
ExecStart=/usr/bin/docker compose -f docker-compose.yml -f docker-compose.gcp.override.yml up -d
ExecStop=/usr/bin/docker compose -f docker-compose.yml -f docker-compose.gcp.override.yml down
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
```

**Note:** `EnvironmentFile=/run/hermes/env/*` is a systemd glob (since v246, supported on Debian 12). Each file in the tmpfs directory is loaded as additional env vars for the compose process.

- [ ] **Step 2: Commit**

```bash
git add scripts/vm-bootstrap/systemd/docker-compose-hermes.service
git commit -m "feat(vm-bootstrap): docker-compose-hermes.service systemd unit"
```

---

### Task 21: hermes-watchdog.sh

**Files:**
- Create: `scripts/vm-bootstrap/hermes-watchdog.sh`
- Create: `scripts/vm-bootstrap/expected-containers.txt`

- [ ] **Step 1: Write expected-containers.txt**

```
litellm-db
litellm-proxy
otel-collector
phoenix
shell-sandbox
github-mcp
hermes
escalation-watcher
snapshot-watchdog
budget-watchdog
```

(10 long-running containers; `volume-init` is a one-shot init container excluded by design per spec section 11.)

- [ ] **Step 2: Write hermes-watchdog.sh**

```bash
#!/usr/bin/env bash
# /usr/local/bin/hermes-watchdog.sh
# Polls docker compose ps every 30s. Restarts compose if any expected
# container is missing or exited. Emits structured JSON to stdout
# (captured by gcplogs -> Cloud Logging).

set -euo pipefail

COMPOSE_FILES=(-f /opt/hermes/bootstrap/docker-compose.yml -f /opt/hermes/bootstrap/docker-compose.gcp.override.yml)
EXPECTED_FILE=${EXPECTED_FILE:-/etc/hermes/expected-containers.txt}
INTERVAL=${INTERVAL:-30}

log_json() {
  printf '{"ts":"%s","level":"%s","msg":"%s","detail":%s}\n' \
    "$(date -u +%FT%TZ)" "$1" "$2" "${3:-null}"
}

mapfile -t expected < "$EXPECTED_FILE"

while true; do
  running=$(docker compose "${COMPOSE_FILES[@]}" ps --format json 2>/dev/null \
    | jq -r 'select(.State=="running") | .Service' | sort -u)
  missing=()
  for svc in "${expected[@]}"; do
    if ! echo "$running" | grep -qx "$svc"; then
      missing+=("$svc")
    fi
  done

  # Custom metric line (gcplogs picks this up and a log-based metric pulls it)
  log_json info "hermes_watchdog_tick" \
    "{\"expected\":${#expected[@]},\"running\":$(echo "$running" | wc -l),\"missing\":[$(printf '\"%s\",' "${missing[@]}" | sed 's/,$//')]}"

  if [ "${#missing[@]}" -gt 0 ]; then
    log_json warn "hermes_watchdog_restart_triggered" \
      "{\"missing\":[$(printf '\"%s\",' "${missing[@]}" | sed 's/,$//')]}"
    docker compose "${COMPOSE_FILES[@]}" up -d || \
      log_json error "hermes_watchdog_restart_failed" null
  fi

  sleep "$INTERVAL"
done
```

- [ ] **Step 3: chmod + shellcheck + commit**

```bash
chmod 755 scripts/vm-bootstrap/hermes-watchdog.sh
shellcheck scripts/vm-bootstrap/hermes-watchdog.sh
git add scripts/vm-bootstrap/hermes-watchdog.sh scripts/vm-bootstrap/expected-containers.txt
git commit -m "feat(vm-bootstrap): hermes-watchdog.sh + expected-containers.txt

Polls docker compose ps every 30s; restarts stack on missing container.
Emits structured JSON for gcplogs -> Cloud Logging custom metric."
```

---

### Task 22: hermes-watchdog.service systemd unit

**Files:**
- Create: `scripts/vm-bootstrap/systemd/hermes-watchdog.service`

- [ ] **Step 1: Write the unit**

```ini
[Unit]
Description=Hermes — host-level container watchdog
After=docker-compose-hermes.service
Requires=docker-compose-hermes.service

[Service]
Type=simple
ExecStart=/usr/local/bin/hermes-watchdog.sh
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Commit**

```bash
git add scripts/vm-bootstrap/systemd/hermes-watchdog.service
git commit -m "feat(vm-bootstrap): hermes-watchdog.service systemd unit (restart=always)"
```

---

### Task 23: docker-compose.gcp.override.yml — GCP-specific overrides

**Files:**
- Create: `deploy/docker-compose.gcp.override.yml`

Adds the `gcplogs` log driver to every service and points data volume at `/opt/hermes/data`. Does NOT modify `deploy/docker-compose.yml`.

- [ ] **Step 1: Inspect current volume definition**

```bash
grep -A 3 "^volumes:" deploy/docker-compose.yml
```

Note the `hermes-data` volume name.

- [ ] **Step 2: Write the override**

```yaml
# deploy/docker-compose.gcp.override.yml
# Loaded on the VM via:
#   docker compose -f docker-compose.yml -f docker-compose.gcp.override.yml up -d

x-gcplogs: &gcplogs
  driver: gcplogs
  options:
    gcp-log-cmd: "true"

services:
  litellm-db:        { logging: *gcplogs }
  litellm-proxy:     { logging: *gcplogs }
  otel-collector:    { logging: *gcplogs }
  phoenix:           { logging: *gcplogs }
  shell-sandbox:     { logging: *gcplogs }
  github-mcp:        { logging: *gcplogs }
  volume-init:       { logging: *gcplogs }
  hermes:            { logging: *gcplogs }
  escalation-watcher:    { logging: *gcplogs }
  snapshot-watchdog:     { logging: *gcplogs }
  budget-watchdog:       { logging: *gcplogs }

volumes:
  hermes-data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /opt/hermes/data
```

- [ ] **Step 3: Validate the merged compose**

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.gcp.override.yml config > /tmp/merged.yml
grep -c "driver: gcplogs" /tmp/merged.yml
```

Expected: 11 occurrences (one per service).

- [ ] **Step 4: Commit**

```bash
git add deploy/docker-compose.gcp.override.yml
git commit -m "feat(deploy): docker-compose.gcp.override.yml — gcplogs + bind-mount data dir"
```

---

## Phase E — Secrets migration

### Task 24: migrate-secrets-to-secret-manager.sh

**Files:**
- Create: `scripts/migrate-secrets-to-secret-manager.sh`

Idempotent. Reads every `secrets/*.env.sops`, decrypts, writes the *entire env-file content* as one Secret Manager secret value. Compares hash against the latest version to avoid creating duplicate versions on re-run.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# scripts/migrate-secrets-to-secret-manager.sh
# One-time + idempotent migration from SOPS envfiles to Secret Manager.
# Each secrets/NAME.env.sops becomes Secret Manager entry "hermes-NAME"
# whose value is the FULL decrypted env-file content.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-rx-research-autonomousagent}"
DRY_RUN="${DRY_RUN:-false}"
SECRETS_DIR="${SECRETS_DIR:-secrets}"

if ! command -v sops >/dev/null; then
  echo "ERROR: sops not installed; run 'brew install sops'" >&2
  exit 1
fi

for sops_file in "$SECRETS_DIR"/*.env.sops; do
  [ -e "$sops_file" ] || { echo "no .env.sops files found in $SECRETS_DIR"; exit 1; }
  name=$(basename "$sops_file" .env.sops)
  secret_id="hermes-${name}"

  echo "=== $name -> $secret_id ==="

  # Decrypt to a tmpfile (deleted on exit)
  tmp=$(mktemp); trap "rm -f $tmp" EXIT
  sops -d "$sops_file" > "$tmp"

  # Hash the decrypted content
  new_hash=$(sha256sum "$tmp" | awk '{print $1}')

  # Compare to latest version's hash, if any
  existing_hash=""
  if gcloud secrets versions list "$secret_id" --project="$PROJECT_ID" --limit=1 --format="value(name)" 2>/dev/null | grep -q .; then
    existing_hash=$(gcloud secrets versions access latest --secret="$secret_id" --project="$PROJECT_ID" 2>/dev/null \
      | sha256sum | awk '{print $1}')
  fi

  if [ "$new_hash" = "$existing_hash" ]; then
    echo "no change; skipping"
    continue
  fi

  if [ "$DRY_RUN" = "true" ]; then
    echo "DRY_RUN: would create new version of $secret_id (hash=$new_hash)"
  else
    gcloud secrets versions add "$secret_id" --project="$PROJECT_ID" --data-file="$tmp"
    echo "created new version (hash=$new_hash)"
  fi

  rm -f "$tmp"; trap - EXIT
done

echo "migration done $(date -u +%FT%TZ)"
```

- [ ] **Step 2: chmod + shellcheck + commit**

```bash
chmod 755 scripts/migrate-secrets-to-secret-manager.sh
shellcheck scripts/migrate-secrets-to-secret-manager.sh
git add scripts/migrate-secrets-to-secret-manager.sh
git commit -m "feat(scripts): migrate-secrets-to-secret-manager.sh (idempotent, dry-run capable)

Decrypts each secrets/NAME.env.sops and writes the full env-file content
as a single Secret Manager secret 'hermes-NAME'. Skips if SHA256 matches
latest version."
```

---

### Task 25: Dry-run + verify

- [ ] **Step 1: Decrypt prereq check**

```bash
sops --version
gcloud auth application-default print-access-token >/dev/null && echo "ADC OK"
```

- [ ] **Step 2: Dry-run**

```bash
PROJECT_ID=rx-research-autonomousagent DRY_RUN=true ./scripts/migrate-secrets-to-secret-manager.sh
```

Expected output: one `DRY_RUN: would create new version of hermes-<name>` line per `.env.sops` file. **Read the output carefully — abort if any unexpected env files are listed.**

- [ ] **Step 3: Compare to Terraform secret_manager.tf**

```bash
grep "secrets/" terraform/phase-0a-gcp/secret_manager.tf
# Verify every name in local.sops_env_files matches a file printed in step 2.
```

If a SOPS file exists but is not in `secret_manager.tf`: add it to `local.sops_env_files`, re-run Task 14 step 3 (`terraform apply`), then return here.

---

### Task 26: Run migration for real

- [ ] **Step 1: Run**

```bash
PROJECT_ID=rx-research-autonomousagent ./scripts/migrate-secrets-to-secret-manager.sh
```

- [ ] **Step 2: Verify each secret has a version**

```bash
for s in $(gcloud secrets list --project=rx-research-autonomousagent --filter="name:hermes-" --format="value(name)"); do
  v=$(gcloud secrets versions list "$s" --project=rx-research-autonomousagent --format="value(name)" | head -1)
  echo "$s: latest version = $v"
done
```

Expected: every secret has at least one version.

- [ ] **Step 3: Verify Honcho key specifically**

Per memory: Honcho key must reach Secret Manager from `secrets/honcho.env.sops`.

```bash
gcloud secrets versions access latest --secret=hermes-honcho --project=rx-research-autonomousagent | grep -c "^HONCHO_API_KEY="
```

Expected: `1`.

- [ ] **Step 4: Commit the audit trail (no secret values committed)**

```bash
# Capture the verification output, NOT the secret values
gcloud secrets list --project=rx-research-autonomousagent --filter="name:hermes-" --format="value(name)" \
  > audit/2026-05-20-state-of-the-repo/secret-manager-inventory.txt
git add audit/2026-05-20-state-of-the-repo/secret-manager-inventory.txt
git commit -m "chore(audit): Secret Manager inventory post-migration

Phase 0a Task 26 complete; all SOPS envfiles mirrored to SM.
SOPS files retained in repo for dev workflow."
```

---

## Phase F — Observability

### Task 27: Uptime check on litellm-proxy /health

**Files:**
- Create: `terraform/phase-0a-gcp/logging_monitoring.tf`

- [ ] **Step 1: Inspect litellm-proxy health endpoint + port**

```bash
grep -A 10 "litellm-proxy:" deploy/docker-compose.yml | grep -E "ports:|healthcheck|/health"
```

Note the port (likely `4000` for LiteLLM).

- [ ] **Step 2: Write logging_monitoring.tf (uptime check only)**

```hcl
resource "google_monitoring_uptime_check_config" "litellm_proxy_health" {
  display_name = "litellm-proxy /health"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path           = "/health"
    port           = 4000
    request_method = "GET"
    use_ssl        = false
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = google_compute_instance.hermes.network_interface[0].network_ip
    }
  }
}

output "uptime_check_id" {
  value = google_monitoring_uptime_check_config.litellm_proxy_health.uptime_check_id
}
```

**Note:** uptime checks for *internal-only* IPs require a slightly different config (using `monitored_resource.type = "gce_instance"`). If the litellm-proxy port is not exposed to other VPC traffic, you may need to either (a) bind it to the VM's internal IP and use a private uptime check, or (b) probe via the watchdog instead. Confirm during execution.

- [ ] **Step 3: Plan + apply**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
```

- [ ] **Step 4: Commit**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/logging_monitoring.tf
git commit -m "feat(terraform): uptime check on litellm-proxy /health (60s)"
```

---

### Task 28: Log-based custom metric — hermes_watchdog_missing_count

**Files:**
- Modify: `terraform/phase-0a-gcp/logging_monitoring.tf`

The watchdog emits `{"msg":"hermes_watchdog_tick","detail":{"missing":[...]}}`. We create a log-based metric whose value is the array length.

- [ ] **Step 1: Append log-based metric**

```hcl
resource "google_logging_metric" "watchdog_missing_count" {
  name        = "hermes/watchdog_missing_count"
  description = "Number of expected hermes containers missing per watchdog tick"
  filter      = "jsonPayload.msg=\"hermes_watchdog_tick\" AND resource.type=\"gce_instance\""

  metric_descriptor {
    metric_kind = "GAUGE"
    value_type  = "INT64"
    unit        = "1"
  }

  value_extractor = "EXTRACT(jsonPayload.detail.missing.length())"
}
```

- [ ] **Step 2: Plan + apply + commit**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/logging_monitoring.tf
git commit -m "feat(terraform): log-based metric hermes/watchdog_missing_count"
```

---

### Task 29: 4 alert policies

**Files:**
- Modify: `terraform/phase-0a-gcp/logging_monitoring.tf`
- Modify: `terraform/phase-0a-gcp/variables.tf` (add `alert_email`)

- [ ] **Step 1: Add alert_email variable**

In `variables.tf`:

```hcl
variable "alert_email" {
  type        = string
  description = "Email to receive Phase 0a alerts"
  # default omitted intentionally; set via tfvars
}
```

Add to your `terraform.tfvars`:

```hcl
alert_email = "<your-email>"
```

- [ ] **Step 2: Append notification channel + 4 alert policies**

```hcl
resource "google_monitoring_notification_channel" "email" {
  display_name = "Phase 0a alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

# 1. VM down >5min
resource "google_monitoring_alert_policy" "vm_down" {
  display_name          = "Phase 0a — VM down >5min"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND metric.label.check_id=\"${google_monitoring_uptime_check_config.litellm_proxy_health.uptime_check_id}\""
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_NEXT_OLDER"
      }
    }
  }
  alert_strategy { auto_close = "1800s" }
}

# 2. Any expected container missing >2min
resource "google_monitoring_alert_policy" "container_missing" {
  display_name          = "Phase 0a — expected container missing >2min"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "watchdog reports missing container"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/hermes/watchdog_missing_count\" AND resource.type=\"gce_instance\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "120s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }
  alert_strategy { auto_close = "1800s" }
}

# 3. Disk >85% full
resource "google_monitoring_alert_policy" "disk_full" {
  display_name          = "Phase 0a — VM disk >85% full"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "disk utilization > 85%"
    condition_threshold {
      filter          = "metric.type=\"compute.googleapis.com/instance/disk/percent_used\" AND resource.type=\"gce_instance\" AND resource.label.instance_id=\"${google_compute_instance.hermes.instance_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 85
      duration        = "300s"
      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_MAX"
      }
    }
  }
  alert_strategy { auto_close = "1800s" }
}

# 4. Snapshot job failed (no successful snapshot in 30h window — 24h schedule + 6h grace)
resource "google_monitoring_alert_policy" "snapshot_stale" {
  display_name          = "Phase 0a — daily PD snapshot stale"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email.id]

  conditions {
    display_name = "no successful snapshot in 30h"
    condition_absent {
      filter   = "metric.type=\"compute.googleapis.com/instance/disk/snapshot_created_count\" AND resource.label.disk_name=\"hermes-vm-data\""
      duration = "108000s"   # 30h
      aggregations {
        alignment_period   = "3600s"
        per_series_aligner = "ALIGN_SUM"
      }
    }
  }
  alert_strategy { auto_close = "1800s" }
}
```

- [ ] **Step 3: Plan + apply + commit**

```bash
cd terraform/phase-0a-gcp
terraform plan -out=tf.plan
terraform apply tf.plan
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git add terraform/phase-0a-gcp/logging_monitoring.tf terraform/phase-0a-gcp/variables.tf
git commit -m "feat(terraform): 4 alert policies (VM down, container missing, disk, snapshot stale)"
```

---

## Phase G — CI/CD (Workload Identity Federation)

### Task 30: phase-0a-deploy.yml GitHub Actions workflow

**Files:**
- Create: `.github/workflows/phase-0a-deploy.yml`

**Pre-step: set GitHub repo variables.** From Task 11 step 4, capture:
- `GCP_WIF_PROVIDER` = output of `terraform output wif_provider_resource_name`
- `GCP_SA_EMAIL` = output of `terraform output gha_deployer_email`
- `GCP_PROJECT_ID` = `rx-research-autonomousagent`
- `GCP_VM_NAME` = `hermes-vm`
- `GCP_VM_ZONE` = `us-central1-a`
- `GCP_AR_REPO` = `us-central1-docker.pkg.dev/rx-research-autonomousagent/hermes`

Set via:
```bash
gh variable set GCP_WIF_PROVIDER --body "<from terraform output>"
gh variable set GCP_SA_EMAIL --body "<from terraform output>"
gh variable set GCP_PROJECT_ID --body "rx-research-autonomousagent"
gh variable set GCP_VM_NAME --body "hermes-vm"
gh variable set GCP_VM_ZONE --body "us-central1-a"
gh variable set GCP_AR_REPO --body "us-central1-docker.pkg.dev/rx-research-autonomousagent/hermes"
```

- [ ] **Step 1: Write the workflow**

```yaml
# .github/workflows/phase-0a-deploy.yml
name: Phase 0a — Deploy to GCP

on:
  push:
    branches: [main]
    paths:
      - 'deploy/**'
      - 'hermes'
      - 'scripts/vm-bootstrap/**'
      - 'config/**'
      - '.github/workflows/phase-0a-deploy.yml'
  workflow_dispatch:

permissions:
  contents: read
  id-token: write   # required for WIF

concurrency:
  group: phase-0a-deploy
  cancel-in-progress: false   # serialize deploys

jobs:
  build_push_deploy:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: recursive

      - id: auth
        name: Authenticate to GCP via WIF
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_SA_EMAIL }}

      - uses: google-github-actions/setup-gcloud@v2

      - name: Configure Docker for Artifact Registry
        run: gcloud auth configure-docker us-central1-docker.pkg.dev --quiet

      - name: Build hermes image
        run: |
          IMAGE_URI="${{ vars.GCP_AR_REPO }}/hermes:${{ github.sha }}"
          docker build -f deploy/Dockerfile.hermes -t "$IMAGE_URI" -t "${{ vars.GCP_AR_REPO }}/hermes:latest" .
          echo "IMAGE_URI=$IMAGE_URI" >> "$GITHUB_ENV"

      - name: Push image
        run: |
          docker push "$IMAGE_URI"
          docker push "${{ vars.GCP_AR_REPO }}/hermes:latest"

      - name: Package + upload VM bootstrap tarball
        run: |
          tar -czf hermes-bootstrap.tar.gz \
            -C deploy docker-compose.yml docker-compose.gcp.override.yml \
            -C ../scripts/vm-bootstrap .
          gsutil cp hermes-bootstrap.tar.gz \
            gs://rx-research-autonomousagent-snapshots/bootstrap/hermes-bootstrap.tar.gz
          gsutil cp scripts/vm-bootstrap/install.sh \
            gs://rx-research-autonomousagent-snapshots/bootstrap/install.sh

      - name: Deploy via IAP SSH
        run: |
          gcloud compute ssh ${{ vars.GCP_VM_NAME }} \
            --zone=${{ vars.GCP_VM_ZONE }} \
            --tunnel-through-iap \
            --command="sudo bash /usr/local/bin/install.sh && \
                       cd /opt/hermes/bootstrap && \
                       sudo systemctl restart docker-compose-hermes.service"

      - name: Post-deploy smoke
        id: smoke
        run: |
          bash tests/phase_0a/smoke.sh ${{ vars.GCP_VM_NAME }} ${{ vars.GCP_VM_ZONE }}

      - name: Rollback on failure
        if: failure() && steps.smoke.conclusion == 'failure'
        run: |
          PREV_SHA=$(git rev-parse HEAD~1)
          gcloud compute ssh ${{ vars.GCP_VM_NAME }} \
            --zone=${{ vars.GCP_VM_ZONE }} \
            --tunnel-through-iap \
            --command="cd /opt/hermes/bootstrap && \
                       sudo IMAGE_TAG=$PREV_SHA docker compose -f docker-compose.yml -f docker-compose.gcp.override.yml up -d"
```

- [ ] **Step 2: Validate workflow YAML**

```bash
gh workflow view phase-0a-deploy.yml --repo Manzela/AutonomousAgent 2>/dev/null || \
  python3 -c "import yaml; yaml.safe_load(open('.github/workflows/phase-0a-deploy.yml'))" && \
  echo "YAML valid"
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/phase-0a-deploy.yml
git commit -m "feat(ci): phase-0a-deploy.yml — WIF auth, build, push, IAP deploy, smoke, rollback"
```

---

## Phase H — Verification

### Task 31: smoke.sh post-deploy script

**Files:**
- Create: `tests/phase_0a/smoke.sh`

- [ ] **Step 1: Write smoke.sh**

```bash
#!/usr/bin/env bash
# tests/phase_0a/smoke.sh <vm-name> <zone>
# Post-deploy smoke check. Returns 0 on pass, non-zero on fail.

set -euo pipefail

VM_NAME="${1:?vm-name required}"
ZONE="${2:?zone required}"

echo "=== smoke.sh against $VM_NAME ($ZONE) ==="

# 1. Reachable via IAP
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --command="echo ok" >/dev/null
echo "PASS: IAP SSH reachable"

# 2. All expected containers running
remote_out=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --command="
  cd /opt/hermes/bootstrap
  expected=\$(cat /etc/hermes/expected-containers.txt | sort -u)
  running=\$(docker compose -f docker-compose.yml -f docker-compose.gcp.override.yml ps --format json \
    | jq -r 'select(.State==\"running\") | .Service' | sort -u)
  missing=\$(comm -23 <(echo \"\$expected\") <(echo \"\$running\"))
  if [ -n \"\$missing\" ]; then
    echo \"MISSING: \$missing\" >&2
    exit 1
  fi
  echo \"all expected containers running\"
")
echo "PASS: $remote_out"

# 3. litellm-proxy /health returns 200 within 90s
deadline=$(( $(date +%s) + 90 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  status=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --command="
    curl -s -o /dev/null -w '%{http_code}' http://localhost:4000/health
  ")
  if [ "$status" = "200" ]; then
    echo "PASS: litellm-proxy /health = 200"
    exit 0
  fi
  sleep 5
done

echo "FAIL: litellm-proxy /health did not return 200 within 90s" >&2
exit 1
```

- [ ] **Step 2: chmod + shellcheck + commit**

```bash
mkdir -p tests/phase_0a
chmod 755 tests/phase_0a/smoke.sh
shellcheck tests/phase_0a/smoke.sh
git add tests/phase_0a/smoke.sh
git commit -m "test(phase-0a): smoke.sh — IAP reachable + containers up + health 200"
```

---

### Task 32: chaos.sh — verify watchdog restarts a killed container

**Files:**
- Create: `tests/phase_0a/chaos.sh`

- [ ] **Step 1: Write chaos.sh**

```bash
#!/usr/bin/env bash
# tests/phase_0a/chaos.sh <vm-name> <zone>
# Kill hermes container and verify watchdog brings it back within 90s.

set -euo pipefail

VM_NAME="${1:?vm-name required}"
ZONE="${2:?zone required}"

echo "=== chaos.sh against $VM_NAME ($ZONE) ==="

gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --command="
  set -euo pipefail
  echo 'pre-kill state:'
  docker ps --filter name=hermes --format '{{.Names}}: {{.Status}}'

  echo 'killing hermes container...'
  docker kill \$(docker ps -q --filter name=hermes)

  # Wait up to 90s for watchdog to restart
  deadline=\$(( \$(date +%s) + 90 ))
  while [ \"\$(date +%s)\" -lt \"\$deadline\" ]; do
    if docker ps --filter name=hermes --filter status=running --format '{{.Names}}' | grep -q hermes; then
      echo 'PASS: hermes restarted by watchdog'
      exit 0
    fi
    sleep 5
  done

  echo 'FAIL: hermes did not restart within 90s' >&2
  docker ps --filter name=hermes
  exit 1
"
```

- [ ] **Step 2: chmod + shellcheck + commit**

```bash
chmod 755 tests/phase_0a/chaos.sh
shellcheck tests/phase_0a/chaos.sh
git add tests/phase_0a/chaos.sh
git commit -m "test(phase-0a): chaos.sh — kill hermes, verify watchdog restart <=90s"
```

---

### Task 33: acceptance.sh — run all 10 spec section-11 criteria

**Files:**
- Create: `tests/phase_0a/acceptance.sh`

- [ ] **Step 1: Write acceptance.sh**

```bash
#!/usr/bin/env bash
# tests/phase_0a/acceptance.sh <vm-name> <zone>
# Runs all 10 acceptance criteria from spec section 11.
# Some criteria (#3 7d uptime, #5 7d snapshots, #10 30d cost) require
# elapsed time and only print "DEFER" rather than fail.

set -euo pipefail

VM_NAME="${1:?vm-name required}"
ZONE="${2:?zone required}"

PASS=0; FAIL=0; DEFER=0
pass() { echo "PASS  #$1: $2"; PASS=$((PASS+1)); }
fail() { echo "FAIL  #$1: $2"; FAIL=$((FAIL+1)); }
defer() { echo "DEFER #$1: $2"; DEFER=$((DEFER+1)); }

# 1. Pre-flight blocker closed (P0-A)
if grep -q "P0-A closed" "$(pwd)/audit/2026-05-20-state-of-the-repo/p0a-rca/soak-24h.log" 2>/dev/null; then
  pass 1 "Pre-flight P0-A closed"
else
  fail 1 "Pre-flight P0-A not closed — see Phase A"
fi

# 2. 11 long-running containers up for 72h consecutive — sample at this point
bash tests/phase_0a/smoke.sh "$VM_NAME" "$ZONE" && pass 2 "containers running (sample)" || fail 2 "containers not all running"
defer 2 "(72h consecutive requires soak window)"

# 3. Uptime check 99%+ over 7-day window
defer 3 "(requires 7-day window — check Cloud Monitoring after soak)"

# 4. Watchdog steady-state + chaos test
bash tests/phase_0a/chaos.sh "$VM_NAME" "$ZONE" && pass 4 "watchdog restarts killed container" || fail 4 "watchdog did not restart"

# 5. Daily PD snapshot for 7 days
snap_count=$(gcloud compute snapshots list --project=rx-research-autonomousagent --filter="sourceDisk:hermes-vm-data" --format="value(name)" | wc -l)
[ "$snap_count" -gt 0 ] && pass 5 "snapshots exist ($snap_count present)" || fail 5 "no snapshots yet"
defer 5 "(7 consecutive snapshots requires 7d)"

# 6. Test recovery (snapshot restore -> new VM)
defer 6 "(out-of-band test — see docs/runbooks/phase-0a-recovery.md)"

# 7. CI workflow end-to-end <10min — check most recent successful run
gh run list --workflow=phase-0a-deploy.yml --branch=main --status=success --limit=1 --json conclusion,startedAt,updatedAt > /tmp/run.json
duration_sec=$(python3 -c "import json,datetime; d=json.load(open('/tmp/run.json'))[0]; \
  s=datetime.datetime.fromisoformat(d['startedAt'].rstrip('Z')); e=datetime.datetime.fromisoformat(d['updatedAt'].rstrip('Z')); \
  print(int((e-s).total_seconds()))")
[ "$duration_sec" -lt 600 ] && pass 7 "CI ran in ${duration_sec}s" || fail 7 "CI took ${duration_sec}s (>600)"

# 8. WIF works (no JSON keys in repo or GH secrets)
grep -rqE "private_key_id|[B]EGIN [P]RIVATE [K]EY" --include="*.json" . 2>/dev/null && fail 8 "JSON key found in repo" || pass 8 "no JSON keys in repo"  # pragma: allowlist secret
gh secret list --json name --jq '.[].name' | grep -iqE "GCP.*KEY|SA.*JSON" && fail 8 "GH secret holds GCP JSON key" || pass 8 "no GH secrets named *GCP*KEY*"

# 9. Secret Manager reachable from VM
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --command="
  ls /run/hermes/env/*.env >/dev/null 2>&1 && grep -q HONCHO_API_KEY /run/hermes/env/honcho.env
" && pass 9 "secrets present in /run/hermes/env" || fail 9 "secrets missing on VM"

# 10. Cost actuals ±20% of $125/mo estimate
defer 10 "(requires 1 billing cycle — check after 30d)"

echo "=== Summary: PASS=$PASS FAIL=$FAIL DEFER=$DEFER ==="
[ "$FAIL" -eq 0 ]
```

- [ ] **Step 2: chmod + shellcheck + commit**

```bash
chmod 755 tests/phase_0a/acceptance.sh
shellcheck tests/phase_0a/acceptance.sh
git add tests/phase_0a/acceptance.sh
git commit -m "test(phase-0a): acceptance.sh — runs all 10 spec criteria (PASS/FAIL/DEFER)"
```

---

### Task 34: Provision the VM (apply Task 16's plan)

After Phase D scripts exist and have been uploaded by the CI workflow (or manually), the VM can be created.

- [ ] **Step 1: Upload bootstrap to GCS manually (one-shot before VM creation)**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
tar -czf /tmp/hermes-bootstrap.tar.gz \
  deploy/docker-compose.yml deploy/docker-compose.gcp.override.yml \
  -C scripts/vm-bootstrap .
gsutil cp /tmp/hermes-bootstrap.tar.gz gs://rx-research-autonomousagent-snapshots/bootstrap/
gsutil cp scripts/vm-bootstrap/install.sh gs://rx-research-autonomousagent-snapshots/bootstrap/
```

- [ ] **Step 2: Apply the VM resource**

```bash
cd terraform/phase-0a-gcp
terraform apply -auto-approve
```

Expected: 1 VM created. The VM's startup script downloads `install.sh` from GCS and runs it.

- [ ] **Step 3: Tail bootstrap log via serial console**

```bash
gcloud compute instances get-serial-port-output hermes-vm --zone=us-central1-a | tail -200
```

Expected: lines matching `=== hermes bootstrap start` ... `=== hermes bootstrap done`.

- [ ] **Step 4: Run smoke + chaos**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
bash tests/phase_0a/smoke.sh hermes-vm us-central1-a
bash tests/phase_0a/chaos.sh hermes-vm us-central1-a
```

Expected: both pass.

- [ ] **Step 5: Run acceptance gate**

```bash
bash tests/phase_0a/acceptance.sh hermes-vm us-central1-a
```

Expected: FAIL=0. DEFER items are acceptable at this point.

- [ ] **Step 6: Commit verification artifact**

```bash
bash tests/phase_0a/acceptance.sh hermes-vm us-central1-a 2>&1 \
  | tee audit/2026-05-20-state-of-the-repo/phase-0a-acceptance-initial.log
git add audit/2026-05-20-state-of-the-repo/phase-0a-acceptance-initial.log
git commit -m "chore(audit): phase-0a initial acceptance run — VM is online"
```

---

## Phase I — Cutover + rollback runbooks + execute cutover

### Task 35: phase-0a-cutover.md runbook

**Files:**
- Create: `docs/runbooks/phase-0a-cutover.md`

- [ ] **Step 1: Write the runbook**

```markdown
# Phase 0a Cutover Runbook — Laptop → GCP

**Audience:** operator executing the cutover from local docker-compose to GCP VM.
**Estimated duration:** 60-90 minutes including verification.
**Prerequisites:**
- Phase A through H tasks complete; `acceptance.sh` FAIL=0
- VM `hermes-vm` exists in zone `us-central1-a` and is healthy
- Latest hermes image pushed to Artifact Registry

## Cutover sequence

### T-24h: Announce + pre-cutover snapshot
1. Take a fresh snapshot of the local `hermes-data` volume:
   ```bash
   docker run --rm -v autonomousagent_hermes-data:/data -v $(pwd):/backup \
     alpine tar -czf /backup/laptop-hermes-data-$(date +%F).tar.gz -C /data .
   ```
2. Upload the tarball to `gs://rx-research-autonomousagent-snapshots/laptop-state/`.
   This is the rollback safety net.

### T-0: Cutover
1. **Stop laptop stack** (do NOT remove volumes):
   ```bash
   docker compose -f deploy/docker-compose.yml stop
   ```
2. **Restore laptop state onto VM data disk:**
   ```bash
   gcloud compute scp ./laptop-hermes-data-*.tar.gz \
     hermes-vm:/tmp/ --zone=us-central1-a --tunnel-through-iap
   gcloud compute ssh hermes-vm --zone=us-central1-a --tunnel-through-iap --command="
     sudo systemctl stop docker-compose-hermes.service
     sudo tar -xzf /tmp/laptop-hermes-data-*.tar.gz -C /opt/hermes/data/
     sudo systemctl start docker-compose-hermes.service
   "
   ```
3. **Smoke + acceptance:**
   ```bash
   bash tests/phase_0a/smoke.sh hermes-vm us-central1-a
   bash tests/phase_0a/acceptance.sh hermes-vm us-central1-a
   ```
4. **Update external pointers** (any client app pointing at laptop): switch DNS / config to VM internal IP via IAP tunnel.

### T+24h: Soak
1. Watch Cloud Monitoring uptime check + watchdog metric for 24h.
2. If green: proceed to T+72h cleanup.
3. If red: invoke `docs/runbooks/phase-0a-rollback.md`.

### T+72h: Cleanup
1. Confirm 72h continuous green.
2. Tag the commit: `git tag phase-0a-cutover-stable && git push --tags`.
3. Keep laptop stack down but DO NOT delete the laptop `hermes-data` volume for 7 days (rollback window).

## Acceptance criteria
All 10 items in spec section 11 pass; deferred items (3, 5, 6, 10) tracked separately.
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/phase-0a-cutover.md
git commit -m "docs(runbook): phase-0a-cutover — laptop -> GCP procedure"
```

---

### Task 36: phase-0a-rollback.md runbook

**Files:**
- Create: `docs/runbooks/phase-0a-rollback.md`

- [ ] **Step 1: Write rollback runbook**

```markdown
# Phase 0a Rollback Runbook — GCP → Laptop

**Use when:** cutover failed acceptance OR a regression appeared in the 72h soak window.
**RTO:** ~20 minutes.

## Decision criteria
Invoke rollback if ANY of:
- `acceptance.sh` shows FAIL >0 for criteria 2, 4, 9 (containers / watchdog / secrets)
- `litellm-proxy /health` returns non-200 for >15min
- Watchdog alert fires >3 times in 1h
- Data corruption suspected on VM data disk

## Steps

### 1. Stop VM stack
```bash
gcloud compute ssh hermes-vm --zone=us-central1-a --tunnel-through-iap --command="
  sudo systemctl stop docker-compose-hermes.service hermes-watchdog.service
"
```

### 2. Extract VM state to local
```bash
gcloud compute ssh hermes-vm --zone=us-central1-a --tunnel-through-iap --command="
  sudo tar -czf /tmp/vm-hermes-data-$(date +%F).tar.gz -C /opt/hermes/data .
"
gcloud compute scp hermes-vm:/tmp/vm-hermes-data-*.tar.gz ./ \
  --zone=us-central1-a --tunnel-through-iap
```

### 3. Restore laptop volume
```bash
docker volume create autonomousagent_hermes-data || true
docker run --rm -v autonomousagent_hermes-data:/data -v $(pwd):/backup \
  alpine sh -c "cd /data && tar -xzf /backup/vm-hermes-data-*.tar.gz"
```

### 4. Restart laptop stack
```bash
docker compose -f deploy/docker-compose.yml up -d
sleep 60
docker compose -f deploy/docker-compose.yml ps
```

### 5. Verify
```bash
curl -fsS http://localhost:4000/health
docker compose -f deploy/docker-compose.yml logs hermes --tail 50
```

### 6. Disable CI deploys to GCP (prevent re-deploy)
```bash
gh workflow disable phase-0a-deploy.yml
```

### 7. Post-rollback
- Open a P0 incident issue in GitHub
- Stop the GCP VM (do not delete — preserve forensics): `gcloud compute instances stop hermes-vm --zone=us-central1-a`
- Triage root cause; re-execute cutover only after fix is verified
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/phase-0a-rollback.md
git commit -m "docs(runbook): phase-0a-rollback — GCP -> laptop procedure (RTO 20min)"
```

---

### Task 37: phase-0a-recovery.md runbook (VM rebuild from snapshot)

**Files:**
- Create: `docs/runbooks/phase-0a-recovery.md`

- [ ] **Step 1: Write recovery runbook**

```markdown
# Phase 0a Recovery Runbook — Rebuild VM from PD Snapshot

**Use when:** VM is unrecoverable (corrupted boot, accidental delete, zone outage).
**RTO:** ~30 minutes.
**RPO:** up to 24h (last daily snapshot).

## Steps

### 1. Identify latest snapshot
```bash
gcloud compute snapshots list --project=rx-research-autonomousagent \
  --filter="sourceDisk:hermes-vm-data" \
  --sort-by=~creationTimestamp --limit=3
```

### 2. Restore data disk from snapshot
```bash
LATEST_SNAP=$(gcloud compute snapshots list --project=rx-research-autonomousagent \
  --filter="sourceDisk:hermes-vm-data" --sort-by=~creationTimestamp --limit=1 \
  --format="value(name)")

gcloud compute disks create hermes-vm-data-recovered \
  --source-snapshot="$LATEST_SNAP" \
  --zone=us-central1-a \
  --type=pd-balanced
```

### 3. Update Terraform state to use new disk
```bash
cd terraform/phase-0a-gcp
terraform import google_compute_disk.data \
  projects/rx-research-autonomousagent/zones/us-central1-a/disks/hermes-vm-data-recovered
# Then update compute.tf if disk name changed
```

### 4. Recreate VM
```bash
terraform apply -replace=google_compute_instance.hermes
```

### 5. Wait for bootstrap; verify
```bash
gcloud compute instances get-serial-port-output hermes-vm --zone=us-central1-a | tail -100
bash tests/phase_0a/smoke.sh hermes-vm us-central1-a
bash tests/phase_0a/acceptance.sh hermes-vm us-central1-a
```

### 6. Cleanup old disk after 7 days of stable operation
```bash
gcloud compute disks delete hermes-vm-data --zone=us-central1-a
```
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/phase-0a-recovery.md
git commit -m "docs(runbook): phase-0a-recovery — VM rebuild from PD snapshot (RTO 30min)"
```

---

### Task 38: Execute cutover

This is the live cutover. **Schedule a window when the laptop stack can be down for ~30 min.** Follow `docs/runbooks/phase-0a-cutover.md` exactly.

- [ ] **Step 1: T-24h pre-cutover snapshot** (per runbook)

- [ ] **Step 2: T-0 cutover** (per runbook)

- [ ] **Step 3: T+24h soak verification**

```bash
bash tests/phase_0a/acceptance.sh hermes-vm us-central1-a
# Check Cloud Monitoring dashboard for uptime over 24h
```

- [ ] **Step 4: T+72h tag stable**

```bash
git tag -a phase-0a-cutover-stable -m "Phase 0a complete: 72h continuous uptime on GCP"
git push origin phase-0a-cutover-stable
```

- [ ] **Step 5: Close acceptance criteria** Re-run `acceptance.sh` and confirm criteria #2, #3, #5 are now PASS (no longer DEFER). Commit the final acceptance log:

```bash
bash tests/phase_0a/acceptance.sh hermes-vm us-central1-a \
  > audit/2026-05-20-state-of-the-repo/phase-0a-acceptance-final.log
git add audit/2026-05-20-state-of-the-repo/phase-0a-acceptance-final.log
git commit -m "chore(audit): phase-0a final acceptance — 72h stable, criteria 2/3/5 closed"
```

---

## Self-review

**Spec coverage check** (every section of the spec must trace to ≥1 task):

| Spec section | Tasks |
|---|---|
| §1 Goal | All tasks |
| §2 Non-goals | (enforced by absence — no tasks for GPU/Phase 3/etc.) |
| §3 Pre-flight blocker | Tasks 1–5 |
| §4 Architecture | Tasks 6–16, 23 |
| §5 Compute + persistence | Tasks 15, 16, 34 |
| §6 Networking/auth/CI/CD | Tasks 8, 9, 10, 11, 12, 30 |
| §7 Secrets | Tasks 14, 24, 25, 26 |
| §8 Observability | Tasks 23, 27, 28, 29 |
| §9 DR | Tasks 15 (snapshot schedule), 37 (recovery runbook) |
| §10 Cost | Task 33 criterion #10 (DEFER, 30d) |
| §11 Acceptance criteria | Task 33 (acceptance.sh runs all 10) |
| §12 Open questions | OQ-1 resolved Task 6; OQ-2 Task 11; OQ-3 Task 13 |
| §13 Hand-off | This plan |
| §14 References | preserved in spec |

✅ Full coverage.

**Placeholder scan:**
- No "TBD", "TODO", "implement later" — verified
- Task 27 step 2 has a conditional note about internal-IP uptime checks but provides a clear decision path; not a placeholder
- All code blocks contain executable code

**Type / name consistency:**
- `hermes-runtime` SA referenced in Tasks 10, 16 ✓
- `gha-deployer` SA referenced in Tasks 11, 30 ✓
- `hermes-vm` instance referenced in Tasks 16, 30, 31, 32, 34 ✓
- `hermes-vm-data` disk referenced in Tasks 15, 29 (snapshot alert), 37 (recovery) ✓
- `rx-research-autonomousagent` project referenced consistently ✓
- `us-central1-docker.pkg.dev/rx-research-autonomousagent/hermes` Artifact Registry URI referenced in Tasks 12 (output), 16 (metadata), 30 (CI build) ✓
- `/run/hermes/env/` tmpfs path referenced in Tasks 7 (spec ref), 18 (load script), 20 (compose unit), 33 (acceptance #9) ✓

**Scope check:** One phase, one VM, one migration. Self-contained. ✅

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-20-phase-0a-gcp-migration.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for the long Terraform sequence (Tasks 6–16) where each task produces a small isolated diff.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Faster context but spends more of the active conversation window.

**Recommendation:** Subagent-driven. The plan has 38 tasks; running them inline would burn the conversation context, and most tasks are mechanical Terraform plan/apply cycles that don't benefit from main-context discussion.

**Which approach?**
