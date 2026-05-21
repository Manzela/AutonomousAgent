# Model Armor Configuration for J1 Trajectory Shipper
#
# SUPERSEDED 2026-05-21: this draft is preserved for audit history only.
# The authoritative module lives at `terraform/phase-0a-gcp/model-armor/`
# (carved into a sub-module to isolate the google-beta ~> 6.43 provider pin).
# Do NOT terraform apply from this directory — it has no backend + no state.
#
# This file defines the Model Armor floor settings and SDP templates for project i-for-ai.
# It follows the conventions of terraform/phase-0a-gcp/.

resource "google_project_service" "model_armor_apis" {
  for_each = toset([
    "modelarmor.googleapis.com",
    "dlp.googleapis.com"
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# Model Armor Configuration for J1 Trajectory Shipper

# DLP Inspect Template for granular PII detection
resource "google_data_loss_prevention_inspect_template" "j1_inspect_template" {
  parent       = "projects/${var.project_id}"
  description  = "Inspect and redact PII for J1 judge verdicts"
  display_name = "j1-inspect-and-redact"

  inspect_config {
    # Terraform provider enum (NOT the REST API's LIKELIHOOD_LOW): UNLIKELY
    # errs aggressively toward redaction without VERY_UNLIKELY's noise floor.
    min_likelihood = "UNLIKELY"

    dynamic "info_types" {
      for_each = [
        "EMAIL_ADDRESS",
        "CREDIT_CARD_NUMBER",
        "PHONE_NUMBER",
        "US_SOCIAL_SECURITY_NUMBER"
      ]
      content {
        name = info_types.value
      }
    }
  }
}

# Project-level Floor Settings
resource "google_model_armor_floorsetting" "project_floor" {
  provider                         = google-beta
  parent                           = "projects/${var.project_id}"
  location                         = "global"
  enable_floor_setting_enforcement = true

  filter_config {
    sdp_settings {
      # Use advanced_config to reference the DLP template
      advanced_config {
        inspect_template = google_data_loss_prevention_inspect_template.j1_inspect_template.id
      }
    }
  }

  depends_on = [google_project_service.model_armor_apis]
}

# SDP Template for explicit sanitization if needed by Task #12.c
resource "google_model_armor_template" "j1_trajectory_shipper" {
  provider    = google-beta
  project     = var.project_id
  location    = "us-central1"
  template_id = "j1-trajectory-shipper"

  filter_config {
    sdp_settings {
      advanced_config {
        inspect_template = google_data_loss_prevention_inspect_template.j1_inspect_template.id
      }
    }
  }

  depends_on = [google_project_service.model_armor_apis]
}
