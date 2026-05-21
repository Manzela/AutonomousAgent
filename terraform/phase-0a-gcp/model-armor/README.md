# Model Armor sub-module

Isolated Terraform module that provisions [Google Cloud Model Armor](https://cloud.google.com/security-command-center/docs/model-armor-overview)
plus a referenced [Sensitive Data Protection (DLP)](https://cloud.google.com/sensitive-data-protection/docs)
InspectTemplate for the J1 trajectory shipper.

## Why a sub-module (not in the root)

The Model Armor resources (`google_model_armor_floorsetting`,
`google_model_armor_template`) were added to the `google-beta` provider in
the `6.x` line. The root `terraform/phase-0a-gcp/providers.tf` is pinned to
`~> 5.30` and a global provider upgrade would force re-plan + re-validate of
every existing Phase 0a resource (VM, AR repo, WIF pool, monitoring alerts,
billing budget).

This sub-module:

- Uses its own `providers.tf` pinned to `google-beta ~> 6.43`
- Uses the same GCS state bucket (`i-for-ai-autonomousagent-tfstate`) but a
  distinct state prefix (`phase-0a-model-armor`), so `terraform apply` here
  cannot disturb root state
- Has no cross-resource dependencies on root — only references `var.project_id`

When the root module is eventually upgraded to `~> 6.x`, this module can be
folded back in by moving the resources + state.

## What this provisions

1. Enables `modelarmor.googleapis.com` and `dlp.googleapis.com` on the project.
2. Creates a project + global `google_data_loss_prevention_inspect_template`
   named `j1-inspect-and-redact` covering `EMAIL_ADDRESS`,
   `CREDIT_CARD_NUMBER`, `PHONE_NUMBER`, `US_SOCIAL_SECURITY_NUMBER` at
   `UNLIKELY` (aggressive redaction acceptable for offline training data).
   Note: the terraform provider's accepted values are
   `{VERY_UNLIKELY, UNLIKELY, POSSIBLE, LIKELY, VERY_LIKELY}` — *not* the
   REST API's `LIKELIHOOD_*` prefix style.
3. Creates a project-level `google_model_armor_floorsetting` that enforces
   the InspectTemplate on every Model Armor call against the project.
4. Creates a regional `google_model_armor_template` named
   `j1-trajectory-shipper` for explicit `templates.sanitize` calls from the
   trajectory shipper.

## Apply procedure

```bash
cd terraform/phase-0a-gcp/model-armor
terraform init
terraform plan -out=tfplan
# Review the plan: must NOT touch any existing phase-0a resources.
terraform apply tfplan
```

## Verification

After apply, see `audit/2026-05-20-model-armor-j1-runbook/runbook.md` §4
for `gcloud model-armor floorsettings describe` checks.

## Dependencies / blockers

- **Blocks J1 launch.** Task #12.b in the disposition memo at
  `audit/2026-05-20-architecture-research-gap-analysis/stream-b-open-questions-disposition.md`.
- **Does NOT close the Persistence Trap on its own** — Task #12.c (audit
  scope `audit/2026-05-21-persistence-trap-12c/`) is the application-layer
  contract that the shipper either captures post-inference OR calls
  `templates.sanitize` against `model_armor_template_name` before writing
  JSONL to GCS. Floor Settings cannot save you from a caller that bypasses
  Model Armor entirely.
- **Cost envelope:** ~$31/mo at 10k verdicts/day (Model Armor $29.80 +
  DLP scan $1.20). See runbook §5.

## Rollback

```bash
terraform destroy
```

Destroys the FloorSetting + template + InspectTemplate. API enablement is
preserved (`disable_on_destroy = false`) so other workloads using DLP/Model
Armor are not affected.
