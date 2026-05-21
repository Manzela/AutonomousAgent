# Phase 2 — Database outputs
#
# Exports Cloud SQL instance metadata for downstream consumption
# (e.g., Cloud SQL Proxy configuration, application runtime env vars).

output "db_instance_name" {
  description = "Cloud SQL instance name (short form)"
  value       = google_sql_database_instance.postgres_vector.name
}

output "db_instance_connection_name" {
  description = "Cloud SQL instance connection name (format: project:region:instance)"
  value       = google_sql_database_instance.postgres_vector.connection_name
}

output "db_private_ip_address" {
  description = "Private IP address of the Cloud SQL instance (VPC peering)"
  value       = google_sql_database_instance.postgres_vector.private_ip_address
}

output "db_database_name" {
  description = "PostgreSQL database name"
  value       = google_sql_database.hermes.name
}

output "db_iam_user" {
  description = "IAM database user (service account-based)"
  value       = google_sql_user.vm_runtime.name
}

output "db_secret_id" {
  description = "Secret Manager secret ID containing database connection metadata"
  value       = google_secret_manager_secret.db_connection.secret_id
}

output "db_secret_resource_name" {
  description = "Full resource name of the database connection secret"
  value       = google_secret_manager_secret.db_connection.id
}

output "db_connection_string_cloudsql_proxy" {
  description = "Cloud SQL Proxy connection string (Unix socket)"
  value       = "host=/cloudsql/${google_sql_database_instance.postgres_vector.connection_name} dbname=${google_sql_database.hermes.name} user=${google_sql_user.vm_runtime.name}"
  sensitive   = false  # No password (IAM auth)
}

output "db_instance_tier" {
  description = "Cloud SQL instance tier (vCPU + RAM)"
  value       = var.db_instance_tier
}

output "db_disk_size_gb" {
  description = "Provisioned SSD size in GB"
  value       = var.db_disk_size_gb
}

output "db_region" {
  description = "Cloud SQL instance region"
  value       = var.region
}

output "db_availability_type" {
  description = "Cloud SQL availability type (REGIONAL for HA)"
  value       = google_sql_database_instance.postgres_vector.settings[0].availability_type
}

output "db_backup_enabled" {
  description = "Whether daily backups are enabled"
  value       = google_sql_database_instance.postgres_vector.settings[0].backup_configuration[0].enabled
}

output "db_pitr_enabled" {
  description = "Whether point-in-time recovery is enabled"
  value       = google_sql_database_instance.postgres_vector.settings[0].backup_configuration[0].point_in_time_recovery_enabled
}
