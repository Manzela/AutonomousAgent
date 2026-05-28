terraform {
  required_providers {
    github = { source = "integrations/github", version = "~> 6.0" }
  }
}

provider "github" {
  owner = "Manzela"
}

resource "github_branch_protection" "main" {
  repository_id = "AutonomousAgent"
  pattern       = "main"

  require_signed_commits  = true
  required_linear_history = true
  enforce_admins          = true
  allows_force_pushes     = false
  allows_deletions        = false

  required_pull_request_reviews {
    required_approving_review_count = 2
    require_code_owner_reviews      = true
    dismiss_stale_reviews           = true
    require_last_push_approval      = true
  }

  required_status_checks {
    strict   = true
    contexts = ["ci", "trivy", "OSV Scanner", "secret-scan", "codeql", "no-undocumented-skips"]
  }
}
