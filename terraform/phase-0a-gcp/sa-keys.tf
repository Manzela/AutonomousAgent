/**
 * W0.6 Per-service SA keys
 *
 * Defines 3 minimum-scope Service Accounts.
 * Keys are generated and then manually SOPS-encrypted per W0.6 requirements.
 * Replaced by WIF identity tokens in W1.D.I-8.
 */

resource "google_service_account" "litellm_proxy" {
  account_id   = "litellm-proxy"
  display_name = "LiteLLM Proxy Runtime SA"
  description  = "Minimum-scope SA for LiteLLM proxy (Vertex AI only). Slated for WIF migration (W1.D.I-8)."
}

resource "google_project_iam_member" "litellm_proxy_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.litellm_proxy.email}"
}

# Keys removed per W1.D.I-8 WIF migration

resource "google_service_account" "cloud_sql_proxy" {
  account_id   = "cloud-sql-proxy"
  display_name = "Cloud SQL Auth Proxy SA"
  description  = "Minimum-scope SA for Cloud SQL Auth Proxy. Slated for WIF migration (W1.D.I-8)."
}

resource "google_project_iam_member" "cloud_sql_proxy_client" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.cloud_sql_proxy.email}"
}


resource "google_service_account" "snapshot_watchdog" {
  account_id   = "snapshot-watchdog"
  display_name = "Snapshot Watchdog SA"
  description  = "Minimum-scope SA for snapshot-watchdog (GCS writes). Slated for WIF migration (W1.D.I-8)."
}

resource "google_storage_bucket_iam_member" "snapshot_watchdog_storage" {
  bucket = google_storage_bucket.snapshots.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.snapshot_watchdog.email}"
}
