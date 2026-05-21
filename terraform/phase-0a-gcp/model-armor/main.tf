# ---------------------------------------------------------------------------
# Model Armor + Sensitive Data Protection (SDP/DLP) for the J1 trajectory
# shipper. Realizes ADR-0008 Q6 (see model-armor-j1-config memory): every
# judge verdict reaching GCS must already be PII-redacted so it is safe for
# Phase 4 RL training.
#
# IMPORTANT: applying this module enforces Floor Settings at the PROJECT level
# — it affects every Model Armor invocation in the project, not just the J1
# shipper. Read audit/2026-05-20-model-armor-j1-runbook/runbook.md before plan.
# ---------------------------------------------------------------------------

resource "google_project_service" "apis" {
  for_each = toset([
    "modelarmor.googleapis.com",
    "dlp.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# DLP/SDP InspectTemplate at project + global so it is reachable by both the
# project-level FloorSetting and the regional google_model_armor_template.
resource "google_data_loss_prevention_inspect_template" "j1" {
  parent       = "projects/${var.project_id}/locations/global"
  description  = "Inspect and redact PII in J1 judge verdicts before they reach GCS (RLAIF substrate)."
  display_name = var.inspect_template_display_name

  inspect_config {
    min_likelihood = var.min_likelihood

    dynamic "info_types" {
      for_each = var.info_types
      content {
        name = info_types.value
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# Project-level Floor Settings — enforces the SDP InspectTemplate on every
# Model Armor call against the project. Without this, individual templates
# can be bypassed by callers that forget to specify them.
resource "google_model_armor_floorsetting" "project" {
  provider                         = google-beta
  parent                           = "projects/${var.project_id}"
  location                         = "global"
  enable_floor_setting_enforcement = true

  filter_config {
    sdp_settings {
      advanced_config {
        inspect_template = google_data_loss_prevention_inspect_template.j1.id
      }
    }
  }

  depends_on = [google_project_service.apis]
}

# Named Model Armor template the J1 trajectory shipper calls via
# templates.sanitize when capturing the verdict payload. This is the
# explicit-sanitize path that closes the Persistence Trap (Task #12.c):
# even if a future caller writes to GCS pre-inference, calling this
# template re-redacts before persistence.
resource "google_model_armor_template" "j1_trajectory_shipper" {
  provider    = google-beta
  project     = var.project_id
  location    = var.region
  template_id = "j1-trajectory-shipper"

  filter_config {
    sdp_settings {
      advanced_config {
        inspect_template = google_data_loss_prevention_inspect_template.j1.id
      }
    }
  }

  depends_on = [google_project_service.apis]
}
