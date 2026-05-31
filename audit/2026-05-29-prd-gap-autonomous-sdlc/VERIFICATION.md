# Verification report — PRD-gap audit (2026-05-29)

**Skill:** `superpowers:verification-before-completion` — *evidence before claims.*
**Method:** every load-bearing claim in `findings.md` / `audit-plan.md` was **re-derived from fresh
command output by the auditor directly** (not trusted from the mapping workflow's subagents), plus an
**independent red-team workflow** (adversarial refutation + false-negative sweep + web-verification).
**Branch/HEAD:** `remediation/p1-01-rewrite-tests`.

> **Headline:** the audit's central thesis holds, but verification found **3 defects in the
> deliverables** (1 outright false claim, 1 imprecise claim, 1 stale assumption). All 3 are corrected
> in-place. Details below. No claim is allowed to stand on a subagent's word alone.

---

## A. Direct-evidence ledger (auditor-run commands)

| # | Claim under test | Verdict | Evidence (fresh) |
|---|---|---|---|
| 1 | `langgraph`/`langgraph-checkpoint`/`deepeval`/`inspect_ai` **installed** (not just declared) | ✅ CONFIRMED | `.venv/bin/python -c import` → `langgraph 1.2.2`, `langgraph.checkpoint` OK, `deepeval 4.0.4`, `inspect_ai 0.3.228`. uv.lock L2615/2632/969/2285. |
| 1b | `aiogram` installed | ❌ **NOT installed** | same import probe → `ModuleNotFoundError`. **Correction:** SP-13 must treat aiogram as a *new* dependency (unlike the spine deps). |
| 2 | `lib/hermes_bridge.invoke_hermes_cli` is dead (zero callers) | ✅ CONFIRMED | `grep -rn` over lib/app/scripts/tests/evals → only the def site `lib/hermes_bridge.py:22`. |
| 3 | F34 `LoopDetector` / F35 `StallDetector` unwired | ✅ CONFIRMED | only `lib/durability/runtime_detectors.py` (def) + `tests/unit/test_runtime_detectors.py`. No instantiation in any hook. |
| 4 | `TrajectoryShipper.ship_batch` "has no caller" | ⚠️ **CORRECTED (imprecise)** | caller exists: `scripts/run_trajectory_shipper.py:15` + tests. BUT that script is **not** scheduled (no CI/cron/compose ref) and `on_session_end` (`lib/evaluators/__init__.py:129`) does **not** ship. Effect (verdicts don't reach the substrate live) holds; phrasing fixed. |
| 5 | `HALT_F21` "written but no `pre_tool_call` reads it" | 🔴 **REFUTED — was FALSE** | `lib/kanban/__init__.py:77` reads `/data/HALT_F21` in `_on_pre_tool_call` and raises `BudgetExhaustedError`. Writer `budget_watchdog.py:139`; also referenced `handlers.py:204`, `anchors/__init__.py:197`. The budget-halt veto **is** wired. **Corrected in findings §3.1.** |
| 6 | No eval framework runs in CI (nightly-eval = import-smoke) | ✅ CONFIRMED | `grep deepeval\|inspect\|promptfoo\|garak .github/workflows` → 0 hits. `nightly-eval.yml:63` runs only `pytest tests/integration/test_evaluators_smoke.py`. |
| 7 | `app/core/schemas.TaskRequest` is flat (no DAG) | ✅ CONFIRMED | `grep` → only `class TaskRequest`; no `TaskGraph`/`TaskNode`/`depends_on`/`sub_task`. |
| 8 | Clarification auto-driver is a `return None` stub | ✅ CONFIRMED | `lib/anchors/__init__.py:181` `_on_pre_tool_call`, `:214` `TODO(P1-1 task 6)`, `:218` `return None`. |
| 9 | `decide_next_action` (FSM) has zero callers | ✅ CONFIRMED | `grep` over lib/app/scripts → no caller (only def). |
| 10 | `INPUT_REQUIRED → FAILED` discards sign-off | ✅ CONFIRMED | `app/core/orchestrator.py:356-364` maps it to `TaskStatus.FAILED`; `test_peer_dispatch.py:316` asserts it. |
| 11 | seed `Orchestrator.submit()` not called at runtime | ✅ CONFIRMED | `grep ".submit("` (non-test) → 0 hits in lib/app/scripts. |

## B. Working-tree P0 landmines (corrects the audit's stale Gate-0 framing)

| meta-audit item | audit's earlier implication | **verified current status** | evidence |
|---|---|---|---|
| P0-1 firewall egress | open/blocking | ✅ **FIXED** | `firewall.tf:39` `destination_ranges=["0.0.0.0/0"]`; no `10.0.0.10`. |
| P0-2 Memorystore AUTH | open/blocking | ✅ **FIXED** | `memorystore/main.tf:50` `auth_enabled` commented out. |
| P0-3 plaintext secret | open/blocking | ⚠️ **PARTIAL** (regraded — see §E.2 #7) | git-tracking FIXED (all `*.sops`); **but plaintext SA private keys on disk** — `ls -la secrets/sa-keys/*.json` = 3× mode 0644 + `hermes-provider.env`, git-ignored but **not deleted/rotated**. The initial "RESOLVED" grade moved the goalposts. |
| P0-6 `update_plan.py` | open/blocking | ✅ **FIXED** | absent from repo root. |
| P0-4 dead observability code | open | ✅ **FALSE POSITIVE** | `ls lib/observability/` → no `ledger.py`/`failure_detectors.py`; `git log --all` empty. Files never existed. Real gap = no TamperEvidentLedger/FailureDetectors impl anywhere. |
| P0-5 OTel redaction misconfig | open | ✅ **FIXED** | `deploy/otel/collector.prod.yaml:31` `allow_all_keys: true` + `blocked_key_patterns` (commit 95dab2a9). |

→ Destructive landmines **P0-1/2/5/6 are cleared** and **P0-4 was a false positive**; but **P0-3
(on-disk SA private keys) and P1-1 (no CI + working-tree regression) remain genuinely OPEN** and are now
Gate-0 items (SP-00b / SP-00c). Not the multi-week blocker first implied — but not "all clear" either.
`audit-plan.md` Gate-0 section updated with this table.

## C. Test-suite ground truth (fresh, this branch)

```
.venv/bin/python -m pytest tests/ lib/ -q --tb=no
→ 55 failed, 622 passed, 23 skipped   (700 collected, 9.93s)
```

- vs. meta-audit (2026-05-28): 698 / 607 pass / 59 fail / 32 skip. The remediation branch **improved**
  (more passing, fewer failing/skipped).
- The 55 failures are **concentrated in `lib/a2a/tests/`** (streaming, tasks_get_cancel,
  taskspec_wiring, server_integration) and are **environment-dependent** — the log shows
  `NameResolutionError: Failed to resolve 'otel-collector'` and connection failures, i.e. they need the
  live docker stack (same root cause as the meta-audit's network failures), not logic regressions.
- **Caveat (honesty):** I did not bring up the live stack to drive these to green; that is an
  environment limitation, stated rather than hidden. A clean-room CI run with the compose stack is the
  authoritative next measurement (this is exactly meta-audit P1-6 / plan SP-12).

## D. Corrections applied to the deliverables this pass

1. **findings.md §3.1 fact #6** — removed the false "HALT_F21 never read" claim (it IS read at
   `lib/kanban/__init__.py:77`); fixed the shipper phrasing to "script-only + unscheduled, not in
   `on_session_end`."
2. **findings.md §2.3** — refined "the loop never runs at runtime" → "not auto-driven from user
   messages; advanceable manually via `/lock`,`/confirm`; questions not auto-generated."
3. **audit-plan.md Gate 0** — replaced the "P0-1..P0-6 must land (all open)" framing with the verified
   status table (P0-1/2/3/6 already fixed; P0-4/P0-5 to confirm).
*(items 4-12 below — applied after the red-team pass)*

---

## E. Red-team synthesis (independent workflow `wf_086e2313-3c6`, 7 agents)

**Verdict: GO-WITH-CORRECTIONS.** The audit's central thesis is **CONFIRMED and well-evidenced** —
the decompose→workflow spine is genuinely absent; LangGraph is installed-but-unimported; the bricks
exist but are unwired; the seed MoE/PPO is correctly excluded. The deliverables must not ship as-is
without the corrections below — **all of which have now been applied.**

### E.1 Red-team NEW claims that I re-confirmed independently (did not trust on its word)

| red-team claim | my independent check | result |
|---|---|---|
| langgraph is a *transitive* dep (not in pyproject) | `grep pyproject.toml` → only `inspect-ai`/`deepeval`/`garak` | ✅ CONFIRMED |
| Postgres saver not installed | `import langgraph.checkpoint.postgres` / `psycopg` → MISSING; absent from uv.lock | ✅ CONFIRMED |
| plaintext SA private keys on disk | `ls -la secrets/sa-keys/` → 3× `*.json` mode 0644 + `hermes-provider.env`, git-ignored | ✅ CONFIRMED |
| P0-5 OTel already fixed | `collector.prod.yaml:31` `allow_all_keys: true` | ✅ CONFIRMED |
| P0-4 files never existed | `ls lib/observability/` + `git log --all` empty | ✅ CONFIRMED (false positive) |
| P1-1 working-tree regression | `git status --short` → M/D on the 5 tests + conftest | ✅ CONFIRMED |

### E.2 All corrections applied to the deliverables (mine + red-team's)

1. **findings.md §3.1 #6** — removed false "HALT_F21 never read" (it's read+vetoes at `lib/kanban/__init__.py:77`).
2. **findings.md §2.3** — "loop never runs" → "not auto-driven; advanceable via `/lock`,`/confirm`".
3. **audit-plan.md Gate 0** — verified-status table replacing the "all P0 open" framing.
4. **audit-plan.md SP-14** — dropped the bogus "add HALT_F21 reader" (already works; 2 readers exist); kept F34/F35 + shipper wiring; corrected shipper to "script-only/unscheduled."
5. **findings §3.1 #1 + §7 + SP-01 + SP-23** — corrected the Postgres-saver overclaim: InMemory is *wire-only*; `AsyncPostgresSaver`+`psycopg` are *add+wire* (not in uv.lock).
6. **NEW SP-00** — promote `langgraph`/`langgraph-checkpoint` to direct pinned deps (currently transitive via garak) **before** SP-22 deletes garak.
7. **Gate 0 P0-3 regrade** — `RESOLVED` → `PARTIAL`: git-hygiene fixed but **plaintext SA private keys on disk not deleted/rotated** (restored the original rotate+delete acceptance bar; flagged the goalpost-moving).
8. **Gate 0 P0-4** — reclassified as **false positive** (files never existed) + noted the real architectural gap (no TamperEvidentLedger/FailureDetectors impl).
9. **Gate 0 P0-5** — marked **FIXED** (was "still verify").
10. **Gate 0 P1-1** — `IN PROGRESS` → **REGRESSED/UNVERIFIED** (working-tree reverts HEAD hardening; runs in no CI). Added **SP-00c**.
11. **NEW SP-00b** — SA-key→WIF + delete on-disk plaintext, as a Gate-0 security prerequisite SP-12 depends on.
12. **NEW content** — absorbed-vs-orphaned meta-audit ledger (P1-3/4/8/10, P2-3); submodule-path prefixes + vendoring note (findings #3, row A, SP-02); `TaskSpec`-has-no-`phase` bridge contract (SP-02); aiogram-is-a-new-dep (SP-13); `execute()` also dead + cross-package caveat (SP-05); **Spine-risks & DR** section (SP-R1..R5: checkpoint PII/CMEK/scrubber, per-graph cost budget, vendor-lock fallback, state-retention, restore drill); **entry-point bridge** SP-B1 (GoalManager↔decomposer); **seed-exclusion justification table** (assertion→checkable); INPUT_REQUIRED line `:356`→`:356-364`; DAGMetric node-typing note.

### E.3 Items the red-team confirmed as accurate in the audit (no change needed)

Thesis core (spine absent; two disconnected halves; `app/core/orchestrator.py` is the P-3 shim;
`decide_next_action`/`submit()`/`hermes_bridge`/F34/F35 unwired; no eval in CI; Telegram raw-httpx;
`trajectory_diff` JSONL); submodule decomposer/GoalManager exist & are siloed; **hermes-agent submodule
is now CLEAN** (the meta-audit's "1565 dirty files / P1-11" is resolved); LangGraph durability/`interrupt()`/
fan-out, DeepEval `DAGMetric`/`assert_test`, aiogram-3 FSM+inline-kb, Plane CE self-host+MCP (GitHub
sync is paid-only ✓), GitHub OIDC/WIF+Environments+auto-merge — all **web-verified real & current**.

### E.4 Residual open items (honest — not resolvable in a read-only audit)

- **Live integration test run** — the 55 failures are network/live-stack-dependent; an authoritative
  green requires bringing up `docker-compose.ci` (that *is* plan SP-00c/SP-06). Not done here.
- **Telegram runtime reachability** and the **`i-for-ai → autonomous-agent-2026` migration status** —
  still unverified (flagged in findings §7); neither blocks plan approval.
- **Key rotation** (P0-3) is an operational action outside this audit's scope; flagged as Gate-0 work.

### E.5 Confidence

Every load-bearing claim in the deliverables is now backed by either auditor-run command output (§A-C)
or independently re-confirmed red-team evidence (§E.1). Three defects I introduced (HALT_F21,
Postgres-saver, P0-3 grade) were caught and corrected. **The deliverables now stand.**

---

## F. PRD triple-check (independent workflow `wf_e636e161-8a4`, 6 reviewers + synthesizer)

`PRD-autonomous-sdlc-agent.md` was adversarially reviewed across **completeness · acceptance-criteria
rigor · traceability · executor-guardrail soundness · deliverable-bucket clarity · factual consistency**.
**Verdict: GO-WITH-CORRECTIONS** (initial completeness = *major-gaps*). **~38 corrections folded into PRD
v1.1**; the high-severity ones:

**Gameable guardrails (the most important class — verification bias the contract itself enabled):**
- **C1/V-1** "evidence block present" was satisfiable by a fabricated count (the meta-audit's "541"
  move). → CI now **re-runs** the pasted commands and treats the `--junitxml` artifact + bot comment as
  authoritative; executor-pasted numbers are advisory.
- **C3** substring-grep can't tell behavioral from legitimate string asserts (false positives). → the
  binding gate is now a **deceptive-mock differential** (compliant passes / deceptive-while-acting fails
  / benign passes); the grep is advisory.
- **C4/C5/C6** had a self-writable `@manual` waiver + `--strict-markers` only in a comment + a no-skip
  workflow that ignored `skipif`/`xfail`. → waiver **forbidden**; addopts committed; no-skip extended;
  C4 made **symbol-granularity callgraph reachability**; C5 adds a clean-container import gate.
- **C8/C9/C11/C12** were self-attested prose. → acceptance committed to `audit/acceptance/*.yaml`
  sha-pinned + `acceptance-frozen` CI (C8); review runs as a **separate CI job under a distinct
  credential** with a workflow-stamped model id + class-inequality check (C9); `provenance-check` via
  git diff (C11); refutation must be a **committed failing pair** (C12). New **C14**: board/Telegram
  "done" is **gate-derived** (reads required-check status), not agent-asserted.
- **Anti-drift sentinel rule** had no test. → added a `no-sentinel-termination` CI grep + an SP-05
  fixture (sentinel emitted while `pytest` non-zero ⇒ loop must NOT pass).

**Missing tasks (completeness):** added **SP-00d** (cosign sign+**verify**, closes P1-5 — V-8 had no
owner), **SP-00e** (builds the C1-C14 enforcement CI the contract assumes), **SP-26** (deployment
rollback — absent from every bucket; distinct from checkpoint-store DR), and gave **SP-O1-O5 / SP-R1-R5**
the missing Expected-outcome + Proof columns.

**Acceptance tests that only checked one direction (would pass while broken):** SP-01 exactly-once
(added per-node counter), SP-02 decomposition fidelity (single-node DAG passed), SP-03 (canned-question
gameable), SP-11 concurrency (no overlap observable), SP-12 (never proved eval *blocks* merge), SP-13
exactly-once, SP-14 (positive-only trips + len>0 stub), SP-15 (RED-only), SP-24 (per-check fixtures),
SP-R1 (one PII shape). All now carry GREEN/negative controls.

**Traceability:** §14's "no orphans" was **provably false** — 11 tasks were absent from the matrix.
Rebuilt: every task + deliverable now appears; enablement tasks tracked as preconditions per R-row.

**Deliverables:** §13 promised a "production signal" the tables lacked → added the column to all three
buckets; **U-6** replaced a precondition checklist with the §12 end-to-end demo (deployed SHA == merged
SHA + per-criterion oracle); added **U-8** (rollback), **U-9** (per-goal cost); §12 DoD made adversarial
(2 held-back goals + 3 negative paths).

**Factual consistency:** `audit-plan.md` SP-22(b) instructed deleting `ledger.py`/`failure_detectors.py`
(P0-4 false positive — never existed) → corrected; SP-04 cite `:364`→`:356-369`; SP-03 cite
`:218`→`:214-218`; SP-14 note expanded to **two** HALT_F21 readers; `≤1 release`→`≥1 release` sign-flip
fixed; SP-00b rotation→**revocation oracle** (not attestation). No re-introduced corrected errors found.

**Status:** all corrections applied in PRD v1.1 + this VERIFICATION record + the `audit-plan.md` SP-22(b)
fix. The PRD is ready to hand to an executing agent **after operator sign-off**.

---

## G. HITL / autonomy hardening (operator Q&A; workflow `wf_2872e6fb-576`, 6 research+audit agents)

Operator questions on bidirectional comms, Plane, GitHub CI/CD, mid-flight steering, and autonomy scope.
**Honest answers (verified against the repo):**

| Q | Answer | Why |
|---|---|---|
| Q1 real-time board↔agent comms ∥ Telegram, no conflict | **PARTIAL → now specified** | Single source of truth (`thread_id`) was implied but unnamed; channels were effectively outbound-only; no inbound board steering, no arbitration. Added **C15** (authority matrix: Telegram=lifecycle, board=content-steer, status=CI; reject/abort>approve) + **SP-17** SteeringEventBus + `SteeringEvent` contract + **U-10**. |
| Q2 Plane as mobile control surface (pre/mid/post-flight) | **YES** | Plane CE self-host has REST Work-Item-Comments + webhooks + first-party MCP + Pages(docs); GitHub two-way sync is Pro-only → use free CE API. Elevated **SP-21** to the control plane; inbound steering lands earlier via SP-17 (P1). |
| Q3 GitHub managed CI/CD + cross-vendor PR review | **YES (already strong; strengthened)** | SP-00d/00e/12 + C9 already cover it. Added **rulesets, merge queue, Environments, OIDC/WIF, reusable workflows, attestations** to §10; the cross-vendor (Gemini) review is a required CI job (C9). |
| Q4 mid-flight trace/notes + adversarial reviewer notices drift + steers | **GAP → now closed** | Everything was post-hoc (SP-15) or unwired (F34/F35); no live trace stream, no monitor. Added **SP-27** (live `AgentNote` stream + monitor of a different model class + 4 steer actions: interrupt-human / inject-RAG / switch-tool / checkpoint-replan) + anti-drift clause (f) + revised **V-5**. |
| Q5 agents open new repos / self-manage without asking each time | **NO for new repos (gated); YES within the run repo (standing)** | The live runtime had a broad classic PAT + GitHub-MCP `--toolsets all` = unbounded power, zero gate. Added **C17 + §4.1 Autonomy Charter** (pre-authorized class vs gated class) + **SP-00f** least-privilege GitHub App. Repo creation is GATED (one operator approval); PRs/branches/worktrees/issues within the run repo are standing-authorized. |

**Q6 forgot-sweep — Tier-1 components added:** prompt-injection / untrusted-read trust boundary (**C16 +
SP-00g** egress firewall); agent identity / non-repudiation (**C13** amend); versioned golden eval dataset
(**SP-G1**); model-pin *enforcement* + upgrade-regression gate (**SP-00i**); license + typosquat/new-dep
gate on agent diffs, blocking auto-merge (**SP-00h**); multi-agent/branch concurrency + global goal cap
(**SP-R6**); kill-switch that revokes the GitHub token + incident runbook + halt-latency SLO (**SP-IR1**);
judge-calibration-drift monitor (**SP-J1**); trace/PII retention TTL + concurrent-goal isolation +
determinism (**SP-R4** extend, §12 (e), §9).

**Two honest CORRECTIONS the workflow caught in its own first pass:** (1) supply-chain — OSV-Scanner
**already** runs on PR diffs (`osv-scanner.yml:5`) + Dependabot covers pip/docker/actions; the real gap is
*license + typosquat + wiring-as-blocking* (SP-00h), not vuln-scanning. (2) model pinning — models **are
already** exactly pinned (no `:latest`); the gap is a *pin-enforcement CI gate + regression-on-bump*
(SP-00i), not raw pinning. PRD §15 records both so an executor doesn't "fix" what already works.

**Status:** PRD bumped to **v1.2**; all amendments applied; consistency-swept. Still **awaiting operator
sign-off** — no code touched outside `audit/`.

---

## H. Sandbox / isolation hardening (operator Q + OpenAI Build-Hours review; workflow `wf_02b9a40f-de9`)

Operator asked: "Does EVERY agent have a sandboxed environment with strict access to tools and files?" +
review the Build-Hours transcript + reuse `openai/build-hours` code if it fits.

**HONEST ANSWER: NO — today the execution path has effectively no sandbox** (verified against code):
- `app/core/orchestrator.py::_execute_local:294` runs the agent capability **in-process** in the harness;
  `AbstractSandbox.run()` has **zero runtime callers** (a dead brick) — sub-agent code/tests run with no
  FS/network/syscall boundary.
- The one Docker `shell-sandbox` (`deploy/docker-compose.yml:272-293`) is a **single shared
  `sleep infinity`** container everything `docker exec`s into — not per-agent, not ephemeral, no
  snapshot/rehydrate; the `cloud_sandbox` tier is an enum value with **no implementation**;
  `FirecrackerSandbox` raises `NotImplementedError`.
- The `hermes` harness holds **live secrets (TELEGRAM/OPENAI/HONCHO/litellm_master_key) on an OPEN egress
  bridge** — the exact prompt-injection-exfil ("lethal trifecta") posture. `ToolsetRouter.resolve()` and
  the `config/limits.yaml` approval block are **dead** (zero runtime callers).

After the v1.3 amendments it becomes a defensible YES: §5.1 harness/compute split; SP-05 real per-node
sandbox (no in-process exec, no secrets, manifest + file-tree, per-role tool allowlist, per-phase egress);
SP-05b worktree-per-agent + **cross-access-denial proof**; SP-05c Cloud Run gVisor managed tier for the
high-risk `cloud_sandbox`; SP-05d auto-destroy + reaper + `/panic` teardown; SP-R7 workspace
snapshot/rehydrate; §8 binding tier-selection rule.

**REUSE VERDICT: PATTERNS ONLY (~0% line-reuse, ~80% design-reuse).** `26-agents-sdk` is 100% coupled to
the OpenAI Agents SDK's proprietary `agents.sandbox.*` subsystem (`BaseSandboxClient`, `SandboxSession`,
`Manifest`, `SnapshotBase`, `SandboxPathGrant`, `S3Mount`, `@function_tool(needs_approval=)`); reusing it
adopts the OpenAI SDK as the runtime — contradicts the LangGraph spine + non-goal #6. `24-api-codex` is
Next.js/TS — irrelevant except lesson #8 (skills as version-controlled `SKILL.md` in git, already partly
SP-25). The 5 patterns map onto our bricks and are **reimplemented natively**: SandboxProvider→`AbstractSandbox`
+adapters; snapshot→SP-R7 (GCS / per-agent branch); manifest copy-vs-mount→SP-05 manifest;
completion-approval→C17 + the dead `config/limits.yaml` approval block; `PendingApproval`/resume→LangGraph
`interrupt()`/`Command(resume=)`. **No `pip install openai-agents`.** Recorded so an executor does not
import the SDK or claim reused build-hours code (C11).

**Resolved an internal contradiction:** SP-05's `network_mode=none` vs SP-00g's default-deny *allowlist*
were mutually exclusive on one sandbox; v1.3 makes egress **per-phase** (TEST=none, BUILD=allowlist).

**Status:** PRD bumped to **v1.3**; all sandbox amendments applied; consistency-swept. Still **awaiting
operator sign-off** — no code touched outside `audit/`.

## I. v1.4 operator-review hardening (6 comments; research workflow `wf_f8020756-837` + triple-check `wf_3e44f257-f6b`)

The operator returned six review comments on the PRD (1: DDLC specialists self-steer to user-intent ∧
industry SOTA + personal-assistant comms + proactive build; 2: machine-verifiable AND measurable/testable
"done" + anti-bias against real agentic-coding failure modes; 3: remove all "demo" framing — this is a
production product; 4: agent legibility / wire orphan data; 5: pragmatism + anti-bias-override +
external-grounding as the agent's own principles + grey-box self-analysis; 6: recommended executor +
unbiased LLM-as-judge). Addressed under the comment-5 governing directives (no over-engineering; override a
user hypothesis when research shows it suboptimal; verify against real 2024-2026 data).

**Workflow 1 — research + adversarial verify (`wf_f8020756-837`, 33 agents, ~2.1M tok).** 5 research
streams ground each candidate in real sources, diff against existing PRD coverage, then a per-finding
adversarial verifier drops redundant/over-engineered ones. **28 candidates → 20 kept, 8 dropped.** Key
grounding: ImpossibleBench (arXiv 2510.20270 — Claude/Qwen3 cheat >79% by deleting/modifying tests;
FS-level read-only is the durable fix), the self-preference-bias literature (2410.21819; code-specific
2505.16222), Spec Kit's `/constitution` (proactive standards injection), the ELEPHANT sycophancy work
(2505.13995), the over-mocked-tests study (2602.00409), the Claude Code CHANGELOG (`/usage` breakdown;
"running / blocked on you / done" agent view), Devin's ACU/session-notification columns, and Anthropic's
2026-04-23 Claude Code postmortem (harness change, model id unchanged → silent quality drop). 8 dropped as
already-covered/over-engineered (build-progress preamble; 90-day history *view*; CLAUDE.md-anchor
endurance; judge-advisory-over-execution; Claude→Gemini handoff contract; subagent task-brief;
reference-grading+position-swap; pin-judge-ids).

**Workflow 2 — triple-check (`wf_3e44f257-f6b`, 92 agents, ~5.5M tok).** Each of 23 deltas (20 + the
comment-3 reframe + the executor section + the operator-requested T.1 tightening) checked by 3 independent
lenses against the *real* PRD (anchor/factual · redundancy/contradiction · rigor/pragmatism/citation) +
per-delta adjudication. **Result: 1 CONFIRMED, 22 CONFIRMED_WITH_FIX, 0 DROP, 0 ESCALATE.** Material fixes:
- **Pervasive anchor drift** corrected by symbol/heading (C8 discipline): U-2=L411, U-3=L412, U-4=L413,
  U-5=L414, U-6=L415, U-9=L418, V-2=L439, V-6=L443, §9 Determinism=L357, §15 header was at L474, demo-rename
  call-sites were L395+L415 (not L379/L399), and the bogus L363 "demo" cite (it is §10 prose) was dropped.
- **4 sibling-delta collisions** resolved: 2.3/2.5 both claimed SP-27 "P5" and both edited signal-(2) →
  split P5(impossible-task)/P6(overconfidence) + one merged signal string; 2.1/2.7 both edited the V-2 cell
  → one merged cell; 2.4/5.1 near-duplicate anti-sycophancy oracles → one SP-03 oracle with two arms
  (`kind=clarification` for false premise, `kind=override` for deprecated choice); 3.1/1.2 share §12 lines.
- **2 architectural contradictions** fixed: (a) blanket read-only on existing tests (2.2) would FS-deny
  SP-04's *own required* edit of `test_peer_dispatch.py:316` → added the C9-approved `## Test Changes`
  carve-out + a single shared net-new-test set across 2.2/2.6/SP-06; (b) **C18's `evidence_url` was
  unobtainable** — the agent has no general-web egress at clarify time (C16/SP-00g allowlist = GitHub +
  registries + Vertex; 1.1 itself forbids a crawler) → changed to a model-grounded challenge with an
  optional, explicitly-*unverified* `cite?`, and corrected the immutability citation (§5 + C16 + anti-drift
  clause (b) + C8, **not** C17).
- **Vacuity gaps** in the anti-vacuity deltas closed (added the missing red or green control to 1.1, 1.2,
  2.1, 2.3, 2.5, 2.6, 4.1, 4.2, 4.3, 5.3) so every new acceptance test has a non-vacuous red+green per
  C2/C12. Enum syncs: "SP-27 P1-P4 + N1" → "P1-P6 + N1 + N4" at §11-P1, V-5, §9.

**Operator decisions (locked):** (1) **cross-vendor judging** approved — this CORRECTS comment 6's literal
"Claude acts as LLM-as-judge"; a model judging its own output is self-preference-biased and violates C9 /
`CLAUDE.md`, so Gemini 3.1 Pro judges Claude-authored app code (SP-06 leaf, SP-27 monitor, C9 review) and
Claude judges the Gemini-delegated GCP work; (2) executor section as **new §15** ("Review & sign-off" →
§16; 2 back-refs at L10/§14 repointed); (3) mutation-kill **advisory at Gate-0, blocking only after SP-G1
baselines** it; (4) "2-3 questions" → "≤5 coverage-tagged" applied at §3/§12/U-2; (5) the "90-day history
view" accepted as already-covered (U-5 + SP-21 + SP-R4) — only the cross-thread "blocked on you" rollup
(SP-17 bullet 6 / SP-21 filter) was net-new.

**Operator-requested tightening (T.1):** mid-flight stakes classification (low/mid/high) is **owned by the
SP-27 monitor (a different model class), not the executor** — the executor never self-gates the BUILD loop;
classification uses deterministic/observed anchors (F34/F35, real subprocess exit, §4.1 gated-action
membership, TaskSpec-mutation, `allowed_paths` violation, SP-15 distance), with self-reported
`AgentNote.confidence` demoted to never-sufficient-alone. The CLARIFY phase has no concurrent monitor, so
its ask-vs-assume triage stays self-made but is backstopped by deterministic gated-action force-escalate +
mandatory human sign-off (R5/U-3) before any build. New SP-27 ownership control: a self-labeled
low-stakes/high-confidence step whose objective signals trip still yields exactly one monitor SteerCommand.

**Net additions in v1.4:** rule **C18**; CI gate **`test-integrity-gate`**; contracts `applied_standards[]`,
`assumptions[]`, `allowed_paths`; per-card cost + milestone projection (SP-16); cross-thread "blocked on
you" (SP-17/SP-21); three-pairing C9 class-inequality (incl. the SP-06 eval-judge); harness-config pinning
(SP-00i/§9); user-perspective oracle (SP-06); §15 executor model. All "demo" framing reframed to "production
acceptance run"; `test_e2e_ship_demo.py` → `test_e2e_ship_acceptance.py`.

**Honesty note:** the cited URLs were gathered by research sub-agents; the load-bearing ones (ImpossibleBench,
self-preference bias, Spec-Kit constitution, Claude Code CHANGELOG, the April-23 postmortem) are
high-confidence; a few niche URLs (`zylos.ai`, `runmaestro.ai`) corroborate rather than carry any delta and
should be spot-checked.

**Status:** PRD bumped to **v1.4**; all 23 triple-checked deltas + the demo-deframe applied to the canonical
PRD; consistency-swept (version, §15/§16 renumber + back-refs, P1-P6/N1/N4 enum, C1-C18, zero stray "demo"
framing). The proposed-diff memo is preserved at `v1.4-proposed-changes.md`. Still **awaiting operator
sign-off** — no code touched outside `audit/`.
