# Phase 0a — Secret Manager: placeholder resources only.
#
# The secret *resources* are Terraform-managed; the secret *values* land
# via the Phase E migration script (Task 23-25). Deliberate separation:
#   - Keeps cleartext secret values out of `terraform plan` output
#   - Keeps them out of state files (which would otherwise need
#     encryption-at-rest hardening beyond GCS bucket defaults)
#   - Lets the SOPS → SM migration be idempotent and auditable as its own
#     script (single-purpose, easier to review)
#
# Source of truth for the secret name list: `ls secrets/*.env.sops`
# in the repo root. Each entry corresponds to one env file that hermes
# loads at runtime. SM secret_id = "autonomousagent-<basename>", value
# (when populated) = the entire decrypted env-file contents.
#
# Naming: autonomousagent-* prefix to avoid colliding with secrets
# owned by sibling workloads on i-for-ai.
#
# replication.auto: GCP picks replication policy; sufficient for these
# non-critical secrets (we have IaC + SOPS as source of truth).

locals {
  # Mirrors `secrets/*.env.sops` in the repo. Update both together when
  # a new SOPS env file is added.
  sops_env_files = [
    "chroma-cloud",
    "hermes-provider",
    "honcho",
    "litellm-db",
    "telegram",
  ]
}

resource "google_secret_manager_secret" "envfiles" {
  for_each = toset(local.sops_env_files)
  project  = var.project_id

  secret_id = "autonomousagent-${each.value}"

  replication {
    auto {}
  }

  labels = {
    phase     = "0a"
    component = "autonomousagent"
    source    = "sops"
  }

  depends_on = [google_project_service.enabled]
}

locals {
  # Individual secrets: raw values (no .env extension), used as Docker
  # compose file-type secrets (bind-mounted to /run/secrets/<name> in
  # containers). load-secrets.sh fetches these to /run/hermes/env/<name>
  # (no .env suffix) so the symlink resolves correctly.
  individual_secrets = [
    "github-pat",
    "litellm-master-key",
  ]
}

# IMPORT REQUIRED for existing environments:
# These secrets were bootstrapped via gcloud. Import before first apply:
#   terraform import 'google_secret_manager_secret.individual["github-pat"]' \
#     projects/i-for-ai/secrets/autonomousagent-github-pat
#   terraform import 'google_secret_manager_secret.individual["litellm-master-key"]' \
#     projects/i-for-ai/secrets/autonomousagent-litellm-master-key
resource "google_secret_manager_secret" "individual" {
  for_each = toset(local.individual_secrets)
  project  = var.project_id

  secret_id = "autonomousagent-${each.value}"

  replication {
    auto {}
  }

  labels = {
    phase     = "0a"
    component = "autonomousagent"
    source    = "individual"
  }

  depends_on = [google_project_service.enabled]
}

# Phase 0a — J3 trajectory shipper runtime config secret.
#
# Holds the small JSON blob the shipper reads to know:
#  - which bucket to upload to (filled in by terraform output)
#  - which Model Armor template to call (filled in via the model-armor sub-module output)
#  - the feature flag (HERMES_J3_SHIPPER_ENABLED — read by scripts/run_trajectory_shipper.py)
#
# Stored as a secret (not env vars baked into the VM image) so that the
# launch flip is a single Secret Manager version write, not an image
# redeploy. Atomic flip semantics — see docs/runbooks/j1-launch-flip.md.
#
# IMPORTANT: the initial secret_data sets feature_flag_enabled=false.
# The atomic flip in docs/runbooks/j1-launch-flip.md adds a NEW secret
# version with true, NOT an in-place edit. Old version remains readable
# for instant rollback.

resource "google_secret_manager_secret" "j3_shipper_config" {
  project   = var.project_id
  secret_id = "autonomousagent-j3-shipper-config"

  replication {
    auto {}
  }

  labels = {
    phase     = "0a"
    component = "autonomousagent"
    tier      = "shipper"
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "j3_shipper_config_v1" {
  secret = google_secret_manager_secret.j3_shipper_config.id

  secret_data = jsonencode({
    bucket_name                   = google_storage_bucket.j3_trajectories.name
    model_armor_template_resource = "projects/${var.project_id}/locations/${var.region}/templates/j1-trajectory-shipper"
    feature_flag_enabled          = false
  })
}

resource "google_secret_manager_secret_iam_member" "j3_shipper_config_vm_reader" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.j3_shipper_config.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.vm_runtime.email}"
}
