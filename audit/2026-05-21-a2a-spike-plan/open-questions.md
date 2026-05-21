# A2A Spike — Open Questions for Product / Sponsor

**Date:** 2026-05-21
**Audience:** Spike sponsor + product owner. Spike implementer cannot resolve these without your input.
**Format:** Each question has (a) the decision needed, (b) what the spike will assume in the absence of an answer, (c) the day on which the assumption becomes a permanent decision unless we hear otherwise.

If you don't answer by the "assumes-by" date, the assumption is committed and changing it later is a separate spike.

---

## Q1 — Composite-identity claim shape

**Decision needed:** What's the canonical claim shape for "this agent is acting on behalf of this human"? The A2A spec v1.0.0 does not standardize this — `Message.role` is only `USER` or `AGENT` and carries no human identity. We are filling a gap.

**Options:**

- (a) **Our gut shape** (see [`auth-design.md`](./auth-design.md) §4.1): nest under `acting_for: {human_sub, human_session_id, consent_scope}`. Easy, but if other A2A implementers converge on a different shape we break interop later.
- (b) **Adopt OAuth2 Token Exchange (RFC 8693) claim names**: `act: {sub, ...}` (actor claim). More standard, but `act` is normally used for the actor that's *performing* the action, not the human delegator — semantic mismatch.
- (c) **Survey the A2A working group / Google A2A SDK examples** for an emerging convention, then adopt it. Best long-term, requires 1-2 days of WG-list digging that the spike doesn't have budget for.

**Spike assumption** (absent answer): **(a)** — proceed with `acting_for` shape, documented as our convention. Surface to A2A WG post-spike.

**Assumes-by:** Day 5 (the day auth lands).

**Cost of being wrong:** A future rewrite of the JWT claim layout and a coordinated cutover with each peer.

---

## Q2 — Which canary peer do we target?

**Decision needed:** What's the "one canary peer" we integrate with for end-of-spike?

**Options:**

- (a) **Stub peer**: a minimal in-house FastAPI app implementing just the methods we need. Fastest. Proves the wire format works; proves nothing about real-world interop.
- (b) **Google's reference `a2a-python` SDK example agent**: deployable from their repo. Proves we interop with the canonical implementation. Higher setup cost.
- (c) **A real-world peer at a partner team / org** (e.g. a Google production agent we have a relationship with). Proves the most, but requires sponsor to broker the relationship and the partner to be ready.

**Spike assumption** (absent answer): **(a)** stub peer for Days 1-9, **(b)** reference SDK as a stretch goal on Day 10. Defer (c) to a v2 spike.

**Assumes-by:** Day 9 (canary peer hookup).

**Cost of being wrong:** If sponsor wants (c) and we did (a), we'll need to redo Day 9 against the real peer.

---

## Q3 — Federation: non-GCP peers

**Decision needed:** Do we need to support peers that don't run on GCP, and therefore sign JWTs with their own keys (not Google-hosted JWKS)?

**Context:** The Gemini-recommended pattern relies on the Google-hosted JWKS at `googleapis.com/service_accounts/v1/jwk/{SA}`. This is great for GCP↔GCP. For non-GCP peers (AWS, Azure, on-prem), we'd need to (a) fetch their JWKS from a URL they publish, and (b) decide how we establish initial trust in that URL.

**Options:**

- (a) **GCP-only for spike + v1.** Defer multi-cloud federation to a future spike. Reasonable if our roadmap is "Google production use-case at scale" (per the 2026-05-20 priority correction).
- (b) **AgentCard-driven federation**: peer publishes JWKS URL in their AgentCard; we trust their AgentCard signature transitively. Requires solving the AgentCard trust ceremony (first-sight problem).
- (c) **Static config of peer JWKS URLs** in our `config/a2a/peers.yaml`. Simple, scales to ~10 peers.

**Spike assumption** (absent answer): **(a)** GCP-only. If a non-GCP peer materializes in the spike window, we'll add a hard error message naming the gap.

**Assumes-by:** Day 5.

**Cost of being wrong:** Future spike needed when first non-GCP peer arrives. Not a blocker.

---

## Q4 — `Allow unauthenticated` Cloud Run for the A2A endpoint — security/privacy sign-off

**Decision needed:** Does our security/privacy review accept Cloud Run with `Allow unauthenticated`, given that we do auth in the application layer with JWT verification and emit explicit audit logs?

**Context:** [`auth-design.md`](./auth-design.md) §3.2 — Gemini flagged that this approach loses *native* GCP IAM Data Access logs and pushes audit to the app layer. We propose to mitigate with structured audit logs that hit Cloud Logging via stdout JSON.

**Options:**

- (a) **Accept**: the app-layer audit log meets HIPAA requirements as long as we route to an immutable log bucket via Log Sink.
- (b) **Reject**: require mTLS or IAM-bound invoker auth at the Cloud Run edge. This breaks the composite-identity model (Cloud Run native auth doesn't carry our custom claims), so we'd need to layer JWT on top of IAM-bound auth.
- (c) **Conditional accept**: accept for spike + dev, require (b) for prod.

**Spike assumption** (absent answer): **(c)** — Cloud Run with `Allow unauthenticated` for spike + dev. Document the prod gap explicitly.

**Assumes-by:** Day 5.

**Cost of being wrong:** Day 10 hand-off blocked on a security re-review. Worst case, we pivot the prod plan to mTLS and the spike's value drops to "we proved the protocol works."

---

## Q5 — PHI in the JWT `acting_for.human_sub`

**Decision needed:** Is the human identifier we put in the JWT (e.g. `user:dmanzela@example.com`) PHI under our BAA?

**Context:** If the human is a patient or clinician identified by email, that email + the fact-of-interaction is potentially PHI. The JWT is signed and traverses a network; signed ≠ encrypted-at-rest in logs. Cloud Logging entries containing the JWT or its decoded claims are subject to HIPAA controls.

**Options:**

- (a) **Treat as PHI**: replace with opaque IDs (`pseudonym:abc123`); store the mapping in a sealed table accessible only to a designated subsystem.
- (b) **Treat as non-PHI**: emails are PII but not PHI on their own; the fact-of-interaction is operational metadata, not health information. Requires sign-off from privacy officer.
- (c) **Mixed**: opaque IDs for patient/clinician categories; raw IDs for internal staff (devs, ops).

**Spike assumption** (absent answer): **(a)** — opaque IDs only. Worst-case safe.

**Assumes-by:** Day 5.

**Cost of being wrong:** If (b) is allowed, we made things harder than necessary; not a security issue, just dev friction. If we'd assumed (b) and the real answer is (a), we'd have a HIPAA finding.

---

## Q6 — Compromise / break-glass plan for an agent SA

**Decision needed:** What's the operational plan when an agent SA's signing key is suspected compromised?

**Context:** Google rotates keys ~biweekly, but rotation is preventive, not reactive. If an SA is compromised (e.g. accidental key export in a log), we need to (a) revoke the SA immediately, (b) tell every peer to stop accepting tokens from it, (c) re-issue a new SA.

**Options:**

- (a) **Manual runbook**: ops disables the SA via gcloud; manual notification to peers (email).
- (b) **Allowlist hot-reload**: each peer watches their `config/a2a/peers.yaml` for changes; removing the issuer from the file causes immediate rejection.
- (c) **Agent revocation registry**: a shared (per-org) registry of revoked issuers that every peer polls. Industry-standard but a real engineering investment.

**Spike assumption** (absent answer): **(a)** manual runbook documented in the hand-off note. Defer (b)/(c) to v2.

**Assumes-by:** Day 10.

**Cost of being wrong:** None for spike. For prod, (a) is acceptable for low call volumes but unacceptable at scale.

---

## Q7 — SSE event-level traceparent convention

**Decision needed:** When a peer sends an SSE event from within its own sub-span, should it include a per-event `traceparent` so we can attach our per-event child span to the right parent?

**Context:** Detailed in [`telemetry-design.md`](./telemetry-design.md) §9. The A2A spec is silent. Reference SDKs don't do it. We propose `_meta: { traceparent: "..." }` in the event JSON.

**Options:**

- (a) **Implement our convention unilaterally** + propose to the A2A WG. We benefit immediately on the receive side from any peer that adopts it; we send it always.
- (b) **Don't bother** — accept that SSE events all share the stream-scope parent. Loses per-event causality.
- (c) **Wait for the WG to standardize.** Indefinite.

**Spike assumption** (absent answer): **(a)** — implement it, document it, prepare a WG proposal.

**Assumes-by:** Day 6.

**Cost of being wrong:** Minimal; the receive path tolerates absence gracefully.

---

## Q8 — Scrubber bypass — fix in spike or document as known gap?

**Decision needed:** The existing `lib/scrubber.py` only runs on outbound LiteLLM calls; A2A messages flow through `lib/a2a/client.py` and bypass it. ([`integration-points.md`](./integration-points.md) §10 flagged this as **P0**.)

**Options:**

- (a) **Fix in spike Day 9** by explicitly calling `scrubber.scrub(...)` from both `client.py` (before sending) and `server.py` (before logging/spanning the inbound body). Cheap, ~half day.
- (b) **Document as known gap**, ship the spike with scrubber bypass, file a P0 ticket for follow-up. We accept that the spike's audit logs may contain unredacted PHI.
- (c) **Refactor `lib/scrubber.py` to be a Hermes middleware** that catches *all* outbound IO, not just LiteLLM. Right answer architecturally, but ~3-5 days of work that isn't in the spike scope.

**Spike assumption** (absent answer): **(a)** — fix narrowly in Day 9, file a follow-up for (c).

**Assumes-by:** Day 9.

**Cost of being wrong:** If sponsor wanted (c), spike rejected for "didn't fix the root cause." Likely tolerable given the spike's purpose is to prove interop, not to fix the scrubber.

---

## Q9 — Feature flag rollout: who flips `HERMES_A2A_ENABLED`?

**Decision needed:** Post-spike, who has authority to flip `HERMES_A2A_ENABLED=1` in dev / staging / prod?

**Options:**

- (a) **Engineering on-call** flips it freely in dev/staging; sponsor + privacy officer co-sign for prod.
- (b) **Sponsor only**, all environments.
- (c) **Anyone**, all environments. We trust the feature flag to gate everything.

**Spike assumption** (absent answer): **(a)**. Dev/staging is the spike's playground; prod requires the documented gates from §3 of [`spike-plan.md`](./spike-plan.md).

**Assumes-by:** Day 10 (hand-off note records the answer).

**Cost of being wrong:** Operational friction; not a security issue if (a) holds.

---

## Q10 — Hermes-agent submodule readiness

**Decision needed:** Before Day 1, can we populate the `hermes-agent/` submodule in this worktree?

**Context:** [`integration-points.md`](./integration-points.md) §1 — the submodule is empty here, and several line references in our integration map come from comments in `lib/durability/*` that quote a pinned Hermes SHA. Without the live submodule we can't verify the hooks land in the right place.

**Options:**

- (a) **Populate now** (5-min `git submodule update --init`). Spike-blocker resolved at zero cost.
- (b) **Skip and verify line references on Day 1** as part of the scaffolding deliverable.

**Spike assumption** (absent answer): **(a)** — populate before Day 1.

**Assumes-by:** Day 0 (kickoff).

**Cost of being wrong:** Day 1 burns 2-4h debugging stale line references.

---

## Q11 — Spike duration adjustability if Day-4 SSE blocks us

**Decision needed:** If Day 4 (SSE streaming) goes over by 1.5 days into Day 6, do we (a) halt per §5 of [`spike-plan.md`](./spike-plan.md), or (b) compress later days to absorb the slip?

**Options:**

- (a) Halt per the kill criterion. Force a sponsor decision.
- (b) Compress Days 7-10 by dropping AgentCard signing (Day 8 → unsigned) and skipping Day 9 polish for the canary peer. Get to Day-10 demo with reduced scope.

**Spike assumption** (absent answer): **(a)** — strict adherence to the kill criterion. We discover real architectural issues fast, not by burning timebox.

**Assumes-by:** Day 4.

**Cost of being wrong:** If (b) was wanted, sponsor decides on Day 6.

---

## Q12 — Spike sponsor + reviewer assignment

**Decision needed:** Who is the spike sponsor (final accept/reject authority) and who is the daily reviewer (30-min sync to unblock)?

**Spike assumption** (absent answer): Sponsor = architecture lead (per CLAUDE.md context). Reviewer = same person. If sponsor and reviewer should be different, name both.

**Assumes-by:** Day 0.

**Cost of being wrong:** Hand-off cycle slows by 1-2 days.

---

## Open meta-question

**Q-meta:** Is the 10-day timebox a hard commitment, or a budget to be revisited at Day 5 with halfway-mark data?

**Spike assumption** (absent answer): Hard commitment. Day 5 has a status sync but not a re-plan checkpoint.

**Assumes-by:** Day 0.

---

## Summary of assumed defaults (if no answers arrive)

| Q | Assumed answer |
|---|---|
| Q1 | `acting_for: {...}` shape; surface to A2A WG post-spike. |
| Q2 | Stub peer for spike; reference SDK as stretch. |
| Q3 | GCP-only; defer multi-cloud federation. |
| Q4 | `Allow unauthenticated` Cloud Run for dev/spike only; flag prod as TODO. |
| Q5 | Opaque IDs for human_sub. |
| Q6 | Manual revocation runbook in hand-off note. |
| Q7 | Implement `_meta.traceparent` SSE convention unilaterally. |
| Q8 | Fix scrubber bypass narrowly in Day 9; file refactor as follow-up. |
| Q9 | Engineering on-call flips dev/staging; sponsor+privacy officer for prod. |
| Q10 | Populate hermes-agent submodule before Day 1. |
| Q11 | Halt strictly per §5 kill criterion. |
| Q12 | Sponsor = architecture lead; reviewer same. |
| Q-meta | Hard 10-day commitment. |

**Total decisions deferred to the implementer's good judgment if no sponsor input arrives: 13.** That's high. Treating this as a risk: if even 3 of these defaults are wrong, the spike's value to the sponsor drops materially.

---

## References

- [`protocol-survey.md`](./protocol-survey.md)
- [`integration-points.md`](./integration-points.md)
- [`auth-design.md`](./auth-design.md)
- [`telemetry-design.md`](./telemetry-design.md)
- [`spike-plan.md`](./spike-plan.md)
