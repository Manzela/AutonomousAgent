# Cost Estimate — Cloud SQL Postgres 16 (Phase 2)

**Instance**: `db-custom-16-64000` (16 vCPU, 64GB RAM)
**Region**: us-central1 (Iowa)
**Availability**: REGIONAL (cross-zone HA)
**Pricing Source**: GCP Cloud SQL Pricing (as of 2026-05-21)

## 1. Monthly Cost Breakdown

| Component | Specification | Unit Price | Quantity | Monthly Cost |
|-----------|---------------|------------|----------|--------------|
| **Instance (HA)** | 16 vCPU, 64GB RAM | $0.0816/vCPU-hr + $0.0109/GB-hr | 730 hrs/mo | $1,180 |
| **Storage (HA)** | 1,000 GB SSD (Regional) | $0.34/GB/mo (HA = 2x) | 1,000 GB | $340 |
| **Backups** | 7 daily backups (~700GB avg) | $0.08/GB/mo | 700 GB | $56 |
| **PITR (Logs)** | 7-day transaction logs (~50GB) | $0.08/GB/mo | 50 GB | $4 |
| **Network Egress** | Negligible (VPC internal only) | $0/GB | 0 GB | $0 |
| **Total** | | | | **$1,580/mo** |

### Calculation Details

#### Instance Cost (Regional HA)

```
vCPU cost = 16 vCPU × $0.0816/vCPU-hr × 730 hrs/mo = $952/mo
RAM cost  = 64 GB × $0.0109/GB-hr × 730 hrs/mo = $228/mo
Instance total = $952 + $228 = $1,180/mo
```

**Note**: Regional HA incurs **2x instance cost** (primary + standby). Single-zone (ZONAL) would be ~$590/mo, but sacrifices cross-zone failover.

#### Storage Cost (Regional HA)

```
SSD storage (HA) = 1,000 GB × $0.34/GB/mo = $340/mo
```

**Note**: Regional HA storage is 2x standard SSD pricing ($0.17/GB/mo × 2 = $0.34/GB/mo). ZONAL would be ~$170/mo.

#### Backup Cost

```
Backup size = 1,000 GB (full database) × 0.7 (compression ratio) = 700 GB
Backup cost = 700 GB × $0.08/GB/mo = $56/mo
```

**Note**: Cloud SQL applies automatic compression (~30% reduction). Backups are incremental after first full backup, so costs may decrease after initial week.

#### PITR (Transaction Logs)

```
Log size = ~50 GB (7 days of WAL files at <1 QPS write)
PITR cost = 50 GB × $0.08/GB/mo = $4/mo
```

**Note**: PITR log retention is capped at 7 days (GCP enforced). If write rate increases to 10 QPS, expect ~100GB logs → ~$8/mo.

## 2. Annual Cost Projection

| Year | Instance | Storage | Backups | Total/mo | Total/yr |
|------|----------|---------|---------|----------|----------|
| **Year 1** | $1,180 | $340 | $60 | $1,580 | $18,960 |
| Year 2 (2x growth) | $1,180 | $680 | $120 | $1,980 | $23,760 |
| Year 3 (5x growth) | $2,360 | $1,700 | $300 | $4,360 | $52,320 |

**Assumptions**:
- Year 1: 100M embeddings, 10M events/mo
- Year 2: 200M embeddings, 20M events/mo (storage autoresize to 2TB)
- Year 3: 500M embeddings, 50M events/mo (instance upgrade to `db-custom-32-128000`)

## 3. Cost Optimization Strategies

### 3.1. Use ZONAL for Dev/Staging

**Savings**: ~40% reduction ($1,580/mo → $950/mo)

```hcl
# terraform/staging/cloud_sql.tf
resource "google_sql_database_instance" "postgres_vector_staging" {
  settings {
    availability_type = "ZONAL"  # Single zone (no standby)
    tier              = "db-custom-8-32000"  # Half the vCPU/RAM
    disk_size         = 500  # Half the storage
  }
}
```

**Trade-off**: No cross-zone failover; acceptable for non-production.

### 3.2. Enable Disk Autoresize

Already enabled in terraform (`disk_autoresize = true`). Cloud SQL automatically grows storage when utilization exceeds 70%, capped at `disk_autoresize_limit = 2000` (2TB).

**Savings**: Avoid over-provisioning; pay only for used storage.

### 3.3. Optimize Backup Retention

```hcl
# Reduce backup retention to 3 days (compliance minimum)
backup_retention_settings {
  retained_backups = 3  # Down from 7
  retention_unit   = "COUNT"
}
```

**Savings**: ~$32/mo (~57% backup cost reduction)

**Trade-off**: Shorter rollback window (3 days vs 7 days).

### 3.4. Defer PITR for Non-Production

```hcl
# Disable PITR for dev/staging
point_in_time_recovery_enabled = false
```

**Savings**: ~$4/mo (negligible, but reduces log storage)

### 3.5. Right-Size Instance Tier

If query latency remains <20ms at 5 QPS with `db-custom-8-32000` (8 vCPU, 32GB RAM), downgrade:

**Savings**: ~$590/mo (~50% instance cost reduction)

**Risk**: Insufficient RAM for 100M HNSW index → query latency degradation.

## 4. Cost Comparison: Postgres vs Alternatives

| Option | Monthly Cost | Pros | Cons | Verdict |
|--------|--------------|------|------|---------|
| **Cloud SQL Postgres** | $1,580 | Managed backups, HA, PITR, pgvector native | High cost | **CHOSEN** (best fit for HA + vectors) |
| GKE + Self-Managed Postgres | $800 | Lower cost, full control | Operational burden, no managed backups | Rejected (team size constraint) |
| Supabase (managed pgvector) | $1,200 | Simplified ops, vector-first | Vendor lock-in, less GCP integration | Rejected (prefer GCP-native) |
| AlloyDB | $2,200 | Better performance at scale | 40% more expensive | Deferred to Year 3 (if perf bottleneck) |

## 5. Budget Impact Analysis

### Existing Phase 0a Monthly Spend

| Component | Cost |
|-----------|------|
| GCE VM (e2-standard-4) | $120 |
| Artifact Registry | $5 |
| Secret Manager | $1 |
| Cloud Logging | $10 |
| Cloud Monitoring | $5 |
| VPC + NAT | $40 |
| **Subtotal** | **$181/mo** |

### Phase 2 Total Monthly Spend

```
Phase 0a: $181/mo
Phase 2 DB: $1,580/mo
Total: $1,761/mo (~$21,132/yr)
```

**Budget Threshold**: ADR-0008 authorized up to $7,750/mo (~$93,000/yr) for Phase 2.
**Headroom**: $6,169/mo (~$74,028/yr) — sufficient for future scaling.

## 6. Trigger Points for Cost Review

| Metric | Current | Trigger | Action |
|--------|---------|---------|--------|
| Storage Usage | 600 GB | >1,400 GB (70% of 2TB cap) | Evaluate partition archival to GCS |
| Query Latency | 5ms p50 | >50ms p95 | Upgrade to `db-custom-32-128000` |
| Write QPS | <1 QPS | >5 QPS | Evaluate PgBouncer or read replicas |
| Monthly Cost | $1,580 | >$2,500 | Audit unused indexes, optimize queries |

## 7. Cost Attribution (Internal Chargeback)

If cost attribution is required (e.g., multi-tenant billing):

```
Episodic memory: ~10GB storage → ~$3/mo (0.2%)
Semantic memory: ~600GB storage → ~$200/mo (12.7%)
Instance overhead: ~$1,377/mo (87.1%)
```

**Note**: Instance overhead (vCPU + RAM) is not proportional to tier usage; semantic memory drives storage cost, but instance cost is fixed.

## 8. Cost Forecasting (Linear Growth Model)

Assuming 10M new events/mo + 10M new embeddings/mo:

| Month | Events (Total) | Embeddings (Total) | Storage (GB) | Monthly Cost |
|-------|----------------|-------------------|--------------|--------------|
| 1 | 10M | 10M | 60 GB | $1,600 |
| 6 | 60M | 60M | 360 GB | $1,650 |
| 12 | 120M | 120M | 720 GB | $1,720 |
| 18 | 180M | 180M | 1,080 GB | $1,800 |
| 24 | 240M | 240M | 1,440 GB | $1,880 |

**Inflection Point**: Month 18 (1TB storage utilization) — consider partition archival or instance upgrade.

## 9. Cost Savings Opportunities (Future)

### 9.1. Committed Use Discounts (CUD)

1-year commitment: **~30% discount** on instance cost.
3-year commitment: **~50% discount** on instance cost.

**Savings (3-year CUD)**:
```
Instance cost: $1,180/mo × 50% = $590/mo saved
Annual savings: $590 × 12 = $7,080/yr
```

**Risk**: Locked into instance tier for 3 years; no flexibility to downsize.

### 9.2. Coldline Storage for Partition Archival

Archive episodic events older than 12 months to GCS Coldline:

```
100M events (old) → 100GB → GCS Coldline $0.004/GB/mo = $0.40/mo
Cloud SQL savings: 100GB × $0.34/GB/mo = $34/mo saved
Net savings: $33.60/mo (~$400/yr)
```

**Implementation**: Quarterly cron job exports old partitions to GCS, then drops partition.

### 9.3. Serverless Export for Backups

Use `gcloud sql export` to GCS instead of Cloud SQL managed backups:

**Savings**: ~$56/mo (eliminate Cloud SQL backup storage)

**Trade-off**: Manual restore process; PITR unavailable.

## 10. Budget Alert Configuration

```hcl
# terraform/monitoring.tf (extend existing budget from Task 27)

resource "google_billing_budget" "cloud_sql" {
  billing_account = var.billing_account
  display_name    = "Cloud SQL Monthly Budget"

  budget_filter {
    services = ["services/9662-B51E-5089"]  # Cloud SQL service ID
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = "2000"  # Alert at $2,000/mo (27% above baseline)
    }
  }

  threshold_rules {
    threshold_percent = 0.8  # 80% of $2,000 = $1,600 (near baseline)
  }
  threshold_rules {
    threshold_percent = 1.0  # 100% of $2,000 = $2,000 (overage)
  }
  threshold_rules {
    threshold_percent = 1.2  # 120% of $2,000 = $2,400 (runaway alert)
  }
}
```

## 11. Cost Summary (TL;DR)

| Metric | Value |
|--------|-------|
| **Monthly Baseline** | $1,580 |
| **Annual (Year 1)** | $18,960 |
| **Annualized w/ 3-Year CUD** | $11,880 (~37% savings) |
| **Max Budget Headroom** | $6,170/mo (ADR-0008 cap) |
| **Cost per 1M Embeddings** | ~$6.40/mo (storage-dominated) |
| **Cost per 1M Events** | ~$0.34/mo (negligible vs embeddings) |

**Recommendation**: Proceed with baseline config. Evaluate 3-year CUD after 6 months of stable operation.
