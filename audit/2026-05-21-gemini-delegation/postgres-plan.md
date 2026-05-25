# Gemini Delegation — Phase 2 Postgres terraform plan (Task #59)

**Status:** Plan stage only — **NO APPLY**. Apply gated on separate explicit operator go-ahead.
**Date:** 2026-05-21
**Delegate:** Gemini CLI v0.42.0 with `gemini-3.1-pro-preview` on `global` Vertex endpoint, project `autonomous-agent-2026`.
**Operator (Claude):** Orchestrates briefing, captures plan output, **stops at plan**. Does NOT trigger apply in this task.

---

## 1. Why this exists

Phase 2 of the AutonomousAgent hierarchical memory tier (per the architecture
research document — episodic event log, semantic vector embeddings,
procedural skill library) needs a transactional store that handles 100M-vector
HNSW workloads with multi-AZ durability. The terraform sub-module at
`terraform/phase-0a-gcp/postgres/` provisions:

1. **Cloud SQL for PostgreSQL 16** instance `autonomousagent-postgres-vector`
   in REGIONAL HA configuration (`db-custom-16-64000` = 16 vCPU / 64 GB RAM,
   1 TB PD-SSD with autoresize to 2 TB).
2. **VPC peering** (`google_compute_global_address` + `google_service_networking_connection`)
   for private-IP-only operation against root's `autonomousagent-vpc`.
3. **IAM database authentication** — no passwords; the root Phase 0a VM
   runtime SA (`autonomousagent-vm-runtime`) authenticates via Cloud SQL
   Auth Proxy + IAM token.
4. **Daily backups + PITR** — 7-day retention each, 01:00 UTC backup window.
5. **`hermes` application schema database** + `roles/cloudsql.client` IAM
   grant + a connection-metadata secret in Secret Manager.

Design source: `audit/2026-05-21-phase2-postgres/PACKET-SUMMARY.md` +
`provisioning-spec.md` + `cost-estimate.md`.

## 2. CRITICAL — gap closed vs the original staging packet

The Gemini-authored staging files at
`audit/2026-05-21-phase2-postgres/terraform/cloud_sql.tf` declared the Cloud
SQL instance with `ipv4_enabled = false` but **did not declare the Service
Networking VPC peering connection that private-IP Cloud SQL requires**.

`terraform plan` against the staging files would have succeeded; `terraform
apply` would have failed at instance creation with:

```
INVALID_ARGUMENT: The network ...autonomousagent-vpc has no service
networking connection.
```

The canonical upstream pattern (per the `google_sql_database_instance` docs +
provider examples) requires **two** resources Gemini's staging packet omitted:

| Resource | Purpose |
|---|---|
| `google_compute_global_address.private_ip_alloc` | Reserves a `/16` peering range on the VPC (`autonomousagent-postgres-peering-range`). |
| `google_service_networking_connection.default` | Establishes the actual peering tunnel that Cloud SQL allocates instance IPs from. |

The sub-module declares both. `audit/2026-05-21-phase2-postgres/terraform/INTEGRATION-NOTE.md`
has a SUPERSEDED warning pointing at the canonical location and documenting
the gap.

## 3. Why a sub-module (not a root merge)

The staging `INTEGRATION-NOTE.md` assumed a root-level merge into
`terraform/phase-0a-gcp/`. The Model Armor sub-module established a
sub-module-per-feature precedent for **operational safety**, not provider-pin
isolation:

| Property | Sub-module benefit |
|---|---|
| Separate GCS state (`prefix = "phase-0a-postgres"`) | A stray `terraform destroy` at the Phase 0a root CANNOT touch the $1,580/mo HA Postgres instance. |
| `lifecycle.prevent_destroy = true` on the SQL instance | Belt + braces — `terraform destroy` from within this sub-module also fails until lifecycle is manually relaxed. |
| Independent apply unit | Changes to DB tier / backup retention re-plan in seconds without disturbing VM, AR, WIF, monitoring, billing modules. |
| Logical phase boundary | Phase 0a = bootstrap; Phase 2 = memory tier. Sub-module mirrors the phase boundary. |
| **NOT** for provider pin reasons | Cloud SQL works fine on `~> 5.30` (root's pin). Contrast with Model Armor which needed `~> 6.43`. |

## 4. What changes in GCP (blast radius)

This **plan** stage produces no GCP-side mutations — `terraform plan` is
strictly a read of existing state + dry-run computation. The eventual apply
(separate task) would create 11 resources:

| Resource type | Count | Cost impact |
|---|---|---|
| `google_project_service` (sqladmin + servicenetworking) | 2 | $0 |
| `google_compute_global_address` (VPC peering range) | 1 | $0 |
| `google_service_networking_connection` | 1 | $0 |
| `google_sql_database_instance` (HA, 16vCPU/64GB, 1TB SSD) | 1 | **~$1,180/mo** |
| `google_sql_database` (hermes) | 1 | $0 |
| `google_sql_user` (CLOUD_IAM_SERVICE_ACCOUNT) | 1 | $0 |
| `google_project_iam_member` (cloudsql.client) | 1 | $0 |
| `google_secret_manager_secret` (connection metadata) | 1 | trivial |
| `google_secret_manager_secret_version` | 1 | trivial |
| `google_secret_manager_secret_iam_member` (secretAccessor) | 1 | $0 |
| **Total** | **11** | **~$1,580/mo** (with storage + backups + PITR) |

Cost breakdown per `audit/2026-05-21-phase2-postgres/cost-estimate.md`:
- Instance: $1,180/mo
- Storage (1TB SSD): $340/mo
- Backups + PITR: $60/mo
- Total: ~$1,580/mo
- Budget headroom: $6,170/mo under ADR-0008 cap of $7,750/mo ($250/day)

**Apply is gated separately.** This task is plan-only.

## 5. Pre-flight verification (Claude side, done)

- [x] Module files present + parseable (5 files: README.md, providers.tf, variables.tf, main.tf, outputs.tf)
- [x] Backend `gs://autonomous-agent-2026-autonomousagent-tfstate/phase-0a-postgres/` isolated from root phase-0a state
- [x] Provider pin `google ~> 5.30` (matches root; sufficient for Cloud SQL)
- [x] Billing project + user_project_override = true (matches root convention)
- [x] VPC peering resources declared (gap closure vs staging packet)
- [x] `lifecycle.prevent_destroy = true` on the SQL instance
- [x] Data sources reference root-owned resources (VPC, VM runtime SA) — fail-loud at plan if root not applied
- [x] All variables have validation blocks where ranges apply
- [x] Outputs cover application runtime needs (connection_name, private_ip, secret_id, etc.)

## 6. IAM the plan requires (Gemini-side)

Plan-only needs `roles/storage.objectViewer` on the state bucket
(`autonomous-agent-2026-autonomousagent-tfstate`) + project-level read on whatever
resources data sources look up (VPC, service account, project services).

The Gemini CLI runs under `manzela@tngshopper.com` ADC which already has
Owner on `autonomous-agent-2026`. No additional grants needed.

For the eventual apply (NOT this task): same identity needs
`roles/cloudsql.admin`, `roles/compute.networkAdmin` (for the
google_compute_global_address), `roles/secretmanager.admin`, and project IAM
write for the `roles/cloudsql.client` binding. Owner covers all of these.

## 7. Exact command sequence — PLAN ONLY

```bash
cd "/Users/danielmanzela/RX-Research Project/wt-framing-2/terraform/phase-0a-gcp/postgres"

# 7.1 Non-destructive: init pulls providers + initializes new GCS backend prefix
terraform init -input=false

# 7.2 Non-destructive: plan reads existing cloud state + writes local tfplan
terraform plan -input=false -out=tfplan -detailed-exitcode

# 7.3 DO NOT RUN — apply is a separate task with its own briefing
# terraform apply -input=false tfplan
```

`-detailed-exitcode` makes plan return 0 (no changes), 1 (error), or 2 (changes
to apply). Expected outcome on first-time plan: **exit 2, 11 to add, 0 to
change, 0 to destroy**.

## 8. Verification gates (post-plan)

Operator (Claude) reviews the captured plan output for:

- [ ] All 11 resources are `to add` (not `to change` or `to destroy`)
- [ ] No root-level Phase 0a resources appear in the plan (state isolation working)
- [ ] Data source lookups succeed (VPC + VM runtime SA found — confirms root is applied)
- [ ] `google_sql_database_instance` block shows `availability_type = "REGIONAL"`,
      `tier = "db-custom-16-64000"`, `disk_size = 1000`, `ipv4_enabled = false`,
      `cloudsql.iam_authentication = on`, backup + PITR enabled
- [ ] No spurious provider blocks or backend re-init prompts
- [ ] Exit code = 2 (changes pending, no errors)

## 9. Anti-gates — things this plan should NOT show

- [ ] No `# (forces replacement)` — there's nothing to replace yet
- [ ] No `to destroy` — destruction in a first-time plan signals state drift
- [ ] No "unauthorized" or "permission denied" — IAM is correct
- [ ] No "network ... has no service networking connection" — that would mean
      VPC peering is missing again (regression vs gap closure)
- [ ] No quota / API-not-enabled errors — APIs are owned by this module

## 10. Apply gate — DEFERRED to a separate task

Per the user's standing directive: "Phase 2 Postgres terraform move + plan
(no apply yet)". Apply has not been authorized.

The plan output captured here will inform a future apply-stage briefing that
will require its own operator green-light (cost: $1,580/mo, irreversibility:
prevent_destroy on a HA instance with ~10 minute provisioning lead time).

## 11. Done-when

Task #59 closes when ALL of:

- [x] Sub-module files authored at `terraform/phase-0a-gcp/postgres/`
- [x] Staging packet marked SUPERSEDED with gap-closure note
- [x] Briefing committed (this file)
- [ ] `terraform init` + `terraform plan -out=tfplan -detailed-exitcode` executed
- [ ] Plan output captured to `postgres-plan.output`
- [ ] Plan output reviewed for §8 gates + §9 anti-gates
- [ ] Sub-module + audit supersession + briefing committed in a single commit

## 12. Appendix A — Gemini delegation prompt (executed at step 7.1+7.2)

```
GOOGLE_GENAI_MODEL=gemini-3.1-pro-preview \
GEMINI_CLI_TRUST_WORKSPACE=true \
GOOGLE_CLOUD_PROJECT=autonomous-agent-2026 \
GOOGLE_CLOUD_LOCATION=global \
gemini --yolo -p "<see Appendix B below>"
```

## 13. Appendix B — Gemini prompt body

> Task: terraform plan (PLAN ONLY — DO NOT APPLY) the Phase 2 Postgres
> sub-module at `terraform/phase-0a-gcp/postgres/`.
>
> Working directory: `/Users/danielmanzela/RX-Research Project/wt-framing-2/terraform/phase-0a-gcp/postgres`
>
> Commands (execute in order, capture full output):
>
>   terraform init -input=false
>   terraform plan -input=false -out=tfplan -detailed-exitcode
>
> Expected outcome: `terraform plan` exits 2 (changes pending), summary
> "Plan: 11 to add, 0 to change, 0 to destroy."
>
> CRITICAL: DO NOT run `terraform apply`. Apply is a separate task with its
> own operator gate. If anything in the plan output looks wrong (resources to
> destroy, root Phase 0a resources appearing, IAM errors, network errors),
> stop and report — do NOT attempt remediation.
>
> Report back:
> 1. Exit codes for init and plan
> 2. Full plan output text (so it can be archived)
> 3. Resource counts (added / changed / destroyed)
> 4. Any warnings or unusual messages

## 14. Appendix C — Plan attempt #1 (BLOCKED — fixed) + Plan attempt #2 (SUCCESS)

### C.1 First plan attempt — BLOCKED

`terraform init` exit 0 (clean: backend `gs://autonomous-agent-2026-autonomousagent-tfstate/phase-0a-postgres`
configured, providers `hashicorp/google` + `hashicorp/google-beta` v5.45.2
downloaded).

`terraform plan` exit 1 with config error:

```
Error: Unsupported argument
  on main.tf line 147, in resource "google_sql_database_instance" "postgres_vector":
 147:   labels = {
An argument named "labels" is not expected here.
```

Root cause: `google_sql_database_instance` does NOT expose a top-level
`labels` arg, unlike most GCP resources. Per the provider schema
(confirmed via context7 `/hashicorp/terraform-provider-google` docs), labels
on Cloud SQL live inside the `settings{}` block as `user_labels` (Map of
String). This is a Cloud SQL surface quirk that the Gemini-authored staging
packet did not encode.

**Fix (Task #63):** moved the labels map from top-level `labels = {...}` into
`settings { user_labels = {...} }`. The `lifecycle.prevent_destroy = true`
block is untouched at the resource level (where it belongs).

Full output: `postgres-plan.output`.

### C.2 Second plan attempt — SUCCESS (READY FOR APPLY GATE)

`terraform plan` exit 2 (changes pending), data sources resolved cleanly:

```
data.google_compute_network.vpc: Read complete after 1s
  [id=projects/autonomous-agent-2026/global/networks/autonomousagent-vpc]
data.google_service_account.vm_runtime: Read complete after 1s
  [id=projects/autonomous-agent-2026/serviceAccounts/autonomousagent-vm-runtime@autonomous-agent-2026.iam.gserviceaccount.com]

Plan: 11 to add, 0 to change, 0 to destroy.
```

Full output: `postgres-replan.output`.

### C.3 §8 verification gates — all pass

| Gate | Expected | Actual | Pass? |
|---|---|---|---|
| 11 resources all `to add` | yes | yes (+create only, no ~ or -) | ✓ |
| No root Phase 0a resources in plan | yes | yes (state isolation working) | ✓ |
| Data source lookups succeed | yes | yes (VPC + SA both resolved) | ✓ |
| `availability_type = "REGIONAL"` | yes | yes | ✓ |
| `tier = "db-custom-16-64000"` | yes | yes | ✓ |
| `disk_size = 1000` | yes | yes | ✓ |
| `ipv4_enabled = false` | yes | yes (private IP only) | ✓ |
| `cloudsql.iam_authentication = on` flag | yes | yes (in database_flags list) | ✓ |
| `backup_configuration.enabled = true` | yes | yes | ✓ |
| `point_in_time_recovery_enabled = true` | yes | yes | ✓ |
| No spurious provider/backend re-init | yes | yes | ✓ |
| Exit code = 2 | yes | yes | ✓ |

### C.4 §9 anti-gates — all pass

| Anti-gate | Should NOT show | Actual | Pass? |
|---|---|---|---|
| `# (forces replacement)` | absent | absent | ✓ |
| `to destroy` | 0 | 0 | ✓ |
| Permission denied / unauthorized | absent | absent | ✓ |
| `no service networking connection` | absent | absent (peering present) | ✓ |
| API not enabled / quota | absent | absent | ✓ |

### C.5 Notable provider-side behaviors observed

- `deletion_protection = true` on `google_sql_database_instance` — provider
  default since v4.48.0, surfaces as a separate GCP-level guard *in addition
  to* the `lifecycle.prevent_destroy = true` we declared. Belt + braces +
  suspenders = correct for a $1,580/mo HA instance.
- `edition = "ENTERPRISE"` — provider default for Postgres custom-tier
  instances; matches the Phase 2 cost-estimate assumption.
- `activation_policy = "ALWAYS"`, `pricing_plan = "PER_USE"` — provider
  defaults, match the always-on production-grade workload posture.
- `ip_configuration.enable_private_path_for_google_cloud_services = true`
  — preserves Google-internal egress (DNS, Logs, Monitoring) over the
  private path, as configured in main.tf.
- VPC peering range allocated as `/16` (`autonomousagent-postgres-peering-range`)
  on `projects/autonomous-agent-2026/global/networks/autonomousagent-vpc` — matches the
  Service Networking expectation; no conflict with the existing 10.10.0.0/24
  subnet.

### C.6 Status — apply gate

**Task #59 (Phase 2 Postgres terraform move + plan): DONE.**
**Task #63 (labels → user_labels schema fix): DONE.**

Per the user's explicit directive for this task ("no apply yet"), the
`tfplan` file at `terraform/phase-0a-gcp/postgres/tfplan` is the artifact for
a future apply-stage briefing. Apply will require:

1. Separate operator green-light (cost: $1,580/mo, ~10 min provisioning
   lead-time, `prevent_destroy` + `deletion_protection` on a HA instance).
2. Re-plan immediately before apply to confirm no drift since this plan.
3. Verification gates as listed in `terraform/phase-0a-gcp/postgres/README.md`
   §Verification.
4. Updates to `audit/2026-05-21-phase2-postgres/acceptance-criteria.md` for
   each of the 7 critical-blocker criteria as they're confirmed.
