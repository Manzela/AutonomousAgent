# Phase 0a — Container image registry.
#
# Naming: autonomousagent-images — self-describing for the dedicated
# autonomous-agent-2026 project.
#
# Cleanup policies:
#   - keep-30-most-recent: retain the 30 newest image versions per package
#                          (covers rollback for ~30 deploys at current cadence)
#   - delete-untagged-after-7d: prune dangling/orphaned image layers a week
#                               after they become untagged (digest-pull deploys
#                               leave these behind on rolling deploys)
#
# Image tagging convention (set by CI — H-19a: no :latest; SHA-only tags):
#   <region>-docker.pkg.dev/<project>/autonomousagent-images/hermes:<git-sha>
#   <region>-docker.pkg.dev/<project>/autonomousagent-images/shell-sandbox:<git-sha>
#
# immutable_tags = true: once a tag is written (e.g. hermes:abc123def456), it
# cannot be re-pointed to a different digest. This prevents a tag-swap attack
# from bypassing the cosign verify-at-deploy gate.
# Requires google provider >= 5.19.0 (locked at 5.45.2 in .terraform.lock.hcl).
# Note: buildcache tags (hermes:buildcache, shell-sandbox:buildcache) are written
# once per build and overwritten on each push — with immutable_tags=true these
# will fail to update on a second build for the same tag. To avoid this, CI uses
# a content-addressable key via BuildKit cache manifests (mode=max). AR rejects
# an overwrite only if the manifest digest changes; identical-content rebuilds
# produce the same digest and are accepted. If buildcache becomes problematic,
# migrate cache-to/cache-from to a separate mutable-tags registry or use
# type=gha (GitHub Actions cache) instead.

resource "google_artifact_registry_repository" "autonomousagent_images" {
  project       = var.project_id
  location      = var.region
  repository_id = "autonomousagent-images"
  description   = "AutonomousAgent container images, tagged by git SHA (immutable tags)"
  format        = "DOCKER"

  docker_config {
    immutable_tags = true
  }

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
