# 0006. Iterative phase build with acceptance gates

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

The full architecture spans local-Mac development, cloud-prod deployment, trajectory pipeline, and RL training. Total scope is multiple weeks of work with substantial cost surface (GCP infra, GPU). Three build-sequencing strategies were considered: big-bang, iterative phases with gates, parallel tracks.

## Decision

We will build in **four sequential phases**, each with a defined acceptance protocol. We do not start phase N+1 until phase N's acceptance gate passes.

| Phase | Deliverable | Gate |
|---|---|---|
| 1 | Local Hermes Agent in Docker on Mac | 10 TG msgs, autonomous skill creation, restart-persistent state, Phoenix traces, no leaks |
| 2 | GCP VM 24/7 deployment | 7-day soak, no manual interventions, no budget breach |
| 3 | Trajectory pipeline → GCS | 1K trajectories, schema valid, reward sanity ≥0.7, 20-trajectory human spot-check |
| 4 | Atropos RL training run | One full cycle, eval improvement ≥2% vs baseline |

Each phase has its own design ADR, plan, and acceptance runbook. Each phase merges to `main` only after acceptance.

## Consequences

### Positive
- Every phase produces working, testable software
- Cost is gated (Phase 2 GCP spend doesn't start until Phase 1 works; Phase 4 GPU spend doesn't start until Phase 3 produces data)
- Failed phases stop or pivot without cascading damage
- Each phase plan is reviewable in isolation

### Negative
- Total wall-clock time is longer than a big-bang approach
- Some Phase 2+ design choices may need to evolve based on Phase 1 learnings (acceptable)

### Neutral
- This pattern matches how production teams ship complex systems

## Alternatives considered

### Option A: Big-bang (build everything at once)
- Pros: Done in one pass
- Cons: Any single broken piece blocks all of it; Phase 4 GPU spend starts before there's data worth training; can't validate before paying for cloud
- Why rejected: Risk and cost overrun unacceptable

### Option B: Parallel tracks (build local + cloud + RL in parallel streams)
- Pros: Wall-clock faster
- Cons: Merge friction; debug surface doubles; dependencies across streams
- Why rejected: Overhead exceeds savings for a single-developer project

## References

- Spec §10
- Phase 1 plan: `docs/superpowers/plans/2026-05-14-phase1-local-deployment.md`
