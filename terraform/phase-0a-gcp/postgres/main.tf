# ---------------------------------------------------------------------------
# Phase 2 — Cloud SQL for PostgreSQL 16 with pgvector + private-IP VPC peering.
#
# Provisions a regional HA Postgres instance for hierarchical memory tiers
# (episodic event log, semantic vector embeddings, procedural skill library).
# Full design: audit/2026-05-21-phase2-postgres/{provisioning,pgvector,schema}-*.md.
#
# CRITICAL GAP CLOSED vs the original staging packet
# (audit/2026-05-21-phase2-postgres/terraform/cloud_sql.tf): Cloud SQL with
# `ip_configuration.ipv4_enabled = false` requires a Service Networking VPC
# peering connection on the host VPC, which the staging files did not
# declare. terraform plan would have succeeded, terraform apply would have
# failed at instance creation with `INVALID_ARGUMENT: The network ... has
# no service networking connection`. This module declares the peering
# explicitly (google_compute_global_address + google_service_networking_connection)
# per the canonical upstream pattern at
# registry.terraform.io/.../sql_database_instance#private-ip-instance.
#
# Network: Private IP only (no public IP), VPC peering on root's
# autonomousagent-vpc (looked up via data source — not created here).
# Auth:    IAM database authentication (no password-based access).
# Backups: Daily backups (7-day retention) + PITR (7-day transaction log).
# ---------------------------------------------------------------------------

resource "google_project_service" "apis" {
  for_each = toset([
    "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# Data sources — look up resources owned by root Phase 0a state. Plan-time
# lookups (no API calls at apply); fail loud with a clear error if root has
# not been applied yet (e.g. "google_compute_network ... not found").
data "google_compute_network" "vpc" {
  name    = var.vpc_name
  project = var.project_id
}

data "google_service_account" "vm_runtime" {
  account_id = var.vm_runtime_sa_account_id
  project    = var.project_id
}

# ---------------------------------------------------------------------------
# VPC peering for Cloud SQL private IP.
#
# Service Networking carves a /N range out of the VPC and hands it to
# Google-managed services (Cloud SQL, Memorystore, Vertex Index Endpoint,
# etc.) to allocate instance IPs from. This range MUST NOT overlap with the
# VPC's existing subnet ranges (root networking.tf uses 10.10.0.0/24, so a
# /16 default keeps us clear by orders of magnitude).
# ---------------------------------------------------------------------------

resource "google_compute_global_address" "private_ip_alloc" {
  project       = var.project_id
  name          = "autonomousagent-postgres-peering-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = var.peering_prefix_length
  network       = data.google_compute_network.vpc.id

  depends_on = [google_project_service.apis]
}

resource "google_service_networking_connection" "default" {
  network                 = data.google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_alloc.name]

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Cloud SQL instance. prevent_destroy + sub-module isolation = belt + braces
# against accidental teardown of the $1,580/mo HA Postgres instance.
# ---------------------------------------------------------------------------

resource "google_sql_database_instance" "postgres_vector" {
  project          = var.project_id
  name             = "autonomousagent-postgres-vector"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier                  = var.db_instance_tier
    availability_type     = "REGIONAL"
    disk_size             = var.db_disk_size_gb
    disk_type             = "PD_SSD"
    disk_autoresize       = true
    disk_autoresize_limit = 2000

    ip_configuration {
      ipv4_enabled                                  = false
      private_network                               = data.google_compute_network.vpc.id
      enable_private_path_for_google_cloud_services = true
    }

    # Daily backups at 01:00 UTC (low-traffic window); PITR transaction
    # logs retained 7 days; backups replicated to the "us" multi-region.
    backup_configuration {
      enabled                        = true
      start_time                     = "01:00"
      location                       = "us"
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = var.db_pitr_retention_days

      backup_retention_settings {
        retained_backups = var.db_backup_retention_days
        retention_unit   = "COUNT"
      }
    }

    maintenance_window {
      day          = var.db_maintenance_day
      hour         = var.db_maintenance_hour
      update_track = "stable"
    }

    # Database flags — IAM auth + memory/parallelism tuning for the 16 vCPU
    # 64 GB tier. shared_buffers = 25% RAM, effective_cache_size = 75% RAM
    # per the canonical Postgres tuning guidance; maintenance_work_mem = 4GB
    # is the HNSW build budget per audit/2026-05-21-phase2-postgres/pgvector-spec.md.
    #
    # NOTE: pgvector is enabled at the database level via `CREATE EXTENSION
    # vector;` in the Alembic baseline migration (Task #29), NOT here.
    # Cloud SQL Postgres 16 ships pgvector pre-installed; no
    # shared_preload_libraries flag is required for HNSW operations.
    dynamic "database_flags" {
      for_each = local.database_flags
      content {
        name  = database_flags.value.name
        value = database_flags.value.value
      }
    }

    # Labels live inside settings{} as user_labels on Cloud SQL — the
    # provider does NOT expose a top-level `labels` arg on
    # google_sql_database_instance (unlike most other GCP resources).
    user_labels = {
      phase     = "2"
      component = "autonomousagent"
      tier      = "memory"
    }
  }

  # Belt: terraform state-level guard against accidental delete.
  # Braces: sub-module state isolation (see providers.tf comment).
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [
    google_project_service.apis,
    google_service_networking_connection.default,
  ]
}

locals {
  database_flags = [
    # IAM database authentication (no password auth allowed).
    { name = "cloudsql.iam_authentication", value = "on" },

    # Memory tuning for 64GB RAM tier.
    { name = "shared_buffers", value = "16777216" },       # 16 GB (25% RAM)
    { name = "effective_cache_size", value = "50331648" }, # 48 GB (75% RAM)
    { name = "maintenance_work_mem", value = "4194304" },  #  4 GB (HNSW build budget)
    { name = "work_mem", value = "131072" },               # 128 MB per query

    # Parallelism (match the 16 vCPU tier).
    { name = "max_parallel_workers", value = "16" },
    { name = "max_parallel_workers_per_gather", value = "4" },

    # Connection limits.
    { name = "max_connections", value = tostring(var.db_max_connections) },

    # WAL tuning for PITR durability.
    { name = "wal_buffers", value = "16384" }, # 16 MB
  ]
}

# Application schema database.
resource "google_sql_database" "hermes" {
  project  = var.project_id
  name     = "hermes"
  instance = google_sql_database_instance.postgres_vector.name
}

# IAM database user — the VM runtime SA logs in via Cloud SQL Auth Proxy
# using its Google IAM token. NOTE: for Cloud IAM service-account auth,
# the Cloud SQL user name is the SA email WITHOUT the `.gserviceaccount.com`
# suffix (`autonomousagent-vm-runtime@i-for-ai.iam`), and `type` MUST be
# CLOUD_IAM_SERVICE_ACCOUNT (NOT CLOUD_IAM_USER, which is for human users).
resource "google_sql_user" "vm_runtime" {
  project  = var.project_id
  name     = "${data.google_service_account.vm_runtime.account_id}@${var.project_id}.iam"
  instance = google_sql_database_instance.postgres_vector.name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

# Cloud SQL Client role on the VM runtime SA — required for the SA's IAM
# token to authenticate against Cloud SQL Auth Proxy. This is project-scoped
# because Cloud SQL Auth Proxy authenticates against the project's metadata
# service, not the individual instance.
resource "google_project_iam_member" "vm_runtime_cloudsql_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${data.google_service_account.vm_runtime.email}"
}

# ---------------------------------------------------------------------------
# Secret Manager — connection metadata (NOT a password; IAM auth only).
#
# Stored as a JSON blob so the application reads ONE secret to bootstrap
# the Cloud SQL Auth Proxy connection string. No password is ever stored
# anywhere — IAM auth replaces it with a short-lived OAuth token at
# connection time, rotated by the proxy.
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "db_connection" {
  project   = var.project_id
  secret_id = "autonomousagent-db-connection"

  replication {
    auto {}
  }

  labels = {
    phase     = "2"
    component = "autonomousagent"
    tier      = "memory"
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_connection" {
  secret = google_secret_manager_secret.db_connection.id

  secret_data = jsonencode({
    host            = google_sql_database_instance.postgres_vector.private_ip_address
    database        = google_sql_database.hermes.name
    user            = google_sql_user.vm_runtime.name
    connection_name = google_sql_database_instance.postgres_vector.connection_name
  })
}

# Explicit secretAccessor grant on the DB-connection secret for the VM
# runtime SA. Root iam.tf already grants project-wide secretmanager.secretAccessor
# to this SA, so this is redundant — but explicit documentation of the
# secret → consumer mapping is more durable than relying on a project-wide
# grant in a different file.
resource "google_secret_manager_secret_iam_member" "vm_runtime_db_secret" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.db_connection.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_service_account.vm_runtime.email}"
}
