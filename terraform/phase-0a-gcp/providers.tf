terraform {
  required_version = ">= 1.7.0"
  required_providers {
    google      = { source = "hashicorp/google",      version = "~> 5.30" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 5.30" }
  }
  # Backend bucket name is literal: Terraform forbids variable interpolation in backend blocks.
  backend "gcs" {
    bucket = "i-for-ai-autonomousagent-tfstate"
    prefix = "phase-0a"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}
