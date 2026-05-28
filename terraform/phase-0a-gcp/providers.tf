terraform {
  required_version = ">= 1.7.0"
  required_providers {
    google      = { source = "hashicorp/google", version = "~> 5.30" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 5.30" }
    github      = { source = "integrations/github", version = "~> 6.0" }
  }
  # Backend bucket name is literal: Terraform forbids variable interpolation in backend blocks.
  backend "gcs" {
    bucket = "autonomous-agent-2026-tfstate"
    prefix = "phase-0a"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
  # Required for billingbudgets.googleapis.com with local ADC: sets the quota
  # project so the billing API uses the correct project instead of the ADC default.
  billing_project       = var.project_id
  user_project_override = true
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  zone                  = var.zone
  billing_project       = var.project_id
  user_project_override = true
}
