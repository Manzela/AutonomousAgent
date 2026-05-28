# Phase 0a — GCP Billing Budget + cost anomaly detection (P0-15).
#
# Note: google_monitoring_alert_policy cost-anomaly alert is omitted
# because GCP billing metrics require billing export to be active on the
# billing account, and the current Terraform provider version (5.45.2)
# does not support disable_metric_validation to bypass API validation.
# Monthly thresholds (50%/75%/90%/100%) below provide standard cost controls.

data "google_billing_account" "primary" {
  billing_account = "01FABE-89B1B2-4C704D"
}

resource "google_billing_budget" "monthly" {
  billing_account = data.google_billing_account.primary.id
  display_name    = "autonomousagent-monthly-2026"

  budget_filter {
    projects = ["projects/${var.project_id}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = "2000"   # monthly cap
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.75
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }
  threshold_rules {
    threshold_percent = 0.5
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [
      google_monitoring_notification_channel.email.id
    ]
    disable_default_iam_recipients = false
  }
}
