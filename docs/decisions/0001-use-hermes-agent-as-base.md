# 0001. Use Hermes Agent as base

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

We need an autonomous agent runtime with built-in self-improvement capabilities (skill creation, memory curation, cross-session search), multi-platform messaging, multiple terminal backends including sandboxed execution, and an RL training pipeline. Building this from scratch would take months and reproduce work already done by the field.

## Decision

We will use [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT, ~150K stars, actively developed) as the agent core. We will not fork it; we will consume it as a git submodule pinned to a specific SHA, wrapping it with our own deployment, configuration, security, and observability layers.

## Consequences

### Positive
- Skip months of foundational agent-loop / skill / memory development
- Inherit a battle-tested RL trajectory pipeline (Atropos environments, trajectory_compressor.py)
- Multi-platform messaging gateway is built in
- Compatible with the agentskills.io standard
- Active community support via Nous Research Discord

### Negative
- We are dependent on Hermes' release cadence and breaking changes
- Hermes upgrades require regression testing against our wrapper
- Custom modifications to agent internals are off the table (we wrap, don't fork)

### Neutral
- We track upstream via the submodule SHA; bumps are explicit, not automatic
- Hermes is MIT-licensed, compatible with our MIT license

## Alternatives considered

### Option A: Build agent loop from scratch on top of Anthropic SDK
- Pros: Total control; no dependencies
- Cons: Months of work; reinvents skill/memory/multi-gateway/RL/sandboxing systems
- Why rejected: Effort vastly exceeds value of differentiation

### Option B: Use LangGraph / LlamaIndex / CrewAI
- Pros: Production-ready frameworks
- Cons: None of them ship the closed self-learning loop, RL trajectory pipeline, or multi-platform gateway out of the box; would still need significant assembly
- Why rejected: Hermes is closer to our requirements with less assembly

### Option C: Fork Hermes
- Pros: Can modify internals
- Cons: Loses upstream benefit; merge friction; we're not the experts on the agent loop
- Why rejected: We don't have a concrete need to change internals; wrap-don't-fork keeps us aligned with upstream

## References

- [Hermes Agent GitHub](https://github.com/NousResearch/hermes-agent)
- Pinned SHA in this project: `ddb8d8fa842283ef651a6e4514f8f561f736c72e`
