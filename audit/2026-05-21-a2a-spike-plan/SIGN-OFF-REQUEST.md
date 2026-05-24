# A2A Spike — 12 Open-Questions Sign-Off Request

**Action requested:** Reply with EITHER:
- "Defaults OK for all 12" (fastest path — proceeds with the recommended default for each Q)
- "Override Q<N>: <answer>" for specific Qs (defaults stand for the rest)
- "Hold — answer these N questions first" (delays Day 1 until resolved)

## Summary table (full text in `open-questions.md`)

| Q# | Topic | Documented default |
|----|-------|--------------------|
| Q1 | Composite-identity claim shape | `acting_for: {...}` shape; surface to A2A WG post-spike |
| Q2 | Which canary peer do we target? | Stub peer for spike; reference SDK as stretch |
| Q3 | Federation: non-GCP peers | GCP-only; defer multi-cloud federation |
| Q4 | `Allow unauthenticated` Cloud Run for A2A endpoint | `Allow unauthenticated` Cloud Run for dev/spike only; flag prod as TODO |
| Q5 | PHI in the JWT `acting_for.human_sub` | Opaque IDs for human_sub |
| Q6 | Compromise / break-glass plan for an agent SA | Manual revocation runbook in hand-off note |
| Q7 | SSE event-level traceparent convention | Implement `_meta.traceparent` SSE convention unilaterally |
| Q8 | Scrubber bypass — fix in spike or document as known gap? | Fix scrubber bypass narrowly in Day 9; file refactor as follow-up |
| Q9 | Feature flag rollout: who flips `HERMES_A2A_ENABLED`? | Engineering on-call flips dev/staging; sponsor+privacy officer for prod |
| Q10 | Hermes-agent submodule readiness | Populate hermes-agent submodule before Day 1 |
| Q11 | Spike duration adjustability if Day-4 SSE blocks us | Halt strictly per §5 kill criterion |
| Q12 | Spike sponsor + reviewer assignment | Sponsor = architecture lead; reviewer same |

## Why this gates Day 1

Per `spike-plan.md` Day 5 (auth), the JWT issuer/audience values depend on Q3 + Q7 answers. Per Day 8 (AgentCard), signing-key rotation depends on Q9. Day 1 scaffolding can proceed in parallel with answer-gathering, but Day 5 cannot.

## Recommendation

Defaults for all 12 are sound for a 10-day spike. Override only if a documented default conflicts with a production-cohort requirement the sponsor knows but the docs don't.
