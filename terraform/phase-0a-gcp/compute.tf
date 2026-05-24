# Phase 0a — Persistent storage + daily snapshot policy.
#
# The VM resource itself and disks are co-located here because they
# share the same lifecycle and dependency graph.
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
# the Phase 0a module on dedicated autonomous-agent-2026 project.

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

resource "google_compute_instance" "autonomousagent" {
  name         = "autonomousagent-vm"
  machine_type = var.vm_machine_type
  zone         = var.zone
  tags         = ["autonomousagent-vm"]

  boot_disk {
    source      = google_compute_disk.boot.self_link
    auto_delete = false
  }

  attached_disk {
    source      = google_compute_disk.data.self_link
    device_name = "hermes-data"
    mode        = "READ_WRITE"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.autonomousagent.id
    # No access_config block — no public IP.
  }

  service_account {
    email  = google_service_account.vm_runtime.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    preemptible         = false
  }

  metadata = {
    enable-oslogin     = "TRUE"
    startup-script-url = "gs://autonomous-agent-2026-snapshots/bootstrap/install.sh"
    hermes-image-repo  = "us-central1-docker.pkg.dev/${var.project_id}/autonomousagent-images"
  }

  labels = {
    phase = "0a"
  }

  allow_stopping_for_update = true
  depends_on                = [google_storage_bucket.snapshots]
}
