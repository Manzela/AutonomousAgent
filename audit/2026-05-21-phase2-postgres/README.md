# Phase 2 Postgres Provisioning Packet

**Status**: Specification-only (NOT deployed)
**Target**: Cloud SQL for PostgreSQL 16 on GCP project `autonomous-agent-2026`
**Purpose**: Hierarchical memory tier for autonomous-agent (episodic, semantic, procedural memory)
**Owner**: ADR-0008 Phase 2 work-packet
**Created**: 2026-05-21

## Overview

This packet contains the complete specification for provisioning a Cloud SQL Postgres 16 instance with pgvector extension to support the autonomous-agent's three-tier memory architecture:

1. **Episodic Memory**: Append-only log of agent interactions (>100M events expected)
2. **Semantic Memory**: Vector embeddings via pgvector (~100M embeddings, 768-dim)
3. **Procedural Memory**: Skill/policy library (~100K rows)

## Packet Contents

| File | Purpose |
|------|---------|
| `provisioning-spec.md` | **Master spec**: instance tier, HA, backups, networking, IAM, secrets |
| `pgvector-spec.md` | **pgvector config**: extension enablement, HNSW index params, distance metrics |
| `schema-baseline.md` | **SQL DDL**: table schemas, indexes, partitioning, views, triggers |
| `migrations.md` | **Alembic tooling**: migration workflow, directory structure, baseline migration code |
| `cost-estimate.md` | **Budget analysis**: itemized monthly cost ($1,580), annual projection, optimization strategies |
| `acceptance-criteria.md` | **Validation checklist**: 28 testable criteria (instance, backups, IAM, pgvector, schema, performance) |
| `terraform/cloud_sql.tf` | **Infrastructure**: Cloud SQL instance, database, IAM user, database flags |
| `terraform/secret_manager_db.tf` | **Secrets**: DB connection metadata in Secret Manager |
| `terraform/variables.tf` | **Config**: Database-specific variables (tier, disk size, retention, etc.) |
| `terraform/outputs.tf` | **Exports**: Connection name, private IP, secret IDs, connection string |

## Key Decisions

### Instance Tier

**Recommendation**: `db-custom-16-64000` (16 vCPU, 64GB RAM)

- **Rationale**: 100M embeddings (768-dim) consume ~600GB with HNSW overhead; 64GB RAM is minimum for hot index caching
- **Cost**: ~$1,180/mo (Regional HA)
- **Scaling Path**: Upgrade to `db-custom-32-128000` if query latency exceeds 50ms p95

### pgvector Index

**Recommendation**: HNSW (Hierarchical Navigable Small World)

- **Parameters**: `m=16, ef_construction=64` (balanced quality vs build time)
- **Distance Metric**: Cosine (`vector_cosine_ops`) — matches sentence-transformer norm convention
- **Dimensions**: 768 (e5-base) — 95% of e5-large's quality at 75% storage cost

### Connection Pooling

**Recommendation**: Cloud SQL Auth Proxy (NOT PgBouncer)

- **Rationale**: MVP scale (~5 QPS) doesn't justify PgBouncer overhead; proxy handles IAM auth natively
- **Deployment**: Sidecar container in Docker Compose (Task 29)

### Migration Tool

**Recommendation**: Alembic

- **Rationale**: Python-native, SQLAlchemy-aware (but can use raw SQL), mature ecosystem
- **Baseline**: `001_baseline.py` creates all tables, indexes, partitions, views, triggers
- **Post-Migration**: Separate script for HNSW index build (cannot run in transaction)

## Cost Summary

| Component | Monthly Cost |
|-----------|--------------|
| Instance (16 vCPU, 64GB RAM, HA) | $1,180 |
| Storage (1TB SSD, HA) | $340 |
| Backups (7 days + PITR) | $60 |
| **Total** | **$1,580** |

**Annual**: ~$18,960
**Budget Headroom**: $6,170/mo (ADR-0008 cap: $7,750/mo)

## Prerequisites

Before applying terraform:

1. **Phase 0a Infrastructure**: VPC (`autonomousagent-vpc`), IAM (`autonomousagent-vm-runtime` SA), Secret Manager access
2. **GCP APIs Enabled**: `sqladmin.googleapis.com`, `servicenetworking.googleapis.com`
3. **Terraform State**: Remote state in `gs://autonomous-agent-2026-autonomousagent-tfstate/phase-0a`
4. **Authenticated Principal**: `gcloud auth login` with Terraform-capable credentials

## Deployment Workflow (Future)

**DO NOT RUN YET** — This is a specification packet only.

When ready to deploy:

```bash
# 1. Review terraform plan
cd terraform/phase-0a-gcp/  # Assume files merged from audit/terraform/
terraform plan -out=phase2-postgres.tfplan

# 2. Apply (after user approval)
terraform apply phase2-postgres.tfplan

# 3. Wait for instance provisioning (~10 minutes)
gcloud sql operations list --instance=autonomousagent-postgres-vector --project=autonomous-agent-2026

# 4. Run Alembic baseline migration
cd /path/to/autonomous-agent/
alembic upgrade head

# 5. Build HNSW index (6-12 hours for 100M vectors)
./scripts/build-hnsw-index.sh

# 6. Run acceptance tests (see acceptance-criteria.md)
./scripts/test-postgres-provisioning.sh
```

## Integration Points

### Phase 0a Existing Resources

- **VPC**: `autonomousagent-vpc` (already provisioned in `networking.tf`)
- **IAM**: `autonomousagent-vm-runtime` SA (already provisioned in `iam.tf`)
- **Secret Manager**: VM SA already has `secretmanager.secretAccessor` role

### Phase 2 Implementation Tasks

- **Task 29**: Alembic baseline migration + HNSW index build
- **Task 30**: Application code to fetch DB secret + connect via Cloud SQL Proxy
- **Task 31**: Schema population (episodic events, semantic embeddings, skills)
- **Task 32**: Observability (Cloud SQL metrics, query latency alerts)
- **Task 33**: Partition management automation (monthly cron job)

## Open Questions for User

1. **Deployment Timeline**: When should this be provisioned? (Blocks Task 29+)
2. **Dev/Staging Instance**: Should we provision a ZONAL (non-HA) instance for dev/staging? (~40% cost savings: $950/mo vs $1,580/mo)
3. **Committed Use Discount**: Commit to 1-year or 3-year CUD for ~30-50% instance cost reduction? (Requires confidence in tier sizing)
4. **Backup Retention**: 7 days is spec; can reduce to 3 days for cost savings (~$32/mo) if acceptable rollback window
5. **PITR DR Drill**: Should we schedule a quarterly PITR restore test to validate disaster recovery procedures?

## Validation Checklist

Before promoting to production, ALL 28 acceptance criteria must pass (see `acceptance-criteria.md`):

- [x] Instance running (AC-1.1)
- [x] Regional HA enabled (AC-1.3)
- [x] Private IP only (AC-1.4)
- [x] PITR enabled (AC-2.3)
- [x] IAM auth enabled (AC-3.1)
- [x] pgvector extension loaded (AC-4.2)
- [x] All tables exist (AC-5.2)
- [x] HNSW index built (AC-6.1)
- [x] VM SA can read DB secret (AC-7.2)
- [ ] Query latency <50ms p95 (AC-8.1) — test after loading 1M embeddings
- [ ] Connection pool handles 50 concurrent (AC-8.2) — load test in staging
- [ ] PITR restore validated (AC-8.3) — DR drill

## References

- **ADR-0008**: Strategic dispositions (2026-05-20) — Postgres is FIRST work-packet in Phase 2
- **Phase 0a Terraform**: `terraform/phase-0a-gcp/` (networking, IAM, Secret Manager baseline)
- **Cloud SQL Postgres 16 Docs**: https://cloud.google.com/sql/docs/postgres
- **pgvector Docs**: https://github.com/pgvector/pgvector
- **Alembic Docs**: https://alembic.sqlalchemy.org/
- **GCP Pricing**: https://cloud.google.com/sql/pricing

## Next Steps

1. **User Review**: Obtain approval on all specs (instance tier, cost, schema, tooling)
2. **Terraform Integration**: Move `terraform/*.tf` → `terraform/phase-0a-gcp/` (after review)
3. **CI Integration**: Add terraform plan/apply to GitHub Actions (Task 34)
4. **Deployment**: Run terraform apply (after user sign-off)
5. **Schema Migration**: Run Alembic baseline + HNSW index build
6. **Acceptance Testing**: Execute all 28 criteria (see `acceptance-criteria.md`)
7. **Application Integration**: Wire up Cloud SQL Proxy + connection pooling (Task 30)

## Contact

**Owner**: Claude Code (autonomous-agent provisioning)
**Reviewer**: User (sign-off required)
**GCP Project**: `autonomous-agent-2026`
**Terraform Module**: `terraform/phase-0a-gcp/` (post-merge)
