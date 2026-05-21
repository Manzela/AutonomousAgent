# Phase 2 — Cloud SQL for PostgreSQL 16 with pgvector
#
# Provisions a regional HA Postgres instance for hierarchical memory tiers:
#   - Episodic memory: append-only event log (>100M rows expected)
#   - Semantic memory: vector embeddings via pgvector (~100M embeddings)
#   - Procedural memory: skill/policy library (~100K rows)
#
# Network: Private IP only (no public IP), VPC peering via existing
# autonomousagent-vpc network (see networking.tf in phase-0a-gcp/).
#
# Auth: IAM database authentication (no password-based access).
#
# Backups: Daily backups (7-day retention) + PITR (7-day transaction log).
#
# Naming: autonomousagent-* prefix to avoid collision with sibling workloads.

resource "google_sql_database_instance" "postgres_vector" {
  project          = var.project_id
  name             = "autonomousagent-postgres-vector"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier              = var.db_instance_tier  # Default: db-custom-16-64000
    availability_type = "REGIONAL"            # Cross-zone HA within us-central1
    disk_size         = var.db_disk_size_gb   # Default: 1000GB SSD
    disk_type         = "PD_SSD"
    disk_autoresize   = true
    disk_autoresize_limit = 2000  # Cap at 2TB to prevent runaway costs

    # Private IP only (no public IP)
    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = "projects/${var.project_id}/global/networks/autonomousagent-vpc"
      enable_private_path_for_google_cloud_services = true
    }

    # Daily backups: 01:00 UTC (low-traffic window)
    backup_configuration {
      enabled                        = true
      start_time                     = "01:00"  # HH:MM
      location                       = "us"     # Multi-region for DR
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }

    # Maintenance window: Sunday 02:00 UTC
    maintenance_window {
      day          = 7  # Sunday
      hour         = 2  # 02:00 UTC
      update_track = "stable"
    }

    # Database flags: IAM auth + performance tuning
    dynamic "database_flags" {
      for_each = local.database_flags
      content {
        name  = database_flags.value.name
        value = database_flags.value.value
      }
    }
  }

  # Prevent accidental deletion
  lifecycle {
    prevent_destroy = true
  }

  labels = {
    phase     = "2"
    component = "autonomousagent"
    tier      = "memory"
  }

  depends_on = [
    google_project_service.enabled,
    google_compute_network.autonomousagent,  # VPC must exist first
  ]
}

# Database flags (performance tuning + IAM auth)
locals {
  database_flags = [
    # IAM database authentication
    {
      name  = "cloudsql.iam_authentication"
      value = "on"
    },
    # Memory tuning for 64GB RAM instance
    {
      name  = "shared_buffers"
      value = "16777216"  # 16GB (25% of 64GB RAM)
    },
    {
      name  = "effective_cache_size"
      value = "50331648"  # 48GB (75% of 64GB RAM)
    },
    {
      name  = "maintenance_work_mem"
      value = "4194304"  # 4GB (for HNSW index builds on 100M vectors)
    },
    {
      name  = "work_mem"
      value = "131072"  # 128MB (per query sort/hash operation)
    },
    # Parallelism (match vCPU count)
    {
      name  = "max_parallel_workers"
      value = "16"
    },
    {
      name  = "max_parallel_workers_per_gather"
      value = "4"
    },
    # Connection limits
    {
      name  = "max_connections"
      value = "200"  # MVP scale (Cloud SQL default is 100)
    },
    # Write-ahead log tuning for PITR
    {
      name  = "wal_buffers"
      value = "16384"  # 16MB (default is 2MB; increase for better PITR)
    },
  ]
}

# Database resource (application schema)
resource "google_sql_database" "hermes" {
  project  = var.project_id
  name     = "hermes"
  instance = google_sql_database_instance.postgres_vector.name
}

# IAM database user (service account-based auth)
# NOTE: SQL user name is truncated SA email (without .gserviceaccount.com)
resource "google_sql_user" "vm_runtime" {
  project  = var.project_id
  name     = "${google_service_account.vm_runtime.account_id}@${var.project_id}.iam"
  instance = google_sql_database_instance.postgres_vector.name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

# Grant Cloud SQL Client role to VM runtime SA (enables IAM auth)
resource "google_project_iam_member" "vm_runtime_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.vm_runtime.email}"
}
