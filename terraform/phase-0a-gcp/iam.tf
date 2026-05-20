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
# Naming: autonomousagent-vm-runtime to avoid collision with the
# pre-existing `github@i-for-ai` SA (which serves a different purpose
# on this shared project).

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
# Naming: autonomousagent-github-ci avoids collision with the
# pre-existing `github@i-for-ai` and `github-dev-stg@i-for-ai` SAs.

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
