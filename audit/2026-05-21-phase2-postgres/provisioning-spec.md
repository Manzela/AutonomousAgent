# Phase 2 Postgres Provisioning Specification

**Target**: Cloud SQL for PostgreSQL 16 on GCP project `autonomous-agent-2026`
**Purpose**: Hierarchical memory tier for autonomous-agent (episodic, semantic, procedural memory)
**Status**: Specification-only (NO deployment)

## 1. Instance Tier

**Recommendation: `db-custom-16-64000` (16 vCPU, 64 GB RAM)**

### Justification

- **Vector Storage**: 100M embeddings (768-dim) consume ~300GB raw storage. With HNSW indexing overhead, expect ~600GB+ total.
- **Memory Pressure**: Vector search is memory-intensive. 64GB RAM is minimum to ensure HNSW index and hot data stay cached. If query latency exceeds 100ms, scale to 128GB RAM.
- **CPU**: 16 vCPUs provide sufficient parallelism for background HNSW index builds without starving ~5 QPS read load.
- **Storage**: Provision 1TB SSD. Cloud SQL IOPS scales with provisioned size; 1TB ensures no throttling during index rebalancing.

### Scaling Path

- **MVP → Production**: If write load exceeds 5 QPS or read latency spikes, upgrade to `db-custom-32-128000` (32 vCPU, 128GB RAM).
- **Storage**: Auto-resize enabled; monitor usage at 70% threshold.

## 2. High Availability

- **Availability Type**: `REGIONAL` (cross-zone failover within us-central1)
- **Failover**: Automatic; RPO <60s, RTO <2 minutes
- **Cross-Region DR**: NOT implemented at MVP (ADR-0008 defers multi-region to Phase 3)

## 3. Backups & PITR

### Daily Backups

- **Frequency**: Daily, 01:00 UTC (low-traffic window)
- **Retention**: 7 backups (COUNT-based)
- **Location**: `us` multi-region (Cloud SQL managed)

### Point-in-Time Recovery (PITR)

- **Enabled**: Yes
- **Transaction Log Retention**: 7 days
- **Recovery Granularity**: Second-level (can restore to any point within 7-day window)

### Maintenance Window

- **Day**: Sunday
- **Time**: 02:00 UTC
- **Track**: Stable (no preview releases)

## 4. Networking

### VPC Peering

- **Network**: `autonomousagent-vpc` (already provisioned in `terraform/phase-0a-gcp/networking.tf`)
- **Subnet**: Private Service Access via VPC peering (Cloud SQL managed)
- **Private IP Only**: `ipv4_enabled = false` (no public IP)
- **Private Path for GCP Services**: Enabled (keeps traffic on GCP backbone)

### Firewall

- **Inbound**: NO firewall rules required (Cloud SQL private IP accessed directly from VM)
- **Outbound**: VM egress already allowed via `autonomousagent-allow-egress-all` (networking.tf:69-78)

## 5. Connection Pooling

**Recommendation: Cloud SQL Auth Proxy**

### Rationale

- **MVP Scale**: ~5 QPS read, <1 QPS write → PgBouncer sidecar is unnecessary overhead
- **IAM Auth Integration**: Cloud SQL Auth Proxy natively handles IAM database authentication handshake (automatic token rotation)
- **No Password Management**: Application code uses connection string with NO password; proxy handles ephemeral IAM tokens
- **Resource Efficiency**: Single proxy sidecar per container; no additional VM overhead

### Deployment Model

- **Docker Compose**: Add `cloud-sql-proxy` service alongside hermes (Task 29 implementation)
- **Connection String**: `host=/cloudsql/<instance-connection-name> dbname=hermes user=autonomousagent-vm-runtime@autonomous-agent-2026.iam`

### Future Scaling

If QPS exceeds 50, transition to PgBouncer for transaction pooling (proxy → PgBouncer → Cloud SQL).

## 6. IAM Database Authentication

### Database User

- **Type**: `CLOUD_IAM_SERVICE_ACCOUNT`
- **Name**: `autonomousagent-vm-runtime@autonomous-agent-2026.iam` (truncate `.gserviceaccount.com` suffix for SQL user)
- **Auth Mechanism**: Temporary IAM tokens (60-minute TTL, automatically refreshed by proxy)

### Database Flags

```hcl
database_flags {
  name  = "cloudsql.iam_authentication"
  value = "on"
}
```

### IAM Bindings

- **Service Account**: `autonomousagent-vm-runtime@autonomous-agent-2026.iam.gserviceaccount.com` (already exists per `iam.tf:18-24`)
- **Role**: `roles/cloudsql.client` (granted at project level, allows IAM auth to all Cloud SQL instances)

## 7. Secret Manager Integration

### Secrets Provisioned

1. **`autonomousagent-db-connection`**: JSON blob containing:
   ```json
   {
     "host": "<private-ip>",
     "database": "hermes",
     "user": "autonomousagent-vm-runtime@autonomous-agent-2026.iam",
     "connection_name": "autonomous-agent-2026:us-central1:hermes-vector-db"
   }
   ```

2. **`autonomousagent-db-migrations-user`**: (Optional) superuser credentials for Alembic migrations (IAM-based, not password)

### IAM Bindings

- **Service Account**: `autonomousagent-vm-runtime`
- **Role**: `roles/secretmanager.secretAccessor` (already granted per `iam.tf:27-42`)

### Consumption Pattern

Application runtime:
1. Fetch `autonomousagent-db-connection` from Secret Manager on startup
2. Parse JSON to extract `connection_name`
3. Connect via Cloud SQL Auth Proxy: `host=/cloudsql/<connection_name> dbname=hermes user=<user>`

## 8. Terraform Module Structure

### File Layout (Staging Area)

```
audit/2026-05-21-phase2-postgres/terraform/
├── cloud_sql.tf          # Instance, database, IAM user
├── secret_manager_db.tf  # Secret resources + IAM bindings
├── iam_db.tf             # Cloud SQL client role binding
├── variables.tf          # Database-specific variables
└── outputs.tf            # Connection name, secret IDs
```

### Integration Plan

After review:
1. Move `terraform/*.tf` → `terraform/phase-0a-gcp/` (merge into main module)
2. Import existing resources if any (unlikely; this is net-new)
3. Add `cloud_sql.tf` dependency on `networking.tf` (VPC must exist before private IP allocation)

## 9. Database Flags (Performance Tuning)

### HNSW Index Builds

```hcl
database_flags {
  name  = "maintenance_work_mem"
  value = "4194304"  # 4GB (for 100M vector index builds)
}
```

### Connection Limits

```hcl
database_flags {
  name  = "max_connections"
  value = "200"  # MVP scale; Cloud SQL default is 100
}
```

### Parallelism

```hcl
database_flags {
  name  = "max_parallel_workers"
  value = "16"  # Match vCPU count for index builds
}
```

## 10. Monitoring & Alerting

### Cloud SQL Metrics (Out of Scope for This Packet)

Defer to Task 30 (Phase 2 observability):
- `cloudsql.googleapis.com/database/cpu/utilization` (alert >80%)
- `cloudsql.googleapis.com/database/memory/utilization` (alert >85%)
- `cloudsql.googleapis.com/database/disk/utilization` (alert >70%)
- `cloudsql.googleapis.com/database/replication/replica_lag` (alert >10s)

## 11. Estimated Monthly Cost

| Component        | Specification                  | Estimated Cost (USD) |
|------------------|--------------------------------|----------------------|
| Instance (HA)    | 16 vCPU, 64GB RAM (Regional)   | ~$1,180 /mo          |
| Storage (HA)     | 1,000 GB SSD (Regional)        | ~$340 /mo            |
| Backups + PITR   | 7 days + transaction logs      | ~$50 /mo             |
| **Total**        |                                | **~$1,570 /mo**      |

### Cost Optimization Notes

- **Non-HA Development Instance**: Save ~40% by using `availability_type = ZONAL` for dev/staging
- **Storage Autoscaling**: Enable `disk_autoresize` with 70% threshold to avoid over-provisioning
- **Backup Compression**: Cloud SQL applies automatic compression; no action required

## 12. Quirks & Gotchas (Postgres 16 + pgvector on Cloud SQL)

### Extension Activation

- **Quirk**: Extensions are NOT pre-loaded on Cloud SQL. Must run `CREATE EXTENSION IF NOT EXISTS vector;` manually.
- **Mitigation**: Include in Alembic baseline migration (`migrations/versions/001_baseline.py`)

### IAM Auth Latency

- **Quirk**: IAM auth adds ~100-200ms to initial connection handshake
- **Mitigation**: Use persistent connections (SQLAlchemy connection pooling); avoid short-lived connections

### Memory Management

- **Quirk**: Postgres 16 improved `VACUUM` parallelism, but pgvector HNSW builds are still heavy
- **Mitigation**: Set `maintenance_work_mem = 4GB` (database flag) to speed up 100M-record index builds

### HNSW Index Locking

- **Quirk**: HNSW index builds acquire `ShareLock` on the table (blocks concurrent writes)
- **Mitigation**: Build indexes CONCURRENTLY: `CREATE INDEX CONCURRENTLY idx_embedding_hnsw ON semantic_embeddings USING hnsw (embedding vector_cosine_ops);`

## 13. Terraform Conventions (Inherited from Phase 0a)

### Naming Prefix

All resources prefixed `autonomousagent-*` to avoid collision with sibling workloads on `autonomous-agent-2026` project.

### Provider Configuration

```hcl
# Match phase-0a-gcp/providers.tf
terraform {
  required_version = ">= 1.7.0"
  required_providers {
    google      = { source = "hashicorp/google",      version = "~> 5.30" }
    google-beta = { source = "hashicorp/google-beta", version = "~> 5.30" }
  }
}
```

### IAM Binding Pattern

Use `google_project_iam_member` (NOT `google_project_iam_binding`) to avoid destroying pre-existing bindings.

### Labels

```hcl
labels = {
  phase     = "2"
  component = "autonomousagent"
  tier      = "memory"
}
```

### Lifecycle

```hcl
lifecycle {
  prevent_destroy = true  # Protect production database from accidental deletion
}
```

## 14. Validation Checklist (Post-Deployment)

1. **Instance Status**: `gcloud sql instances describe hermes-vector-db --project autonomous-agent-2026 --format="value(state)"` → `RUNNABLE`
2. **Private IP Allocated**: Instance has private IP, NO public IP
3. **pgvector Enabled**: `SELECT * FROM pg_available_extensions WHERE name = 'vector';` → 1 row
4. **IAM Auth Works**: Connect via proxy with IAM user (no password)
5. **Backups Scheduled**: `gcloud sql backups list --instance hermes-vector-db` → at least 1 backup
6. **PITR Enabled**: `gcloud sql instances describe hermes-vector-db --format="value(settings.backupConfiguration.pointInTimeRecoveryEnabled)"` → `True`
7. **Secret Accessible**: Application SA can read `autonomousagent-db-connection` from Secret Manager
8. **Maintenance Window**: Scheduled for Sunday 02:00 UTC (check Cloud Console)

## 15. Deferred to Phase 2 Implementation

- **Schema Migrations**: Alembic setup (Task 29)
- **Connection Pool Tuning**: PgBouncer fallback if QPS exceeds 50 (Task 30)
- **Read Replicas**: Defer to Phase 3 (ADR-0008 multi-region decision)
- **Disaster Recovery Runbook**: Cross-region restore procedures (Task 31)
