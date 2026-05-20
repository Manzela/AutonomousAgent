variable "project_id" {
  type        = string
  description = "GCP project ID (default: rx-research-autonomousagent)"
  default     = "rx-research-autonomousagent"
}

variable "billing_account" {
  type        = string
  description = "GCP billing account ID — required only on first project create"
  default     = ""
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
}

variable "vm_machine_type" {
  type    = string
  default = "e2-standard-4"
}

variable "vm_boot_disk_gb" {
  type    = number
  default = 50
}

variable "vm_data_disk_gb" {
  type    = number
  default = 100
}

variable "github_owner" {
  type    = string
  default = "Manzela"
}

variable "github_repo" {
  type    = string
  default = "AutonomousAgent"
}
