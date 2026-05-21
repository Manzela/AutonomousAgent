# Outstanding-Threads Roadmap — 2026-05-21

**Status:** Draft for user review (post-brainstorming, pre-implementation-plan).
**Skill chain:** `superpowers:brainstorming` → THIS SPEC → `superpowers:writing-plans` → `superpowers:executing-plans`.
**Verification:** Every "Evidence" line below is backed by a fresh check run in this session. Authoritative provenance: `audit/2026-05-21-verification-synthesis.md` (HEAD `7678746`).
**Scope statement:** Forward-looking roadmap for the work that is still open as of 2026-05-21, after the audit-closure sprint landed (commits `f5d404c…7678746`, 35 commits ahead of `main` on branch `feat/framing-2-bolt-on`). Prior-sprint claims are NOT re-verified here.

## Context

The Framing #2 sprint closed the following with verified evidence (see `audit/2026-05-21-verification-synthesis.md` for the 15-row per-claim table):

- All 7 architecture-research gap-closure work streams have written specs (`audit/2026-05-21-*/`)
- Persistence Trap (#12.c) code shipped (`a847f1a`) + 8/8 contract tests pass (`38856f2`)
- J9 observability: gauge `agent.memory.context_usage_pct` at `lib/durability/runtime_detectors.py:51`; detector wiring at `01e6584`; MeterProvider at `56336f5`
- F34/F36 production handlers (`a20dd58`)
- Phase 2 Postgres sub-module at `terraform/phase-0a-gcp/postgres/` (plan-only, terraform-validated, `8cf3270`)
- Model Armor sub-module at `terraform/phase-0a-gcp/model-armor/` (plan-only, terraform-validated, `0911028`)
- ADR-0010 Firecracker sandbox tier at `docs/decisions/0010-firecracker-sandbox-tier.md` (`d94ec3e`)
- Phase 3 Governor design at `audit/2026-05-21-phase3-governor/` (`33dd934`)
- Verification synthesis at `audit/2026-05-21-verification-synthesis.md` (`7678746`)
- Phase 0a deploy CI workflow `.github/workflows/phase-0a-deploy.yml` exists and has run successfully twice (runs `26186474575` 5m52s + `26186461876` 3m5s) — **AC-7 verified closed this session** (was incorrectly listed as open in draft tiering)

What remains: 9 threads enumerated below, tier-assigned by criticality, each with verifiable evidence + acceptance criteria + unblock conditions.

## Decision summary

| Tier | Threads | # | Authorization-gated | Cost-triggering | Critical-path |
|---|---|---|---|---|---|
| **P0** | #1, #2 | 2 | 1 (PR open) | 0 | yes |
| **P1** | #3, #4, #5 | 3 | 3 | 1 (~$1,611/mo total) | yes |
| **P2** | #6, #7 | 2 | 1 | 0 | no |
| **P3** | #8, #9 | 2 | 0 (correctly deferred) | 0 | no (trigger-gated) |

**Total active backlog:** 7 threads (P0 + P1 + P2). **Correctly-deferred:** 2 threads (P3).

## Sequencing principles (autonomous-agent execution best practice)

1. **Mechanical close-outs first** — P0 clears the slate without decisions, batches Stream B test hardening into the same PR
2. **Bundle authorization asks** — one user touch covers Thread #1 (PR open) + Thread #4 (Stream A GO) + Thread #5 (Persistence Trap approval) so the user isn't pinged five times
3. **Parallel where dependencies don't bind** — A2A spike Day 0 prep (Hermes submodule check) runs in parallel with Stream A apply
4. **GCP delegation honored** — every Stream A apply step goes through Gemini-CLI per standing directive, never direct `terraform apply` from this session
5. **Trigger-gated work stays gated** — Firecracker (#8) and Governor (#9) get cards but explicit "no action" recommendations
6. **Verify before claim** — every action terminates with a verification command, not an assertion

---

## Tier P0 — Mechanical close-out, no decisions

### Thread #1 — Land outstanding PRs

**Tier:** P0
**Status:** Open
**Evidence (verified):**
- Branch `feat/framing-2-bolt-on` at HEAD `7678746`; 35 commits ahead of `main` (`git log --oneline main..HEAD | wc -l` = 35, this session)
- Working tree clean (`git status --short` = empty, this session)
- 33 audit-closure commits + 1 format-fix (`a1f9a2d`) + 1 synthesis (`7678746`) verified individually in `audit/2026-05-21-verification-synthesis.md` rows 4-7
- PR #112 last known state: "draft, all 17 CI green on `ac5a7189`" per memory `project_state_2026-05-20_phase0a.md` (2026-05-20) — **not re-verified this session**

**Gap:**
- (a) PR #112 current status unknown (may have merged, may have stale conflicts vs current `main`)
- (b) `feat/framing-2-bolt-on` has no open PR

**Acceptance criteria:**
- `gh pr view 112 --json state,statusCheckRollup,mergeable` returns concrete state
- If PR #112 mergeable + checks green: squash-merge per repo convention
- `gh pr create --base main --head feat/framing-2-bolt-on --title "<conv-commit>: <summary>" --body "<…>"` returns a PR URL
- New PR CI all green within 10 min
- Both PRs squash-merge cleanly (squash-only per `repo_workflow_constraints` memory)

**Unblock:** User authorization (verbatim): "open PR for `feat/framing-2-bolt-on` and verify/merge PR #112 if green."

**Recommendation:** Single bundled action — verify #112 first (may already be merged), then open framing-2 PR with verification synthesis as the body anchor.

**Cost / risk:** $0; risk = stale PR #112 may have merge conflicts vs current main (mitigation: rebase or re-open from current state).

**Cross-refs:** `audit/2026-05-21-verification-synthesis.md`; MEMORY `project_state_2026-05-20_phase0a.md`, `repo_workflow_constraints.md`.

---

### Thread #2 — Docker-test skip-guard hardening

**Tier:** P0
**Status:** Open (no auth needed — Stream B test hardening only)
**Evidence (verified):**
- Failing test in synthesis row 1 = `test_shell_sandbox_no_root_fs_write` at `tests/integration/test_sandbox_isolation.py:30-48` per subagent V4 this session
- Root cause CORRECTED this session: test needs Docker Compose stack via `deploy/docker-compose.yml`, NOT `secrets/litellm-db.env` (synthesis doc misattributed)
- No `@pytest.mark.docker` decorator + no `_docker_available()` probe currently
- `_proxy_reachable()` skip-guard pattern exists in sibling test `test_p1_6_failure_matrix.py:pytestmark` — proven pattern to mirror
- `pyproject.toml` does not register `docker` marker (V4 noted cosmetic warning)

**Gap:** Test FAILS hard locally when Docker stack absent; cleanly-skipping pattern not applied.

**Acceptance criteria:**
- Add `@pytest.mark.docker` + `_docker_available()` skip-probe to `test_sandbox_isolation.py:30-48` (mirror `_proxy_reachable()` pattern)
- Register `docker` marker in `pyproject.toml`
- Docker stack absent: `uv run --extra dev pytest tests/integration/test_sandbox_isolation.py -q` → all tests show SKIPPED
- Docker stack present: existing assertions still pass
- CI workflow (`phase-0a-deploy.yml` already brings up the stack) continues to execute the assertions, not skip
- `audit/2026-05-21-verification-synthesis.md` updated row 1 (or addendum) to correct root cause

**Unblock:** None — within Stream B authorized scope.

**Recommendation:** Bundle into Thread #1 PR so single review + single CI run covers both. ~30 min implementation.

**Cost / risk:** $0; risk = mask Docker regression in CI if CI ever stops bringing up the stack. Mitigation: existing `phase-0a-deploy.yml` job already brings up the stack; add an explicit CI check that asserts the test runs (not skips) when `RUN_DOCKER_TESTS=1`.

**Cross-refs:** V4 subagent report (this session); `audit/2026-05-21-verification-synthesis.md`.

---

## Tier P1 — Active, authorization-gated, critical path

### Thread #3 — A2A spike kickoff (Google production MUST)

**Tier:** P1
**Status:** Auth-gated (priority overrides J8 defer per `feedback_a2a_priority_correction.md`)
**Evidence (verified):**
- 6 spec docs at `audit/2026-05-21-a2a-spike-plan/` totaling ~100 KB (V1 subagent this session):
  - `protocol-survey.md` — A2A v1.0.0 spec, 11 operations mapped to Hermes
  - `integration-points.md` — `lib/a2a/` plugin scaffold
  - `auth-design.md` — JWT composite-identity (22 KB)
  - `telemetry-design.md` — Cloud Trace dual-emit + SSE per-event traceparent (19 KB)
  - `spike-plan.md` — 10-day Day 1-10 deliverables + kill criteria
  - `open-questions.md` — 12 sponsor Qs (Q1-Q12 + Q-meta) each with documented default
- Zero implementation: `lib/a2a/` directory does NOT exist; no tests; no PRs; no CI/terraform refs (V1)
- ADR-0010 references A2A peer-exec as Firecracker forcing function (`docs/decisions/0010-firecracker-sandbox-tier.md:21-23`)
- MEMORY `feedback_a2a_priority_correction.md`: "A2A is a priority (Google Production use case at scale is a MUST) - do not defer it."

**Gap:**
- **Day 0 prereq**: hermes-agent submodule currently empty per V1 — Day 1 cannot start until populated
- 0/7 test files (test_plugin_loads, test_server_dispatch, test_client, test_streaming, test_auth, test_telemetry, test_task_bridge)
- 0/6 lib/a2a/*.py code files (server, client, agent_card, task_bridge, auth, __init__)
- No `docker-compose.canary.yml`, no CI wiring

**Acceptance criteria:**
- Day 0: hermes-agent submodule populated + `import lib.a2a` succeeds
- 12 open-questions resolved (user signs off on defaults or overrides) BEFORE Day 1 code
- Spike Day 1-10 deliverables complete per `spike-plan.md`
- 7 test files exist + pass: `uv run --extra dev pytest lib/a2a/tests/ -q` shows all green
- End-to-end smoke: agent-to-agent dispatch round-trips successfully via `tasks.send` + `tasks.get`
- Telemetry: Cloud Trace shows dual-emit spans with parent-child A2A semantics
- Kill criteria from `spike-plan.md` not triggered

**Unblock:** User authorization (one of):
- "Begin A2A spike — defaults for the 12 open questions are fine" (fastest path)
- "Begin A2A spike — answers for Q1-Q12: ..." (user overrides)
- "Hold — answer these N questions first: ..." (delay Day 1)

**Recommendation:**
- Day 0 (1 day): Sonnet subagents populate Hermes submodule + circulate 12-Q defaults for sign-off
- Day 1-10: per `spike-plan.md`; one Sonnet implementer subagent per day's deliverable; Opus reserved for architecture choices (e.g., auth-design edge cases); Haiku for mechanical scaffolding
- Each day's deliverable gates on green tests + Cloud Trace assertion before next day starts

**Cost / risk:** Engineering cost only (no infra $$ during spike); risk = scope creep on 10-day estimate (mitigation: kill criteria in spike-plan).

**Cross-refs:** `audit/2026-05-21-a2a-spike-plan/`; ADR-0010; MEMORY `feedback_a2a_priority_correction.md`, `audit_2026-05-21-arch_research_gap_closure.md`.

---

### Thread #4 — Stream A apply via Gemini-CLI

**Tier:** P1
**Status:** Auth-gated (cost-triggering)
**Evidence (verified):**
- Sub-modules `terraform/phase-0a-gcp/{postgres,model-armor}/` exist with READMEs documenting apply procedures (read directly this session)
- Postgres module: `terraform/phase-0a-gcp/postgres/main.tf` provisions 11 resources (Cloud SQL `db-custom-16-64000` HA, VPC peering, hermes database, IAM user, connection secret) — read in full this session
- Model Armor module: `terraform/phase-0a-gcp/model-armor/README.md` documents FloorSetting + j1-trajectory-shipper template + DLP InspectTemplate
- Both modules `terraform validate` clean per synthesis rows 11-12 (verified prior session, re-run not done in this session)
- Synthesis doc row 1 explicitly lists Stream A apply as "out of scope" (gated behind user-explicit authorization)
- GCS trajectory bucket: NOT confirmed present in any terraform file — needs spec-time check

**Gap:**
- Model Armor: plan-only, not applied → FloorSetting not enforced in `i-for-ai` project → J1 launch blocked
- Postgres: plan-only, not applied → no Cloud SQL instance → Phase 2 memory tier cannot bootstrap (triggers $1,580/mo on RUNNABLE)
- GCS trajectory bucket: status unclear — must verify in `terraform/phase-0a-gcp/gcs.tf` (file present per `ls`) before Gemini-CLI apply

**Acceptance criteria:**
- Pre-apply: verify `terraform/phase-0a-gcp/gcs.tf` either already provisions trajectory bucket OR add minimal resource block in this thread's PR
- Gemini-CLI delegated apply, per Gemini-CLI procedure (gemini-gcp skill); session here does NOT run `terraform apply` directly
- Model Armor verification: `gcloud model-armor floorsettings describe --project=i-for-ai` returns enforced FloorSetting per `audit/2026-05-20-model-armor-j1-runbook/runbook.md` §4
- Postgres verification: `gcloud sql instances describe autonomousagent-postgres-vector --project=i-for-ai --format='value(state,ipAddresses[].type,settings.ipConfiguration.ipv4Enabled)'` returns `RUNNABLE  PRIVATE  False`; IAM auth flag `on`
- GCS bucket verification: `gsutil ls -L gs://<trajectory-bucket>` shows VM runtime SA `objectCreator` grant
- Cost verification: billing reaches projected ~$1,611/mo within 24h post-apply (Postgres $1,580 + Model Armor $31 + GCS minimal)
- All applied within $7,750/mo cap per `terraform/phase-0a-gcp/billing.tf` budget alert

**Unblock:** User authorization (verbatim): "GO for Stream A apply via Gemini-CLI — acknowledge Postgres $1,580/mo cost trigger on RUNNABLE."

**Recommendation:**
- Sequence to minimize cost exposure: (a) GCS trajectory bucket first (zero-cost), (b) Model Armor (~$31/mo), (c) Postgres last (largest commit). Verify each step's acceptance criterion before next step proceeds.
- Use Gemini-CLI via gemini-gcp skill; provide it the plan output for review before apply
- Capture every Gemini-CLI command + output as evidence under `audit/2026-05-21-gemini-delegation/`

**Cost / risk:** ~$1,611/mo when all live; risk = Postgres `lifecycle.prevent_destroy = true` intentionally blocks `terraform destroy` (rollback requires editing main.tf to relax). Worst case: instance created but unused = $1,580/mo waste until `prevent_destroy` relaxed + destroy. Mitigation: only apply Postgres after Persistence Trap approval (#5) AND Stream B J3 shipper feature flag wired.

**Cross-refs:** `terraform/phase-0a-gcp/postgres/README.md`, `terraform/phase-0a-gcp/model-armor/README.md`, `audit/2026-05-20-model-armor-j1-runbook/`, `audit/2026-05-21-phase2-postgres/`; MEMORY `model_armor_j1_config.md`, `phase2_postgres_tier.md`, `Gemini + Antigravity orchestration setup`.

---

### Thread #5 — Persistence Trap contract approval

**Tier:** P1
**Status:** Auth-gated (J1 launch blocker per MEMORY `audit_2026-05-21-arch_research_gap_closure.md`: "only J1-blocker remaining is Persistence Trap approval")
**Evidence (verified):**
- Implementation shipped: commit `a847f1a feat(lib): J3 trajectory shipper — Persistence Trap (#12.c) implementation`
- Contract tests shipped: commit `38856f2 test(tests): Persistence Trap contract — 8 variants + DO NOT WEAKEN T3`; 8/8 pass per synthesis row 15
- Contract spec lives at `audit/2026-05-21-persistence-trap-12c/` (4 files per `ls audit/`)
- MEMORY `persistence_trap_contract.md`: "J3 shipper MUST call Model Armor sanitize before GCS upload; canary tokens + halt-LOUD posture; J1-blocking"

**Gap:** Code is shipped + tested but contract has not been formally approved → J1 launch (J3 shipper writing real trajectories to GCS) still feature-flagged off pending user sign-off.

**Acceptance criteria:**
- User reads `audit/2026-05-21-persistence-trap-12c/` and confirms (one approval covers all three):
  - (a) canary-token strategy acceptable (test canaries: `canary+persistencetrap@example.test`, SSN `999-88-7777`, PAN `4111-1111-1111-1111`, phone `(555) 010-1234`)
  - (b) halt-LOUD posture on F37 (Persistence Trap fired) acceptable — shipper aborts the write, raises a P0 alert, requires operator un-halt
  - (c) Model Armor sanitize-before-GCS-upload is the correct enforcement point
- Approval recorded as a memo at `audit/2026-05-21-persistence-trap-12c/USER-APPROVAL.md` with timestamp + verbatim approval phrase
- J1 launch feature flag flipped (engineering work — paired with Thread #4 Model Armor apply complete)

**Unblock:** User says verbatim: "Persistence Trap contract approved." Combined with Thread #4 Model Armor live → J1 unblocked atomically.

**Recommendation:** User-touch only — no engineering required to obtain approval. Bundle into the same user message as Thread #4 GO.

**Cost / risk:** $0; risk if NOT approved = J3 shipper writes blocked → no trajectory data → no offline fine-tuning data flow (Phase 4 GPU work has no input data).

**Cross-refs:** `audit/2026-05-21-persistence-trap-12c/`; MEMORY `persistence_trap_contract.md`, `model_armor_j1_config.md`.

---

## Tier P2 — Pre-existing tech debt

### Thread #6 — P0-A 24h survival test

**Tier:** P2
**Status:** Pre-existing (open since 2026-05-19 audit)
**Evidence (verified):**
- Documented in `audit/2026-05-19-resume-orchestration/audit-plan.md` (V3 subagent this session) — Plan Task 5 (24h idle soak)
- `audit/2026-05-20-state-of-the-repo-v2/findings.md` §3.1: "the only remaining bar to clear"
- Original Hermes crash (exit-137) NON-REPRODUCED per Task 1 at commit `0a38ed8` (MEMORY `project_state_2026-05-20_phase0a.md`) — agent recovered without intervention, stayed up 6+ hours
- J9 observability shipped post-incident: gauge at `lib/durability/runtime_detectors.py:51`, MeterProvider at `56336f5`, detector wiring at `01e6584` → continuous real-time signal now exists

**Gap:** No 24h soak harness exists; original symptom never re-triggered.

**Acceptance criteria — Option A (build the test):**
- Harness: `tests/integration/test_24h_idle_soak.py` runs agent for 24h with synthetic idle traffic
- Asserts: no exit-137; RSS growth bounded; all health checks pass throughout; J9 gauge `agent.memory.context_usage_pct` stays below alert threshold for the full 24h
- Concrete RSS/gauge thresholds defined at writing-plans phase (default proposal: RSS p95 < 1.5× start-of-soak baseline; J9 gauge < 80%)
- Nightly CI workflow OR operator-on-demand
- Cost: ~$5/day CI runtime

**Acceptance criteria — Option B (formally retire):**
- ADR `docs/decisions/0011-p0a-24h-soak-retired.md`: "P0-A retired: original RCA non-reproduced; replaced by continuous J9 observability + alerts"
- MEMORY `project_state_2026-05-20_phase0a.md` updated to mark P0-A as retired
- Audit-plan updated: `audit/2026-05-19-resume-orchestration/audit-plan.md` Plan Task 5 marked CLOSED-RETIRED

**Unblock:** User picks Option A or B (no fast-path default — both are defensible).

**Recommendation:** Option B (retire). Rationale: original symptom non-reproduced, J9 observability shipped (`56336f5`) provides continuous real-time signal, dedicated 24h soak adds CI cost with low ROI given non-reproduction. If a symptom recurs later, build the soak test then with the specific failure-mode in hand.

**Cost / risk:** Option A = ~$5/day CI runtime + 24h cycle time per change to the test; Option B = $0, accepts residual risk that crash recurs without dedicated soak (mitigation: J9 gauges + alert routing already in place).

**Cross-refs:** `audit/2026-05-19-resume-orchestration/audit-plan.md`; `audit/2026-05-20-state-of-the-repo-v2/findings.md`; MEMORY `project_state_2026-05-20_phase0a.md`, `audit_2026-05-19_p0_wave.md`.

---

### Thread #7 — J13 Hermes upstream PR

**Tier:** P2
**Status:** Auth-gated (external-repo contribution)
**Evidence (verified):**
- J13 defined as P2 task in `audit/2026-05-20-architecture-research-gap-analysis/audit-plan.md` (V3 subagent this session)
- Upstream: `NousResearch/hermes-agent`
- Hook registration exists upstream but is never invoked at the LiteLLM call site
- J9 detector wiring depends on either J13 landing OR a wrapper-side workaround in `lib/observability/__init__.py`

**Gap:** Either (a) upstream PR wiring `invoke_hook("pre_llm_call", ...)` + `invoke_hook("post_llm_call", ...)` into LiteLLM call site, OR (b) wrapper-side workaround.

**Acceptance criteria — Option A (upstream PR):**
- Fork branch on `NousResearch/hermes-agent`
- PR open with hook invocations at LiteLLM call site
- Upstream maintainer review + merge (latency = weeks)

**Acceptance criteria — Option B (wrapper workaround):**
- `lib/observability/__init__.py` patches Hermes LiteLLM wrapper to invoke hooks at the right lifecycle points
- New test `tests/integration/test_observability_hooks_fire.py` asserts both hooks fire on a known LiteLLM call
- J9 detectors validated on real post-LLM events
- Pin Hermes submodule SHA to prevent silent breakage on upstream version bump

**Unblock:** User picks Option A or B. Option A requires explicit "open the PR" authorization per the audit memo.

**Recommendation:** Option B (wrapper workaround). Rationale: avoids external-repo review latency; J9 detectors get exercised on real events now; pinning Hermes SHA + CI hooks-fire test bounds the drift risk. Reserve Option A for if/when Hermes maintainers signal interest in this hook semantics.

**Cost / risk:** Option A = weeks of upstream review latency + risk of rejection; Option B = wrapper drift risk on each Hermes version bump (mitigation: pinned SHA + CI test). Engineering cost for B: ~half-day.

**Cross-refs:** `audit/2026-05-20-architecture-research-gap-analysis/audit-plan.md`; J9 observability commits `01e6584`, `56336f5`.

---

## Tier P3 — Correctly gated, no action now

### Thread #8 — H1 Firecracker P0 infra

**Tier:** P3
**Status:** Gated-on-trigger (waits for A2A spike P1 ship)
**Evidence (verified):**
- ADR at `docs/decisions/0010-firecracker-sandbox-tier.md` (211 lines, full Context/Decision/Consequences) per V-D direct verification in `audit/2026-05-21-verification-synthesis.md` row 8
- Scope packet at `audit/2026-05-21-h1-firecracker/`
- MEMORY `h1_firecracker_scope.md`: "separate firecracker_sandbox tier (not cloud_sandbox replacement); GCP N2 nested-virt; A2A peer-exec first consumer; ~$265/mo"

**Gap:** No infra provisioned, no `lib/sandbox/firecracker_*.py`, no terraform module — correctly deferred.

**Acceptance criteria (TRIGGERED only):**
- Per `audit/2026-05-21-h1-firecracker/` scope: A2A peer-exec successfully dispatches to Firecracker microVM
- microVM boots in < 2s
- Isolation verified (no escape into host)

**Unblock:** A2A spike (#3) ships AND production traffic confirms peer-exec needs Firecracker isolation (not just gVisor or container).

**Recommendation:** NONE — correctly deferred. Re-evaluate after #3 ships AND first real A2A traffic flows.

**Cost / risk:** ~$265/mo when activated; risk of premature implementation = wasted engineering before consumer exists.

**Cross-refs:** ADR-0010; `audit/2026-05-21-h1-firecracker/`; MEMORY `h1_firecracker_scope.md`.

---

### Thread #9 — Phase 3 Governor

**Tier:** P3
**Status:** Gated-on-trigger (Q4 2026 — A2A traffic live AND first policy-conflict incident)
**Evidence (verified):**
- Design at `audit/2026-05-21-phase3-governor/` (commit `33dd934`)
- MEMORY `phase3_governor_design.md`: "standalone service + sidecars, fail-open monitor + fail-closed kill; A2A traffic gates Q4 2026 trigger; no implementation until trigger"
- Strategic disposition Q7 locked per `project_strategic_dispositions_2026-05-20.md`

**Gap:** No implementation — correctly deferred per ADR-0008 disposition.

**Acceptance criteria (TRIGGERED only):**
- Trigger condition documented: sustained A2A traffic at or above the threshold defined in `audit/2026-05-21-phase3-governor/` design AND ≥ 1 policy-conflict incident in production
- Per design at `audit/2026-05-21-phase3-governor/`: standalone Governor service + per-agent sidecars; fail-open monitoring + fail-closed kill switch

**Unblock:** Trigger condition met. Until then: no action.

**Recommendation:** NONE — correctly deferred. Phase 3 build-out is deliberately scheduled AFTER A2A production traffic gives us real policy-conflict data to govern against.

**Cost / risk:** TBD on activation; risk of premature implementation = governs nothing if A2A traffic hasn't materialized.

**Cross-refs:** `audit/2026-05-21-phase3-governor/`; MEMORY `phase3_governor_design.md`, `project_strategic_dispositions_2026-05-20.md`.

---

## Batched authorization asks (recommended ordering)

To minimize user-touch overhead (autonomous-agent best practice), bundle authorizations:

### Round 1 — One user message covers four asks:

| Ask | Thread | Cost trigger | Engineering work that follows |
|---|---|---|---|
| Open PR for `feat/framing-2-bolt-on` + verify/merge PR #112 if green | #1 | $0 | Bundle Thread #2 fix into the framing-2 PR |
| GO for Stream A apply via Gemini-CLI (acknowledge $1,611/mo) | #4 | $1,611/mo on RUNNABLE | Gemini-CLI session executes sequenced apply (GCS → Model Armor → Postgres) |
| Persistence Trap contract approved | #5 | $0 | J1 launch feature flag flipped after #4 Model Armor live |
| Begin A2A spike (defaults for 12 open questions OK?) | #3 | $0 engineering | Day 0 Hermes submodule populate → Day 1-10 subagent dispatch |

### Round 2 — Pre-existing items, separate decision cycle (user can defer):

| Ask | Thread |
|---|---|
| P0-A 24h survival: Option A (build test) or Option B (retire ADR) | #6 — recommended B |
| J13 Hermes: Option A (upstream PR) or Option B (wrapper) | #7 — recommended B |

### No-action (P3): Threads #8, #9 stay parked until trigger conditions met.

---

## Risks and acceptance gates

| Risk | Trigger | Mitigation |
|---|---|---|
| Postgres cost overrun | Instance reaches RUNNABLE | Billing alert at $7,750/mo cap per `terraform/phase-0a-gcp/billing.tf`; verify within 24h post-apply |
| `prevent_destroy` blocks rollback | Need to remove unused Postgres instance | Documented in `terraform/phase-0a-gcp/postgres/README.md` rollback section; requires deliberate main.tf edit |
| A2A spike scope creep | 10-day estimate slips to > 15 days | Day 0 prereq + kill criteria in `audit/2026-05-21-a2a-spike-plan/spike-plan.md`; reassess at Day 5 |
| Persistence Trap bypass | J3 shipper writes to GCS pre-Model-Armor-sanitize | Code shipped + 8 tests guard the contract; feature flag remains off until #4 + #5 BOTH met |
| PR #112 merge conflicts | `main` moved since 2026-05-20 draft | Rebase first; if conflicts non-trivial, open fresh PR from current HEAD |
| Hermes upstream divergence (#7 Option B) | NousResearch ships new Hermes version | Pin submodule SHA; CI test asserts hooks fire on known LiteLLM call |
| Gemini-CLI session failure mid-apply | Network drop, auth refresh, etc. | Each apply step in #4 is independent; Postgres has `lifecycle.prevent_destroy` so partial apply is recoverable; capture full Gemini-CLI transcript under `audit/2026-05-21-gemini-delegation/` |

---

## What this spec does NOT cover (scope statement)

- **Sub-task ordering within A2A spike Day 1-10**: handed to `writing-plans` skill in the next step
- **Cloud SQL post-apply tasks**: Alembic baseline migration (Task #29), HNSW index build per `pgvector-spec.md`, Cloud SQL Auth Proxy sidecar (Task #30) — these follow #4 apply, separate writing-plans cycle
- **Phase 4 GPU procurement**: separate stream, $5K/mo Unsloth budget per ADR-0008 Q1
- **J8.v2 memo authoring**: memory hygiene (J8 was overridden by `feedback_a2a_priority_correction.md`); not a roadmap thread
- **Test pyproject.toml `markers` registration warning**: cosmetic, folded into Thread #2's pyproject edit
- **Re-verification of prior-sprint claims** (commits before `f5d404c`): out of scope per `audit/2026-05-21-verification-synthesis.md` §Honest scope statement
- **GCP IAM rotation / secret rotation**: continuous-ops work, not roadmap

---

## Next step (terminal state of brainstorming flow)

After user reviews + approves this spec: invoke `superpowers:writing-plans` skill to produce the per-thread implementation plan (P0 + P1 threads first; P2 threads on user trigger; P3 threads skipped until trigger conditions).

The implementation plan will:
- Decompose Thread #3 (A2A spike) into Day 0-10 per-day deliverables with subagent assignments + model tiering
- Define the Gemini-CLI handoff payload for Thread #4 (Stream A apply) including pre-apply + post-apply verification commands
- Specify Thread #1 PR body content + Thread #2 code change in a single diff
- Capture Thread #5 user-approval memo template
- Park Thread #6, #7 with explicit "user chooses A or B" gates
- Leave Thread #8, #9 untouched per trigger-gated status
