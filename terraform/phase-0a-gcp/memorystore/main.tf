# Cloud Memorystore (Redis) — distributed jti replay cache for A2A auth.
#
# Design spec: docs/superpowers/specs/2026-05-25-redis-jti-replay-cache-design.md §4
#
# This instance provides the distributed SET NX EX 600 replay-detection
# primitive consumed by lib/a2a/auth.py. The L1 in-process cache (60s TTL)
# serves as fallback when Memorystore is unreachable.
#
# Capacity: 1GB handles ~3M entries at 160 bytes each (5K/sec burst × 600s TTL).
# Cost: ~$150/mo for STANDARD_HA in us-central1.

# Data source: look up the existing VPC by name.
data "google_compute_network" "vpc" {
  name    = var.vpc_network_name
  project = var.project_id
}

# ─────────────────────────────────────────────────────────────────────
# Memorystore Redis instance.
# ─────────────────────────────────────────────────────────────────────

resource "google_redis_instance" "jti_replay_cache" {
  name           = "autonomousagent-jti-replay"
  project        = var.project_id
  region         = var.region
  tier           = var.tier
  memory_size_gb = var.memory_size_gb
  redis_version  = var.redis_version
  display_name   = "A2A JTI replay cache"

  authorized_network = data.google_compute_network.vpc.id

  # TLS for PHI-adjacent traffic — Memorystore terminates TLS on port 6380.
  transit_encryption_mode = "SERVER_AUTHENTICATION"

  # AUTH disabled — VPC isolation + TLS is the trust boundary.
  # AUTH adds a static secret to manage with no extra security under
  # private-VPC + IAM-gated peering. Enable later via:
  #   auth_enabled = true
  # if a defense-in-depth review requires it.

  # Eviction policy: LRU. jti entries have TTL but cap memory.
  redis_configs = {
    maxmemory-policy = "allkeys-lru"
    timeout          = "0"  # never close idle client conns
  }

  # Persistence: DISABLED. jti cache is ephemeral by design — if
  # Memorystore restarts, the 60s L1 fallback covers the gap. Enabling
  # RDB/AOF would bloat backups with worthless 600s-lived keys.
  persistence_config {
    persistence_mode = "DISABLED"
  }

  maintenance_policy {
    weekly_maintenance_window {
      day = "SUNDAY"
      start_time {
        hours   = 6   # 06:00 UTC Sunday — low traffic
        minutes = 0
      }
    }
  }

  labels = {
    component = "a2a-auth"
    env       = var.env_label
    owner     = "platform"
  }

  lifecycle {
    # Don't destroy the cache without explicit intent.
    prevent_destroy = true
  }
}
