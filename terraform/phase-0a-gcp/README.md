# Terraform Phase 0a GCP Module

Provisions Phase 0a GCP infrastructure for AutonomousAgent always-online deployment.

## Overview

This module scaffolds the Terraform configuration for deploying the AutonomousAgent on Google Cloud Platform. It configures two GCP providers (google, google-beta), a remote GCS backend for state management, and foundational variables for VM provisioning and GitHub integration.

## Prerequisites

1. **GCP Project**: Create or use existing project `rx-research-autonomousagent`
2. **gcloud CLI**: Install and authenticate with `gcloud auth login`
3. **Billing Account**: Link a billing account to the GCP project
4. **Service Account**: Create a service account with Compute, Storage, and IAM permissions
5. See plan Task 5 (lines 314-362) for detailed setup steps

## Terraform Backend

Remote state is stored in GCS bucket `rx-research-autonomousagent-tfstate` under prefix `phase-0a`.

**Status**: Scaffold only. Backend bucket creation via `gcloud storage buckets create gs://rx-research-autonomousagent-tfstate` and `terraform init` are deferred to a follow-up commit once user GCP authentication and billing account are confirmed.

## Configuration

### Default Variables

- `project_id`: GCP project ID (default: `rx-research-autonomousagent`)
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
