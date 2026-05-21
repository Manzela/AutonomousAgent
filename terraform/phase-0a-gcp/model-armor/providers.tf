# Model Armor sub-module — isolated to allow newer google-beta provider (~> 6.x)
# required for google_model_armor_floorsetting and google_model_armor_template
# resources, without forcing an upgrade of the root phase-0a-gcp module pinned
# to ~> 5.30.
#
# State is stored in the same GCS bucket as root, but under prefix
# "phase-0a-model-armor" so apply/destroy here cannot disturb root state.

terraform {
  required_version = ">= 1.7.0"
  required_providers {
    google      = { source = "hashicorp/google",      version = "~> 6.43" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 6.43" }
  }
  backend "gcs" {
    bucket = "i-for-ai-autonomousagent-tfstate"
    prefix = "phase-0a-model-armor"
  }
}

provider "google" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}
