# Training Data Governance, Provenance, and Poisoning Review

**Version:** 1.0.0
**Last Reviewed:** June 4, 2026
**Status:** APPROVED
**Classification:** PUBLIC

---

## 1. Executive Summary

This document reviews the data governance posture of the Hermes Autonomous Agent. It evaluates:
1. **Training Data Poisoning risks** and their mitigation at the application layer.
2. **Training Data Provenance & licensing compliance** for the underlying models.

Because Hermes is a runtime orchestrator utilizing pre-trained foundation models (via API endpoints) rather than a fine-tuned or custom-trained model, direct training data poisoning is **Not Applicable (N/A)**. However, downstream data poisoning (indirect prompt injection) is a real threat vector and is fully mitigated.

---

## 2. Training Data Poisoning Review

### 2.1 Direct Model Poisoning (N/A)
Direct model poisoning occurs when an attacker manipulates the training dataset of a machine learning model to introduce backdoors or bias.
- **Postures:** N/A. Hermes does not collect user data to perform automated continuous fine-tuning or training. Models are static pre-trained weights hosted on secure GCP Vertex AI platforms.
- **Provider Vetting:** We rely on Google Cloud Vertex AI's infrastructure security to prevent unauthorized weight modifications or base dataset poisoning of models like Gemini and Claude.

### 2.2 Indirect/Prompt Poisoning (Mitigated)
The primary vector of data poisoning in autonomous agents is **indirect prompt injection**—where malicious content fetched by tools (e.g. reading a compromised file or web page) contains instructions that hijack the agent's reasoning.

#### Mitigation Layers:
1. **PII & Secret Scrubber (`lib/scrubber.py`):** Automatically redacts sensitive fields, credentials, and API keys before they reach logs or model contexts.
2. **Untrusted Content Wrapper (`app/core/trust.py`):** Implements a `UntrustedContent` (C16) wrapper. It tags all external tool outputs as untrusted, preventing the agent from executing privileged actions classified by untrusted data.
3. **Model Armor Guardrails (`lib/guardrails/model_armor.py`):** Evaluates prompts and model responses against Vertex AI Model Armor templates to block injection patterns.
4. **Sandboxed Code Execution (`app/core/sandbox.py`):** Prevents commands from executing on the host system; sandbox environments are isolated and default-deny network access.

---

## 3. Training Data Provenance & Source Licensing

### 3.1 Base Foundation Models
Hermes integrates with established, commercially-licensed models:
- **Gemini & Claude (Vertex AI):** Governed by commercial licenses with Google Cloud. Google certifies intellectual property indemnity for enterprise customers using Vertex AI models.
- **Open-Weights Models (e.g. Llama 3 / Gemma 4):** Evaluated against their respective community licenses (Llama 3 Community License Agreement, Gemma Terms of Use).

### 3.2 Codebase and Prompt Licensing
- **Dependency Guard (`.github/workflows/license-dep-gate.yml`):** Automatically scans all dependencies for license compliance. Enforces an allowlist of open-source licenses (MIT, Apache 2.0, BSD) and blocks copyleft licenses (GPL/AGPL) that could compromise codebase IP.
- **No Proprietary Contamination:** All prompts, graphs, and agent definitions are custom-written or derived from public, permissible open-source templates.
