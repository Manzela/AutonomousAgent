# Phase 2 — Database-specific variables
#
# Extends phase-0a variables (project_id, region) with Cloud SQL-specific
# configuration. These defaults match the provisioning spec recommendations.

variable "db_instance_tier" {
  type        = string
  description = "Cloud SQL instance tier. Default: db-custom-16-64000 (16 vCPU, 64GB RAM) for 100M vector workload."
  default     = "db-custom-16-64000"
}

variable "db_disk_size_gb" {
  type        = number
  description = "Provisioned SSD size in GB. Default: 1000GB (1TB) for 100M vectors + HNSW index + headroom."
  default     = 1000
}

variable "db_backup_retention_days" {
  type        = number
  description = "Number of daily backups to retain. Must be between 1 and 365. Default: 7."
  default     = 7
  validation {
    condition     = var.db_backup_retention_days >= 1 && var.db_backup_retention_days <= 365
    error_message = "Backup retention must be between 1 and 365 days."
  }
}

variable "db_pitr_retention_days" {
  type        = number
  description = "Point-in-time recovery transaction log retention (days). Must be between 1 and 7. Default: 7."
  default     = 7
  validation {
    condition     = var.db_pitr_retention_days >= 1 && var.db_pitr_retention_days <= 7
    error_message = "PITR retention must be between 1 and 7 days."
  }
}

variable "db_max_connections" {
  type        = number
  description = "Maximum concurrent database connections. Default: 200 (MVP scale)."
  default     = 200
}

variable "db_maintenance_day" {
  type        = number
  description = "Day of week for maintenance window (1=Monday, 7=Sunday). Default: 7 (Sunday)."
  default     = 7
  validation {
    condition     = var.db_maintenance_day >= 1 && var.db_maintenance_day <= 7
    error_message = "Maintenance day must be between 1 (Monday) and 7 (Sunday)."
  }
}

variable "db_maintenance_hour" {
  type        = number
  description = "Hour of day for maintenance window (0-23 UTC). Default: 2 (02:00 UTC)."
  default     = 2
  validation {
    condition     = var.db_maintenance_hour >= 0 && var.db_maintenance_hour <= 23
    error_message = "Maintenance hour must be between 0 and 23."
  }
}
