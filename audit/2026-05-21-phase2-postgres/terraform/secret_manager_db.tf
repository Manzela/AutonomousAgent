# Phase 2 — Secret Manager: Database connection details
#
# Stores Cloud SQL instance connection metadata (NOT passwords — IAM auth only).
# Application runtime fetches this secret to construct the Cloud SQL Proxy
# connection string.
#
# Secret value (JSON blob):
#   {
#     "host": "<private-ip>",
#     "database": "hermes",
#     "user": "autonomousagent-vm-runtime@i-for-ai.iam",
#     "connection_name": "i-for-ai:us-central1:autonomousagent-postgres-vector"
#   }
#
# IAM binding: autonomousagent-vm-runtime SA granted secretAccessor role
# (already granted globally per phase-0a iam.tf, but scoped here for clarity).

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

  depends_on = [google_project_service.enabled]
}

# Secret value: database connection metadata (JSON)
resource "google_secret_manager_secret_version" "db_connection" {
  secret = google_secret_manager_secret.db_connection.id

  secret_data = jsonencode({
    host            = google_sql_database_instance.postgres_vector.private_ip_address
    database        = google_sql_database.hermes.name
    user            = google_sql_user.vm_runtime.name
    connection_name = google_sql_database_instance.postgres_vector.connection_name
  })
}

# Grant VM runtime SA access to read the secret
# NOTE: This is redundant with the global secretmanager.secretAccessor role
# binding in phase-0a iam.tf (lines 27-42), but included here for explicit
# documentation of the DB secret → VM runtime SA dependency.
resource "google_secret_manager_secret_iam_member" "vm_runtime_db_secret" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.db_connection.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.vm_runtime.email}"
}
