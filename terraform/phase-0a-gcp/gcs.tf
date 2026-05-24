# Phase 0a — Local snapshot staging bucket.
#
# Resolves OQ-3: daily PD snapshots stage in this in-region bucket for
# fast restore. Cross-region durability (weekly off-region copies) remains
# the responsibility of the existing PR #108 spend-log GCS bucket, which
# is structured for that purpose; this bucket is deliberately narrow.
#
# Naming: autonomous-agent-2026-snapshots — project-prefixed to keep
# globally unique and distinct from the tfstate bucket.
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
  name                        = "autonomous-agent-2026-snapshots"
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

# Phase 0a — J3 trajectory shipper destination bucket.
#
# Per-record Model Armor sanitize output from lib/trajectory/shipper.py
# lands here. The bucket holds redacted judge-event JSONL; un-redacted
# payloads MUST NEVER reach it (enforced by application-layer
# Persistence Trap test contract at tests/integration/test_persistence_trap.py).
#
# Naming: i-for-ai-autonomousagent-j3-trajectories — matches the
# i-for-ai-autonomousagent-* convention. Hyphenated to avoid the
# underscore-vs-dash mismatch that previously surfaced in
# audit/2026-05-21-gemini-delegation/model-armor-apply.output.
#
# Location: us-central1 (regional) — co-located with the VM, the Cloud SQL
# instance, and the Model Armor regional template. Cross-region durability
# for trajectories is not in scope for Phase 0a; Phase 4 RL training-data
# ingest will replicate as needed.
#
# Retention: 365 days. Trajectories are training-substrate input — the
# Phase 4 RL training pipeline will reach back over a year of judge
# verdicts. Lifecycle deletion at 365d prevents indefinite accumulation.
# Versioning OFF — the redacted record is the only record; a "previous
# version" would be unredacted by definition (Persistence Trap violation).

resource "google_storage_bucket" "j3_trajectories" {
  project                     = var.project_id
  name                        = "i-for-ai-autonomousagent-j3-trajectories"
  location                    = upper(var.region) # GCS API expects "US-CENTRAL1"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning { enabled = false }

  lifecycle_rule {
    condition {
      age = 365 # days
    }
    action {
      type = "Delete"
    }
  }

  # Belt + braces against accidental teardown: Persistence Trap data is the
  # training substrate. Loss of this bucket = loss of all sanitized judge
  # events. Sub-module isolation cannot apply here (this is a root resource
  # by design — Postgres-level isolation is for the $1,580/mo instance).
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.enabled]
}

# VM runtime SA needs object-write (NOT delete, NOT read) on the bucket.
# storage.objectCreator is the least-privilege role that allows POST of new
# objects without enabling read of existing objects or modification of bucket
# config. This matches the Persistence Trap "write-only / append-only"
# semantics — the shipper never reads back what it wrote.
resource "google_storage_bucket_iam_member" "j3_trajectories_vm_writer" {
  bucket = google_storage_bucket.j3_trajectories.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.vm_runtime.email}"
}
