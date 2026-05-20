# Terraform Phase 0a GCP Module

Provisions Phase 0a GCP infrastructure for AutonomousAgent always-online deployment.

## Overview

This module scaffolds the Terraform configuration for deploying the AutonomousAgent on Google Cloud Platform. It configures two GCP providers (google, google-beta), a remote GCS backend for state management, and foundational variables for VM provisioning and GitHub integration.

## Prerequisites

1. **GCP Project**: Use existing project (already created, billing wired to 01FABE-89B1B2-4C704D)
2. **gcloud CLI**: Install and authenticate with `gcloud auth login`
3. **Billing Account**: Link a billing account to the GCP project
4. **Service Account**: Create a service account with Compute, Storage, and IAM permissions
5. See plan Task 5 (lines 314-362) for detailed setup steps

## Terraform Backend

Remote state is stored in GCS bucket `i-for-ai-autonomousagent-tfstate` under prefix `phase-0a`.

**Status**: Scaffold only. Backend bucket creation via `gcloud storage buckets create gs://i-for-ai-autonomousagent-tfstate --project=i-for-ai --location=us-central1` and `terraform init` are deferred to a follow-up commit once user GCP authentication and billing account are confirmed.

## Configuration

### Default Variables

- `project_id`: GCP project ID (default: `i-for-ai`)
- `region`: GCP region (default: `us-central1`)
- `zone`: GCP zone (default: `us-central1-a`)
- `vm_machine_type`: Compute Engine machine type (default: `e2-standard-4`)
- `vm_boot_disk_gb`: Boot disk size (default: 50)
- `vm_data_disk_gb`: Data disk size (default: 100)
- `github_owner`: GitHub account (default: `Manzela`)
- `github_repo`: GitHub repository (default: `AutonomousAgent`)

Copy `terraform.tfvars.example` to `terraform.tfvars` and override as needed.

## Reference

Full plan: `docs/superpowers/plans/2026-05-20-phase-0a-gcp-migration.md`, Task 6 (lines 363-522)
