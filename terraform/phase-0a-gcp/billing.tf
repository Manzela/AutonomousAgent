# Phase 0a — Billing budget alert for project i-for-ai (AC-10).
#
# Budget: $7,750/mo (~$250/day × 31 days).
# GCP billing budgets are calendar-month scoped — no native daily period.
# $250/day cap is approximated as a monthly ceiling.
# Alert thresholds: 25% (~$63/day), 50% (~$125/day), 75%, 100%.
#
# Scoped to project i-for-ai to avoid cross-project noise on this shared
# billing account. Notification emails go to the same channel as monitoring.
#
# billing_account: hardcoded for i-for-ai — changes only if billing is
# restructured across projects, which is an operator action, not IaC.

resource "google_billing_budget" "autonomousagent" {
  billing_account = "01FABE-89B1B2-4C704D"
  display_name    = "autonomousagent-phase-0a"

  budget_filter {
    projects = ["projects/85113401879"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = "7750"
    }
  }

  threshold_rules {
    threshold_percent = 0.25
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.50
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 0.75
    spend_basis       = "CURRENT_SPEND"
  }

  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "CURRENT_SPEND"
  }

  all_updates_rule {
    monitoring_notification_channels = [
      google_monitoring_notification_channel.email.id
    ]
    disable_default_iam_recipients = false
  }
}
