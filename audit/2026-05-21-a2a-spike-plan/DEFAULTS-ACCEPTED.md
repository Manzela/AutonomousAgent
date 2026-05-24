# A2A Spike — Open Questions: Defaults Accepted

**Decision date:** 2026-05-21
**Operator:** Daniel Manzela (sole sponsor + reviewer for this spike — confirms Q12 default)
**Verbatim operator instruction:**

> **"Proceed with sensible defaults on the 12 open questions"**

This file closes out the Day-0 sponsor sign-off request (`SIGN-OFF-REQUEST.md`, commit 34f9452) and unblocks Day 1 of the spike plan. Each of the 12 questions (+ Q-meta) is locked to its documented default below, with a brief per-question justification recording *why* the default is acceptable for this spike's scope. Any future change to one of these decisions is treated as a new spike, per the original sign-off request's terms.

The defaults themselves live in `open-questions.md` (per-question detail) and `SIGN-OFF-REQUEST.md` (summary table). This file does not re-state the options; it records acceptance and reasoning.

---

## Per-question lock-in

| Q | Default accepted | Why this default is right for the spike |
|---|---|---|
| **Q1** | `acting_for: {human_sub, human_session_id, consent_scope}` claim shape | Spike's purpose is to prove the wire format works. WG convergence is a Q3-2026 concern; we surface a proposal post-spike. Cost-of-being-wrong (claim rewrite) is a contained engineering task, not a security or correctness issue. |
| **Q2** | Stub peer Days 1-9; Google reference SDK as Day 10 stretch | The interop value-add of a real Google SDK peer is preserved as a stretch goal without making it a blocker. If Day 10 has bandwidth, we earn the interop proof; if not, we still have a valid spike. |
| **Q3** | GCP-only federation; non-GCP deferred | Standing priority (memory: `a2a_priority_correction.md`) is **Google production use-case at scale**. Multi-cloud federation is a Phase 4 concern when training compute moves; pre-optimizing for it now is YAGNI. |
| **Q4** | `Allow unauthenticated` Cloud Run for spike + dev; prod gap flagged | Acceptable because the spike is sponsor + reviewer == same person (no external production exposure). The HIPAA-controlled audit-log routing via Log Sink is a separate hardening pass before prod. Documenting the gap explicitly is more honest than pretending the Cloud Run edge is hardened. |
| **Q5** | Opaque `pseudonym:abc...` IDs for `acting_for.human_sub` | Worst-case safe. Per the Persistence Trap contract (memory: `persistence_trap_contract.md`), we err on the side of "treat as PHI" anywhere we'd otherwise have to argue the negative. Opaque IDs also play nicely with future deletion / right-to-erasure requirements. |
| **Q6** | Manual revocation runbook in the hand-off note | Fits the spike's pre-prod scope. Per Q9's split, prod adds the privacy-officer gate; for spike-tier traffic volumes (single canary peer), manual SA disable + email notification is operationally adequate. v2 promotes to allowlist hot-reload (Q6 option b). |
| **Q7** | Implement `_meta.traceparent` SSE convention unilaterally + draft WG proposal | The receive side tolerates absence; we lose nothing by sending. Per-event causality is a real operational need for debugging cross-agent traces, and the WG-standardization path is too slow for the spike timebox. |
| **Q8** | Fix scrubber bypass narrowly in Day 9; file P0 ticket for full refactor | Spike's audit logs must not leak un-scrubbed PHI — that is non-negotiable per the Persistence Trap posture. The narrow fix is the minimum to keep the spike's outputs reviewable; the refactor to a Hermes-middleware (Q8 option c) is right but out of timebox. |
| **Q9** | Engineering on-call (dev/staging) + sponsor & privacy officer co-sign (prod) | Standard rollout gate. Spike-tier work happens in dev/staging where on-call has authority; prod's higher bar matches the Q4 / Q5 PHI posture. |
| **Q10** | Populate hermes-agent submodule before Day 1 — **ALREADY SATISFIED** | Verified at commit `5098c06` (HEAD: `docs(a2a): hermes-agent submodule verified — Plan B Task 0.1 close-out`). See `HERMES-SUBMODULE-VERIFIED.md` for the audit trail. Day 1 scaffolding can rely on live submodule line references, not stale comment quotes. |
| **Q11** | Halt strictly per §5 kill criterion if Day-4 SSE slips into Day 6 | Per standing feedback (memory: prefer real architectural discoveries over timebox-burning). A spike that runs over silently teaches the wrong lesson; a halt + sponsor decision teaches the right one. |
| **Q12** | Sponsor = reviewer = Daniel Manzela (sole maintainer) | Confirmed by who is reading this. Hand-off cycle is in-conversation, not async. |
| **Q-meta** | Hard 10-day commitment; Day 5 is a status sync, not a re-plan | Aligns with the kill-criterion enforcement (Q11). A budget that flexes silently is not a timebox; it's a polite suggestion. |

## Day 1 unblock

Day 5 (auth) — unblocked: Q3 default = GCP-only resolves the JWKS-source question; Q7 default = unilateral SSE traceparent resolves the trace-attribute convention.

Day 8 (AgentCard) — unblocked: Q9 default = on-call-flips rollout resolves the signing-key rotation authority question.

Day 1 (scaffolding) — unblocked outright: no Q1-Q12 answers were blocking the scaffolding sub-deliverables. The hermes-agent submodule (Q10) was the only Day-0 hard pre-req and is already satisfied.

## Scope of acceptance

This acceptance covers the 12 questions + Q-meta as drafted in `open-questions.md` as of commit `34f9452`. It does NOT pre-authorize:

- Any *new* open question that surfaces during the spike (those land in `open-questions.md` for a fresh decision).
- Any deviation from the Persistence Trap contract (`audit/2026-05-21-persistence-trap-12c/test-contract.md`) — that contract supersedes spike defaults wherever they conflict.
- Any change to the spike's 10-day timebox (Q-meta locks this).
- Production rollout of A2A traffic — Q4 + Q9 explicitly defer that to separate gates.

## References

- [`SIGN-OFF-REQUEST.md`](./SIGN-OFF-REQUEST.md) — the question the operator answered.
- [`open-questions.md`](./open-questions.md) — full per-Q detail (options + cost-of-being-wrong).
- [`spike-plan.md`](./spike-plan.md) — the 10-day spike plan these defaults unblock.
- [`HERMES-SUBMODULE-VERIFIED.md`](./HERMES-SUBMODULE-VERIFIED.md) — proves Q10 default is satisfied.
- Memory: `a2a_priority_correction.md` (rationale for Q3's GCP-only default).
- Memory: `persistence_trap_contract.md` (rationale for Q5 + Q8's conservative defaults).
