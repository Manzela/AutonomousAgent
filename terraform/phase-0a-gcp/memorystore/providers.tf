# Memorystore Redis sub-module — distributed jti replay cache for A2A auth.
#
# Carved into a sub-module (mirroring terraform/phase-0a-gcp/postgres/) so
# the Memorystore instance has its OWN state file and cannot be accidentally
# destroyed by a `terraform destroy` at the Phase 0a root.
#
# Provider versions: pinned to the SAME `~> 5.30` baseline as the root
# Phase 0a module — Memorystore REDIS_7_0, STANDARD_HA, TLS, and IAM
# are all supported on google ~> 5.30.
#
# Backend: same GCS bucket as root, distinct prefix `phase-0a-memorystore` so
# `terraform plan` / `terraform apply` here cannot disturb root state.
#
# Design spec: docs/superpowers/specs/2026-05-25-redis-jti-replay-cache-design.md §4

terraform {
  required_version = ">= 1.7.0"
  required_providers {
    google      = { source = "hashicorp/google", version = "~> 5.30" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 5.30" }
  }
  backend "gcs" {
    bucket = "autonomous-agent-2026-tfstate"
    prefix = "phase-0a-memorystore"
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
