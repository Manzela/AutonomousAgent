# Phase 0a — IAM: runtime identity for the AutonomousAgent GCE VM.
#
# Single dedicated service account scoped to least-privilege for the
# operations the VM actually performs:
#   - pull container images       → artifactregistry.reader
#   - read runtime secrets        → secretmanager.secretAccessor
#   - emit logs                   → logging.logWriter
#   - emit custom metrics         → monitoring.metricWriter
#   - write to snapshot bucket    → storage.objectCreator
#
# CI service account + Workload Identity Federation live in wif.tf
# (Task 11) — distinct identity, distinct trust boundary.
#
# Naming: autonomousagent-vm-runtime — dedicated SA in the
# autonomous-agent-2026 project.

resource "google_service_account" "vm_runtime" {
  project      = var.project_id
  account_id   = "autonomousagent-vm-runtime"
  display_name = "AutonomousAgent VM runtime identity"
  description  = "Attached to the GCE VM; pulls images/secrets, writes logs/metrics/snapshots"
  depends_on   = [google_project_service.enabled]
}

locals {
  vm_runtime_roles = [
    "roles/secretmanager.secretAccessor",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/artifactregistry.reader",
    "roles/storage.objectViewer",
    "roles/storage.objectCreator",
  ]
}

resource "google_project_iam_member" "vm_runtime_roles" {
  for_each = toset(local.vm_runtime_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.vm_runtime.email}"
}

# GitHub Actions CI deployer SA. Impersonated by the WIF principalSet
# (see wif.tf). Scope:
#   - compute.instanceAdmin.v1     — manage the GCE VM (start/stop/SSH)
#   - artifactregistry.writer      — push container images
#   - iam.serviceAccountUser       — act as autonomousagent-vm-runtime
#                                    when (re)attaching to the VM
#   - iap.tunnelResourceAccessor   — open IAP TCP tunnels for SSH
#
# Naming: autonomousagent-github-ci — dedicated SA in
# autonomous-agent-2026 project.

resource "google_service_account" "github_ci" {
  project      = var.project_id
  account_id   = "autonomousagent-github-ci"
  display_name = "AutonomousAgent GitHub Actions CI deployer"
  description  = "Impersonated by GitHub Actions via WIF; deploys to the VM"
  depends_on   = [google_project_service.enabled]
}

locals {
  github_ci_roles = [
    "roles/compute.instanceAdmin.v1",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
    "roles/iap.tunnelResourceAccessor",
  ]
}

resource "google_project_iam_member" "github_ci_roles" {
  for_each = toset(local.github_ci_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.github_ci.email}"
}

# ---------------------------------------------------------------------------
# CRIT-2 fix: hermes_agent SA — referenced in wif-migration.tf Cloud Run
# service but was not defined anywhere, causing `terraform plan` to fail.
#
# This SA is the Cloud Run runtime identity for the Hermes agent service.
# It uses metadata-server ADC (no SA key files) once the Cloud Run migration
# (W1.D.I-8) is complete.  Until then, the SA exists but Cloud Run is not
# deployed; the docker-compose path uses per-service SA key mounts (W0.6).
# ---------------------------------------------------------------------------

resource "google_service_account" "hermes_agent" {
  project      = var.project_id
  account_id   = "autonomousagent-hermes-agent"
  display_name = "AutonomousAgent Hermes Cloud Run runtime identity"
  description  = "Attached to the Cloud Run hermes service; minimum scopes for Vertex/SQL/Logging"
  depends_on   = [google_project_service.enabled]
}

locals {
  hermes_agent_roles = [
    "roles/aiplatform.user",         # Vertex AI (LLM inference)
    "roles/cloudsql.client",         # Cloud SQL for pgvector memory store
    "roles/secretmanager.secretAccessor", # Runtime secrets (Honcho, Telegram, etc.)
    "roles/logging.logWriter",       # Cloud Logging
    "roles/monitoring.metricWriter", # Cloud Monitoring custom metrics
    "roles/storage.objectCreator",   # GCS snapshot bucket writes
  ]
}

resource "google_project_iam_member" "hermes_agent_roles" {
  for_each = toset(local.hermes_agent_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.hermes_agent.email}"
}
