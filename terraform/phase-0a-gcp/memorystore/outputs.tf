# Cloud Memorystore outputs.
#
# Consumed by: Cloud Run REDIS_URL env var, operator runbook in
# docs/superpowers/specs/2026-05-25-redis-jti-replay-cache-design.md §7.

output "redis_jti_replay_host" {
  description = "Private IP of the Memorystore instance. Used to construct REDIS_URL."
  value       = google_redis_instance.jti_replay_cache.host
}

output "redis_jti_replay_port" {
  description = "Redis port (6379 plaintext, 6380 for TLS when transit_encryption_mode = SERVER_AUTHENTICATION)."
  value       = google_redis_instance.jti_replay_cache.port
}

output "redis_jti_replay_connection_name" {
  description = "Memorystore instance name for operator reference."
  value       = google_redis_instance.jti_replay_cache.name
}

output "redis_url_tls" {
  description = "Pre-formatted REDIS_URL for TLS connections (rediss://). Set this as Cloud Run env var."
  value       = "rediss://${google_redis_instance.jti_replay_cache.host}:6380/0"
  sensitive   = false  # No password — auth disabled, VPC-isolated.
}

output "redis_url_plaintext" {
  description = "Pre-formatted REDIS_URL for plaintext connections (redis://). Use for dev/test only."
  value       = "redis://${google_redis_instance.jti_replay_cache.host}:6379/0"
  sensitive   = false
}

output "redis_memory_size_gb" {
  description = "Provisioned memory size in GB (echoed for documentation/audit)."
  value       = var.memory_size_gb
}

output "redis_tier" {
  description = "Memorystore tier (echoed for documentation/audit)."
  value       = var.tier
}

output "redis_region" {
  description = "Memorystore region."
  value       = var.region
}
