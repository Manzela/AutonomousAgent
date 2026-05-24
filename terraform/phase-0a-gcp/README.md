# Terraform Phase 0a GCP Module

Provisions Phase 0a GCP infrastructure for AutonomousAgent always-online deployment.

## Overview

This module scaffolds the Terraform configuration for deploying the AutonomousAgent on Google Cloud Platform. It configures two GCP providers (google, google-beta), a remote GCS backend for state management, and foundational variables for VM provisioning and GitHub integration.

> [!IMPORTANT]
> **GCP Project Migration**: Infrastructure has been migrated from the shared `i-for-ai` project to the dedicated `autonomous-agent-2026` project (project number `870615250682`). All Terraform state now lives in the new backend bucket.

## Prerequisites

1. **GCP Project**: `autonomous-agent-2026` (dedicated, billing wired to `01FABE-89B1B2-4C704D`)
2. **gcloud CLI**: Install and authenticate with `gcloud auth login`
3. **Billing Account**: Already linked to `autonomous-agent-2026`
4. **Service Account**: `autonomousagent-vm-runtime` (Compute, Storage, IAM — provisioned by this module)
5. See plan Task 5 (lines 314-362) for detailed setup steps

## Terraform Backend

Remote state is stored in GCS bucket `autonomous-agent-2026-tfstate` under prefix `phase-0a`.

```bash
# Backend bucket was created with:
gcloud storage buckets create gs://autonomous-agent-2026-tfstate \
  --project=autonomous-agent-2026 \
  --location=us-central1 \
  --uniform-bucket-level-access \
  --public-access-prevention
gcloud storage buckets update gs://autonomous-agent-2026-tfstate --versioning
```

## Configuration

### Default Variables

- `project_id`: GCP project ID (default: `autonomous-agent-2026`)
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
