# Hermes Agent — Regulatory Framework Mapping

**Version**: 1.0
**Date**: 2026-06-04
**Status**: INITIAL — Requires legal/compliance review

## Overview

This document maps the Hermes Agent's existing controls to three major regulatory and standards frameworks:
1. **NIST AI RMF** (AI Risk Management Framework 1.0)
2. **EU AI Act** (Regulation 2024/1689)
3. **ISO/IEC 42001:2023** (AI Management System)

---

## 1. NIST AI RMF 1.0 Mapping

### GOVERN — Policies, Processes, Procedures

| Function | Sub-category | Hermes Control | Status |
|----------|-------------|----------------|--------|
| GOVERN 1.1 | Legal & regulatory requirements identified | This regulatory mapping document | DONE |
| GOVERN 1.2 | Trustworthy AI characteristics integrated | `ActionClass` 3-tier, HITL gates, eval_gate | DONE |
| GOVERN 1.3 | Processes for risk management | STRIDE threat model (`docs/security/threat-model-stride.md`) | DONE |
| GOVERN 1.4 | Feedback mechanisms | `decision_record`, operator interrupts, Telegram channel | DONE |
| GOVERN 1.5 | Organizational policies | System prompt (`config/hermes/MEMORY.md`), GEMINI.md | DONE |
| GOVERN 2.1 | Roles & responsibilities defined | On-call rotation (`docs/operations/on-call.md`) | DONE |
| GOVERN 2.2 | Training for AI risk management | Runbooks in `docs/operations/` | PARTIAL |
| GOVERN 3.1 | Decision-making oversight | Ship gate interrupt (mandatory HITL) | DONE |
| GOVERN 4.1 | Organizational practices monitored | SLO/SLI definitions, error budget policy | DONE |
| GOVERN 5.1 | Policies and resources allocated | Budget caps (per-graph, daily), billing alerts | DONE |
| GOVERN 6.1 | Transparent system documentation | System card (`docs/governance/system-card.md`) | DONE |

### MAP — Context & Use Characterization

| Function | Sub-category | Hermes Control | Status |
|----------|-------------|----------------|--------|
| MAP 1.1 | Intended purpose documented | System card §1 (Intended Use) | DONE |
| MAP 1.2 | Interdependencies mapped | C4 architecture diagrams, dependency graph | DONE |
| MAP 1.5 | Deployment context documented | `docs/architecture/`, Terraform IaC | DONE |
| MAP 2.1 | Known limitations documented | System card §4 (Limitations) | DONE |
| MAP 2.2 | Potential misuse documented | System card §1 (Out-of-Scope Uses) | DONE |
| MAP 3.1 | Benefits & costs assessed | SLO/SLI cost SLIs, budget caps | DONE |
| MAP 3.2 | Scientific validity of AI approach | Eval framework, judge panel design | DONE |
| MAP 4.1 | Bias risks identified | Bias & fairness framework (`evals/bias_fairness.py`) | DONE |
| MAP 5.1 | Impacts on affected communities | Limited — single-org deployment | N/A |

### MEASURE — Analysis, Assessment, Monitoring

| Function | Sub-category | Hermes Control | Status |
|----------|-------------|----------------|--------|
| MEASURE 1.1 | Appropriate metrics identified | SLO/SLI definitions | DONE |
| MEASURE 2.1 | Evaluation methodology | Golden eval suite, multi-axis judge panel | DONE |
| MEASURE 2.2 | Evaluation results documented | System card §5, `scheduled-golden-eval.yml` | DONE |
| MEASURE 2.3 | AI system tested for bias | Counterfactual bias framework | DONE |
| MEASURE 2.4 | AI system tested for safety | Adversarial probes (PyRIT, DeepEval), prompt injection scan CI | DONE |
| MEASURE 2.5 | AI system tested against pre-deployment criteria | 25 required CI status checks | DONE |
| MEASURE 2.6 | AI system evaluated in deployment context | Integration tests, Compose health check | DONE |
| MEASURE 3.1 | Risks and impacts monitored | OTel spans, drift detection, PII leak monitoring | DONE |
| MEASURE 4.1 | Measurement approaches documented | SLO/SLI document | DONE |

### MANAGE — Prioritize, Respond, Recover

| Function | Sub-category | Hermes Control | Status |
|----------|-------------|----------------|--------|
| MANAGE 1.1 | Risk treatment plans | STRIDE threat model, Go-Live audit remediation | DONE |
| MANAGE 2.1 | Response plans | Kill switch (SP-IR1), rollback (SP-26), on-call matrix | DONE |
| MANAGE 2.2 | Contingency plans | Revision rollback, HALT sentinel, FailureMatrix recovery | DONE |
| MANAGE 3.1 | Risks monitored continuously | Budget watchdog, OTel dashboards, drift detection | DONE |
| MANAGE 4.1 | Risk treatment activities documented | Audit trail, decision_record, forensic log archive | DONE |

---

## 2. EU AI Act (Regulation 2024/1689)

### Risk Tier Assessment

| Criterion | Assessment |
|-----------|-----------|
| **Prohibited (Art. 5)** | NO: Not applicable — no social scoring, biometric surveillance, or subliminal manipulation |
| **High-Risk (Art. 6, Annex III)** | WARNING: Potentially applicable if used for employment decisions (resume analysis, hiring) |
| **Limited Risk (Art. 52)** | DONE: Applicable — AI system interacting with humans; transparency obligations apply |
| **Minimal Risk** | Primary classification for current use case (code generation tool) |

### Compliance Requirements (if classified as Limited Risk)

| Requirement | Article | Hermes Control | Status |
|-------------|---------|----------------|--------|
| Transparency: users know they interact with AI | Art. 52(1) | System card, `identity` block in system prompt | DONE |
| AI-generated content labeled | Art. 52(3) | Output metadata includes `agent_id`, audit trail | DONE |
| Risk management system | Art. 9 (high-risk) | STRIDE threat model, SLOs, on-call | DONE |
| Data governance | Art. 10 (high-risk) | PII scrubber, Model Armor, memory isolation | DONE |
| Technical documentation | Art. 11 (high-risk) | System card, architecture docs, this mapping | DONE |
| Record-keeping | Art. 12 (high-risk) | Full trajectory logging, forensic archive | DONE |
| Transparency to deployers | Art. 13 (high-risk) | System card §4 (Limitations), §5 (Risks) | DONE |
| Human oversight | Art. 14 (high-risk) | Mandatory HITL gates (sign_off, ship_gate) | DONE |
| Accuracy, robustness, cybersecurity | Art. 15 (high-risk) | Eval suite, SBOM, Trivy/OSV/CodeQL, sandbox isolation | DONE |

### Actions for High-Risk Classification

If the system is used in Annex III scenarios (employment, education, etc.):

- [ ] Conduct conformity assessment
- [ ] Register in EU AI database
- [ ] Implement quality management system
- [ ] Establish post-market monitoring
- [ ] Report serious incidents to market surveillance

---

## 3. ISO/IEC 42001:2023 Gap Analysis

### Clause Coverage

| Clause | Requirement | Hermes Coverage | Gap |
|--------|------------|-----------------|-----|
| 4.1 | Understanding the organization | System card, GEMINI.md, CLAUDE.md | None |
| 4.2 | Interested parties | On-call doc, system card users section | None |
| 4.3 | Scope of AIMS | System card §1 (Intended Use, Out-of-Scope) | None |
| 5.1 | Leadership commitment | Operator HITL gates, escalation matrix | None |
| 5.2 | AI policy | System prompt, ActionClass, FORBIDDEN actions | None |
| 5.3 | Roles & responsibilities | On-call rotation, escalation matrix | None |
| 6.1 | Risk assessment | STRIDE threat model, Go-Live audit | None |
| 6.2 | AI objectives | SLO/SLI definitions | None |
| 7.1 | Resources | Budget caps, GCP resource quotas | None |
| 7.2 | Competence | Runbooks, on-call procedures | None |
| 7.3 | Awareness | System card, transparency labeling | None |
| 7.4 | Communication | Telegram channel, GitHub issues, PagerDuty | None |
| 7.5 | Documented information | Full documentation suite | None |
| 8.1 | Operational planning | Task DAG, deployment pipelines | None |
| 8.2 | AI risk assessment | Per-graph budget, eval_gate, scope scoring | None |
| 8.3 | AI risk treatment | Kill switch, rollback, Model Armor | None |
| 8.4 | AI system lifecycle | CI/CD, 25 branch protection gates, IaC | None |
| 9.1 | Monitoring & measurement | OTel, SLOs, drift detection | None |
| 9.2 | Internal audit | Go-Live audit report, scheduled golden eval | None |
| 9.3 | Management review | Error budget review (monthly), SLO review (quarterly) | None |
| 10.1 | Nonconformity & corrective action | Post-mortem process, action items | None |
| 10.2 | Continual improvement | Drift detection, bias framework, SLO revision | None |

### Annex A Controls

| Control | Description | Status |
|---------|-------------|--------|
| A.2 | AI policy | DONE: System prompt + ActionClass |
| A.3 | Objectives for AI | DONE: SLO/SLI |
| A.4 | AI risk management | DONE: STRIDE + budget gates |
| A.5 | Resources for AI | DONE: GCP quota + budget caps |
| A.6 | Stakeholder communication | DONE: Multi-channel (Telegram, GitHub, PagerDuty) |
| A.7 | AI lifecycle management | DONE: CI/CD + IaC |
| A.8 | Data management for AI | DONE: PII scrubber + Model Armor + memory isolation |
| A.9 | Third-party management | PARTIAL — DPA tracking needed |
| A.10 | AI system impact assessment | DONE: System card + STRIDE |

### Outstanding Gaps

| Gap | ISO Clause | Remediation |
|-----|-----------|-------------|
| DPA tracking for LLM providers | A.9 | Create vendor DPA register |
| Formal conformity self-assessment | 9.2 | Schedule first internal audit |
| Training data provenance | 8.4 | Not applicable (no fine-tuning; prompt-only) |

---

## Review Schedule

- **Quarterly**: Framework mapping review
- **Annually**: Full re-assessment against updated framework versions
- **On change**: Any model change, new capability, or regulatory update

**Next review**: Q3 2026
