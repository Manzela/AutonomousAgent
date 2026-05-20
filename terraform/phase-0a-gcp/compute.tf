# Phase 0a — Persistent storage + daily snapshot policy.
#
# The VM resource itself lands in Task 16; this file owns the disks and
# the snapshot schedule because they have an independent lifecycle
# (the data disk MUST survive VM rebuilds).
#
# Architecture:
#   - boot disk:  50GB pd-balanced, Debian 12, ephemeral-equivalent
#                 (no snapshots — VM is reproducible from startup-script
#                 + Artifact Registry images)
#   - data disk: 100GB pd-balanced, backs the docker `hermes-data`
#                 named volume on the VM, daily-snapshotted
#
# Snapshot schedule cadence + retention:
#   daily at 07:00 UTC (~3am US Central — low-activity window)
#   7-day retention
#   on_source_disk_delete = KEEP_AUTO_SNAPSHOTS so accidental disk
#   deletion does not also nuke the recovery snapshots
#
# Naming: autonomousagent-* prefix for consistency with the rest of
# the Phase 0a module on shared i-for-ai.

resource "google_compute_resource_policy" "daily_snapshot" {
  project = var.project_id
  name    = "autonomousagent-data-daily-snapshot"
  region  = var.region

  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "07:00"
      }
    }
    retention_policy {
      max_retention_days    = 7
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
    snapshot_properties {
      storage_locations = [var.region]
      labels = {
        phase = "0a"
        disk  = "autonomousagent-data"
      }
    }
  }
}

resource "google_compute_disk" "boot" {
  project = var.project_id
  name    = "autonomousagent-vm-boot"
  type    = "pd-balanced"
  zone    = var.zone
  size    = var.vm_boot_disk_gb
  image   = "debian-cloud/debian-12"
}

resource "google_compute_disk" "data" {
  project = var.project_id
  name    = "autonomousagent-vm-data"
  type    = "pd-balanced"
  zone    = var.zone
  size    = var.vm_data_disk_gb
}

resource "google_compute_disk_resource_policy_attachment" "data_snapshot" {
  project = var.project_id
  name    = google_compute_resource_policy.daily_snapshot.name
  disk    = google_compute_disk.data.name
  zone    = var.zone
}
