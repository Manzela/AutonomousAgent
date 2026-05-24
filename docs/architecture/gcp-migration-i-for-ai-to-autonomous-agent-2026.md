# GCP Migration Plan: `i-for-ai` → `autonomous-agent-2026`

> [!IMPORTANT]
> **Status: COMPLETE** — Executed 2026-05-21. All 6 phases executed successfully.
> - Phase 0-5: Infrastructure provisioned + verified in `autonomous-agent-2026`
> - Phase 6: `i-for-ai` fully decommissioned — **zero-orphan audit PASSED**
> - 15 security hardening controls applied (CIS GCP Benchmark aligned)
> - Only `gs://i-for-ai-autonomousagent-tfstate/` remains (30-day retention, delete after 2026-06-20)

## Objective
Migrate all AutonomousAgent GCP deployments and services from the shared `i-for-ai` project to a dedicated `autonomous-agent-2026` project for blast-radius isolation.

---

## Current State: Complete Resource Inventory

### Deployed GCP Resources in `i-for-ai`

| # | Resource Type | Resource Name/ID | Terraform File | Status |
|---|---|---|---|---|
| 1 | **GCE VM** | `autonomousagent-vm` (e2-standard-4) | [compute.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/compute.tf) | Declared |
| 2 | **Boot Disk** | `autonomousagent-vm-boot` (50GB pd-balanced) | compute.tf | Declared |
| 3 | **Data Disk** | `autonomousagent-vm-data` (100GB pd-balanced) | compute.tf | Declared |
| 4 | **Snapshot Policy** | `autonomousagent-data-daily-snapshot` | compute.tf | **Already applied** |
| 5 | **VPC Network** | `autonomousagent-vpc` | [networking.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/networking.tf) | **Already applied** |
| 6 | **Subnet** | `autonomousagent-subnet` (us-central1) | networking.tf | **Already applied** |
| 7 | **Firewall Rules** | `autonomousagent-*` (IAP, egress, deny-all) | networking.tf | **Already applied** |
| 8 | **Cloud NAT** | `autonomousagent-nat` | networking.tf | **Already applied** |
| 9 | **Cloud Router** | `autonomousagent-router` | networking.tf | **Already applied** |
| 10 | **GCS Bucket** | `i-for-ai-autonomousagent-snapshots` | [gcs.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/gcs.tf) | **Already applied** |
| 11 | **GCS Bucket** | `i-for-ai-autonomousagent-tfstate` | [providers.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/providers.tf) | **Already applied** |
| 12 | **Artifact Registry** | `autonomousagent-images` (DOCKER) | [artifact_registry.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/artifact_registry.tf) | **Already applied** |
| 13 | **Service Account** | `autonomousagent-vm-runtime@i-for-ai` | [iam.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/iam.tf) | **Already applied** |
| 14 | **Service Account** | `autonomousagent-github-ci@i-for-ai` | iam.tf | **Already applied** |
| 15 | **WIF Pool** | `autonomousagent-github` | [wif.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/wif.tf) | **Already applied** |
| 16 | **WIF Provider** | `autonomousagent-actions` (GitHub OIDC) | wif.tf | **Already applied** |
| 17 | **Secret Manager** | `autonomousagent-chroma-cloud` | [secret_manager.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/secret_manager.tf) | **Already applied** |
| 18 | **Secret Manager** | `autonomousagent-hermes-provider` | secret_manager.tf | Declared |
| 19 | **Secret Manager** | `autonomousagent-honcho` | secret_manager.tf | **Already applied** |
| 20 | **Secret Manager** | `autonomousagent-litellm-db` | secret_manager.tf | **Already applied** |
| 21 | **Secret Manager** | `autonomousagent-telegram` | secret_manager.tf | **Already applied** |
| 22 | **Secret Manager** | `autonomousagent-github-pat` | secret_manager.tf | Needs import |
| 23 | **Secret Manager** | `autonomousagent-litellm-master-key` | secret_manager.tf | Needs import |
| 24 | **Secret Manager** | `autonomousagent-j3-shipper-config` | (manual) | Deployed |
| 25 | **Billing Budget** | `autonomousagent-phase-0a` ($7,750/mo) | [billing.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/billing.tf) | Declared |
| 26 | **Monitoring** | Email notification channel | [monitoring.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/monitoring.tf) | Declared |
| 27 | **11 APIs** | compute, iam, secretmanager, etc. | [project.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/project.tf) | **Already enabled** |

### Files Referencing `i-for-ai` (Complete List)

#### Terraform (14 files)
| File | Reference Type | Count |
|---|---|---|
| [variables.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/variables.tf) | `default = "i-for-ai"` | 1 |
| [providers.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/providers.tf) | Backend bucket name (literal) | 1 |
| [project.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/project.tf) | Comments (project number) | 3 |
| [gcs.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/gcs.tf) | Bucket name literal | 3 |
| [iam.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/iam.tf) | Comments only | 2 |
| [wif.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/wif.tf) | Comments only | 2 |
| [secret_manager.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/secret_manager.tf) | Import examples (comments) | 3 |
| [billing.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/billing.tf) | Comments + project number | 3 |
| [compute.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/compute.tf) | `startup-script-url` GCS path | 2 |
| [artifact_registry.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/artifact_registry.tf) | Comments only | 1 |
| [networking.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/networking.tf) | Comments only | 1 |
| [monitoring.tf](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/terraform/phase-0a-gcp/monitoring.tf) | Comments only | ? |

#### Deploy & Config (4 files)
| File | Reference Type |
|---|---|
| [deploy/litellm/config.yaml](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/deploy/litellm/config.yaml) | `vertex_project: i-for-ai` (3 model entries) |
| [deploy/docker-compose.gcp.override.yml](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/deploy/docker-compose.gcp.override.yml) | AR image URLs (2 entries) |
| [deploy/otel/collector.prod.yaml](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/deploy/otel/collector.prod.yaml) | `project: i-for-ai` (Cloud Trace exporter) |

#### GitHub Actions (1 file)
| File | Reference Type |
|---|---|
| [.github/workflows/phase-0a-deploy.yml](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/.github/workflows/phase-0a-deploy.yml) | `GCP_PROJECT`, `AR_REPO`, `BOOTSTRAP_TARBALL` env vars |

#### Scripts (3 files)
| File | Reference Type |
|---|---|
| [scripts/verify-prereqs.sh](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/scripts/verify-prereqs.sh) | `gcloud config` check |
| [scripts/smoke.sh](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/scripts/smoke.sh) | Comment |
| [scripts/vm-bootstrap/install.sh](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/scripts/vm-bootstrap/install.sh) | `gsutil cp` GCS path |
| [scripts/migrate-secrets-to-secret-manager.sh](file:///Users/danielmanzela/RX-Research%20Project/AutonomousAgent/scripts/migrate-secrets-to-secret-manager.sh) | `PROJECT_ID` default |

#### Docs & Audit (8+ files)
| File | Nature |
|---|---|
| Various under `docs/superpowers/specs/` | Design docs referencing project |
| Various under `audit/` | Audit findings referencing deployed state |
| `audit/model-mesh-decision.md` | Vertex AI model mesh config |
| `docs/superpowers/HANDOFF-2026-05-17.md` | Handoff notes |

---

## Migration Process: 6 Phases

### Phase 0: Create New Project + Wire Billing

```bash
# 1. Create the project
gcloud projects create autonomous-agent-2026 \
  --name="AutonomousAgent 2026" \
  --organization=<ORG_ID>  # if applicable, otherwise omit

# 2. Link billing account
gcloud billing projects link autonomous-agent-2026 \
  --billing-account=01FABE-89B1B2-4C704D

# 3. Enable required APIs (same 11 as i-for-ai)
for api in \
  compute.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  storage.googleapis.com \
  iap.googleapis.com \
  cloudresourcemanager.googleapis.com \
  sts.googleapis.com \
  billingbudgets.googleapis.com \
  aiplatform.googleapis.com; do
  gcloud services enable "$api" --project=autonomous-agent-2026
done

# 4. Grant your user account Owner role (temporary, for bootstrap)
gcloud projects add-iam-policy-binding autonomous-agent-2026 \
  --member="user:manzela@tngshopper.com" \
  --role="roles/owner"
```

> [!IMPORTANT]
> Vertex AI API (`aiplatform.googleapis.com`) must also be enabled if you want Vertex AI model access (Claude via Vertex, Gemini) on the new project. Check if your Vertex AI quota/model access needs to be re-requested.

### Phase 1: Terraform Refactor (Code Changes — No Apply Yet)

#### 1.1 Create new tfstate bucket in new project
```bash
gsutil mb -p autonomous-agent-2026 -l us-central1 \
  gs://autonomous-agent-2026-tfstate

gsutil versioning set on gs://autonomous-agent-2026-tfstate
```

#### 1.2 Update Terraform files

| File | Change |
|---|---|
| `variables.tf` L3-4 | `default = "autonomous-agent-2026"` |
| `providers.tf` L9 | `bucket = "autonomous-agent-2026-tfstate"` |
| `gcs.tf` L22 | `name = "autonomous-agent-2026-snapshots"` |
| `compute.tf` L114 | `startup-script-url = "gs://autonomous-agent-2026-snapshots/..."` |
| `billing.tf` L19 | `projects = ["projects/<NEW_PROJECT_NUMBER>"]` |
| `project.tf` | Update comments with new project number |
| `secret_manager.tf` | Update import example comments |
| All `.tf` files | Update `i-for-ai` references in comments |

#### 1.3 Update `providers.tf` backend
```hcl
backend "gcs" {
  bucket = "autonomous-agent-2026-tfstate"
  prefix = "phase-0a"
}
```

> [!WARNING]
> The Terraform `backend` block does NOT support variable interpolation. The bucket name must be a literal string. You cannot use `var.project_id` here.

### Phase 2: Provision Resources in New Project

```bash
# 1. Initialize Terraform with new backend
cd terraform/phase-0a-gcp
terraform init -reconfigure \
  -backend-config="bucket=autonomous-agent-2026-tfstate"

# 2. Plan (all resources will show as "to create")
terraform plan -var="project_id=autonomous-agent-2026" \
  -out=migration.tfplan

# 3. Review the plan carefully — expect ~27 new resources
# 4. Apply
terraform apply migration.tfplan
```

This creates ALL resources fresh in the new project:
- VPC + subnet + firewall rules + Cloud NAT + Cloud Router
- GCE VM + boot disk + data disk + snapshot policy
- Artifact Registry repo
- Service accounts (vm-runtime + github-ci)
- WIF pool + provider
- Secret Manager secrets (7 empty shells)
- Billing budget
- Monitoring notification channel

### Phase 3: Data Migration (Secrets, GCS, Images)

#### 3.1 Secret Values
```bash
# For each secret, copy the value from i-for-ai to autonomous-agent-2026
for secret in \
  autonomousagent-chroma-cloud \
  autonomousagent-hermes-provider \
  autonomousagent-honcho \
  autonomousagent-litellm-db \
  autonomousagent-telegram \
  autonomousagent-github-pat \
  autonomousagent-litellm-master-key \
  autonomousagent-j3-shipper-config; do

  # Read from old project
  VALUE=$(gcloud secrets versions access latest \
    --secret="$secret" --project=i-for-ai 2>/dev/null) || continue

  # Write to new project
  echo -n "$VALUE" | gcloud secrets versions add "$secret" \
    --project=autonomous-agent-2026 --data-file=-

  echo "✓ Migrated $secret"
done
```

#### 3.2 GCS Bucket Contents
```bash
# Copy snapshot bucket contents
gsutil -m rsync -r \
  gs://i-for-ai-autonomousagent-snapshots/ \
  gs://autonomous-agent-2026-snapshots/
```

#### 3.3 Container Images
```bash
# Copy images from old AR to new AR
# Option A: Use gcrane (recommended)
gcrane cp \
  us-central1-docker.pkg.dev/i-for-ai/autonomousagent-images/hermes:latest \
  us-central1-docker.pkg.dev/autonomous-agent-2026/autonomousagent-images/hermes:latest

gcrane cp \
  us-central1-docker.pkg.dev/i-for-ai/autonomousagent-images/shell-sandbox:latest \
  us-central1-docker.pkg.dev/autonomous-agent-2026/autonomousagent-images/shell-sandbox:latest

# Option B: Docker pull/tag/push
docker pull us-central1-docker.pkg.dev/i-for-ai/autonomousagent-images/hermes:0.1.0
docker tag ... us-central1-docker.pkg.dev/autonomous-agent-2026/autonomousagent-images/hermes:0.1.0
docker push us-central1-docker.pkg.dev/autonomous-agent-2026/autonomousagent-images/hermes:0.1.0
```

#### 3.4 Data Disk (if VM was running)
If the VM in `i-for-ai` has data on the persistent disk that needs to be preserved:
```bash
# Create snapshot of old data disk
gcloud compute disks snapshot autonomousagent-vm-data \
  --project=i-for-ai --zone=us-central1-a \
  --snapshot-names=migration-data-snapshot

# Create disk from snapshot in new project
gcloud compute disks create autonomousagent-vm-data \
  --project=autonomous-agent-2026 --zone=us-central1-a \
  --source-snapshot=projects/i-for-ai/global/snapshots/migration-data-snapshot \
  --type=pd-balanced --size=100GB
```

### Phase 4: Update Runtime & CI Configs

#### 4.1 Deploy configs
| File | Change |
|---|---|
| `deploy/litellm/config.yaml` | `vertex_project: autonomous-agent-2026` (3 places) |
| `deploy/docker-compose.gcp.override.yml` | AR image URLs → `autonomous-agent-2026` (2 places) |
| `deploy/otel/collector.prod.yaml` | `project: autonomous-agent-2026` |

#### 4.2 GitHub Actions workflow
| File | Change |
|---|---|
| `.github/workflows/phase-0a-deploy.yml` L62 | `GCP_PROJECT: autonomous-agent-2026` |
| `.github/workflows/phase-0a-deploy.yml` L67 | `AR_REPO: us-central1-docker.pkg.dev/autonomous-agent-2026/autonomousagent-images` |
| `.github/workflows/phase-0a-deploy.yml` L68 | `BOOTSTRAP_TARBALL: gs://autonomous-agent-2026-snapshots/...` |

#### 4.3 Scripts
| File | Change |
|---|---|
| `scripts/verify-prereqs.sh` L30-33 | Check for `autonomous-agent-2026` |
| `scripts/vm-bootstrap/install.sh` L55 | `gs://autonomous-agent-2026-snapshots/...` |
| `scripts/migrate-secrets-to-secret-manager.sh` L15 | `PROJECT_ID="${PROJECT_ID:-autonomous-agent-2026}"` |

#### 4.4 Docs & Audit files
Update all references in `docs/` and `audit/` directories. These are documentation-only changes and can be batch-searched-and-replaced.

> [!TIP]
> Use a targeted `sed` or IDE find-and-replace, but **exclude** the `terraform/` directory (already handled in Phase 1) and any historical audit logs where preserving the original project name is appropriate for the audit trail.

#### 4.5 Vertex AI Model Access

> [!NOTE]
> **Resolved**: Vertex AI quotas are **organization-wide** — since both `i-for-ai` and `autonomous-agent-2026` are under the same GCP Organization, Claude (Anthropic via Vertex) and Gemini model access carries over automatically. You must still:
> 1. Enable the Vertex AI API on `autonomous-agent-2026` (included in Phase 0 API enablement)
> 2. Verify model endpoints respond: `curl` a test request against `autonomous-agent-2026` Vertex AI endpoint
> 3. Update ADC/gcloud config: `gcloud config set project autonomous-agent-2026`

#### 4.6 Local Dev Tools Migration

Both Gemini CLI and Claude Code must be repointed to the new project:

```bash
# 1. gcloud default project
gcloud config set project autonomous-agent-2026

# 2. Gemini CLI (~/.gemini/settings.json)
# Change: "cloudProject": "i-for-ai" → "cloudProject": "autonomous-agent-2026"
# Or if using selectedType: vertex-ai, update the project reference

# 3. Claude Code (CLAUDE.md / environment)
# CLAUDE_CODE_USE_VERTEX=1 remains unchanged (flag only)
# The project is picked up from gcloud ADC or GOOGLE_CLOUD_PROJECT env var
export GOOGLE_CLOUD_PROJECT=autonomous-agent-2026
```

### Phase 5: Verification & Cutover

```bash
# 1. Verify new project resources
gcloud compute instances list --project=autonomous-agent-2026
gcloud secrets list --project=autonomous-agent-2026
gcloud artifacts repositories list --project=autonomous-agent-2026 --location=us-central1

# 2. Verify Terraform state is clean
cd terraform/phase-0a-gcp
terraform plan -var="project_id=autonomous-agent-2026"
# Should show: "No changes. Your infrastructure matches the configuration."

# 3. Verify CI workflow
# Push a test branch, confirm WIF auth works against autonomous-agent-2026

# 4. Verify VM connectivity
gcloud compute ssh autonomousagent-vm \
  --project=autonomous-agent-2026 \
  --zone=us-central1-a \
  --tunnel-through-iap

# 5. Verify LiteLLM can reach Vertex AI models on new project
curl -X POST "https://us-central1-aiplatform.googleapis.com/v1/projects/autonomous-agent-2026/..." \
  -H "Authorization: Bearer $(gcloud auth print-access-token)"

# 6. Verify OTel traces flow to Cloud Trace on new project
# Check: https://console.cloud.google.com/traces?project=autonomous-agent-2026

# 7. Verify Gemini CLI works with new project
GOOGLE_CLOUD_PROJECT=autonomous-agent-2026 gemini --version
```

### Phase 6: Full Decommission — Zero Orphans in `i-for-ai`

> [!CAUTION]
> **Mandate**: After migration, `i-for-ai` must have **zero** AutonomousAgent / Hermes agent resources remaining. No orphan idle services, no dangling SAs, no stale secrets. This checklist is exhaustive.

> [!WARNING]
> Only execute after Phase 5 verification passes AND you've operated on the new project for at least 48 hours with no issues.

#### 6.1 Compute Resources
```bash
# VM (if it exists)
gcloud compute instances delete autonomousagent-vm \
  --project=i-for-ai --zone=us-central1-a --quiet

# Disks
gcloud compute disks delete autonomousagent-vm-boot \
  --project=i-for-ai --zone=us-central1-a --quiet
gcloud compute disks delete autonomousagent-vm-data \
  --project=i-for-ai --zone=us-central1-a --quiet

# Snapshot policy
gcloud compute resource-policies delete autonomousagent-data-daily-snapshot \
  --project=i-for-ai --region=us-central1 --quiet

# Snapshots (any auto-generated from the policy)
for snap in $(gcloud compute snapshots list --project=i-for-ai \
  --filter="labels.disk=autonomousagent-data" --format="value(name)"); do
  gcloud compute snapshots delete "$snap" --project=i-for-ai --quiet
done
```

#### 6.2 Networking
```bash
# Firewall rules (all autonomousagent-* prefixed)
for rule in $(gcloud compute firewall-rules list --project=i-for-ai \
  --filter="name~autonomousagent" --format="value(name)"); do
  gcloud compute firewall-rules delete "$rule" --project=i-for-ai --quiet
done

# Cloud NAT
gcloud compute routers nats delete autonomousagent-nat \
  --router=autonomousagent-router --project=i-for-ai --region=us-central1 --quiet

# Cloud Router
gcloud compute routers delete autonomousagent-router \
  --project=i-for-ai --region=us-central1 --quiet

# Subnet
gcloud compute networks subnets delete autonomousagent-subnet \
  --project=i-for-ai --region=us-central1 --quiet

# VPC Network (must be last — all dependent resources deleted first)
gcloud compute networks delete autonomousagent-vpc \
  --project=i-for-ai --quiet
```

#### 6.3 Secret Manager
```bash
# Delete ALL autonomousagent-* secrets and their versions
for secret in \
  autonomousagent-chroma-cloud \
  autonomousagent-hermes-provider \
  autonomousagent-honcho \
  autonomousagent-litellm-db \
  autonomousagent-telegram \
  autonomousagent-github-pat \
  autonomousagent-litellm-master-key \
  autonomousagent-j3-shipper-config; do
  gcloud secrets delete "$secret" --project=i-for-ai --quiet 2>/dev/null || true
done
```

#### 6.4 IAM — Service Accounts
```bash
# Remove IAM bindings first, then delete SAs
for sa in \
  autonomousagent-vm-runtime \
  autonomousagent-github-ci; do
  gcloud iam service-accounts delete \
    "${sa}@i-for-ai.iam.gserviceaccount.com" \
    --project=i-for-ai --quiet
done
```

#### 6.5 Workload Identity Federation
```bash
# Delete WIF provider first, then pool
gcloud iam workload-identity-pools providers delete autonomousagent-actions \
  --workload-identity-pool=autonomousagent-github \
  --location=global --project=i-for-ai --quiet

gcloud iam workload-identity-pools delete autonomousagent-github \
  --location=global --project=i-for-ai --quiet
```

#### 6.6 Artifact Registry
```bash
# Delete the entire Docker repo (all images inside it)
gcloud artifacts repositories delete autonomousagent-images \
  --location=us-central1 --project=i-for-ai --quiet
```

#### 6.7 GCS Buckets
```bash
# Delete snapshot bucket (and all contents)
gsutil -m rm -r gs://i-for-ai-autonomousagent-snapshots/
gsutil rb gs://i-for-ai-autonomousagent-snapshots

# Delete tfstate bucket (AFTER confirming new tfstate is working)
# Keep for 30 days as backup, then delete
# gsutil -m rm -r gs://i-for-ai-autonomousagent-tfstate/
# gsutil rb gs://i-for-ai-autonomousagent-tfstate
```

> [!WARNING]
> The `i-for-ai-autonomousagent-tfstate` bucket should be kept for **30 days** as a rollback safety net, then deleted.

#### 6.8 Billing Budget
```bash
# The billing budget scoped to i-for-ai project number 85113401879
# will be recreated in autonomous-agent-2026 by Terraform.
# Delete the old one from i-for-ai via console or API:
# Cloud Console → Billing → Budgets & alerts → Delete "autonomousagent-phase-0a"
```

#### 6.9 Monitoring
```bash
# Delete notification channel from i-for-ai
# (Terraform will create a new one in autonomous-agent-2026)
# Use console: Monitoring → Alerting → Notification channels → Delete autonomousagent email channel
```

#### 6.10 Post-Cleanup Verification

```bash
# FINAL AUDIT: Verify ZERO autonomousagent resources remain in i-for-ai
echo "=== Compute ==="
gcloud compute instances list --project=i-for-ai --filter="name~autonomousagent"
gcloud compute disks list --project=i-for-ai --filter="name~autonomousagent"

echo "=== Networking ==="
gcloud compute networks list --project=i-for-ai --filter="name~autonomousagent"
gcloud compute firewall-rules list --project=i-for-ai --filter="name~autonomousagent"

echo "=== IAM ==="
gcloud iam service-accounts list --project=i-for-ai --filter="email~autonomousagent"

echo "=== WIF ==="
gcloud iam workload-identity-pools list --project=i-for-ai --location=global \
  --filter="displayName~AutonomousAgent"

echo "=== Secrets ==="
gcloud secrets list --project=i-for-ai --filter="name~autonomousagent"

echo "=== Artifact Registry ==="
gcloud artifacts repositories list --project=i-for-ai --location=us-central1 \
  --filter="name~autonomousagent"

echo "=== GCS ==="
gsutil ls -p i-for-ai | grep autonomousagent

echo "=== Snapshots ==="
gcloud compute snapshots list --project=i-for-ai --filter="name~autonomousagent OR labels.disk~autonomousagent"

# ALL commands above should return EMPTY results.
# If any return results, investigate and delete.
```

> [!IMPORTANT]
> **Success criteria**: Every command in §6.10 returns zero results. `i-for-ai` is 100% clean of AutonomousAgent scope.

---

## Resolved Decisions

| # | Question | Decision | Rationale |
|---|---|---|---|
| 1 | **Vertex AI quota** | ✅ Org-wide — carries over | Both projects under same GCP Organization; quotas are inherited |
| 2 | **LiteLLM `vertex_project`** | ✅ New project (`autonomous-agent-2026`) | Full isolation — no residual dependency on `i-for-ai` |
| 3 | **Organization** | ✅ Same org | IAM inheritance + billing continuity |
| 4 | **Billing account** | ✅ Same account (`01FABE-89B1B2-4C704D`) | Approved |
| 5 | **Region** | ✅ Stay in `us-central1` | No relocation |
| 6 | **Gemini CLI / Claude Code** | ✅ Point to new project | Full migration — zero residual `i-for-ai` refs |
| 7 | **Orphan cleanup** | ✅ Zero-tolerance | `i-for-ai` must be 100% clean of all `autonomousagent-*` / Hermes resources |

---

## Risk Matrix

| Risk | Severity | Mitigation |
|---|---|---|
| **Vertex AI model access gap during cutover** | 🟡 Medium | Org-wide quotas mitigate; verify endpoint before cutover |
| **WIF pool/provider recreation breaks CI** | 🟡 Medium | Test WIF auth on a branch before merging |
| **Secret values lost during migration** | 🔴 Critical | SOPS files are the source of truth; can always re-migrate from repo |
| **Data disk content loss** | 🟡 Medium | Cross-project snapshot copy before destroying old disk |
| **GCS bucket name collision** | 🟢 Low | New bucket names use `autonomous-agent-2026-` prefix (globally unique) |
| **Billing budget references wrong project number** | 🟡 Medium | Update `billing.tf` with new project number after project creation |
| **Orphan resources left in `i-for-ai`** | 🟡 Medium | §6.10 audit script catches any missed resources |
| **Local dev tools still pointing at `i-for-ai`** | 🟢 Low | §4.6 covers gcloud, Gemini CLI, Claude Code |

---

## Estimated Timeline

| Phase | Duration | Blocking? |
|---|---|---|
| Phase 0: Create project | 15 min | No |
| Phase 1: Terraform refactor | 1 hour | No (code-only) |
| Phase 2: Provision resources | 30 min | Yes (new infra) |
| Phase 3: Data migration | 1 hour | Yes (secrets, images) |
| Phase 4: Config updates + dev tools | 2 hours | Yes (many files) |
| Phase 5: Verification | 1-2 hours | Yes (end-to-end) |
| Phase 6: Full decommission + audit | 1 hour | After 48h soak |
| **Total** | **~7-8 hours** + 48h soak | — |
