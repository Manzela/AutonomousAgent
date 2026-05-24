variable "project_id" {
  type        = string
  description = "GCP project ID (default: autonomous-agent-2026 — dedicated project, billing wired)"
  default     = "autonomous-agent-2026"
}

variable "billing_account" {
  type        = string
  description = "GCP billing account ID — required only on first project create"
  sensitive   = true
  default     = ""
}

variable "region" {
  type        = string
  description = "GCP region for regional resources (VPC subnet, GCS buckets)."
  default     = "us-central1"
}

variable "zone" {
  type        = string
  description = "GCP zone for zonal resources (GCE VM, persistent disks)."
  default     = "us-central1-a"
}

variable "vm_machine_type" {
  type        = string
  description = "GCE machine type for the always-online VM."
  default     = "e2-standard-4"
}

variable "vm_boot_disk_gb" {
  type        = number
  description = "Boot disk size in GB for the GCE VM."
  default     = 50
}

variable "vm_data_disk_gb" {
  type        = number
  description = "Data disk size in GB for the persistent docker-data volume."
  default     = 100
}

variable "github_owner" {
  type    = string
  default = "Manzela"
}

variable "github_repo" {
  type    = string
  default = "AutonomousAgent"
}
