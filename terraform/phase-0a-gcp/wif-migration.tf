# WIF (Workload Identity Federation) setup for Cloud Run
# Replaces the W0.6 static SA keys.

resource "google_iam_workload_identity_pool" "github_pool" {
  project                   = var.project_id
  workload_identity_pool_id = "github-actions-pool"
  display_name              = "GitHub Actions Pool"
  description               = "OIDC pool for GitHub Actions deployments"
}

resource "google_iam_workload_identity_pool_provider" "github_provider" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_pool.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions-provider"
  display_name                       = "GitHub Actions Provider"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
  }

  attribute_condition = "attribute.repository == 'Manzela/AutonomousAgent'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Bind the service accounts to the WIF provider
resource "google_service_account_iam_member" "litellm_proxy_wif" {
  service_account_id = google_service_account.litellm_proxy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/Manzela/AutonomousAgent"
}

resource "google_service_account_iam_member" "cloud_sql_proxy_wif" {
  service_account_id = google_service_account.cloud_sql_proxy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/Manzela/AutonomousAgent"
}

resource "google_service_account_iam_member" "snapshot_watchdog_wif" {
  service_account_id = google_service_account.snapshot_watchdog.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool.name}/attribute.repository/Manzela/AutonomousAgent"
}

# Cloud Run Service configuration for Hermes
resource "google_cloud_run_v2_service" "hermes_service" {
  name     = "hermes-agent"
  location = var.region
  project  = var.project_id

  template {
    service_account = google_service_account.hermes_agent.email

    containers {
      image = "ghcr.io/manzela/autonomousagent-hermes:latest"

      # Notice: NO GOOGLE_APPLICATION_CREDENTIALS
      # Uses the metadata server provided by Cloud Run native identity

      env {
        name  = "CLOUD_SQL_DSN"
        value = "postgresql://..." # Typically fetched from Secret Manager
      }
    }
  }
}
