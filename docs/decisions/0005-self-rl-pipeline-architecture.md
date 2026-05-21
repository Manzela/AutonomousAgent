# 0005. Self-RL pipeline: soft loop now, hard loop in Phase 4

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

"Self-RL" can mean two very different things:
1. **Soft loop**: the agent improves its in-context behavior via skill creation, memory curation, and user modeling (no GPU; runs continuously; ships in Phase 1)
2. **Hard loop**: actual reinforcement-learning fine-tuning of model weights from collected trajectories using Atropos environments (requires GPU; runs sporadically; ships in Phase 4)

We need both, but they have very different cost profiles, infrastructure needs, and risk surfaces.

## Decision

We will ship the soft loop in Phase 1 (it's free; it's what makes Hermes "self-improving" out of the box). We will collect Atropos-format trajectories continuously from Phase 3 onward (cheap; produces training data). We will gate the hard loop in Phase 4 behind:

1. **Automated preflight**: dataset size ≥1K new trajectories, schema valid, reward sanity score ≥0.7, GPU quota available, monthly run budget available
2. **Telegram approval gate**: user must tap "Approve" in an inline-keyboard prompt before any GPU instance is provisioned
3. **Hard guardrails**: max 4 runs/month, max 24h per run, mid-run cost-overrun aborts the instance via Compute Engine API, eval-regression aborts the registration

Trained models are open-weight only (Llama, Qwen, DeepSeek). They land in a GCS model registry and are NOT auto-swapped into LiteLLM — that requires a separate human decision.

## Consequences

### Positive
- Soft loop delivers immediate value; users see continuous improvement
- Hard loop only spends GPU $ when there's data worth training on AND human approval
- Approval gate prevents unattended cost overruns
- Auto-trigger detection means we don't have to remember to check dataset readiness
- Multiple safety layers (preflight, approval, mid-run abort, eval-regression abort)

### Negative
- Phase 4 is significantly more complex than Phase 1-3
- Approval flow requires stable Telegram integration
- Reward signals are imperfect (we use weighted heuristics, not human labelers); training quality depends on this
- Eval suite must be carefully designed and maintained

### Neutral
- Phase 4 is opt-in: `rl_training.enabled: true` in `limits.yaml` arms the auto-trigger; `false` keeps the pipeline dark
- The first Phase 4 run is always eval-only (baseline) before any actual training

## Alternatives considered

### Option A: Skip the hard loop entirely (only soft loop)
- Pros: Much simpler; no GPU cost
- Cons: User wants both; long-term improvement is bounded by base model
- Why rejected: User explicitly asked for full Atropos pipeline

### Option B: Manual trigger only for hard loop (no auto-detection)
- Pros: Maximum human control
- Cons: User has to remember to check; data sits idle; cycle time slow
- Why rejected: User asked for auto-trigger with approval gate, which is more disciplined than manual

### Option C: Full automation (no approval gate)
- Pros: Fastest iteration
- Cons: Unbounded cost risk; one bad trigger = $400+ wasted
- Why rejected: Cost discipline non-negotiable

## References

- Spec §6
- `config/limits.yaml` `rl_training` section
- Atropos: https://github.com/NousResearch/atropos
- ADR-0009 (`0009-judge-panel-as-rlaif.md`) — names the AI-feedback signal source this ADR left open
