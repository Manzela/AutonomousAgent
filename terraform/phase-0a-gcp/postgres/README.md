# Postgres sub-module — Phase 2

Isolated Terraform module that provisions the Phase 2 [Cloud SQL for
PostgreSQL 16](https://cloud.google.com/sql/docs/postgres) instance + IAM
database user + connection secret + VPC peering required for private-IP
operation, plus the dependencies needed for [pgvector
HNSW](https://github.com/pgvector/pgvector#hnsw) workloads.

## Why a sub-module (not in root)

Unlike Model Armor (which is sub-moduled to isolate a `~> 6.43` provider
pin from the root's `~> 5.30`), Cloud SQL works fine on `~> 5.30`. The
sub-module exists for **operational safety**:

- **Separate state** (`gs://i-for-ai-autonomousagent-tfstate/phase-0a-postgres/`)
  → a stray `terraform destroy` at the Phase 0a root CANNOT touch the
  $1,580/mo HA Postgres instance. The instance also has
  `lifecycle.prevent_destroy = true` as a second line of defense.
- **Independent apply unit** → DB tier or backup retention can change
  without re-planning Phase 0a (VM, AR, WIF, monitoring, billing).
- **Logical phase boundary** → Phase 0a (bootstrap) ≠ Phase 2 (memory tier).

## What this provisions

1. Enables `sqladmin.googleapis.com` + `servicenetworking.googleapis.com`.
2. Allocates a `/16` VPC peering range
   (`autonomousagent-postgres-peering-range`) on root's `autonomousagent-vpc`.
3. Establishes the Service Networking connection used by Cloud SQL for
   private-IP allocation. **GAP CLOSED vs the original staging packet** —
   the staging files in `audit/2026-05-21-phase2-postgres/terraform/`
   referenced the VPC but did not declare the peering, which would have
   failed at apply time with `INVALID_ARGUMENT: no service networking
   connection`.
4. Creates the `autonomousagent-postgres-vector` Cloud SQL instance:
   - `POSTGRES_16`, REGIONAL HA in `us-central1`, `db-custom-16-64000`
     (16 vCPU / 64 GB RAM), 1 TB SSD with autoresize to 2 TB.
   - Private IP only (no public IP), IAM database auth enforced via
     `cloudsql.iam_authentication = on`.
   - Daily backups at 01:00 UTC, 7-day retention, PITR with 7-day WAL.
   - Memory + parallelism tuning for the 16 vCPU tier (see `main.tf`
     `locals.database_flags`).
5. Creates the `hermes` application database.
6. Creates the `CLOUD_IAM_SERVICE_ACCOUNT` user for the root Phase 0a
   VM runtime SA (`autonomousagent-vm-runtime`), grants project-wide
   `roles/cloudsql.client`.
7. Creates `autonomousagent-db-connection` Secret Manager secret holding
   connection metadata (JSON: host, database, user, connection_name).
   No password — IAM auth only.

## Resource count (apply plan)

| Resource type | Count |
|---|---|
| `google_project_service` | 2 |
| `google_compute_global_address` | 1 |
| `google_service_networking_connection` | 1 |
| `google_sql_database_instance` | 1 |
| `google_sql_database` | 1 |
| `google_sql_user` | 1 |
| `google_project_iam_member` | 1 |
| `google_secret_manager_secret` | 1 |
| `google_secret_manager_secret_version` | 1 |
| `google_secret_manager_secret_iam_member` | 1 |
| **Total** | **11** |

## Cost envelope

~$1,580/mo (instance $1,180 + storage $340 + backups + PITR $60) per
`audit/2026-05-21-phase2-postgres/cost-estimate.md`. Budget headroom:
$6,170/mo under the ADR-0008 cap of $7,750/mo. Cloud SQL billing starts
the moment the instance reaches RUNNABLE — prorated for partial month.

## Pre-flight before plan / apply

Root Phase 0a must already be applied (this sub-module looks up the VPC
and VM runtime SA via data sources):

```bash
cd terraform/phase-0a-gcp
terraform plan   # should be no-change if Phase 0a is up-to-date
```

The sub-module's `data "google_compute_network" "vpc"` will fail at plan
time with `googleapi: Error 404: The resource ... was not found` if root
has not been applied.

## Apply procedure (operator)

```bash
cd terraform/phase-0a-gcp/postgres
terraform init
terraform plan -out=tfplan
# Review the plan: must NOT touch any existing phase-0a resources.
# Expected: 11 to add, 0 to change, 0 to destroy.
terraform apply tfplan
```

Instance provisioning takes ~10 minutes. First daily backup runs at 01:00
UTC the day after apply.

## Verification (post-apply)

See `audit/2026-05-21-phase2-postgres/acceptance-criteria.md` for the full
28-criterion checklist. Minimal smoke check:

```bash
# Instance is RUNNABLE + private IP only.
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format='value(state,ipAddresses[].type,settings.ipConfiguration.ipv4Enabled)'
# Expected: RUNNABLE  PRIVATE  False

# IAM auth flag is on.
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format='value(settings.databaseFlags)' | grep iam_authentication
# Expected: name='cloudsql.iam_authentication';value='on'

# Connection secret exists + VM runtime SA has access.
gcloud secrets describe autonomousagent-db-connection --project=i-for-ai
gcloud secrets get-iam-policy autonomousagent-db-connection --project=i-for-ai
```

## Post-apply tasks (NOT in this module)

- **Task #29:** Alembic baseline migration — `CREATE EXTENSION vector;` +
  schema DDL + HNSW index build (`m=16, ef_construction=64`). See
  `audit/2026-05-21-phase2-postgres/{migrations,pgvector-spec,schema-baseline}.md`.
- **Task #30:** Application runtime — Cloud SQL Auth Proxy sidecar in
  Docker Compose, connection pool wiring.
- **Task #31-33:** Schema population, observability dashboards, partition
  automation.

## Rollback

The `lifecycle.prevent_destroy = true` on the Cloud SQL instance will
block `terraform destroy` until the lifecycle is manually relaxed. This is
intentional — destroying a Phase 2 database loses ALL hierarchical memory.

To intentionally tear down (e.g. a staging instance), edit `main.tf` to
remove `prevent_destroy`, re-apply (no-op state change), then:

```bash
terraform destroy
```

Order of destruction matters: Service Networking connection cannot be
destroyed while a Cloud SQL instance with private IP exists on the same
VPC, so terraform will remove the instance first automatically.

## Open questions for operator

Tracked in `audit/2026-05-21-phase2-postgres/PACKET-SUMMARY.md` §Open Questions:

1. **Deployment timing** — when to apply.
2. **Dev/staging instance** — provision ZONAL non-HA (~$950/mo) alongside prod?
3. **Committed Use Discount** — 1-year or 3-year CUD for 30-50% instance discount?
4. **Backup retention reduction** — 7 → 3 days saves ~$32/mo.
5. **PITR DR drill cadence** — quarterly restore test?
