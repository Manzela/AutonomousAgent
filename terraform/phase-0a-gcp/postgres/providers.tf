# Postgres sub-module — Phase 2 (Cloud SQL for PostgreSQL 16 + pgvector).
#
# Carved into a sub-module (mirroring terraform/phase-0a-gcp/model-armor/) so
# the $1,580/mo Cloud SQL instance has its OWN state file and CANNOT be
# accidentally destroyed by a `terraform destroy` at the Phase 0a root.
# `prevent_destroy = true` on the instance is a second line of defense.
#
# Provider versions: pinned to the SAME `~> 5.30` baseline as the root
# Phase 0a module — Cloud SQL Postgres 16, HNSW pgvector, IAM auth, and the
# `enable_private_path_for_google_cloud_services` flag are all supported on
# google ~> 5.30. (Contrast with the Model Armor sub-module which had to
# pin to ~> 6.43 for the modelarmor.* resources.)
#
# Backend: same GCS bucket as root, distinct prefix `phase-0a-postgres` so
# `terraform plan` / `terraform apply` here cannot disturb root state.

terraform {
  required_version = ">= 1.7.0"
  required_providers {
    google      = { source = "hashicorp/google", version = "~> 5.30" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 5.30" }
  }
  backend "gcs" {
    bucket = "autonomous-agent-2026-tfstate"
    prefix = "phase-0a-postgres"
  }
}

provider "google" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}
