# Data Processing Agreements (DPAs) & Vendor Compliance Register

**Version:** 1.0.0
**Last Reviewed:** June 4, 2026
**Status:** APPROVED
**Classification:** CONFIDENTIAL

---

## 1. Purpose & Scope

This document establishes the inventory and compliance status of Data Processing Agreements (DPAs) for all model providers, APIs, and data vendors integrated with the Hermes Autonomous Agent. As a Tier-1 enterprise autonomous agent operating in a production environment, Hermes enforces strict data privacy, zero data retention (ZDR) policies for model training, and localized data residency.

---

## 2. Model & Data Vendor Compliance Register

All LLM and API interactions are routed through standardized enterprise contracts containing binding DPAs. The table below lists the active vendors, their compliance posture, and configuration references.

| Vendor | Service / API | Active DPA Date | Zero-Retention for Training | Data Residency | CMEK Support |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Google Cloud** | Vertex AI API | March 15, 2026 | **Yes** (Standard Google Cloud terms prohibit customer data use for training) | `us-central1` (Enforced via IAM/VPC) | **Yes** (Customer-Managed Encryption Keys enabled) |
| **Anthropic** | Claude API (via Vertex AI) | April 10, 2026 | **Yes** (Under Google Cloud Vertex agreement) | `us-central1` | **Yes** |
| **OpenAI** | Enterprise API | May 01, 2026 | **Yes** (Enterprise API terms guarantee zero data retention) | USA / Multi-region | **Yes** |
| **LiteLLM** | Proxy / Gateway | N/A (Self-Hosted) | **Yes** (Local processing only; no outbound vendor retention) | Local Virtual Private Cloud (VPC) | **Yes** (Local disks encrypted) |

---

## 3. Data Privacy and Governance Controls

### 3.1 Zero Data Retention (ZDR) Enforcements
Hermes is strictly configured to use enterprise API endpoints where vendor training on customer inputs/outputs is disabled:
- **Vertex AI:** Standard GCP terms apply, ensuring that prompt and generated data are never used by Google to train foundation models.
- **OpenAI Enterprise:** The workspace API configuration explicitly opts out of data sharing.

### 3.2 Data Residency and Boundary Controls
To comply with GDPR and CCPA requirements, all processing occurs within designated geographical boundaries:
- **Enforcement:** Enforced via Terraform resource constraints (`us-central1`) and VPC Service Controls.
- **Data classification:** Regulated by `compliance/data-classification.yaml`. Restricted data is encrypted at rest using Cloud KMS Customer-Managed Encryption Keys (CMEK).

### 3.3 Ephemeral Memory Policies
The Hermes Agent memory store (`MemoryRecord`) implements namespace isolation and automated TTLs:
- **Ephemeral State:** Set to auto-delete after ≤ 1 hour (`expires_at` TTL).
- **GDPR Compliance:** Verified by `test_gdpr_deletion.py` ensuring complete purge of historical user memory upon request.
