# Runbook: Google Cloud Model Armor for J1 Trajectory Shipper

## 1. Overview
This runbook configures **Google Cloud Model Armor** on project `autonomous-agent-2026`. This is a mandatory compliance requirement for the **J1 trajectory shipper** (Task #12).

Model Armor provides a runtime sanitization layer that redacts Sensitive Data (PII) from judge verdicts before they are persisted to GCS. This prevents the "Persistence Trap" where raw user PII could leak into offline training datasets for Phase 4 RL tuning.

## 2. Prerequisites
### API Enablement
The following APIs must be enabled on `autonomous-agent-2026`:
- `modelarmor.googleapis.com` (Model Armor API)
- `dlp.googleapis.com` (Cloud Data Loss Prevention / Sensitive Data Protection API)

### IAM Roles
The operator/service-account applying this configuration requires:
- `roles/modelarmor.admin` OR `roles/modelarmor.floorSettingsAdmin`
- `roles/dlp.admin` (for SDP template management)
- `roles/serviceusage.serviceUsageAdmin` (to enable APIs)

## 3. Deployment Steps

### Option A: Terraform (Preferred)
1. Copy the drafted `model_armor.tf` from this audit directory to `terraform/phase-0a-gcp/`.
2. Ensure you are using `google-beta` provider version `6.43.0` or higher.
3. Run:
   ```bash
   terraform plan -target=google_model_armor_floorsetting.project_floor
   terraform apply -target=google_model_armor_floorsetting.project_floor
   ```

### Option B: gcloud CLI (Fallback)
If Terraform is unavailable, execute the following commands:

```bash
# 1. Enable APIs
gcloud services enable modelarmor.googleapis.com dlp.googleapis.com --project autonomous-agent-2026

# 2. Create DLP Inspect Template
# This defines the specific InfoTypes and the UNLIKELY threshold (aggressive
# redaction). Enum naming differs across surfaces: gcloud uses hyphenated
# lowercase (--min-likelihood=unlikely); terraform provider uses
# SCREAMING_SNAKE (UNLIKELY); REST API uses LIKELIHOOD_UNSPECIFIED etc. The
# value LIKELIHOOD_LOW does not exist in any of these surfaces.
gcloud dlp templates create j1-inspect-and-redact \
    --project=autonomous-agent-2026 \
    --display-name="j1-inspect-and-redact" \
    --info-types=EMAIL_ADDRESS,CREDIT_CARD_NUMBER,PHONE_NUMBER,US_SOCIAL_SECURITY_NUMBER \
    --min-likelihood=unlikely

# 3. Configure Project Floor Settings
# Reference the created DLP template (extract name from previous step)
gcloud model-armor floorsettings update \
    --project=autonomous-agent-2026 \
    --location=global \
    --enable-floor-setting-enforcement=true \
    --sdp-filter-settings-inspect-template=projects/autonomous-agent-2026/locations/global/inspectTemplates/j1-inspect-and-redact
```

## 4. Verification Steps
Run the provided `validate.sh` script or execute manually:

```bash
# Verify Floor Settings
gcloud model-armor floorsettings describe --project=autonomous-agent-2026 --location=global
```

**Expected Output:**
- `enableFloorSettingEnforcement: true`
- `sdpFilterSettings.enforcement: ENABLED`
- `infoTypes` contains: `EMAIL_ADDRESS`, `CREDIT_CARD_NUMBER`, etc.

## 5. Cost Estimation
Based on J1 expected traffic of **10K judge verdicts/day**:

| Component | Usage | Est. Monthly Cost |
| :--- | :--- | :--- |
| **Model Armor** | 300M Tokens (2M Free) | ~$29.80 |
| **SDP (DLP) Scan** | ~1.2 GB Scanned | ~$1.20 |
| **Total** | | **~$31.00** |

*Note: Pricing assumes standalone Model Armor usage. If SCC Premium/Enterprise is active, Model Armor tokens may be included.*

## 6. Rollback
### Terraform
```bash
terraform destroy -target=google_model_armor_floorsetting.project_floor
```

### gcloud
```bash
gcloud model-armor floorsettings update \
    --project=autonomous-agent-2026 \
    --location=global \
    --enable-floor-setting-enforcement=false
```

## 7. Dependencies & Preconditions
1. **Task #12 Blocker**: The GCS bucket for J1 trajectories must exist.
2. **Task #12.c (Persistence Trap)**: **CRITICAL.** Verify that the trajectory shipper code captures the payload **POST-inference** (after Model Armor redaction) or explicitly calls the `templates.sanitize` method. Setting the floor does NOT automatically sanitize data written directly by the application to GCS unless the application uses the Model Armor sanitized output.

## 8. Open Questions / Assumptions
- **Regional Availability**: Model Armor is currently available in `us-central1`. Ensure `autonomous-agent-2026` workloads are compatible with this region.
- **Provider Version**: The existing `terraform/phase-0a-gcp/` uses provider `~> 5.30`. Model Armor resources likely require version `6.x` or `google-beta`. An upgrade to the root `providers.tf` may be required before promotion.
- **DLP Integration**: This runbook uses `basic_config` for SDP. If a shared organizational DLP template is preferred, the config must be updated to reference `inspect_template_settings`.
