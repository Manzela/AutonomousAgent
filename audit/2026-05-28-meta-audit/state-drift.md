# Terraform / GCP State Drift Observation Log

## PC-2 Memorystore observation

- **Timestamp**: 2026-05-28
- **Instance**: `autonomousagent-jti-replay`
- **authEnabled**: `null` (false)
- **transitEncryptionMode**: `SERVER_AUTHENTICATION`
- **state**: `READY`
- **currentLocationId**: `us-central1-a`
- **Decision**: Since `authEnabled` is null (false), we will proceed with **Option A** for P0-2, which is a simple revert of `terraform/phase-0a-gcp/memorystore/main.tf` to match the actual GCP configuration.

## P0-15 Cost anomaly detection observation

- **Timestamp**: 2026-05-28
- **Action**: Monthly budget `google_billing_budget.monthly` was successfully created and applied with a $2,000 cap.
- **Observation**: The `google_monitoring_alert_policy.hourly_spend_spike` alert policy was omitted because GCP billing metrics require billing export to be active on the billing account (which is not configured), and the pre-cached Terraform provider version (5.45.2) does not support the `disable_metric_validation` argument to bypass existence checks.
