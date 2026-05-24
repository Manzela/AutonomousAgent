# ---------------------------------------------------------------------------
# Project + region (mirror root terraform/phase-0a-gcp/variables.tf defaults).
# ---------------------------------------------------------------------------

variable "project_id" {
  description = "GCP project ID hosting the Phase 2 Cloud SQL instance."
  type        = string
  default     = "i-for-ai"
}

variable "region" {
  description = "GCP region for the Cloud SQL instance + VPC peering. us-central1 is the canonical home (matches root Phase 0a region + Model Armor region + GCE VM region)."
  type        = string
  default     = "us-central1"
}

# ---------------------------------------------------------------------------
# Names of root-Phase-0a resources looked up via data sources (NOT created
# here). Defaults match the names declared in root iam.tf + networking.tf.
# ---------------------------------------------------------------------------

variable "vpc_name" {
  description = "Name of the root Phase 0a VPC that hosts the GCE VM and will host Cloud SQL via private-IP peering. Looked up via data \"google_compute_network\"."
  type        = string
  default     = "autonomousagent-vpc"
}

variable "vm_runtime_sa_account_id" {
  description = "account_id of the root Phase 0a VM runtime service account (the IAM identity that connects to Cloud SQL). Looked up via data \"google_service_account\"."
  type        = string
  default     = "autonomousagent-vm-runtime"
}

# ---------------------------------------------------------------------------
# Cloud SQL instance shape — defaults from the Phase 2 Postgres packet
# (audit/2026-05-21-phase2-postgres/provisioning-spec.md + cost-estimate.md).
# DO NOT lower db_instance_tier below db-custom-16-64000 without first
# re-reading cost-estimate.md §4 (HNSW memory budget). Anti-pattern:
# db-custom-2-7680 ($280/mo) is INSUFFICIENT for HNSW build on 100M vectors.
# ---------------------------------------------------------------------------

variable "db_instance_tier" {
  description = "Cloud SQL instance tier. db-custom-16-64000 = 16 vCPU + 64GB RAM; floor for HNSW on the projected ~100M vector working set per audit/2026-05-21-phase2-postgres/cost-estimate.md."
  type        = string
  default     = "db-custom-16-64000"
}

variable "db_disk_size_gb" {
  description = "Provisioned SSD size in GB. 1000GB (1TB) = ~600GB vectors + HNSW index + 40% headroom."
  type        = number
  default     = 1000
}

variable "db_backup_retention_days" {
  description = "Daily backups to retain. PITR transaction log retention is set separately."
  type        = number
  default     = 7

  validation {
    condition     = var.db_backup_retention_days >= 1 && var.db_backup_retention_days <= 365
    error_message = "db_backup_retention_days must be between 1 and 365."
  }
}

variable "db_pitr_retention_days" {
  description = "Point-in-time recovery transaction log retention (days). Cloud SQL caps at 7."
  type        = number
  default     = 7

  validation {
    condition     = var.db_pitr_retention_days >= 1 && var.db_pitr_retention_days <= 7
    error_message = "db_pitr_retention_days must be between 1 and 7."
  }
}

variable "db_max_connections" {
  description = "PostgreSQL max_connections. 200 covers MVP scale (~50 concurrent app conns + headroom for Cloud SQL Auth Proxy + admin sessions). Cloud SQL default is 100; we override."
  type        = number
  default     = 200
}

variable "db_maintenance_day" {
  description = "Day of week for Cloud SQL maintenance window (1=Monday, 7=Sunday)."
  type        = number
  default     = 7

  validation {
    condition     = var.db_maintenance_day >= 1 && var.db_maintenance_day <= 7
    error_message = "db_maintenance_day must be between 1 (Monday) and 7 (Sunday)."
  }
}

variable "db_maintenance_hour" {
  description = "Hour of day for Cloud SQL maintenance window (0-23 UTC)."
  type        = number
  default     = 2

  validation {
    condition     = var.db_maintenance_hour >= 0 && var.db_maintenance_hour <= 23
    error_message = "db_maintenance_hour must be between 0 and 23."
  }
}

# ---------------------------------------------------------------------------
# VPC peering range. /16 carves a generous private range so Cloud SQL plus
# any other Service Networking consumers (e.g. Memorystore, Vertex Index
# Endpoints) on the same VPC have room.
# ---------------------------------------------------------------------------

variable "peering_prefix_length" {
  description = "CIDR prefix length for the VPC peering range that Service Networking allocates to Google-managed services. 16 = ~65k addresses; matches the upstream provider example."
  type        = number
  default     = 16

  validation {
    condition     = var.peering_prefix_length >= 16 && var.peering_prefix_length <= 24
    error_message = "peering_prefix_length must be between 16 and 24."
  }
}
