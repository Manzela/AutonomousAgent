# Phase 0a — Cloud Monitoring: notification channel, log-based metric for
# watchdog restart events, and alert policies for AC-3 and AC-4.
#
# AC-3: VM availability — alerts when autonomousagent-vm is stopped.
# AC-4: Watchdog restart — alerts when hermes_watchdog_restart_triggered
#       appears in gcplogs output from the VM.
#
# Note: External uptime check for litellm /health omitted — VM has no public
# IP. VM-level availability (uptime metric) is the best GCP-native proxy.

resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "autonomousagent-email-alert"
  type         = "email"

  labels = {
    email_address = "manzela@tngshopper.com"
  }

  depends_on = [google_project_service.enabled]
}

# Log-based metric: count watchdog restart events emitted by hermes-watchdog.sh
# as structured JSON via gcplogs -> Cloud Logging.
resource "google_logging_metric" "watchdog_restart" {
  project = var.project_id
  name    = "autonomousagent/watchdog_restart_triggered"

  filter = <<-EOT
    resource.type="gce_instance"
    resource.labels.instance_name="autonomousagent-vm"
    jsonPayload.msg="hermes_watchdog_restart_triggered"
  EOT

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    unit         = "1"
    display_name = "AutonomousAgent Watchdog Restart Events"
  }

  depends_on = [google_project_service.enabled]
}

# Alert: any watchdog restart event (threshold = 0 to alert on first occurrence).
resource "google_monitoring_alert_policy" "watchdog_restart" {
  project      = var.project_id
  display_name = "autonomousagent-watchdog-restart"
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = "Watchdog restart triggered"
    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/autonomousagent/watchdog_restart_triggered\" resource.type=\"gce_instance\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_COUNT"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  documentation {
    content   = "A hermes container restart was triggered by the host-level watchdog. Check `journalctl -u hermes-watchdog.service` and `docker compose logs` on autonomousagent-vm."
    mime_type = "text/markdown"
  }

  depends_on = [google_logging_metric.watchdog_restart]

  alert_strategy {
    auto_close = "1800s"
  }
}

# Alert: VM uptime drops to zero (instance stopped or terminated).
resource "google_monitoring_alert_policy" "vm_down" {
  project      = var.project_id
  display_name = "autonomousagent-vm-down"
  combiner     = "OR"
  enabled      = true

  conditions {
    display_name = "VM not running"
    condition_threshold {
      filter          = "metric.type=\"compute.googleapis.com/instance/uptime\" resource.type=\"gce_instance\" metadata.system_labels.name=\"autonomousagent-vm\""
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "300s"

      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MEAN"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  documentation {
    content   = "autonomousagent-vm is not reporting uptime. Check GCE console — the instance may be stopped or preempted."
    mime_type = "text/markdown"
  }

  depends_on = [google_project_service.enabled]

  alert_strategy {
    auto_close = "1800s"
  }
}
