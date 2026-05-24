# 0009. Judge panel is the project's RLAIF substrate

**Status:** Accepted
**Date:** 2026-05-20
**Decision-makers:** Daniel Manzela (+ Claude Opus 4.7)

## Context

The 4-judge consensus panel (`lib/evaluators/consensus.py`, gated by
`config/limits.yaml:evaluators`) is described in several places as a
"per-tool-call evaluator" or "approach-rejection gate." That framing
captures what it does at a single decision point but **misses its
long-run role**: the same JSONL stream of consensus decisions also
trains every downstream RL signal in the system.

Until this ADR, the architecture-research doc and several prior
discussions assumed RLAIF (Reinforcement Learning from AI Feedback)
would require a separate component — typically described as a
"reward model" or "preference model" trained on judge outputs. That
is not the case here. The judge panel's per-decision verdicts, written
to the JSONL event log defined by J1, *are themselves* the AI-feedback
signal. There is no second-stage reward model; the judge consensus is
both the gate and the trajectory ingredient.

This naming has been ambient since the J1 schema landed but was not
recorded as a decision. Without recording it, the judge panel keeps
getting described in narrow per-call terms, which obscures the
contract with the RL pipeline (ADR-0005) and makes the trajectory
shipper (J3) look like an unrelated component instead of the
hand-off mechanism it is.

## Decision

We will treat the 4-judge consensus panel as the project's RLAIF
substrate. Specifically:

1. **The judge panel's consensus event log is the AI-feedback signal.**
   No separate reward model is trained on top of judge outputs.
   The trajectory shipper (J3) feeds the JSONL stream directly into
   the RL pipeline's reward computation, alongside the heuristic
   weights in `config/limits.yaml:rl_rewards`.

2. **The J1 schema (v1) is the inter-component contract** between the
   judge panel (producer) and the RL pipeline (consumer). It lives at
   `trajectories/judge-events.jsonl` per `config/limits.yaml:evaluators.judge_events.path`.
   Changes to the schema require a v-bump and dual-write window per
   normal contract-evolution practice.

3. **The judge panel's per-axis model assignment**
   (`config/limits.yaml:evaluators.per_axis_model`) is the locus of any
   future "constitutional AI" or "model-as-judge" refinements.
   Per-axis swaps are an RLAIF tuning lever, not just a cost lever.

4. The 5th-judge tiebreaker model
   (`config/limits.yaml:evaluators.consensus.fifth_judge_model`) is
   recorded into the JSONL event when it fires, so downstream reward
   shaping can weight tiebreaker decisions differently from clean
   consensus decisions if needed.

## Consequences

### Positive

- Eliminates a phantom component. "Where is the reward model?" has a
  concrete answer: the JSONL stream from the judge panel, period.
- The J1 schema becomes a load-bearing contract — schema-validation
  CI gates (`tests/unit/test_judge_events.py`) are doing real
  work, not just hygiene.
- Future reward-shaping experiments (intent-category weighting,
  consensus-confidence weighting, axis-level reward decomposition)
  have a single source of truth to operate on.

### Negative

- We are now committed to maintaining the JSONL event-log shape with
  the same discipline as any other inter-service contract. Schema
  drift = silent reward signal corruption.
- Judge model selection (`per_axis_model`) becomes harder to change
  freely — a substitution that affects judge calibration shifts the
  reward signal in non-obvious ways.

### Neutral

- ADR-0005 is unchanged in its high-level architecture decisions
  (soft loop now, hard loop in Phase 4); this ADR refines the
  "what is the AI-feedback signal" question that ADR-0005 left
  open. ADR-0005 will be cross-referenced.

## Alternatives considered

### Option A: Train a separate reward model on judge outputs

- Pros: Standard RLHF/RLAIF architecture; well-trodden path; could
  capture latent patterns the judges miss.
- Cons: Adds a model to train, version, swap, and monitor; introduces
  a calibration gap between the live judge consensus and the trained
  reward model; doubles the failure modes.
- Why rejected: The judge panel already produces structured, axis-
  decomposed, intent-aware verdicts at every decision point. A separate
  reward model would be lossy compression of richer signal, and would
  delay the RL pipeline's hard-loop launch (Phase 4) by adding a model
  training step before training can even start.

### Option B: Defer the naming until Phase 4

- Pros: Avoids committing to a contract before the RL pipeline ships.
- Cons: Without the naming, J3 (trajectory shipper) and J1 (event
  schema) keep getting described as orthogonal — making it likely they
  drift apart by the time Phase 4 lands. Cheaper to commit now.
- Why rejected: The cost of naming is one ADR. The cost of letting
  the contract drift is integration debt across J1/J3/RL.

## References

- ADR-0005 (`docs/decisions/0005-self-rl-pipeline-architecture.md`) —
  parent architecture decision (RL pipeline soft/hard loop split)
- `lib/evaluators/consensus.py` — judge-panel implementation
- `lib/evaluators/judge_events.py` (J1) — JSONL event-log writer
- `config/limits.yaml:evaluators` — judge-panel configuration block,
  including `judge_events.path` (the trajectory hand-off contract)
- `audit/2026-05-20-architecture-research-gap-analysis/audit-plan.md` J10 —
  closes this audit item
- `audit/2026-05-20-architecture-research-gap-analysis/findings.md`
  §Component 6 — research-doc gap analysis that motivated this naming
