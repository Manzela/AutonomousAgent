# Populated incrementally by later tasks.
output "project_id" {
  value = var.project_id
}

# Task 10 — runtime identity attached to the VM.
output "vm_runtime_sa_email" {
  value       = google_service_account.vm_runtime.email
  description = "Service account attached to the GCE VM"
}

# Task 11 — CI federation outputs; consumed by .github/workflows/phase-0a-deploy.yml
# as repository variables GCP_WIF_PROVIDER and GCP_DEPLOYER_SA.
output "wif_provider_resource_name" {
  value       = google_iam_workload_identity_pool_provider.autonomousagent_actions.name
  description = "Full resource name of the WIF provider — set as GCP_WIF_PROVIDER repo variable"
}

output "github_ci_sa_email" {
  value       = google_service_account.github_ci.email
  description = "CI deployer SA email — set as GCP_DEPLOYER_SA repo variable"
}

# Task 12 — base path for `docker tag`/`docker push` in CI.
output "artifact_registry_repo" {
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.autonomousagent_images.repository_id}"
  description = "Fully-qualified Artifact Registry repo base path"
}

# Task 13 — daily PD snapshot staging bucket.
output "snapshot_bucket" {
  value       = google_storage_bucket.snapshots.url
  description = "GCS bucket for in-region PD snapshot staging"
}

# Task 16 — VM identity and addressing.
output "vm_name" {
  value       = google_compute_instance.autonomousagent.name
  description = "GCE VM instance name"
}

output "vm_internal_ip" {
  value       = google_compute_instance.autonomousagent.network_interface[0].network_ip
  description = "VM internal IP (no public IP — access via IAP SSH)"
}

output "vm_zone" {
  value       = var.zone
  description = "Zone the VM is deployed in"
}

output "j3_trajectories_bucket_name" {
  description = "Name of the J3 trajectory shipper destination bucket. Consumed by the autonomousagent-j3-shipper-config Secret Manager secret (see secret_manager.tf)."
  value       = google_storage_bucket.j3_trajectories.name
}
