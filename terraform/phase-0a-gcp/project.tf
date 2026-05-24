# Phase 0a — API enablement on the autonomous-agent-2026 project.
#
# The project (autonomous-agent-2026, project number 870615250682,
# billing 01FABE-89B1B2-4C704D) is a dedicated project for the
# AutonomousAgent workload, migrated from the shared i-for-ai project.
#
# disable_on_destroy = false: prevents `terraform destroy` from disabling
# APIs while the project is in active use.

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
