# Phase 0a — API enablement on the existing i-for-ai project.
#
# The project itself is assumed pre-created (i-for-ai, project number 85113401879,
# billing 01FABE-89B1B2-4C704D — already wired). Pre-flight discovery
# confirmed all 11 APIs below are already enabled, so the initial apply is
# a state-only import (no GCP-side mutation). The resources are still
# declared so that:
#   1. The dependency graph (depends_on = [google_project_service.enabled])
#      in later resources is honored.
#   2. Re-apply from a fresh state file (e.g. disaster recovery) re-enables
#      anything that was disabled out-of-band.
#
# disable_on_destroy = false: prevents `terraform destroy` from disabling
# APIs that other workloads on i-for-ai (Vertex AI, image-cache, lora-*)
# depend on.

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
