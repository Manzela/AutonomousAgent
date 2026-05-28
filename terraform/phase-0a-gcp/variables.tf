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

# P2-15: Slack / webhook URL for incident alert fan-out.
# Set via TF_VAR_slack_alert_webhook_url or tfvars. When empty, only the
# email channel is used (safe default — no alerts lost).
variable "slack_alert_webhook_url" {
  type        = string
  description = "Incoming webhook URL for Slack/PagerDuty incident alerts. Set to '' to disable."
  default     = ""
  sensitive   = true
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

variable "ar_repo" {
  type        = string
  description = "Artifact Registry repo path (without trailing slash or image name). Set via TF_VAR_ar_repo or tfvars."
  default     = "us-central1-docker.pkg.dev/autonomous-agent-2026/autonomousagent-images"
}

variable "image_tag" {
  type        = string
  description = "Immutable image tag (git SHA prefix) to deploy. Must be supplied at apply time by CI/CD — no default to prevent accidental :latest deploy."
  default     = ""

  validation {
    condition     = length(var.image_tag) > 0
    error_message = "image_tag must be set (e.g. sha-abc123def456). Never deploy with an empty tag."
  }
}
