# Phase 0a — Local snapshot staging bucket.
#
# Resolves OQ-3: daily PD snapshots stage in this in-region bucket for
# fast restore. Cross-region durability (weekly off-region copies) remains
# the responsibility of the existing PR #108 spend-log GCS bucket, which
# is structured for that purpose; this bucket is deliberately narrow.
#
# Naming: i-for-ai-autonomousagent-snapshots — `i-for-ai-` prefix is the
# project-namespace convention; `-snapshots` to keep distinct from the
# already-existing i-for-ai-autonomousagent-tfstate bucket.
#
# Location: us-central1 (regional, NOT multi-region) — co-located with
# the VM and PDs for fast snapshot operations; cross-region is the other
# bucket's job, not this one's.
#
# Lifecycle: 30 day delete is intentionally generous — daily PD snapshots
# are themselves small (delta-encoded by GCE) and 30d gives a wide
# recovery window without ballooning cost.

resource "google_storage_bucket" "snapshots" {
  project                     = var.project_id
  name                        = "i-for-ai-autonomousagent-snapshots"
  location                    = upper(var.region) # GCS API expects "US-CENTRAL1"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning { enabled = true }

  lifecycle_rule {
    condition {
      age = 30 # days
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.enabled]
}
