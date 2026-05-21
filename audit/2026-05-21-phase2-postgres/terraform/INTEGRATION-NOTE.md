# Terraform Integration Note

**Status**: Staging area (NOT yet merged into phase-0a-gcp/)
**Destination**: `terraform/phase-0a-gcp/` (after user approval)

## Pre-Integration Checklist

Before moving these files into `terraform/phase-0a-gcp/`:

1. **API Enablement**: Add Cloud SQL APIs to `project.tf`:
   ```hcl
   # In terraform/phase-0a-gcp/project.tf, add to local.required_apis:
   "sqladmin.googleapis.com",
   "servicenetworking.googleapis.com",
   ```

2. **Variable Inheritance**: These files reference existing variables from `variables.tf`:
   - `var.project_id` (default: "i-for-ai")
   - `var.region` (default: "us-central1")

   New variables defined in `terraform/variables.tf`:
   - `var.db_instance_tier` (default: "db-custom-16-64000")
   - `var.db_disk_size_gb` (default: 1000)
   - `var.db_backup_retention_days` (default: 7)
   - `var.db_pitr_retention_days` (default: 7)
   - `var.db_max_connections` (default: 200)
   - `var.db_maintenance_day` (default: 7)
   - `var.db_maintenance_hour` (default: 2)

3. **Resource Dependencies**: These files depend on existing resources:
   - `google_project_service.enabled` (from `project.tf`)
   - `google_service_account.vm_runtime` (from `iam.tf`)
   - `google_compute_network.autonomousagent` (from `networking.tf`)

   All references are correct (verified 2026-05-21).

4. **Outputs**: These files export new outputs (see `outputs.tf`):
   - `db_instance_connection_name` — for Cloud SQL Proxy
   - `db_private_ip_address` — for VPC-internal connections
   - `db_secret_id` — for application runtime
   - etc.

## Integration Procedure

```bash
# 1. Copy terraform files to phase-0a-gcp/
cp terraform/*.tf /path/to/terraform/phase-0a-gcp/

# 2. Update project.tf to enable Cloud SQL APIs
# (Manual edit — add sqladmin.googleapis.com and servicenetworking.googleapis.com)

# 3. Initialize terraform (detect new resources)
cd /path/to/terraform/phase-0a-gcp/
terraform init

# 4. Review plan
terraform plan -out=phase2-postgres.tfplan

# 5. Apply (after user approval)
terraform apply phase2-postgres.tfplan
```

## Terraform State

- **Backend**: `gs://i-for-ai-autonomousagent-tfstate/phase-0a`
- **New Resources**: 5 (instance, database, IAM user, secret, secret version)
- **Modified Resources**: 2 (project IAM member for cloudsql.client, secret IAM member)

## Rollback Plan

If provisioning fails:

```bash
# 1. Destroy Cloud SQL resources (preserves VPC/IAM)
terraform destroy -target=google_sql_database_instance.postgres_vector
terraform destroy -target=google_sql_database.hermes
terraform destroy -target=google_sql_user.vm_runtime
terraform destroy -target=google_secret_manager_secret_version.db_connection
terraform destroy -target=google_secret_manager_secret.db_connection

# 2. Remove terraform files
rm cloud_sql.tf secret_manager_db.tf variables.tf outputs.tf

# 3. Re-init
terraform init
```

## Cost Impact (Post-Apply)

Expect **immediate** cost impact:
- Instance provisioning: ~10 minutes
- Monthly billing: $1,580/mo (prorated for partial month)
- First backup: 01:00 UTC next day

## Next Steps After Integration

1. Run Alembic baseline migration (see `migrations.md`)
2. Build HNSW index (see `pgvector-spec.md`)
3. Execute acceptance tests (see `acceptance-criteria.md`)
4. Wire up Cloud SQL Proxy in Docker Compose (Task 30)

---

**IMPORTANT**: Do NOT merge these files into phase-0a-gcp/ without user approval. This is a STAGING area only.
