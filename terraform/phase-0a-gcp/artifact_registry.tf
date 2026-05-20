# Phase 0a — Container image registry.
#
# Naming: autonomousagent-images (renamed from generic `hermes` to avoid
# collision and to be self-describing on the shared i-for-ai project,
# which already hosts cloud-run-source-deploy, gcf-artifacts,
# mcp-cloud-run-deployments, and vertex-serving repos).
#
# Cleanup policies:
#   - keep-30-most-recent: retain the 30 newest image versions per package
#                          (covers rollback for ~30 deploys at current cadence)
#   - delete-untagged-after-7d: prune dangling/orphaned image layers a week
#                               after they become untagged (compose pull
#                               leaves these behind on rolling deploys)
#
# Image tagging convention (set by CI in Task 30):
#   <region>-docker.pkg.dev/<project>/autonomousagent-images/hermes:<git-sha>
#   <region>-docker.pkg.dev/<project>/autonomousagent-images/hermes:latest

resource "google_artifact_registry_repository" "autonomousagent_images" {
  project       = var.project_id
  location      = var.region
  repository_id = "autonomousagent-images"
  description   = "AutonomousAgent container images, tagged by git SHA"
  format        = "DOCKER"

  cleanup_policies {
    id     = "keep-30-most-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 30
    }
  }

  cleanup_policies {
    id     = "delete-untagged-after-7d"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s"
    }
  }

  depends_on = [google_project_service.enabled]
}
