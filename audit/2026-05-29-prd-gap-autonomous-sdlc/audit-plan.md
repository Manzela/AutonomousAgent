# AutonomousAgent — Build Plan: the Autonomous-SDLC-Agent spine (SOTA 2.0)

**Date:** 2026-05-29
**Status:** AWAITING APPROVAL — this is an audit deliverable. Do **not** implement until sign-off.
**Companion:** [`findings.md`](./findings.md)
**Source enrichment:** background workflow (10-dim map · 4 OOTB-research · completeness critic),
verified against `.venv` and live code.

## How to read this

- The goal is to turn the repo from *"a pile of strong but disconnected bricks"* into *"a loop that
  closes end-to-end"*: **goal → decompose → clarify → sign-off → parallel SDLC → test/QA/fix →
  eval-gate → ship → notify**, built LEGO-style from bricks **already in the repo or already in
  `uv.lock`.**
- Items are namespaced **`SP-*`** (Spine Plan) to avoid collision with the meta-audit's `P0-N..P2-N`.
- Each item: **what · why · where (`file:line`) · OOTB brick · effort · acceptance.**
- Priorities are by **leverage on closing the loop**, not effort.

## Relationship to the 2026-05-28 meta-audit (read this first)

The meta-audit proved that this repo's dominant, *realized* failure mode is **"a file exists" ≠ "it
runs / it's verified"** (≈5 of 76 claimed items real; 59 failing tests; a working-tree `terraform
apply` that bricks egress; a plaintext key). **That is exactly the failure the target product must
prevent.** Therefore:

> **Gate 0 (blocking): confirm the foundation is real before any `SP-*` work.**
> You cannot build a *trustworthy autonomous shipper* on top of an untrustworthy verification layer —
> doing so would automate the meta-audit's findings.
>
> **Verified status on `remediation/p1-01-rewrite-tests` (2026-05-29, all re-derived from fresh command
> output — see `VERIFICATION.md`):**
>
> | meta-audit item | status | evidence |
> |---|---|---|
> | P0-1 firewall egress | ✅ **FIXED** | `firewall.tf:39` `0.0.0.0/0` on port-scoped rules; `10.0.0.10` gone; `deny_egress_all` @65534 |
> | P0-2 Memorystore AUTH | ✅ **FIXED** | `memorystore/main.tf:50` `auth_enabled` commented; never set true in any commit |
> | P0-3 secret hygiene | ⚠️ **PARTIAL** | git-tracking FIXED (all `*.sops`); **BUT plaintext on disk NOT deleted/rotated** — `secrets/sa-keys/{cloud-sql-proxy,litellm-proxy,snapshot-watchdog}.json` (real GCP SA private keys, mode 0644) + `secrets/hermes-provider.env`. The earlier "RESOLVED" grade moved the goalposts from rotate+delete to merely not-tracked. **Restore the original bar.** |
> | P0-4 dead observability code | ✅ **FALSE POSITIVE** | `lib/observability/{ledger,failure_detectors}.py` **never existed** (`git log --all` empty). Close as "no file to delete"; the *real* gap is that TamperEvidentLedger / FailureDetectors have **zero implementation anywhere** (architectural, not cleanup). |
> | P0-5 OTel redaction | ✅ **FIXED** | `collector.prod.yaml:31` `allow_all_keys: true` + `blocked_key_patterns` (commit 95dab2a9) |
> | P0-6 `update_plan.py` | ✅ **FIXED** | absent; pre-commit guard + CLAUDE.md block re-introduction |
> | P1-1 safety-test de-theatre | 🔴 **REGRESSED / UNVERIFIED** | hardening is committed in HEAD **but the working tree reverts it** (`git status`: `M` on the 5 tests + `conftest.py`, `D` `_canary_server.py`, `D` `test_naive_multiturn_probe.py`) **and** the suite runs in **NO CI workflow** (`@pytest.mark.integration+docker`; `INTEGRATION_LIVE_STACK` never set; `no-skip-on-remediation.yml` is a static grep that executes nothing). A dev running pytest today exercises the OLD keyword-theatre versions. |
>
> **New Gate-0 prerequisites this verification surfaced (do these FIRST):**
> - **SP-00 — Pin the spine.** `langgraph`/`langchain` are **NOT in `pyproject.toml`** — langgraph is
>   present only **transitively**, and the chain `garak → langchain → langgraph → langgraph-checkpoint`
>   is the **SOLE path** (verified in uv.lock: `langgraph ← langchain ← garak` only; `deepeval`/`inspect`
>   pull no langchain). So when SP-22 drops `garak` ("framework cosplay" per the meta-audit), the spine's
>   foundation **will be removed**, and a `garak` bump can silently version-shift it. Only `InMemorySaver`
>   is installed (`langgraph-checkpoint-postgres` + `psycopg` are **absent**). **Promote `langgraph==1.2.2`
>   + `langgraph-checkpoint` to direct, pinned deps in `pyproject.toml` and re-lock before any `SP-*`.**
> - **SP-00b — SA-key → WIF + on-disk plaintext.** 3 plaintext SA private keys on disk + `sa-keys.tf`
>   still provisions long-lived SAs. This is a P0-3-class secret-on-disk exposure broader than the one
>   the meta-audit caught. Delete the on-disk plaintext after confirming `.sops` decrypts, make
>   `encrypt_secrets.sh` auto-delete on success, and land the WIF migration **before** SP-12 (which
>   assumes keyless deploy).
> - **SP-00c — Make P1-1 real in CI.** Restore (or intentionally re-commit) the hardened tests, and run
>   them in a workflow that brings up `docker-compose.ci`, sets `INTEGRATION_LIVE_STACK=1`, and **fails
>   on collected-but-skipped** (meta-audit P1-6). Otherwise the spine's safety foundation is
>   asserted-but-unrun — the exact thesis-B failure.
>
> **Absorbed-vs-orphaned meta-audit ledger** (items this plan must not silently drop):
> - *Superseded by SP-*:* P1-1→SP-00c, P1-6→SP-00c/SP-06, P1-2 (ship_trajectory)→SP-14, P1-9
>   (trajectory_diff JSONL)→SP-15, P1-12→SP-22.
> - *Orphaned — add explicitly:* **P1-4 (`llm.call.cost` histogram never `.record()`ed)** — MUST land
>   before SP-23 claims cost observability and before SP-06/SP-11 fan-out multiplies spend; **P1-3**
>   (REJECTED feedback queue never drained); **P1-8** (`evals/trace_to_eval_pipeline.py` still
>   hardcodes a fake trace list); **P1-10** (OPA `a2a-policy.rego` orphan — note: A2A *identity* authz
>   IS live via JWT + `peers.yaml` at `server.py:202`; only *per-tool* OPA authz is orphaned);
>   **P2-3** (Langfuse compose still missing ClickHouse/Redis/MinIO + hardcoded `NEXTAUTH_SECRET`).
>
> Net: the destructive landmines (P0-1/2/5/6) are cleared; **P0-3 (on-disk SA keys) and P1-1 (CI +
> working-tree) are genuinely open** and are now Gate-0 items. This is **not** the multi-week blocker
> first implied, but it is **not** "all clear" either.

---

## North-Star architecture (target end-state)

A single **LangGraph `StateGraph`** (already a dependency) is the spine. It *wires the existing
bricks* — it does not replace them, and it lets us **delete** the hand-rolled checkpointer.

```
 Telegram (aiogram3) ─┐                                    ┌─ Kanban cards (AbstractBoard → Plane v2)
                      ▼                                    │   Telegram milestones (notification_policy)
        ┌──────────────────────── LangGraph StateGraph (checkpointer: InMemory CI / Postgres prod) ───────────────────────┐
        │  goal_intake → clarify ⇄ (Vertex structured-output Q-gen) → decompose(TaskGraph DAG) → [interrupt()=PRD sign-off]│
        │        │                                                                                        │               │
        │        └── lib/anchors TaskSpec (PRD contract, sha-pinned) ◄── decide_next_action() FSM ────────┘               │
        │                                                                                                                  │
        │   fan_out(asyncio.gather over ready DAG nodes) → execute(Hermes via hermes_bridge) → test/QA → fix-loop          │
        │                                   │                                                      ▲                       │
        │                                   ▼                                                      │ (until pass)          │
        │            eval_gate: DeepEval DAGMetric(reads locked TaskSpec) + Inspect oracle(pytest) + drift(cosine)         │
        │                                   │ green                                                                        │
        │                                   ▼                                                                              │
        │            ship: open PR → GitHub Actions (required eval checks) → Environments(reviewer) → auto-merge/deploy    │
        └──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
   cross-cutting (already built, mostly): durability hooks (F34/F35/HALT — to be WIRED), scrubber, Model Armor,
   A2A peers, OTel/Cloud Trace, memory store. Self-improvement (seed MoE/PPO) stays OUT of scope (see §NOT-doing).
```

The **seed orchestrator's MoE router / PPO / reward model / bootstrap (dimensions N, O)** are
deliberately **excluded** — they close none of the 11 requirements and are the single biggest
over-engineering trap (see §"What we are deliberately NOT doing").

---

## P0 — Make the loop physically exist and gate on real evidence

### SP-01 — Stand up the LangGraph spine (the front door + the loop body + durable backbone)
- **What:** One `StateGraph` with nodes `goal_intake → clarify → decompose → sign_off → fan_out →
  execute → eval_gate → ship`, checkpointed per `thread_id`. Adapter pattern per CLAUDE.md:
  `InMemorySaver` (CI) / `AsyncPostgresSaver` (prod) behind an `AbstractCheckpointer`.
- **Why:** Closes vision req #1 (a front door) and #6 (the loop), and gives durable resume for free.
  It also lets us **delete** the bespoke file checkpointer rather than maintain two persistence
  systems (split-brain risk).
- **Where:** new `app/core/graph.py`; `app/adapters/{inmemory,gcp}/checkpointer.py`; retire
  `lib/durability/checkpoint.py` + `resume.py` file-based paths (keep the F-matrix/handlers).
- **OOTB brick:** `langgraph 1.2.2` + `langgraph-checkpoint 4.1.1` — **CI/`InMemorySaver` path is
  wire-only (installed).** **Prod path is add+wire (corrected):** `langgraph.checkpoint.postgres`
  (`AsyncPostgresSaver`) + `psycopg` are **NOT installed and NOT in uv.lock** — add an explicit
  sub-step `uv add langgraph-checkpoint-postgres "psycopg[binary]"`. Depends on **SP-00** (pin
  `langgraph` as a direct dep first — it is currently only transitive).
- **Effort:** 3–4 days CI skeleton; +1 day for the Postgres adapter (add deps + `AbstractCheckpointer`
  GCP subclass).
- **Acceptance:** kill the process mid-graph; on restart the run resumes from the last completed
  node, exactly-once on `thread_id` (CI test with `InMemorySaver`; **separate** integration test on
  Cloud SQL *after* the Postgres saver is added).

### SP-02 — Add the `TaskGraph` DAG schema + `TaskSpec → TaskRequest` bridge
- **What:** `TaskNode {id, phase, summary, depends_on: list[id], acceptance_ref}` and `TaskGraph` in
  `app/core/schemas.py`; a translator from a locked `TaskSpec` to ready `TaskRequest`s.
- **Why:** `app/core/schemas.TaskRequest` is flat — there is **no DAG type in `app/`**, so reqs #2
  and #6 cannot execute. This is the missing data contract everything dispatches over.
- **Critical sub-contract (verification surfaced):** `lib/anchors/task_spec.TaskSpec` has **no `phase`
  field**, but `TaskRequest` requires `phase ∈ {research,draft,refine,verify,ship}`. So the bridge is
  **not a trivial rename** — it must *assign* an SDLC phase per sub-task. Specify that mapping
  explicitly (it's a data contract, not just wiring); without it even a complete spine can't translate
  a locked spec into the executor's action space.
- **Where:** `app/core/schemas.py`; populate by reusing the decomposer prompt from
  **`hermes-agent/hermes_cli/kanban_decompose.py`** (note: this lives in the pinned **upstream
  submodule** — **vendor a copy** of the prompt into `app/`/`evals/` rather than importing across the
  submodule boundary, to avoid coupling the spine to a submodule bump + the upstream `auxiliary_client`
  config; it's a vendoring/licensing decision, not an in-repo refactor).
- **OOTB brick:** Pydantic (in repo) + a vendored copy of the `kanban_decompose` prompt.
- **Effort:** 2–3 days.
- **Acceptance:** property test — a locked `TaskSpec` produces a valid DAG (no cycles; every
  `depends_on` resolves; every node has a valid `phase`); topological order matches the decomposer's
  parent indices.

### SP-03 — Drive the clarification loop + generate the questions (reqs #3, #4)
- **What:** Replace the `return None` stub at `lib/anchors/__init__.py:214` with a driver that (a)
  calls a **Vertex structured-output "spec-drafter"** (Pydantic-constrained to `TaskSpec`) to emit
  draft PRD fields + a typed clarifying-question list + an ambiguity/gap report, (b) feeds confidence
  into `ClarificationState`, (c) calls `decide_next_action()` as the LangGraph conditional edge.
- **Why:** The FSM is correct but has **zero callers**; nothing *generates* questions or `confidence`.
  This is what makes "iteratively ask to solidify the PRD" actually happen.
- **Where:** `lib/anchors/__init__.py:214-217`, `clarification_loop.py` (edge), new `spec_drafter`.
- **OOTB brick:** Vertex AI structured output (`responseSchema`/Pydantic) — GCP-native, the existing
  backend, **no new dependency**. Prompt templates borrowed from **GitHub Spec Kit**
  (`/specify→/clarify→/analyze`); acceptance criteria in **EARS** notation (Kiro) so judges can score
  them.
- **Effort:** 3 days.
- **Acceptance:** given an ambiguous goal, the loop emits ≥1 grounded clarifying question, raises
  `confidence` as answers arrive, and locks within the 6-question budget; makes the judge panel
  meaningful (it currently scores against `'{}'`).

### SP-04 — Real, durable PRD sign-off gate + fix the `INPUT_REQUIRED` black hole (req #5)
- **What:** A LangGraph `interrupt()` / `Command(resume=...)` gate after `clarify`. Execution cannot
  proceed past it without a human resume. Re-map A2A `INPUT_REQUIRED` to **route to this interrupt**
  instead of `FAILED`.
- **Why:** No entrypoint currently blocks on `locked`; `app/core/orchestrator.py:356` actively
  *discards* the approval signal. This is the literal "user verifies and signs off" requirement.
- **Where:** `app/core/graph.py` (interrupt node); `app/core/orchestrator.py:345-369` (`_map_a2a_status`).
- **OOTB brick:** LangGraph HITL primitive (installed) + existing `lib/kanban/telegram_bridge` to
  surface the draft PRD; durable across restart via the SP-01 checkpointer.
- **Effort:** 2 days.
- **Acceptance:** a graph paused at sign-off survives a process restart and resumes only on an
  explicit `/approve`; an A2A peer returning `INPUT_REQUIRED` parks at the interrupt (not FAILED).

### SP-05 — Wire the code→test→fix executor (req #7, the actual product)
- **What:** An `execute` node that drives the **existing Hermes coding agent** to make changes, runs
  the project's tests, parses failures, and loops fix→test until green or a budget cap. Loop
  termination = **real `pytest` exit code**, not the `MINI_SWE_AGENT_FINAL_OUTPUT` sentinel.
- **Why:** Nothing in the running system does generate→test→fix. `lib/hermes_bridge.invoke_hermes_cli`
  is **dead (zero callers)**; `MiniSWERunner` is orphaned. Without this, no other item matters.
- **Where:** wire `lib/hermes_bridge.py` (today dead) behind the `execute` node; run in the hardened
  `deploy/sandboxes` profile (seccomp + `cap_drop=ALL` + `network_mode=none`), not `ubuntu:latest`.
  Note: `app/core/orchestrator.execute()` (the A2A shim) **also has zero non-test callers** — this is
  the first task that creates a runnable entrypoint for it. If delegating decomposition/coding to the
  hermes-agent submodule, account for its **separate `auxiliary_client` config** (cross-package
  boundary) — prefer driving Hermes via `hermes_bridge`'s subprocess/CLI seam over importing across it.
- **OOTB brick:** reuse **Hermes** (LEGO-minimal — don't add SWE-agent when a coding agent exists).
  Use **Inspect AI** `swe_bench`-style task only as the *oracle* in SP-06, not as a second editor.
- **Effort:** 4–5 days.
- **Acceptance:** given a repo with a failing test and a spec, the node produces a patch that makes
  `pytest` pass in the hardened sandbox, within the budget cap, with the fix-loop bounded.

### SP-06 — Convert eval theatre into a BLOCKING CI gate (reqs #7, #10)
- **What:** A **DeepEval `DAGMetric`** whose root nodes are **HARD non-LLM** PRD-conformance checks
  read straight off the locked `TaskSpec` (acceptance criteria satisfied? tests added? scope
  respected? no out-of-scope files touched?), falling through to a subjective `GEval`/Faithfulness
  leaf only when hard gates pass. Backed by the existing Vertex judges from `config/limits.yaml`,
  invoked via `assert_test()` in **one** GitHub Actions job that fails the PR. Add an **Inspect AI**
  task that applies the patch in a digest-pinned image and scores on `pytest` exit.
- **Why:** `deepeval`/`inspect`/`promptfoo` are installed but **no workflow runs them**; existing
  safety/eval tests are substring theatre / hardcoded-perfect trajectories (meta-audit §4). This is
  the "loop until it meets the PRD" half of req #7 and the anti-hallucination gate of req #10.
- **Where:** new `evals/prd_conformance.py` (DAGMetric); new `.github/workflows/eval-gate.yml`
  (sets `INTEGRATION_LIVE_STACK=1`); `tests/integration/test_inspect_sandbox.py` (make it a real task).
- **OOTB brick:** `deepeval 4.0.4`, `inspect_ai 0.3.228`, `promptfoo` (all installed).
- **Effort:** 4–5 days.
- **Acceptance:** the gate **fails** a deliberately spec-violating PR and **passes** a conformant one;
  runs in CI on every PR; emits a machine-readable PRD verdict the loop can consume.

---

## P1 — Close the loop end-to-end

### SP-11 — Parallel fan-out node (req #6: "single loop of parallel agents")
- **What:** A node that walks the `TaskGraph` in dependency order, translates each *ready* node to a
  `TaskRequest`, and dispatches concurrently via `app/core/orchestrator.execute()` with
  `asyncio.gather`, joining at the LangGraph super-step boundary.
- **Why:** Even with a DAG + executor, there's no batch/gather layer; `submit()` is single-task.
- **OOTB brick:** LangGraph super-steps + `asyncio.gather` + the existing A2A `execute()` for remote
  experts. **No new orchestrator** (do not port the seed `Orchestrator`).
- **Effort:** 3 days. **Acceptance:** independent sub-tasks run concurrently; dependent ones wait;
  a 3-node diamond DAG completes with correct ordering and one trace.

### SP-12 — Autonomous GitHub Actions CI/CD loop (reqs #6, #7, #9)
- **What:** (a) `repository_dispatch(type=ci-failure)` → wakes the agent via the existing A2A gateway
  to self-heal; (b) **Deployment Environments** (staging/prod) with required reviewers = durable
  prod sign-off; (c) native **auto-merge gated on the SP-06 eval checks** (never lint-only);
  (d) **OIDC/WIF** keyless deploy to `autonomous-agent-2026` (the repo's planned P-14); (e) extract
  the 16 workflows' duplication into `workflow_call` reusable workflows.
- **Why:** No agent-driven PR→CI→merge→deploy loop exists; `nightly-eval` files an issue the agent
  can't pick up; deploy still uses (or plans) long-lived SA keys.
- **OOTB brick:** GitHub-native (Environments, required checks, auto-merge, reusable workflows) +
  `google-github-actions/auth` WIF. Zero new infra.
- **Effort:** 4 days. **Acceptance:** a red CI run wakes the agent, which opens a fixing PR; auto-merge
  fires only when eval+test checks are green; prod deploy requires a human Environment approval; no SA
  JSON key in Actions.

### SP-13 — aiogram 3 migration: conversational approvals + full-lifecycle notifications (reqs #4, #5, #8)
- **What:** Replace raw-httpx Telegram with **aiogram 3** to get inline-keyboard `/approve|/reject`
  (resuming the SP-04 `interrupt()`) and an FSM for the clarification dialog; expand
  `notification_policy` from 4 Kanban transitions to the full SDLC lifecycle (decompose-started,
  questions-sent, PRD-signed, sub-agents-spawned, test-results, deploy-confirmed).
- **Why:** Outbound is fire-and-forget with no inline keyboards/FSM, so reqs #4/#5 can't be
  conversational. Keep the scrubber in the path; Telegram is a convenience channel, **not** the
  system of record (the durable gate is GitHub Environments / the signed PRD artifact).
- **OOTB brick:** `aiogram 3.x` — **NOT installed** (verified); this is a *new* dep: `uv add aiogram`
  (FSM + inline-keyboard `callback_query` confirmed in current docs). **Effort:** 3 days.
  **Acceptance:** a PRD approval round-trips via an inline button that resumes the paused graph;
  lifecycle milestones notify exactly once.

### SP-14 — Wire the dormant safety detectors (req #10)
- **What:** 3-line extension of the existing `lib/durability` `post_tool_call` hook to instantiate and
  call **F34 `LoopDetector`** + **F35 `StallDetector`** (both have **zero call sites**); invoke
  `TrajectoryShipper.ship_batch`/`ship_trajectory` from the `judge_events` writer / `on_session_end`
  hook (today they run only via the unscheduled `scripts/run_trajectory_shipper.py`).
- **Why:** Loop/stall detection is unwired and judge verdicts never reach the audit/RL substrate in the
  running system — core long-running-agent risks left unguarded.
- **CORRECTION (verification):** an earlier draft listed "make `pre_tool_call` read `HALT_F21` and veto"
  here — **drop it: that already works.** `HALT_F21` is read and vetoed by **two** registered
  `pre_tool_call` hooks (`lib/kanban/__init__.py:77` raises `BudgetExhaustedError`;
  `lib/anchors/__init__.py:197` returns a block dict). The only real follow-up is *consolidating* the
  two duplicate readers into `lib/durability` (optional), not adding one.
- **OOTB brick:** existing repo code; pure wiring. **Effort:** 2 days. **Acceptance:** a synthetic
  tool-call loop trips F34; a stalled session trips F35; an over-budget session is vetoed; a completed
  session ships a non-empty trajectory.

### SP-15 — Context-drift + groundedness gate (reqs #7, #10)
- **What:** Upgrade `evals/trajectory_diff.py` from JSONL line-reading to single-JSON semantic diff
  (embedder cosine vs. the locked spec/golden trajectory); add a Faithfulness/groundedness leaf to the
  SP-06 DAGMetric so hallucinated changes fail the gate.
- **Why:** Closes the explicit "eliminate context drift / hallucination" requirement with a measurable
  gate rather than a vocabulary check.
- **OOTB brick:** DeepEval Faithfulness + the existing embedder. **Effort:** 2 days. **Acceptance:** an
  injected off-spec/hallucinated step lowers the drift score below threshold and fails the gate.

### SP-16 — `AbstractBoard` port over the existing Kanban (req #9, v1)
- **What:** Wrap Hermes' SQLite kanban behind `AbstractBoard`; project **DAG nodes as cards** (one
  card per sub-task, not "1 session = 1 card"); keep the Telegram bridge as a board listener.
- **Why:** The current board is a flat per-session card; the vision needs project/sub-task structure.
  The port makes the later Plane swap a one-adapter change.
- **OOTB brick:** in-repo + the port. **Effort:** 2 days. **Acceptance:** a decomposed plan renders as
  a parent project with child sub-task cards whose status tracks the graph.

---

## P2 — Richer surfaces, dedupe, production hardening

### SP-21 — Plane Community Edition as the "local Linear" board (req #9, v2)
- **When:** only once projects/views/docs/agent-comms are genuinely exercised. **What:** self-host
  Plane CE; implement the `AbstractBoard` GCP/Plane adapter via Plane's REST + **first-party MCP
  server**; use Pages as the docs surface; wire two-way GitHub linkage via webhooks (free CE) with an
  origin-tag to prevent sync ping-pong. **Anti-trap:** do **not** buy Plane Pro for GitHub sync; do
  **not** hand-roll a board UI. **Effort:** 1 week.

### SP-22 — Delete the over-engineering (the LEGO discipline, req #11)
- **What:** (a) **Do not port** the seed MoE router / PPO / reward model / bootstrap into `app/` —
  tombstone them as research; (b) **[P0-4 FALSE POSITIVE — `lib/observability/{ledger,failure_detectors}.py`
  never existed per `git log --all`; nothing to delete. The real gap (no TamperEvidentLedger /
  FailureDetectors impl anywhere) is tracked via SP-0d, not a cleanup task];** (c) collapse the duplicate
  file checkpointer into the SP-01 LangGraph checkpointer (after SP-R3's ≥1-release fallback window);
  (d) prune `INTEGRATION.md` P-1..P-17 to the ~5 codes the loop actually traverses; (e) drop the
  hallucinated Phase 7/8.5/10 claims (meta-audit P1-12). **Why:** the breadth-first stub farm is the
  maintainer-confusion risk the meta-audit documented. **Effort:** 2–3 days.

### SP-23 — GCP-native production substrate (reqs #10, #11)
- **What:** Cloud SQL (pgvector) backs the LangGraph checkpointer **and** the memory store (one DB,
  defer the `db-custom-16-64000` HA tier until load justifies it); **Pub/Sub** as *ingress under*
  LangGraph (not as the execution engine); WIF everywhere (no SA key files); VPC-SC perimeter once the
  data-plane spans ≥2 resources. **Anti-trap:** do **not** rebuild durable execution on
  Pub/Sub+Cloud-Tasks+Scheduler (P-12/P-13) — LangGraph already provides checkpoint/resume/join/HITL.
  **Effort:** 1 week. Target `autonomous-agent-2026` from day one (per CLAUDE.md migration).

### SP-24 — Real pre-deploy gate + compliance mapping (meta-audit P1-12 tie-in)
- **What:** `scripts/predeploy_gate.sh` running explicit checks (eval gate green, no dead-code
  importer violations, no `-dirty` submodule, secrets encrypted, terraform plan clean) wired as the
  `production` Environment's required gate. **Effort:** 2 days.

### SP-25 — Spec-Kit prompt templates + EARS acceptance criteria as repo assets
- **What:** Vendor the Spec Kit `/specify→/clarify→/analyze→/plan→/tasks` prompts and an EARS cheat
  sheet under `docs/` as the canonical prompts for SP-03 — **templates, not runtime deps.**
  **Effort:** 1 day.

---

## What we are deliberately NOT doing (anti-over-engineering — the critic's 8 traps)

1. **No porting the seed MoE/PPO/reward/bootstrap.** It closes none of the 11 reqs; LangGraph fan-out
   + a static capability map replaces it. (Biggest budget sink avoided.)
2. **No durable execution on raw Pub/Sub + Cloud Tasks + Scheduler** (P-12/P-13) — that recreates the
   fragile hand-rolled durability the team is escaping. LangGraph checkpointer already does it.
3. **No dual checkpointers.** Delete the file-based one; one persistence system.
4. **No Firecracker (~$265/mo) or Cloud SQL HA (~$1,580/mo) before the loop closes.** shell-sandbox +
   local/pgvector suffice for v1.
5. **No Plane/OpenProject stood up now.** Defer behind `AbstractBoard` until the board is exercised.
6. **No second/third spec system** (Spec Kit *and* BMAD *and* anchors). `lib/anchors` is the runtime
   spine; others contribute prompts only.
7. **No bespoke eval DSL.** Use DeepEval/Inspect declaratively so failures are debuggable.
8. **No agent write-access to its own gates** (rubrics, required-check defs, branch protection,
   auto-merge config) — per the repo's rubric-immutability + 4-eyes rules. The agent passes gates; it
   does not edit them.

---

## Spine-design risks & DR (added during verification — the red-team flagged these as omitted)

The LangGraph spine introduces a **stateful system-of-record** (the checkpointer) that the original
plan under-specified. Address these *as part of SP-01/SP-23*, not later:

- **SP-R1 — Checkpoint PII + encryption.** Checkpoints persist full graph state (goal text,
  clarification dialogue, tool outputs) to Cloud SQL. **Route checkpoint serialization through
  `lib/scrubber.py`** (the same scrubber on the model/tool path) and enable **CMEK** on the checkpoint
  Cloud SQL instance (`postgres/main.tf` sets no `kms_key_name` today). Otherwise the spine becomes a
  new unscrubbed PII sink.
- **SP-R2 — Per-graph cost budget.** The spine multiplies cost (LLM-per-node × parallel fan-out).
  `F21`/`budget_watchdog` is an **out-of-band poller** — blow-through is possible between polls. Add an
  **inline per-graph token/cost budget** that pre-empts `fan_out` (SP-11) when exceeded, independent of
  the daily poller. Land **meta-audit P1-4 (`llm.call.cost` histogram never recorded)** first or the
  spend is invisible.
- **SP-R3 — Vendor lock-in / fallback.** The plan pins `langgraph 1.2.x` and deletes the hand-rolled
  file checkpointer (anti-trap #3). Keep the file checkpointer as a **tested fallback for ≥1 release**
  and add a **contract test** on the LangGraph checkpoint schema so a fast-moving `1.x` bump can't
  silently break resume. (Pairs with **SP-00** pinning.)
- **SP-R4 — State explosion / retention.** fix→test→eval loops + diamond DAGs checkpoint full state per
  super-step. Bound **checkpoint state size + retention** (TTL/compaction), reusing the ephemeral-GC
  pattern already in the seed.
- **SP-R5 — DR for the checkpoint store.** A new system-of-record needs a **tested restore/rollback**,
  not just `snapshot.sh`. Confirm the checkpoint Cloud SQL inherits **PITR** (`postgres/main.tf:108`),
  add a restore drill, and CMEK (SP-R1). *Snapshot without tested restore is not DR.*

## Entry-point bridge & seed-exclusion justification (added during verification)

- **SP-B1 — Unify the two existing autonomy surfaces.** `GoalManager` (the `/goal` continuation loop,
  `hermes-agent/hermes_cli/goals.py`) and the kanban decomposer (`_auto_decompose_tick`) are **two
  parallel loops with zero cross-references** — a `/goal` never creates a triage card; a decomposed
  card never closes a goal. The spine's `goal_intake` must bridge them: a user goal → a triage card the
  decomposer picks up → the `GoalManager` "done" verdict closes the root card. Without this a human
  shuttles work between the two loops.
- **Seed-exclusion is justified, not just asserted** (the red-team correctly flagged the original as an
  assertion). Concretely, the seed's research dimensions map to the 11 reqs as:

  | Seed capability (N/O) | Which of the 11 reqs it could serve | Why the spine covers it without porting |
  |---|---|---|
  | Bilinear MoE router (`moe_router`) | #6 (route work to the right agent) | A **static capability map + LangGraph conditional edges** route deterministically; no learned router needed at this scale |
  | PPO policy-update loop | none (it's *online RL*, not delivery) | The vision needs decompose→test→fix, not self-optimizing routing; pure cost/complexity |
  | Intrinsic reward model | #7 (score quality) | **DeepEval PRD-conformance gate (SP-06)** scores against the locked spec — explicit, debuggable, vs. a learned reward |
  | Free-agent registry / spawn / bootstrap | #6 (parallel agents) | **`asyncio.gather` fan-out (SP-11)** over a fixed capability set covers parallelism; self-spawning experts is unscoped |

  Net: porting N/O closes **none** of the 11 reqs and is the largest budget trap (anti-trap #1).

## Sequencing

```
Gate 0 (verified status): P0-1/2/5/6 FIXED ✓ | P0-4 false-positive ✓ | P0-3 (on-disk SA keys) OPEN | P1-1 (CI+worktree) OPEN
   ↓  + SP-00 pin langgraph (transitive→direct) · SP-00b SA-key→WIF + delete plaintext · SP-00c P1-1 in CI   ← BLOCKING
SP-01 LangGraph spine (InMemory wire-only; +Postgres add) ── SP-02 DAG+phase bridge ── SP-03 clarify+Q-gen ── SP-04 sign-off gate  (P0 core)
        ↓
SP-05 executor (wire dead hermes_bridge) ──────── SP-06 eval gate (CI-blocking)   + SP-R1..R5 spine risks/DR woven in   (P0 trust)
        ↓
SP-11 fan-out ─ SP-12 autonomous CI/CD ─ SP-13 aiogram(+add) ─ SP-14 wire F34/F35+shipper ─ SP-15 drift ─ SP-16 board port ─ SP-B1 entry bridge  (P1)
        ↓
SP-21 Plane ─ SP-22 delete over-eng (keep langgraph!) ─ SP-23 GCP prod ─ SP-24 predeploy gate ─ SP-25 templates   (P2)
```

**Rough budget:** P0 ≈ 3–3.5 weeks, P1 ≈ 2–2.5 weeks, P2 ≈ 2 weeks — *after* Gate 0. The P0 block is
the minimum that makes the loop physically exist and gate on real evidence.

## Definition of done (the demo that proves the loop closes)

> A user sends an end-goal over Telegram. The agent asks 2-3 clarifying questions, drafts a PRD
> (`TaskSpec`), and asks for approval via an inline button. On `/approve`, it decomposes into a DAG,
> runs sub-tasks through parallel sub-agents, loops fix→test until `pytest` is green, passes a
> DeepEval PRD-conformance gate, opens a PR, waits for Actions, and — after a human Environment
> approval for prod — auto-merges and deploys, posting each milestone to Telegram and the Kanban
> board. Kill the process at any point; it resumes from the last checkpoint.

When that demo runs green end-to-end, the 11 requirements are met.

---

## Process reminders (from CLAUDE.md)

- **Reviewer model class:** every P0/P1 fix by an LLM agent must be reviewed by a *different* model
  class (Opus→Gemini OK; Opus→Opus forbidden). Record `Reviewer model:` in each PR.
- **Commit signing** (gitsign/GPG), **squash-only merges**, **conventional commit titles**,
  **`autonomousagent-*`** GCP resource prefix, **target `autonomous-agent-2026`** (never new
  resources in `i-for-ai`).
