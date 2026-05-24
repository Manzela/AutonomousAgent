# Phase 0a — Workload Identity Federation for GitHub Actions.
#
# Replaces long-lived JSON service-account keys with short-lived OIDC
# tokens minted by token.actions.githubusercontent.com. The federation
# graph is:
#
#   GitHub Actions workflow (OIDC token)
#     → google_iam_workload_identity_pool_provider.autonomousagent_actions
#     → google_iam_workload_identity_pool.autonomousagent_github
#     → impersonates google_service_account.github_ci
#     → uses scoped roles to deploy
#
# Naming:
#   pool      = autonomousagent-github
#   provider  = autonomousagent-actions
# These are in the dedicated autonomous-agent-2026 project — no
# collision risk with sibling workloads.
#
# attribute_condition restricts the federation to one repo only — even
# if another GitHub repo somehow obtains an OIDC token signed by the
# same issuer, the token's repository claim must match Manzela/AutonomousAgent
# or impersonation fails closed.

resource "google_iam_workload_identity_pool" "autonomousagent_github" {
  project                   = var.project_id
  workload_identity_pool_id = "autonomousagent-github"
  display_name              = "AutonomousAgent GitHub"
  description               = "OIDC federation for AutonomousAgent CI"
  depends_on                = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "autonomousagent_actions" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.autonomousagent_github.workload_identity_pool_id
  workload_identity_pool_provider_id = "autonomousagent-actions"
  display_name                       = "Manzela/AutonomousAgent"

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.actor"            = "assertion.actor"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
  }

  attribute_condition = "attribute.repository == \"${var.github_owner}/${var.github_repo}\" && attribute.repository_owner == \"${var.github_owner}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_ci_can_be_impersonated" {
  service_account_id = google_service_account.github_ci.id
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.autonomousagent_github.name}/attribute.repository/${var.github_owner}/${var.github_repo}"
}
