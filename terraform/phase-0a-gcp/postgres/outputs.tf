# Phase 2 — Cloud SQL outputs.
#
# Consumed by: application runtime (Cloud SQL Auth Proxy config), Alembic
# baseline migration (Task #29), acceptance-test scripts in
# audit/2026-05-21-phase2-postgres/acceptance-criteria.md.

output "db_instance_name" {
  description = "Cloud SQL instance short name."
  value       = google_sql_database_instance.postgres_vector.name
}

output "db_instance_connection_name" {
  description = "Cloud SQL connection name `project:region:instance` — the format Cloud SQL Auth Proxy expects."
  value       = google_sql_database_instance.postgres_vector.connection_name
}

output "db_private_ip_address" {
  description = "Private IP of the Cloud SQL instance (allocated from the Service Networking peering range)."
  value       = google_sql_database_instance.postgres_vector.private_ip_address
}

output "db_database_name" {
  description = "Application schema database name."
  value       = google_sql_database.hermes.name
}

output "db_iam_user" {
  description = "Cloud SQL user name for the VM runtime SA (CLOUD_IAM_SERVICE_ACCOUNT type)."
  value       = google_sql_user.vm_runtime.name
}

output "db_secret_id" {
  description = "Secret Manager secret ID containing DB connection metadata."
  value       = google_secret_manager_secret.db_connection.secret_id
}

output "db_secret_resource_name" {
  description = "Fully-qualified resource name of the DB-connection secret (for IAM bindings + audit references)."
  value       = google_secret_manager_secret.db_connection.id
}

output "db_connection_string_cloudsql_proxy" {
  description = "DSN fragment for Cloud SQL Auth Proxy (Unix socket). The application appends auth + extra params at connect time."
  value       = "host=/cloudsql/${google_sql_database_instance.postgres_vector.connection_name} dbname=${google_sql_database.hermes.name} user=${google_sql_user.vm_runtime.name}"
  sensitive   = false # No password material — IAM auth only.
}

output "db_instance_tier" {
  description = "Cloud SQL instance tier (echoed for documentation/audit)."
  value       = var.db_instance_tier
}

output "db_disk_size_gb" {
  description = "Provisioned SSD size in GB."
  value       = var.db_disk_size_gb
}

output "db_region" {
  description = "Cloud SQL instance region."
  value       = var.region
}

output "db_availability_type" {
  description = "Cloud SQL availability type (REGIONAL for HA)."
  value       = google_sql_database_instance.postgres_vector.settings[0].availability_type
}

output "db_backup_enabled" {
  description = "Whether daily backups are enabled (always true in this module)."
  value       = google_sql_database_instance.postgres_vector.settings[0].backup_configuration[0].enabled
}

output "db_pitr_enabled" {
  description = "Whether point-in-time recovery is enabled (always true in this module)."
  value       = google_sql_database_instance.postgres_vector.settings[0].backup_configuration[0].point_in_time_recovery_enabled
}

output "vpc_peering_range_name" {
  description = "Name of the VPC peering range allocated for Service Networking. Other private-IP Google-managed services on this VPC (Memorystore, Vertex Index Endpoint, etc.) would share this allocation."
  value       = google_compute_global_address.private_ip_alloc.name
}
