# Stream B open-questions disposition

**Date:** 2026-05-20
**Decided by:** Daniel Manzela (via AskUserQuestion + text correction)
**Source questions:** `BRIEFING_GEMINI_STREAM_B.STATUS.md` (4 briefing questions, a subset) + ADR-0008 §"Open Questions" (8 questions — authoritative)
**Author:** Claude (wt-framing-2)
**Numbering:** This memo uses ADR-0008's Q1-Q8 numbering throughout. The briefing's 4 questions map to ADR Q1, Q3, Q4, Q8.
**Worktree note:** ADR-0008 lives on `research/framing-1-moe-rl-spike` (Stream B branch). This disposition memo lives on `feat/framing-2-bolt-on` (this worktree) because Framing #2 owned the verification + decision-forcing process. ADR-0008 must be amended on its native branch in a follow-up commit.

---

## 1. Decisions (all 8 ADR-0008 open questions)

| # | Question | Decision | Rationale source |
|---|----------|----------|------------------|
| Q1 | Monthly GPU budget for Phase 4 Unsloth-RL experiments | **$5,000/mo (Aggressive)** | User 2026-05-20 |
| Q2 | RL Framework standardization (Q1 Phase 4) | **Unsloth-RL only (single-node)** | User 2026-05-20 |
| Q3 | SQLite → PostgreSQL/pgvector migration timing | **Phase 2 — prioritize as foundation** | User 2026-05-20 |
| Q4 | A2A (inter-agent coordination) priority | **PRIORITY — Google production scale is a MUST** (NOT deferred) | User 2026-05-20 — **OVERRIDES J8 memo `48bad41`** |
| Q5 | Sandbox tiers — gVisor/Firecracker urgency | **Firecracker for high-risk tools only** (Docker for the rest) | User 2026-05-20 |
| Q6 | J1 trajectory shipper (judge verdicts as RLAIF substrate) | **Approve immediately + Model Armor SDP scrub gate (mandatory)** | User 2026-05-20 |
| Q7 | Metacog Governor (Component 7) scope | **Failure-matrix now (J4), standalone Governor in Phase 3** | User 2026-05-20 |
| Q8 | Telemetry dialect (OpenInference vs OTel GenAI) | **Dual-emit both (J11 shipped)** | J2/J11 implementation |

---

## 2. Strategic implications

### Q1 + Q2 — GPU budget + Unsloth-RL standardization

**Scale interpretation (from Stream B `01-gpu-runtime-survey.md`):**
- ~$5,000/mo ≈ ~100 GPU-hours/mo of A100 80GB at $1.50/hr (RunPod) or ~50 hrs at $3/hr (Lambda Labs).
- Equivalent to **continuous always-on capacity** for a single A100 ~3 days/week.
- Enables daily-cadence experiments on 7B-70B QLoRA + the full "Phase-Aware Router POC" from synthesis §"Minimum-Viable Framing #1 Slice" (8x1B or 4x3B MoE with 500+ trajectories per run).

**Combined infra envelope:**
- Current GCP infra budget: **$7,750/mo** (per `099bad8 fix(terraform): raise billing budget to $250/day`).
- Plus Q1 GPU budget: **$5,000/mo**.
- **Total ceiling: $12,750/mo.** Confirm this fits overall project budget before Phase 4 trigger.

**Q2 — Unsloth-RL only (no OpenRLHF in Q1 Phase 4):**
- Single-codepath: avoids dual-framework maintenance burden during early experiments.
- A100 80GB sufficient for 7B QLoRA + GRPO (Unsloth-RL fits in ~15GB VRAM for 7B, ~48GB for 70B).
- OpenRLHF (Ray-based multi-node) deferred until Unsloth-RL hits a hard ceiling — likely at 70B+ scale or multi-tenant training fleets.
- Trade-off accepted: slower scale-up path, but lower setup cost and faster Q1 iteration.

**Follow-up actions:**
1. **No spend until Phase 4 trigger** — J1 trajectory collection (see Q6) must accumulate first; the budget is "approved capacity" not "active draw". Avoids burn on empty experiments.
2. **Provider selection** — RunPod ($0.50/hr A6000, $1.50/hr A100) vs Lambda Labs vs GCP H100. Stream B survey recommends RunPod for cost-per-experiment; deferred to Phase 4 start.
3. **Budget alert wiring** — when Phase 4 starts, add a GCP Billing Alert at $4,000/mo + Telegram escalation (parallels existing F21 daily-budget pattern in failure matrix).
4. **Single-framework lock-in** — Phase 4 plan must explicitly forbid OpenRLHF in Q1 to prevent scope creep into dual-framework maintenance.

**Constraint:** GPU spend must be **per-experiment metered** and tagged to allow attribution to specific Unsloth-RL runs. No always-on idle GPUs.

### Q3 — Postgres migration in Phase 2 (foundation)

**Scope (from Stream B `03-memory-architecture-pocs.md` recommendation):**
- Migrate **SQLite kanban → PostgreSQL** as the primary durability substrate.
- Introduce **pgvector extension** for the future memory layer (episodic/semantic/procedural split per the hybrid memory architecture).
- **Single Postgres instance** serves both purposes; no separate datastores.

**Work estimate:**
- Schema design + migration scripts: ~3 days.
- Data migration (live SQLite → Postgres with downtime window): ~2 days.
- Test suite updates (parametrize against Postgres test container): ~2 days.
- Ops setup (managed Postgres on GCP Cloud SQL or self-hosted on existing VM): ~1 day.
- **Total: ~1.5 weeks of focused work.**

**Phase 2 sequencing:**
1. **Start of Phase 2** — provision Postgres before any other Phase 2 feature work.
2. Subsequent Phase 2 features (multi-agent, observability, memory, A2A) build on Postgres from day 1.
3. Avoids the trap of building 3-5 features against SQLite then doing a forced migration that breaks all of them.

**Open sub-decision (NOT blocking this disposition):**
- **Managed (GCP Cloud SQL) vs self-hosted (on the existing always-on VM)?** Cloud SQL is ~$50-200/mo for small instances; self-hosted is free but adds ops burden. **Recommend Cloud SQL** for Phase 2 to minimize ops; revisit at Phase 3 scale.

**Risk:** Phase 0a (GCP migration) is still landing. Postgres migration in Phase 2 must NOT block on Phase 0a being declared "done" — Postgres can be provisioned in parallel since it's a new service, not a modification to the existing VM.

### Q4 — A2A as production priority (OVERRIDES J8)

**Decision:** A2A (Agent-to-Agent inter-agent coordination) is a **priority**, NOT deferred. Justification: Google production use case at scale is a MUST.

**Implications:**
- **Supersedes J8 memo `48bad41`** (which recommended deferring A2A indefinitely on grounds that ACP (Zed) ≠ A2A (Google) and we had no single-agent-scale use case). User has confirmed a forthcoming Google production use case that makes A2A a hard requirement.
- **Component 8 in ADR-0008's gap table upgrades** from "40% (MCP only)" to **active H2 2026 work item**.
- A2A is orthogonal to MCP, not a replacement: MCP = tool/resource access for a single agent; A2A = inter-agent coordination across multiple agents.
- Production-scale A2A requires: agent discovery, capability negotiation, message routing, auth/identity propagation across agent boundaries, observable cross-agent trace correlation.

**Follow-up actions:**
1. **Re-open J8** — author **J8.v2 memo** as a positive specification (not a deferral), detailing A2A integration scope, threat model, and production requirements.
2. **ADR-0008 amendment** — update Component 8 row in gap table from "40% MCP only" to "active work item — A2A integration in Phase 2/3 H2 2026".
3. **New work-packet** — scope a 3-subagent investigation (mirroring the Framing #1 spike pattern):
   - (a) A2A protocol survey + Google spec review.
   - (b) Production reference implementations (Google reference impl, LangGraph multi-agent, AutoGen agent communications, CrewAI delegation patterns).
   - (c) Auth/identity propagation patterns for multi-agent systems (mTLS, JWT-with-agent-claims, workload identity federation on GCP).
4. **Phase sequencing** — A2A spike should start in early Phase 2 (parallel with Postgres provisioning per Q3) so H2 2026 implementation has a vetted spec to build on.
5. **Cross-link to Q7** — A2A is the eventual trigger for the standalone Governor service (deferred to Phase 3 in Q7). Governor design should explicitly account for observing inter-agent A2A traffic.

**Constraint:** A2A as priority must NOT collapse the rest of Phase 2's scope. Time-box the A2A spike to ~2 weeks. If the spike surfaces blockers (eg auth integration with our existing Vertex/Honcho stack), surface them immediately rather than letting Component 8 become the Phase 2 critical path.

### Q5 — Firecracker for high-risk tools (Docker for the rest)

**Scope:**
- Identify highest-risk tools in `config/toolsets.yaml` (likely candidates: `shell_sandbox`, arbitrary code execution tools, file-system write tools, network-egress tools).
- Migrate **only those high-risk tools** to Firecracker microVMs (VM-grade isolation, ~125ms boot).
- All other tools remain on Docker (current 5-tier toolset config).

**Work estimate:** ~3 weeks of focused work.
1. Threat model: tool classification (high/medium/low risk) — ~3 days.
2. Firecracker host setup on existing GCP VM (or dedicated bare-metal-equivalent host) — ~3 days.
3. High-risk tool migration (4-6 tools): ~1 week.
4. Failure-matrix update: new F-codes for Firecracker-specific failures (eg VM startup timeout, microVM crash, Firecracker API unavailable) — ~2 days.
5. Integration testing + rollback procedure — ~3 days.

**Open sub-decision (NOT blocking this disposition):**
- **Firecracker on GCP** requires nested virtualization or running on bare-metal nodes. Likely path: dedicated bare-metal-equivalent VM (eg `n2-standard-8` with nested-virt enabled, or `c3-bare-metal-*` instances). Confirm at H1 2026 planning.

**Cross-link:** Q4 (A2A priority) does NOT change the sandbox calculus directly, but inter-agent message routing across a Firecracker boundary needs explicit auth — fold into the A2A spike's "auth/identity propagation" leg.

### Q6 — J1 trajectory shipper + Model Armor PII scrub gate (mandatory)

**Approve J1 immediately** (judge verdicts persisted to `trajectories/judge-events.jsonl` as RLAIF substrate) **WITH** the following critical configuration to prevent PII leakage into long-term training data:

**Model Armor configuration (mandatory before J1 production launch):**

1. **Floor Settings at Project Level** (or Folder Level if multiple judge envs):
   - Activate floor settings on the GCP project hosting trajectory infrastructure.
2. **SDP (Sensitive Data Protection) profile:**
   - Inspection template: **`INSPECT_AND_REDACT`**.
   - InfoTypes: `EMAIL_ADDRESS`, `CREDIT_CARD_NUMBER`, `PHONE_NUMBER`, `US_SOCIAL_SECURITY_NUMBER` (standard PII baseline; expand as additional risk surfaces are identified).
3. **Confidence threshold:** **"Low and above"** — aggressive over-redaction is acceptable here because this is offline training data, NOT user-facing inference. False positives cost nothing; false negatives cost compliance.

**Critical: The Persistence Trap (architectural invariant)**

Model Armor is a runtime shield — it sanitizes data **in transit** between application and model. Pipeline order is everything:

- **Correct path:** `User Input → Model Armor Sanitize API → J1 Judge → Shipper → GCS Bucket`
  - The prompt reaching the judge is already sanitized; the shipper persists only the sanitized version. Safe.
- **Dangerous path:** `User Input → Shipper (saves raw) → J1 Judge`
  - If the shipper captures the raw payload BEFORE Model Armor sees it, Floor Settings do NOT save you. PII is persisted to long-term storage indefinitely.

**Verification gate before J1 launch:**
- Trajectory shipper service MUST capture payload **post-inference** (after Model Armor has redacted), OR
- Explicitly call `Model Armor templates.sanitize` method on the captured payload before writing JSONL.

Either path is acceptable. Both must be code-reviewed and tested.

**Blockers cleared / still open for J1 launch:**
- ✅ User approval received (this disposition).
- ⏳ **GCS bucket provision** (Task #12, existing blocker).
- ⏳ **Model Armor Floor Settings + SDP template configured at GCP project level** (NEW blocker — must complete before first trajectory write).
- ⏳ **Pipeline verification** — confirm trajectory shipper data flow captures post-inference (or explicitly invokes sanitize) (NEW blocker).

**Follow-up actions:**
1. Add **Task #12.b**: "Configure Model Armor Floor Settings + SDP profile at GCP project level (`INSPECT_AND_REDACT`, infoTypes baseline, confidence: Low+)" — blocks J1 launch.
2. Add **Task #12.c**: "Verify J1 trajectory shipper captures payload POST-inference or explicitly calls Model Armor sanitize API; add integration test for the Persistence Trap pattern" — blocks J1 launch.
3. Update J1 implementation spec (when written) to require **sanitization-before-persistence as an architectural invariant**, with a test that fails-loud if raw user input reaches the JSONL writer.
4. Add an F-code for "Model Armor sanitize API unavailable" — likely **Fail-Loud** (data integrity > availability). Wire into the failure matrix `lib/durability/failure_matrix.py`.

**Constraint:** No trajectory writes in any environment (dev, staging, prod) until both Model Armor config AND pipeline verification are complete. This is non-negotiable — a single PII-leaking trajectory persisted to long-term storage is a compliance incident.

### Q7 — Governor scope: failure-matrix now, standalone in Phase 3

**Decision:** Keep the current J4 path (F34/F35/F36 detectors in `lib/durability/runtime_detectors.py`) for H1 2026. Plan a standalone **Metacog Governor service** for Phase 3 when multi-agent coordination (Q4 A2A) demands centralized behavioral observability.

**Rationale:**
- J4 detectors already shipped — F34 (F-LOOP), F35 (F-STALL), F36 (F-CONTEXT) cover the immediate behavioral-anomaly surface.
- Standalone Governor requires a full MAPE-K loop service (~4-6 weeks new service) — overkill for single-agent H1 scope.
- A2A-as-priority (Q4) creates the eventual trigger: multi-agent coordination needs centralized cross-agent behavioral telemetry that the in-process failure-matrix cannot provide.

**Follow-up actions:**
1. **Phase 3 planning** — design standalone Governor service spec (MAPE-K loop, behavioral anomaly detection, cross-agent telemetry aggregation, A2A traffic observability).
2. **Sequence Governor AFTER A2A integration** — Governor needs A2A traces to observe inter-agent traffic; building Governor first would target an empty data plane.

---

## 3. Q8 — already shipped (no additional work)

### Q8 — OTel dual-emit shipped
- **Reference:** J11 implementation (rolled into J2 commits — see `lib/observability/otel_setup.py` + span attribute helpers).
- **Why dual-emit not migration:** Both OpenInference (`llm.*`) and OTel GenAI (`gen_ai.*`) consumers see their native fields. Phoenix (OpenInference-native) and any future OTel-GenAI native consumer both work without translation shims.
- **Cost:** One extra attribute dict per span (~30 bytes overhead). Negligible.

---

## 4. Follow-up actions (queued)

| Action | Owner | When | Blocks |
|---|---|---|---|
| Amend ADR-0008 with all 8 dispositions (esp. Q4 A2A correction overriding J8) | Next session on `research/framing-1-moe-rl-spike` | Before Stream B merges | Stream B PR |
| Author **J8.v2** memo (A2A as positive spec, not deferral) | Next session on `research/framing-1-moe-rl-spike` | With ADR-0008 amend | ADR-0008 alignment |
| Scope A2A integration spike (3-subagent investigation per Q4) | Phase 2 planning session | Start of Phase 2 (parallel with Postgres) | A2A work-packet |
| Phase 2 plan: add Postgres provisioning as first work-packet | Phase 2 planning session | Start of Phase 2 | All Phase 2 feature work |
| Phase 4 plan: confirm $5K/mo GPU envelope + billing alert at $4K + Telegram escalation | Phase 4 planning session | Phase 4 trigger | RL training runs |
| Phase 4 plan: confirm Unsloth-RL single-node standardization (no OpenRLHF in Q1) | Phase 4 planning session | Phase 4 trigger | RL framework setup |
| H1 plan: scope Firecracker migration for high-risk tools (~3 weeks) | H1 planning session | After Phase 2 ships | Sandbox hardening |
| **Task #12.b**: Configure Model Armor Floor Settings + SDP profile at GCP project level | DevOps / Phase 2 | Before first J1 trajectory write | J1 launch |
| **Task #12.c**: Verify trajectory shipper captures payload post-inference (or calls sanitize); add Persistence Trap integration test | J1 implementation | Before J1 launch | J1 launch |
| Add F-code for "Model Armor sanitize API unavailable" to `lib/durability/failure_matrix.py` (Fail-Loud) | J1 implementation | With J1 launch | n/a |
| Phase 3 plan: design standalone Governor service spec (MAPE-K, A2A traffic observer) | Phase 3 planning session | When A2A multi-agent traffic begins | Component 7 standalone |
| Update memory `project_state_*.md` with these 8 strategic decisions | This session | Now | n/a |

---

## 5. Verification

Per `superpowers:verification-before-completion`:
- **Q1, Q2, Q3, Q5, Q6, Q7** decisions: captured via `AskUserQuestion` UI 2026-05-20; user answers explicit and recorded in tool result.
- **Q4 decision:** user corrected via text reply 2026-05-20 ("(A2A priority) is a priority (Google Production use case at scale is a MUST) - do not defer it"). **Overrides** J8 memo `48bad41`.
- **Q6 implementation spec:** user provided detailed Model Armor configuration (Floor Settings, SDP `INSPECT_AND_REDACT`, infoTypes baseline, "Low and above" confidence threshold) AND the Persistence Trap verification gate. Captured verbatim in §2 Q6.
- **Q8 disposition:** J11 dual-emit verified by `tests/unit/test_otel_genai_attrs.py` (in V3 test suite — 422 passed).

---

## 6. Memory updates required (next step in this session)

Per the auto-memory system (`/Users/danielmanzela/.claude/projects/.../memory/`), the following strategic dispositions must be persisted so future sessions don't re-litigate them:

1. **GPU budget $5K/mo + Unsloth-RL standardization** (Q1+Q2) — project memory.
2. **Postgres Phase 2 priority** (Q3) — project memory.
3. **A2A as production priority — OVERRIDES J8** (Q4) — project memory + feedback memory (user correction during this session).
4. **Firecracker for high-risk tools in H1** (Q5) — project memory.
5. **J1 + Model Armor SDP gate (Persistence Trap)** (Q6) — project memory + reference memory (Model Armor config pointer).
6. **Failure-matrix now, standalone Governor in Phase 3** (Q7) — project memory.
