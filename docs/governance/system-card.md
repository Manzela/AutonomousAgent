# Hermes Agent — System Card

**Version**: 1.0
**Date**: 2026-06-04
**Model Provider**: Google Vertex AI (Gemini)
**Operator**: [CONFIGURE: Organization Name]

## 1. System Overview

### What is Hermes Agent?

Hermes Agent is an autonomous AI coding agent that operates within the software development lifecycle. Given a high-level goal, it:

1. **Clarifies** requirements through structured Q&A with the operator
2. **Decomposes** the goal into a dependency-aware task DAG
3. **Executes** each task in isolated sandboxed environments
4. **Evaluates** outputs against the locked specification (scope gating)
5. **Ships** approved deliverables with human-in-the-loop approval

### Intended Use

- **Primary**: Autonomous execution of software engineering tasks within a controlled organizational environment
- **Users**: Software engineering teams that need AI-assisted code generation, testing, and deployment
- **Domain**: General-purpose software development (no domain-specific training)

### Out-of-Scope Uses

- NO: Autonomous operation without human oversight (HITL gates are mandatory)
- NO: Financial, legal, or medical advice generation
- NO: Processing of regulated personal data (HIPAA, PCI-DSS)
- NO: Autonomous deployment to production without human approval
- NO: Multi-tenant operation (single-tenant architecture)

## 2. Model Information

| Property | Value |
|----------|-------|
| **Base Model** | Gemini 2.5 Pro (via Vertex AI) |
| **Judge Models** | Gemini 2.5 Pro, Gemini 2.5 Flash (multi-axis evaluation) |
| **Routing** | LiteLLM proxy for model selection and cost tracking |
| **Fine-tuning** | None (prompt-only; system prompt in `config/hermes/MEMORY.md`) |
| **Context Window** | 1M tokens (model default) |
| **Output Limit** | 65K tokens (model default) |

## 3. Capabilities

| Capability | Description | Maturity |
|------------|-------------|----------|
| Goal decomposition | Converts natural-language goals into task DAGs | Production |
| Code generation | Generates, modifies, and reviews code | Production |
| Sandboxed execution | Runs generated code in isolated containers | Production |
| Multi-axis evaluation | Judges output quality across correctness, style, safety | Production |
| Budget management | Per-graph and daily spend caps | Production (P0-2) |
| Drift detection | Weekly golden eval regression detection | Operational |
| A2A federation | Peer-to-peer agent communication (JSON-RPC) | Staging |

## 4. Limitations

### Known Limitations

1. **No real-time learning**: The system does not learn from runtime interactions. All behavior is determined by the system prompt and model weights.
2. **Single-tenant**: Not designed for multi-tenant deployment. Memory stores and checkpoints are per-deployment.
3. **English-centric**: Primarily tested with English-language goals. Non-English inputs may produce degraded results.
4. **Code-centric**: Optimized for software engineering tasks. Non-code outputs (documentation, design) are less rigorously evaluated.
5. **Network isolation trade-offs**: `CloudRunJobSandbox` cannot enforce network isolation (rejects `network_allowed=True`); `FirecrackerSandbox` (full isolation) is not yet production-ready.

### Known Risks

| Risk | Mitigation | Residual |
|------|-----------|----------|
| Prompt injection via untrusted input | `UntrustedContent` wrapper, `guard_action_class`, Model Armor screening | LLM may occasionally process injected instructions despite safeguards |
| Cost runaway | Per-graph budget cap, daily budget watchdog, billing alerts | First wave of a runaway may overspend before budget_verdict pre-empts |
| PII exposure | 15-pattern scrubber, Model Armor, `ScrubFilter` on logs | Novel PII patterns not in the scrubber dictionary may leak |
| Hallucinated code | Multi-axis judge panel, eval_gate scope scoring | Subtle logic errors may pass eval if they don't violate scope |
| Sandbox escape | gVisor isolation (Cloud Run), rlimit (local) | In-process fallback path has no isolation |

## 5. Evaluation Results

### Golden Eval Suite

| Metric | Score | Threshold |
|--------|-------|-----------|
| Correctness | [Run `scheduled-golden-eval.yml`] | ≥ 95% |
| Safety (prompt injection resistance) | [Run adversarial probes] | ≥ 90% |
| Scope adherence | [Run eval_gate tests] | ≥ 95% |

### Bias & Fairness

See `evals/bias_fairness.py` for the counterfactual evaluation framework. Baseline results TBD after first full evaluation run.

## 6. Safety Controls

| Layer | Control | Enforcement |
|-------|---------|-------------|
| Input | Model Armor `SanitizeUserPrompt` | Pre-goal-intake |
| Input | `scrub_string` (PII patterns) | Goal intake |
| Process | `ActionClass` 3-tier (PRE_AUTH/GATED/FORBIDDEN) | Per-tool decision |
| Process | `UntrustedContent` escalation guard | Per-action |
| Process | Budget cap (per-graph + daily) | Per-wave in fan_out |
| Process | Clarify loop limit (≤5 rounds) | Per-graph |
| Process | Fan-out concurrency cap | Per-deployment |
| Output | `eval_gate` scope scoring | Pre-ship |
| Output | Model Armor `SanitizeModelResponse` | Pre-delivery |
| Output | Human approval (ship_gate interrupt) | Mandatory |
| System | Kill switch (SP-IR1) | Operator-triggered |
| System | Rate limiting (SlowAPI) | Per-endpoint |
| Deployment | Squid egress proxy (16-domain allowlist) | Network-level |

## 7. Ethical Considerations

- **Autonomy boundaries**: The agent never ships without explicit human approval
- **Transparency**: All decisions are recorded in the `decision_record` and `audit` trail
- **Accountability**: Full trajectory logging enables post-hoc review of any action
- **Bias mitigation**: Counterfactual evaluation framework (`evals/bias_fairness.py`) for ongoing monitoring
- **Privacy**: PII scrubbing at ingress, process, and egress; no training on user data

## 8. Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-06-04 | Initial system card (Go-Live audit P2-11) |

---

**Review cadence**: Updated on every model change, system prompt change, or capability addition.
**Contact**: Platform Engineering Team
