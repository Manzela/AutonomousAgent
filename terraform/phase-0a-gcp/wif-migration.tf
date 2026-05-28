# HIGH-1 fix (2026-05-28): WIF for Cloud Run runtime identity.
#
# ARCHITECTURE DECISION — Why runtime SAs do NOT use the GitHub WIF pool:
#
#   GitHub Actions WIF (wif.tf) authenticates CI/CD RUNNERS.  The federation
#   works because Actions jobs can call token.actions.githubusercontent.com to
#   obtain an OIDC token proving "I am job X in repo Y".  Cloud Run containers
#   CANNOT do this — they have no access to the GitHub OIDC endpoint and no
#   GitHub job context.
#
#   Cloud Run provides identity natively: when `template.service_account` is
#   set, GCP's metadata server automatically vends short-lived access tokens
#   for that SA.  Any library that calls google.auth.default() or uses ADC
#   picks these up without any credential files or WIF configuration.
#
#   Binding runtime SAs to the GitHub WIF pool (original wif-migration.tf)
#   was WRONG in two ways:
#     1. The binding is unreachable from Cloud Run — runtime services can
#        never present a GitHub OIDC token, so the WIF path is dead code.
#     2. It is a privilege-escalation risk: a GitHub Actions job (any workflow
#        in Manzela/AutonomousAgent) could have impersonated litellm_proxy,
#        cloud_sql_proxy, and snapshot_watchdog — runtime SAs with DB and
#        Secret Manager access.
#
#   CORRECT identity graph:
#     CI/CD:    GitHub Actions OIDC → WIF pool (wif.tf) → github_ci SA
#     Runtime:  Cloud Run service → metadata server ADC → hermes_agent SA
#               GCE VM → metadata server ADC → vm_runtime SA
#
#   The three per-service SAs in sa-keys.tf (litellm_proxy, cloud_sql_proxy,
#   snapshot_watchdog) are W0 transitional artefacts.  W1.D.I-8 migrates
#   those services to Cloud Run and drops the key files; at that point each
#   service uses its Cloud Run service_account + metadata-server ADC.

# -----------------------------------------------------------------------------
# Cloud Run service — Hermes agent
# -----------------------------------------------------------------------------
# service_account is the only identity mechanism needed.
# NO GOOGLE_APPLICATION_CREDENTIALS env var — ADC uses the metadata server.
# image_tag must be supplied at apply time by the CI/CD workflow so the
# deployed revision is always traceable to a specific git SHA.

resource "google_cloud_run_v2_service" "hermes_service" {
  name     = "hermes-agent"
  location = var.region
  project  = var.project_id

  # Deny unauthenticated public invocations — only authenticated callers
  # (e.g. internal services, github_ci SA via IAM invoker binding) may call.
  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.hermes_agent.email

    containers {
      image = "${var.ar_repo}/hermes:${var.image_tag}"

      # Liveness: restart if the health endpoint stops responding.
      liveness_probe {
        http_get {
          path = "/healthz"
          port = 8080
        }
        initial_delay_seconds = 10
        period_seconds        = 30
        failure_threshold     = 3
      }
    }

    # Cap concurrency and resources to prevent runaway cost.
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
  }
}

# Allow the github_ci SA (used by CI/CD) to deploy new revisions.
resource "google_cloud_run_v2_service_iam_member" "github_ci_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.hermes_service.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.github_ci.email}"
}
