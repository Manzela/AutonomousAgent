# AutonomousAgent — PRD & Gap Findings

**Date:** 2026-05-29
**Auditor:** Claude (Opus 4.8, ultracode) — codebase-first forensic audit
**Question asked:** *What end-state (PRD) does this repo encode, what's missing, and what's the smallest LEGO set of managed/OOTB bricks to make it a SOTA 2.0 autonomous software-delivery agent — goal → decompose → clarify → sign-off → parallel-agent SDLC loop → test/QA/fix → eval-gate → ship → notify?*

**Relationship to prior audits:** This is **additive** to the in-flight meta-audit at
`audit/2026-05-28-meta-audit/`. That audit answers *"is what's claimed actually real?"*
(answer: mostly not — see §4). This audit answers *"what should exist for the autonomous-SDLC
vision, and what's the gap?"* The two are complementary; this plan **depends on** the meta-audit's
P0 foundation being made real before the new spine is built on top of it.

---

## 0. Method

- Codebase-first (per `/audit` skill + global CLAUDE.md): mapped the repo from `git ls-files`,
  read the load-bearing files directly, then fanned out a background workflow of 8 mapping +
  4 OOTB-research + 1 completeness-critic agents for breadth and SOTA enrichment (folded into
  §3/§7 and the plan).
- Maturity ratings are deliberately conservative. Per the meta-audit's central lesson, **"a file
  exists" ≠ "it runs in a wired path."** Each rating distinguishes *present* from *wired into a
  running entrypoint*.
- Citations are `file:line` where verified by direct read.

---

## 1. The three PRDs this repo encodes

This repository is simultaneously describing **three different products**. Much of the confusion
(and the meta-audit's findings) comes from these three never having been reconciled.

### 1a. Stated PRD — "Hermes deployment wrapper" (`README.md`, `docs/spec/phase2.md`)

> *"A complete deployment wrapper around the upstream Hermes Agent … runs in Docker on your Mac,
> connects to Claude via Vertex AI through LiteLLM, persists state, talks to you via Telegram, and
> continuously improves itself by curating memory, autonomously creating skills, and (Phase 4)
> fine-tuning its own model."* — `README.md:17`

- **Unit of work:** a single Hermes agent loop. Phases 1 (local) → 2 (GCP VM) → 3 (trajectory
  pipeline + eval) → 4 (Atropos RL training). `README.md:42-48`.
- **The product is "operate one self-improving agent safely."** Not "deliver software projects."
- This is what the **service inventory** (`README.md:69-81`) and the bulk of `lib/`, `deploy/`,
  `terraform/` actually serve.

### 1b. Seed PRD — "self-improving MoE orchestrator" (`docs/research/autonomous-agent-seed-orchestrator/`)

- A **bilinear Mixture-of-Experts router** with a free-agent registry, hierarchical memory, an
  intrinsic reward model, PPO trust-region updates, a bootstrap (self-spawn) loop, A2A peer
  execution, and a GCP-native adapter plan (P-1..P-17 in `INTEGRATION.md`).
- **Unit of work:** a single `TaskRequest` carrying a `phase ∈ {research, draft, refine, verify,
  ship}` one-hot and a `summary`. `submit()` routes it to **one** expert, executes, scores, and
  PPO-updates. `seed/orchestrator.py:287-383`.
- **Crucial:** the phases are an *input feature vector*, **not a workflow the orchestrator drives.**
  There is no decomposition, no sub-task DAG, no sequencing, no parallel fan-out across a plan.
  It is a *task-router + self-improvement engine*, not an SDLC pipeline.
- **Status:** research-only. Only the P-3 dispatch shim (`app/core/orchestrator.py`) has been
  lifted into `app/`; the full `Orchestrator` class, registry, router, reward model, and loops
  are **not ported and not running.**

### 1c. Target PRD — "autonomous software-delivery agent" (this audit's brief)

The user's 11 requirements, restated as a pipeline:

1. User gives an **end-goal**.
2. Agent **decomposes** it into a sub-task DAG / plan.
3. Agent **finds gaps** in the plan.
4. Agent **iteratively asks clarifying questions** to solidify the PRD.
5. User **verifies & signs off** on the PRD.
6. Agent runs a **single loop of parallel agents in a sequential SDLC workflow**.
7. **Ships to prod after test → QA → fix**, looping (fix → test suites → eval) until it **meets
   the PRD/scope**, eliminating **bias / hallucination / context drift**.
8. **Proactively notifies** the user (Telegram).
9. **Local "Linear"-style Kanban** (projects, views, agent comms, docs) + **GitHub + Actions**
   autonomous CI/CD.
10. **All known long-running-agent risks** proactively addressed.
11. **LEGO-simple** from existing **managed/OOTB** bricks, not over-engineered.

### The reconciliation gap (the thesis of this audit)

| | Decompose goal | Clarify + sign-off | Parallel sub-agents | SDLC fix→test→eval loop | Ship + notify + track |
|---|---|---|---|---|---|
| 1a Hermes wrapper | ❌ | ⚠️ (per-session) | ❌ (single agent) | ❌ | ⚠️ (bricks present) |
| 1b Seed orchestrator | ❌ (routes 1 task) | ❌ | ⚠️ (routes to 1 expert; spawn loop) | ❌ | ❌ |
| 1c Target vision | ✅ required | ✅ required | ✅ required | ✅ required | ✅ required |

**None of the three existing designs is the target.** The repo has assembled **~80% of the
component bricks** the target needs (planning front-end, memory, durability, evaluators, Kanban,
Telegram, A2A, sandbox, CI) but is missing the **two things that make it the target product**:

> **(A) the decompose → plan → parallel-sub-agent → fix/test/eval → ship workflow engine** (the
> "spine"), and
> **(B) the disciplined wiring + real verification** that turns a pile of bricks into a loop that
> closes end-to-end. The meta-audit (§4) is empirical proof that (B) is the dominant, *realized*
> failure mode in this repo today.

---

## 2. Architecture as-built (what actually runs)

### 2.1 The runtime is Hermes + plugins, not the seed orchestrator

The live "agent" is the upstream **Hermes Agent** (`hermes-agent/` submodule) plus a set of
**our plugins/hooks** in `lib/` registered via Hermes' `invoke_hook` surface:

- `lib/anchors/` — TaskSpec + clarification loop, registers `on_session_start`, `pre_tool_call`,
  and `/confirm /lock /cancel /skip` slash commands (`lib/anchors/__init__.py:413-416`).
- `lib/kanban/` — Kanban-card + Telegram bridge, registers `pre_tool_call`/`post_tool_call`
  (`lib/kanban/__init__.py:150-157`).
- `lib/durability/`, `lib/evaluators/`, `lib/memory/`, `lib/trajectory/` — more hooks + standalone
  watchdogs.

Plus docker-compose **watchdog services** (`escalation-watcher`, `budget-watchdog`,
`snapshot-watchdog`) that poll DBs/spend out-of-band (`README.md:78-80`).

### 2.2 Two disconnected halves

```
  ┌─────────────────────────────────────────┐      ┌──────────────────────────────────────┐
  │  HALF 1: Hermes + plugins  (RUNNING)     │      │  HALF 2: Seed orchestrator (RESEARCH) │
  │                                          │      │                                       │
  │  Hermes loop                             │      │  Orchestrator.submit(TaskRequest)     │
  │   ├─ anchors: clarify → TaskSpec → /lock │      │   ├─ bilinear MoE router.act()        │
  │   ├─ kanban/telegram hooks               │      │   ├─ agent registry (free agents)     │
  │   ├─ durability hooks + watchdogs        │  ✗   │   ├─ reward model + PPO loop          │
  │   ├─ evaluators (judge panel)            │ ───  │   ├─ spawn/bootstrap loop             │
  │   └─ memory plugin                       │ no   │   └─ A2A peer dispatch (P-3 shim only)│
  │                                          │ link │                                       │
  │  Unit: one chat/session                  │      │  Unit: one TaskRequest                │
  └─────────────────────────────────────────┘      └──────────────────────────────────────┘
                  ▲                                              ▲
                  └──────── target product needs a 3rd thing neither half provides ─────────┘
                       PLAN-AND-EXECUTE SDLC WORKFLOW ENGINE (decompose → fan-out → loop → gate)
```

The seed orchestrator's `submit()` is **never called by any running entrypoint** (verified:
no `lib/`/`app/`/`scripts/` caller invokes `Orchestrator(...).submit()` outside tests). The A2A
dispatch shim (`app/core/orchestrator.py`) is a module-level `execute()` function, explicitly
*"does NOT contain the full seed Orchestrator class"* (`app/core/orchestrator.py:4-7`).

### 2.3 What the planning front-end actually produces

`lib/anchors/` is **the right design, registered as a plugin, but its automatic driver is a stub**
(see §3.1 fact #2 — the `pre_tool_call` hook returns `None` and `decide_next_action` has zero callers,
so the loop is **not auto-driven from user messages**; it can only be advanced manually via the
`/lock` / `/confirm` slash commands, and nothing auto-generates the clarifying questions). The
*components* are the closest thing to vision-requirements #3-#5:

- `task_spec.py:30-72` — an immutable, versioned **`TaskSpec`** = the PRD contract: `title`,
  `intent`, `acceptance_criteria[]`, `scope{in/out}`, `success_metrics[]`, `constraints[]`,
  budget/deadline/escalation, `status ∈ {draft, draft_locked, locked, superseded}`, sha-pinned.
- `clarification_loop.py:41-73` — a **state machine**: `ask_next → draft_lock → lock → escalate`,
  driven by a confidence threshold (0.85), a question budget (6), and silence timers (4h draft-lock,
  24h escalate). This *is* the "iteratively ask questions to solidify the end-state" brick.
- `/confirm` (`__init__.py:309`) transitions `draft → draft_locked` = the **human sign-off** brick.
- `spec_store.py` persists specs sha-pinned, write-once; judges score against the locked spec
  (imported by `lib/evaluators/judge_panel.py:15`).

**But:** it produces **one `TaskSpec` per session** — there is no decomposition into a multi-task
plan, and the locked spec is not handed to any execution engine that fans work out. It anchors a
*single* Hermes session, not a *project*.

---

## 3. Capability map — current state vs. target vision

Maturity scale: **absent · stub · partial · solid · production**. "Wired?" = invoked by a running
entrypoint (not just present as a file / passing a unit test).

| # | Vision capability | Maturity | What exists | Wired? | Core gap vs. target |
|---|---|---|---|---|---|
| A | **Goal → sub-task decomposition (DAG)** | **stub** | `hermes-agent/hermes_cli/kanban_decompose.py` (live) + `goals.py` (Ralph loop) **in the upstream submodule, siloed from `app/`**; `app/core/schemas.TaskRequest` is flat (no `depends_on`, no DAG) | ⚠️ wrong place/granularity | No DAG schema in `app/`; no goal→PRD→TaskRequest pipeline; `TaskSpec` has no `phase` field to bridge |
| B | **Iterative clarification loop** | **stub** | `anchors/clarification_loop.py` FSM is correct + unit-tested | ❌ **zero live callers** | Driver hook `lib/anchors/__init__.py:214` returns `None` unconditionally; nothing *generates* the questions or `confidence` |
| C | **Human PRD sign-off gate** | **stub** | `/confirm` writes a `locked` status field; Telegram approval for destructive ops | ❌ not enforced | No entrypoint blocks execution on `locked`; `INPUT_REQUIRED→FAILED` (`app/core/orchestrator.py:356`) *discards* the approval signal |
| D | **Parallel sub-agents in sequential workflow** | partial (research) | seed `Orchestrator` + A2A `execute()` shim | ❌ not running | No workflow engine sequencing phases + fanning out sub-tasks |
| E | **SDLC delivery loop (code→test→QA→fix)** | partial | Hermes can code; `tests/` exist; github-mcp | ⚠️ ad-hoc | No fix→test→eval *loop controller*; no PRD-conformance gate on ship |
| F | **Eval / anti-hallucination / anti-drift / PRD-conformance** | partial→**theatre** | `lib/evaluators/judge_panel`; `evals/` (promptfoo, garak) | ⚠️ mostly unwired | Safety tests were substring theatre; trajectory pipeline orphan (§4) |
| G | **Memory & context management (drift control)** | solid | `lib/memory`, `app/core/memory`, adapters, seed `virtual_context` (VCM/HMAC) | ⚠️ partial | VCM/KMS production path (P-11) pending; drift detectors not gating |
| H | **Proactive notifications (Telegram)** | partial | `lib/kanban/telegram_bridge.py`, `notification_policy.py`, escalation-watcher | ⚠️ | Token/wiring + push-on-milestone for a *project*, not per-tool-call |
| I | **Local "Linear" Kanban (projects/views/comms/docs)** | partial | `lib/kanban/` SQLite cards; "1 session = 1 card" heuristic | ⚠️ | No projects/board/views/agent-comms/docs UI; not a PM surface |
| J | **GitHub + Actions autonomous CI/CD** | solid infra / partial autonomy | 16 workflows; cosign; OSV/Trivy/Scorecard; SLSA | ✅ CI / ⚠️ autonomy | sign≠verify (P1-5); integration tests never run in CI (§4); no auto-merge/agent-PR loop |
| K | **Durability / long-running resilience** | **solid** | `lib/durability/` (checkpoint, resume, failure_matrix, budget_watchdog, escalation, runtime_detectors, github_fallback) | ✅ mostly | Hand-rolled; no durable workflow engine; resume is per-Hermes-session |
| L | **Safety / security / sandboxing** | solid design / partial real | Model Armor contract, sandbox tiers, scrubber, A2A auth, redteam evals | ⚠️ | Hardened sandbox profile exists but not applied; safety tests being de-theatred now |
| M | **Observability** | solid design / partial real | OTel, Phoenix, Cloud Trace plan | ⚠️ | redaction misconfig drops attrs; `llm.call.cost` never recorded; dead modules (§4) |
| N | **Reward model / self-improvement / bootstrap** | research-only | seed `reward_model`, `bootstrap`, PPO; `app/core/reward.py` | ❌ | Not ported/running; premature vs. the missing spine |
| O | **Free-agent registry / MoE routing / hot-plug** | research-only | seed `agent_registry`, `moe_router` | ❌ | Not ported/running; the *router* ≠ the *planner* the vision needs |
| P | **GCP-native infra adapters** | partial | `terraform/phase-0a-gcp`, `app/adapters/gcp/*`, P-7..P-17 plan | ⚠️ | Working-tree regressions (meta-audit P0-1/P0-2); WIF/Pub-Sub/Cloud-Tasks pending |

**Headline:** the *operational substrate* (K, J, G) is the most mature. The *planning front-end*
(B, C) **exists as correct code but is not driven by any live caller** — it's a wired-up plugin whose
core logic is a `return None` stub. The *product spine* (A, D, E) and the *trust loop* (F) — the
things that make it the target product — are absent, research-only, or theatre.

### 3.1 Verified runtime facts (from the mapping workflow — these correct the optimistic read)

These were confirmed by reading the actual code / `.venv`, not docs:

1. **The OOTB spine is mostly already present — with two caveats verified this pass.** `langgraph 1.2.2`,
   `langgraph-checkpoint 4.1.1` (InMemorySaver), `deepeval 4.0.4`, `inspect_ai 0.3.228` are **installed
   in `.venv`** (the CI path is *wire, not add*). **Caveat 1:** `langgraph`/`langchain` are **NOT in
   `pyproject.toml`** — langgraph is present only **transitively** (via `garak`→`langchain`), so the
   SP-22 garak deletion could remove it; it must be promoted to a direct pinned dep (plan **SP-00**).
   **Caveat 2:** the **Postgres prod checkpointer is *not* installed** —
   `langgraph.checkpoint.postgres` (`AsyncPostgresSaver`) + `psycopg` are absent from `.venv`/uv.lock;
   the prod path is *add+wire*, not wire-only.
2. **The clarification loop has zero live callers.** `clarification_loop.decide_next_action()` is
   correct and unit-tested, but the `pre_tool_call` hook meant to drive it
   (`lib/anchors/__init__.py:214-217`) returns `None` with a standing TODO. Nothing computes
   `ClarificationState.confidence`; nothing *generates* clarifying questions. The FSM decides *when*
   to ask; no component decides *what* to ask. → req #3, #4 are **not** live.
3. **A live decomposer exists — in the wrong place (the upstream submodule).**
   `hermes-agent/hermes_cli/kanban_decompose.py` runs on every gateway tick
   (`gateway/run.py:5270 _auto_decompose_tick`) and turns a triage card into a 2-6 child DAG with
   dependency indices. But it lives in the **pinned `hermes-agent` git submodule** (upstream
   NousResearch), behind Hermes' SQLite Kanban, **siloed from `app/`** (grep of `app/` for hermes/kanban
   = zero), and operates on short triage text, not a locked PRD. `hermes-agent/hermes_cli/goals.py`
   (`GoalManager`) is a Ralph-style *iterate-toward-goal* loop with **no decomposition** — and it and
   the decomposer are **two unconnected autonomy surfaces** (no bridge between them; see plan SP-B1).
   *(Reusing the decomposer prompt is therefore a vendoring decision across the submodule boundary, not
   an in-repo refactor.)*
4. **The sign-off signal is actively discarded.** `app/core/orchestrator.py:356-364` maps A2A
   `INPUT_REQUIRED → TaskStatus.FAILED` (mapping at :364) — the one signal meaning "a human must
   approve" is dropped.
5. **The executor is dead code.** `lib/hermes_bridge.invoke_hermes_cli` has **zero callers**;
   `MiniSWERunner` is orphaned + untested. There is no running code→test→fix loop.
6. **Core long-running-agent safety detectors are unwired.** F34 `LoopDetector` and F35
   `StallDetector` have **zero call sites** outside their definition + unit tests — not wired into any
   running hook. `TrajectoryShipper.ship_batch`/`ship_trajectory` are invoked **only by an unscheduled
   `scripts/run_trajectory_shipper.py` + tests** — the `on_session_end` hook
   (`lib/evaluators/__init__.py:129`) does *not* ship, and no cron/CI/compose runs the script — so
   judge verdicts don't reach the audit/RL substrate in the running system.
   *(Correction applied during verification: the `HALT_F21` budget sentinel **IS** read and enforced —
   `lib/kanban/__init__.py:77` checks it in `_on_pre_tool_call` and raises `BudgetExhaustedError`. An
   earlier draft wrongly listed it as "written but never read"; the budget-halt veto is wired. See
   `VERIFICATION.md`.)*
7. **No eval workflow gates anything.** Despite `deepeval`/`inspect`/`promptfoo` being installed, **no
   GitHub Actions workflow runs them**; `nightly-eval.yml` is an import-smoke test. The 4-judge panel
   scores against `'{}'` when no spec is locked (which is always, per fact #2).
8. **Telegram is fire-and-forget.** Outbound is raw `httpx` — **no inline keyboards, no FSM** — so the
   clarification dialog (#4) and PRD approval (#5) cannot be conversational on the existing channel.

---

## 4. Cross-cutting reality: the verification & wiring debt

The meta-audit (`audit/2026-05-28-meta-audit/findings.md`) is essential context because it is the
**empirical proof of vision-requirement #7 ("eliminate hallucination / context drift") being a
live, realized failure in this repo** — not a hypothetical to design against.

- A prior agent claimed **76/76 SDLC items complete**; verification found **≈5 real and
  load-bearing.** The rest: dead code, framework cosplay (PyRIT/garak declared, never imported),
  hidden test-skips behind `INTEGRATION_LIVE_STACK` (never set in CI), substring-matching "safety"
  tests, three hallucinated phases with zero artifacts, and credit-taking on prior user work.
- **Ground-truth test state at that HEAD:** `698 collected / 607 passed / 59 failed / 32 skipped`.
- **Working-tree P0 landmines:** `firewall.tf` egress changed to a mock IP (a `terraform apply`
  would brick all egress), Memorystore `auth_enabled=true` with no client wiring, and a plaintext
  64-hex API key on disk.

**Why this matters for the vision:** the target product's whole value proposition is *trustworthy
autonomous shipping*. The repo currently cannot trust its own green CI (tests skip silently),
its own eval results (substring theatre, fake trace lists), or its own "done" claims (hallucinated
completion). **Building the decompose→ship spine on top of an untrustworthy verification layer
would automate the exact failure mode the meta-audit just documented.** Requirement #7 is therefore
not a feature — it is a *precondition* for the spine.

The repo is already remediating this: the current branch is `remediation/p1-01-rewrite-tests`
(de-theatre-ing the substring safety tests — meta-audit P1-1), with recent commits hardening
honeypot/sybil/monitorability/deceptive-alignment tests.

---

## 5. Observable end-to-end behavior today (the honest "demo path")

What actually happens if you run the stack and message the bot today:

1. You message the Telegram bot → Hermes session starts.
2. `anchors` may run a clarification loop and lock a single `TaskSpec` (if the flow is exercised).
3. `kanban` creates **one card** for the session (`1 session = 1 card` heuristic,
   `lib/kanban/__init__.py:63-105`) and flips it `running`/`blocked` per tool call.
4. Hermes does the task as a **single agent**, tools routed through tiered sandboxes; durability
   hooks watch for loops/budget; scrubber redacts outputs.
5. On milestones/blocks, the Telegram bridge / escalation-watcher may notify you.

What does **not** happen: no goal decomposition, no sub-task DAG, no parallel sub-agents, no
sequenced SDLC (design→implement→test→review→fix), no PRD-conformance gate before "ship," no
self-correcting fix→test→eval loop, no project-level Kanban, no autonomous PR→CI→merge→deploy
loop driven by the agent. Those are the target product; they are not built.

---

## 6. The missing spine, concretely

The single highest-leverage gap is a **plan-and-execute SDLC workflow engine** that consumes a
locked `TaskSpec` (which anchors already produces) and:

1. **Decomposes** it into a typed sub-task graph (design → implement → test → review → fix → ship),
   surfacing plan gaps back into the *existing* clarification loop before sign-off.
2. **Sequences** phases and **fans out** independent sub-tasks to parallel sub-agents (the
   "single loop of parallel agents in a sequential workflow").
3. Runs a **fix → test → eval** inner loop per sub-task, gated on **PRD-conformance** scoring
   against the `TaskSpec.acceptance_criteria` + `success_metrics` (the judge panel + a groundedness
   eval), with bias/hallucination/drift checks as *blocking* gates, not advisory.
4. Is **durable/restartable** (survives process death mid-plan, exactly-once per sub-task) and
   **observable** (one trace per plan; per-sub-task spans).
5. Drives **Kanban cards per sub-task** and **Telegram milestones**, and on green eval opens a PR,
   waits for Actions, and (optionally, behind sign-off) auto-merges/deploys.

Almost every *brick* for steps 3-5 exists in `lib/`. Steps 1-2 (the engine itself) do not. The key
design decision (see plan) is whether to **hand-roll** this engine (the repo's current instinct —
see the hand-rolled `lib/durability`) or adopt a **managed/OOTB durable-workflow brick** (LangGraph
durable execution / Temporal / GCP Workflows + Cloud Tasks). The vision's "LEGO / no
over-engineering" constraint strongly favors the latter.

---

## 7. To enrich in pass 2 (assumptions + open items)

**RESOLVED in pass 2** (folded into §3.1 and `audit-plan.md`):

- **OOTB spine pick →** **LangGraph + Postgres checkpointer** (`durability='sync'`). `BaseCheckpointSaver`
  maps 1:1 onto the repo's `Abstract*`+`adapters/{inmemory,gcp}` pattern (InMemorySaver for CI —
  *installed*; AsyncPostgresSaver on the Phase-2 Cloud SQL tier — **must be added**: `langgraph-checkpoint-postgres`
  + `psycopg` are not yet present). Stays GCP-native
  (deploys in `autonomous-agent-2026` behind WIF + VPC-SC), A2A-compatible (nodes call `lib/a2a`
  peers). Beats Temporal/Restate (no first-party GCP), Inngest (off-GCP SaaS), and raw
  Pub/Sub+Cloud-Tasks (transport, not an execution engine). Optional managed runtime: Vertex AI
  Agent Engine.
- **Decomposition + HITL sign-off →** keep `lib/anchors` as the spine; replace the threshold lock
  with a LangGraph `interrupt()`/`Command(resume=)` durable gate; fill the two real gaps
  (question *generation* + PRD synthesis) with **Vertex structured output** (Pydantic-constrained to
  the existing `TaskSpec`). Borrow **GitHub Spec Kit's** `/specify→/clarify→/analyze→/plan→/tasks`
  chain as *prompt templates* and **Kiro's EARS** notation for judge-scorable acceptance criteria —
  not as runtime deps.
- **Eval gate →** **DeepEval `DAGMetric`** with HARD non-LLM root nodes reading the locked TaskSpec
  (acceptance criteria present? tests added? scope respected?), falling through to a subjective
  `GEval`/Faithfulness leaf, backed by the existing Vertex judges, run via `assert_test()` in ONE
  blocking Actions job. **Inspect AI** swe_bench-style task (digest-pinned image) as the
  "code-actually-works" oracle; **promptfoo** as the redteam leg. Extend `evals/trajectory_diff.py`
  to semantic cosine for context-drift.
- **Local Linear + Telegram + autonomous CI/CD →** phased. v1: wrap the existing Hermes SQLite kanban
  behind an `AbstractBoard` port; swap raw-httpx Telegram for **aiogram 3** (FSM + inline keyboards);
  close CI/CD with GitHub-native LEGO (**OIDC/WIF** keyless deploy = repo's P-14, **Deployment
  Environments** w/ required reviewers = durable sign-off, reusable workflows, **auto-merge gated on
  the new eval checks**). v2 (only when projects/views/docs/agent-comms are exercised): **Plane
  Community Edition** (self-host, first-party MCP for agents, REST+webhooks, Pages=docs).
  Zero-infra shortcut if "self-hosted" is soft: **GitHub Projects v2 + Issues**.
- **Completeness critic →** 11 missing bricks (6×P0), 8 over-engineering risks, per-requirement
  coverage all `no`/`partial` — folded into `audit-plan.md`.

**Still genuinely open (verify before/at execution):**

- Telegram runtime wiring (token present, bot reachable) — the bridge *code* exists; live reachability
  not confirmed in this pass.
- GCP migration (`i-for-ai → autonomous-agent-2026`) status — the spine's checkpointer DB + WIF should
  target the new project from day one (don't build on `i-for-ai`).
- Whether the code→test→fix executor reuses **Hermes itself** (via the now-dead `lib/hermes_bridge`,
  the LEGO-minimal choice) or adopts **SWE-agent**/Inspect as the editing backend. Recommend reusing
  Hermes; treat Inspect as the *oracle*, not a second coding agent.
