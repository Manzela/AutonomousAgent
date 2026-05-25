# Memorystore Redis variables.
# Design spec: docs/superpowers/specs/2026-05-25-redis-jti-replay-cache-design.md §4

variable "project_id" {
  description = "GCP project ID."
  type        = string
  default     = "autonomous-agent-2026"
}

variable "region" {
  description = "GCP region for the Memorystore instance."
  type        = string
  default     = "us-central1"
}

variable "env_label" {
  description = "Environment label (prod, staging, dev)."
  type        = string
  default     = "prod"
}

variable "memory_size_gb" {
  description = "Memorystore memory size in GB. 1GB handles ~3M jti entries under burst (5K/sec × 600s TTL)."
  type        = number
  default     = 1

  validation {
    condition     = var.memory_size_gb >= 1 && var.memory_size_gb <= 300
    error_message = "memory_size_gb must be between 1 and 300."
  }
}

variable "redis_version" {
  description = "Redis version. REDIS_7_0 is the latest supported by Memorystore."
  type        = string
  default     = "REDIS_7_0"
}

variable "tier" {
  description = "Memorystore tier. STANDARD_HA = primary + replica with automatic failover."
  type        = string
  default     = "STANDARD_HA"

  validation {
    condition     = contains(["BASIC", "STANDARD_HA"], var.tier)
    error_message = "tier must be BASIC or STANDARD_HA."
  }
}

variable "vpc_network_name" {
  description = "VPC network name (without self_link prefix). Must match the VPC used by Cloud Run / GCE."
  type        = string
  default     = "autonomousagent-vpc"
}
