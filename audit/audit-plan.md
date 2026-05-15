# Audit Plan — AutonomousAgent 10× Path Forward

**Audited:** 2026-05-15 · **Pass:** 2 (enriched with Hermes upstream + AA-Atelier sweep) · **Status:** Awaiting approval

> Companion document to `findings.md`. Ranked by P0 (blocking; unbreaks current Phase 1 + Phase 1 acceptance), P1 (10× transformation: anchors, evaluators, durability), P2 (operational hardening + walk-away validation), P3 (Phase 4 economics + future).

---

## Decision frame

The user's stated north-star outcome is:

> "Close my PC, go for a weekend, come back and see that the project has been built, tested, validated, cross-referenced and self-iterated until it's flawless and meets all the PRD scope and the acceptance criteria."

Walking away for a weekend = **48–72 hours of uninterrupted, unsupervised execution**. Today's `phase/1` deployment will not survive that. The 10× plan below adds, in order: (1) the missing pieces that make a single task survive an unsupervised weekend on Mac, (2) the cloud-prod migration so it survives without YOUR machine being on, (3) the self-hosted Qwen inference layer that makes the multi-judge evaluator pattern affordable at scale, (4) the trajectory + RL training that compounds the agent's quality over time.

**Token-economics ground-truth (CSV `tng - comm-it.cloud - 1_Reports, 2026-05-01 — 2026-05-15`)**:
- 15-day actual: **744M tokens / $693** ($46/day)
- Top SKU alone (Opus 4.7 input cache read): **487M tokens / $244** in 15d
- 30-day extrapolation: **1.5B tokens / $1,386/mo** — already comparable to one committed-use A100 80GB ($1,500-1,800/mo on GCP)
- Post-P1 with multi-judge evaluators: expect **3–5× token amplification** on the worker/evaluator chatter classes → easily $4-7K/mo of sub-agent overhead at current usage growth, almost all of which Qwen captures

**Recommended sequencing**: P0 → P1 → P2 → P3 → P4, in that order. Don't skip P0; the P1 transformation depends on a healthy stack. P2 (cloud-prod) requires P1 acceptance to avoid migrating a broken system. P3 (Qwen self-hosting) needs P2's GCP infra to be available.

---

## P0 — Unblock current Phase 1 (effort: hours)

These finish what's already in flight. Without them you can't even validate the simpler "send the bot a message and get a coherent reply" path.

### P0-1 · Verify Telegram → agent → Vertex AI round-trip works after `1a284de`

- **What**: User reported "Provider authentication failed" before the latest fix. The fix mounted `cli-config.yaml` to `~/.hermes/config.yaml` and added `OPENAI_API_KEY` from the LiteLLM master key to the env. Direct CLI verified working (`pong`); Telegram path **not yet re-tested**.
- **Why**: Without this, no acceptance possible.
- **Where**: User-side action — send a message to `@Manzelagent_bot`. If still fails, check `gateway` resolution path: `docker logs autonomous-agent-hermes-1`.
- **Effort**: 5 minutes (user) + up to 1h debug if it still fails.

### P0-2 · Eliminate empty `config/hermes/limits.yaml` artifact

- **What**: A 0-byte `config/hermes/limits.yaml` was created during the docker mount-conflict troubleshooting. Confirmed by file listing in `implementation_planV2.md` (Tier 1 [DELETE] item).
- **Why**: Stale artifacts mislead readers; confuses future readers about the canonical limits.yaml location.
- **Where**: `git rm config/hermes/limits.yaml` (only if empty) — verify before delete.
- **Effort**: 1 minute.

### P0-3 · Install host-side test deps to unbreak `pytest`

- **What**: `pyproject.toml` defines `[dev]` extras (jsonschema, pytest, etc.) but our host venv at `.venv/` had partial install — user observed `ModuleNotFoundError: No module named 'yaml'` when running validator with system `python` instead of venv python.
- **Why**: Local CI loop slow without functional pytest; bootstrap.sh fixed this for itself but devs need it directly too.
- **Where**: From `.worktrees/phase1/` run `source .venv/bin/activate && uv pip install -e ".[dev]"` (or simply rely on `.venv/bin/python` everywhere).
- **Effort**: 2 minutes.

### P0-4 · Confirm OTel traces actually reach Phoenix

- **What**: User changed `OTEL_EXPORTER_OTLP_ENDPOINT` from 4317 (gRPC) to 4318 (HTTP). Phoenix's OTLP HTTP receiver lives at `:6006/v1/traces`. There may still be a port mismatch between collector → Phoenix → UI.
- **Why**: Without working traces in dev, debugging long-running agent loops is blind. This is also a Phase 1 acceptance criterion.
- **Where**: After P0-1 succeeds, send 1 turn through the bot and verify a span shows up at http://localhost:6006/projects.
- **Effort**: 15 minutes (probe + tune if needed).

### P0-5 · Ship one missing CHANGELOG cleanup

- **What**: T1.2 + T1.7 produced a duplicate "Worktree-per-phase branching" bullet in `[Unreleased]/Added`. Acknowledged in session notes; never cleaned up.
- **Why**: Trivial doc hygiene; user is reviewing the artifacts.
- **Where**: `CHANGELOG.md` — remove the over-claiming first bullet (lists all 4 phase branches as if created); keep the precise T1.7 bullet (only `phase/1` actually created).
- **Effort**: 2 minutes.

---

## P1 — The 10× transformation (effort: 1–2 weeks)

This is the actual leap from "deployable plumbing" to "autonomous agent that survives a weekend." The order below is **dependency-respecting** — each item's prerequisites are above it.

### P1-1 · Implement the immutable `TaskSpec.json` (Dynamic Parameter Locking)

- **What**: When a user posts a new project request via Telegram, the agent enters a clarification loop: it asks ≤6 targeted questions to lock acceptance criteria, scope, success metrics, constraints, escalation thresholds, and budget. The result is written as `/data/specs/{slug}.json` (immutable, version-controlled, sha-stamped).
- **Why**: Per `autonomous_agent_principles.md` §3 and `implementation_planV2.md` Tier 3, this is the **single most important design decision**. Every downstream evaluation and orchestrator decision references this anchor. Without it: context drift, hallucinations, "I solved a related problem" failures.
- **Upstream reuse (pass-2 finding)**: Hermes ships a `clarify` tool (`hermes-agent/toolsets.py:126`) that asks the user multiple-choice or open-ended questions. **20% of the work is already done.** We wrap it with a state machine that decides "another question" vs "lock & freeze," and add the persistence + sha-stamping ourselves.
- **Where (new code)**:
  - `lib/anchors/task_spec.py` — Pydantic model + JSON schema for TaskSpec
  - `lib/anchors/clarification_loop.py` — state machine that drives the existing `clarify` tool until lock criteria met
  - `lib/anchors/spec_store.py` — immutable persistence + sha-stamping
  - Hermes integration: register as a Hermes plugin via `register(ctx)` lifecycle hook (`hermes-agent/AGENTS.md:465-489`); add `/new <intent>` slash command in the gateway
- **Where (existing files)**: `config/limits.yaml` add `anchors:` section (max questions, lock-on-N-no-changes, escalate-after-h)
- **Tests**: unit (schema + loop state machine) + integration (force a clarification through the test stack)
- **Effort**: **1.5 days** (was 2; -0.5d for `clarify` tool reuse)

### P1-2 · Implement the closed-loop evaluator (worker → evaluator → orchestrator)

- **What**: After every "complete" agent action (tool dispatch + observable result), dispatch N evaluator subagents that each score the result against the locked `TaskSpec.json` on its assigned axis (correctness, scope-fit, safety, completeness). Majority vote determines accept / reject-with-feedback / escalate.
- **Why**: Per `autonomous_agent_principles.md` §4 and `implementation_planV2.md` Tier 3 (`hermes/evaluators/multi_agent_consensus.py`). This is the literal mechanism that replaces "you, the human, in the loop." Multiple-judge consensus prevents single-judge mode collapse.
- **Upstream reuse (pass-2 finding)**: Hermes' **`tools/delegate_tool.py:1909`** (`delegate_task`) is exactly the dispatch primitive we need. It's a synchronous ThreadPoolExecutor (max-3 concurrent default; configurable in `delegation.max_concurrent_children`) that gives each child:
  - Isolated context, fresh conversation (`AIAgent` instance)
  - Restricted toolset via intersection + blocklist (`tools/delegate_tool.py:941-963`)
  - Custom system prompt built from goal + context
  - Independent iteration budget (`tools/delegate_tool.py:997`)
  - Optional model override via `delegation.provider` config (`tools/delegate_tool.py:1981`)
  - **`delegate`, `clarify`, `memory`, `send_message`, `execute_code` are blocked for children** — preventing recursive evaluator delegation
- **Where (new code)**:
  - `lib/evaluators/judge.py` — single-judge prompt + rubric + score parser (calls `delegate_task` as the dispatch primitive)
  - `lib/evaluators/consensus.py` — N-judge majority vote with disagreement-escalation (uses `delegate_task` batch mode for parallelism)
  - `lib/evaluators/orchestrator_hook.py` — Hermes plugin via `post_tool_call` lifecycle hook (`hermes-agent/AGENTS.md:465-525`)
- **Acceptance**: scoring rubrics for each judge MUST reference the failure-matrix from P1-6 (see "Refined" note below — we don't vote blind)
- **Tests**: unit (judge mock + consensus tie-break) + integration (force a known-bad output through the loop, observe rejection)
- **Effort**: **1.5 days** (was 2; -0.5d for `delegate_task` reuse — we're not building dispatch, just scoring on top)
- **Risk**: Evaluator collapse — if all judges share the Vertex AI Anthropic Claude model family, they may unanimously accept bad output. Mitigation: route at least 1 judge through Sonnet vs Opus; in P3-1, route safety/code-correctness judges to self-hosted Qwen for true model diversity.

### P1-3 · Implement per-step checkpointing + resume-from-last-good

- **What**: Every N steps (configurable in `limits.yaml` — default N=5), serialize the in-flight state to `/data/checkpoints/{session}/step-{N}.json`. On container restart, the orchestrator finds the latest checkpoint for any in-flight session and resumes from there.
- **Why**: Per `autonomous_agent_principles.md` §5 and `implementation_planV2.md` Tier 3 (`hermes/memory/checkpointing.py`). Without this, a 48h weekend job loses everything on the first OOM / container restart / OS update.
- **Upstream reuse (pass-2 finding)**: Hermes' `batch_runner.py` already has `_load_checkpoint`, `_save_checkpoint`, and a `--resume` flag for offline batch jobs — **the JSON serialization + load-on-startup pattern is 70% built**. Our work is to extend that exact pattern from batch context → live agent-loop context. AGENTS.md mentions a reserved `checkpoints` config section (`hermes-agent/AGENTS.md:325`) that's not yet implemented; we can populate it.
- **Where (new code)**:
  - `lib/durability/checkpoint.py` — extend `batch_runner.py`'s pattern for live agent-loop scope
  - `lib/durability/resume.py` — Hermes plugin via `on_session_start` lifecycle hook (`hermes-agent/AGENTS.md:465-489`); scans for incomplete sessions
  - `config/limits.yaml` add `durability:` section (interval_steps, retention_count, autoresume_enabled)
- **Tests**: integration (kill -9 mid-task, restart, assert resume from checkpoint)
- **Effort**: **1 day** (was 1.5; -0.5d for batch_runner pattern reuse)
- **Risk**: per-step checkpointing can fill disk in 48h. Mitigation: rolling retention (keep last 50 checkpoints + every 100th), hourly gzip compression.

### P1-4 · Implement `MEMORY/REJECTED.md` institutional memory

- **What**: When the evaluator loop rejects an approach 3 times for the same TaskSpec, append a structured entry to `/data/MEMORY/REJECTED.md` (workspace-shared) capturing: what was tried, why it failed, alternative directions to consider. The agent reads this file at the start of every session as additional context.
- **Why**: Per `autonomous_agent_principles.md` §5 (#1 killer of autonomous agents is the error loop) and `implementation_planV2.md` Tier 3. This is the difference between "tries the same broken approach forever" and "learns dead ends across sessions."
- **Where (new code)**:
  - `lib/memory/rejected.py` — append/dedupe/load
  - Wire into `lib/evaluators/orchestrator_hook.py` (after 3rd rejection of same approach)
  - Wire into Hermes startup as a context-injection
- **Effort**: 0.5 days

### P1-5 · Wire multi-agent Kanban orchestrator (mostly already shipped by Hermes)

- **What**: Activate Hermes' Kanban subsystem and bridge it with our Telegram gateway so each inbound message becomes a card and card status changes notify the user.
- **Why**: Per `autonomous_agent_principles.md` §1 (Hermes Kanban model) and `implementation_planV2.md` Tier 3. Gives the user a visible, queryable, multi-task work surface — needed once you queue up multiple weekend jobs.
- **Upstream reuse (pass-2 finding — MAJOR)**: **Hermes ships the entire Kanban subsystem.** `hermes-agent/hermes_cli/kanban_db.py:559-673` defines a 20+ field SQLite-backed task schema (`id`, `title`, `body`, `assignee`, `status`, `priority`, `claim_lock`, `consecutive_failures`, `worker_pid`, `last_heartbeat_at`, etc.). Statuses are `triage`, `todo`, `ready`, `running`, `blocked`, `done`, `archived` (`hermes-agent/hermes_cli/kanban_db.py:93`). Persistence: SQLite WAL + BEGIN IMMEDIATE + compare-and-swap (`hermes-agent/hermes_cli/kanban_db.py:61-68`); default DB at `~/.hermes/kanban/kanban.db`; multi-board support. **Programmatic Python API** exists (`create_task`, `claim_task`, `complete_task`).
- **Where (mostly config + wiring)**:
  - Mount `hermes-data:/root/.hermes/kanban` so the SQLite DB persists across container restarts
  - `lib/kanban/telegram_bridge.py` — translate Telegram messages → `kanban_db.create_task`; translate card-status transitions → Telegram notifications. Hermes plugin via `pre_tool_call`/`post_tool_call` hooks.
  - Optional: `lib/dashboard/server.py` (FastAPI 80 LOC) renders the SQLite board as a read-only HTML page on port 7878
- **Effort**: **0.5 days** (was 1.5; -1d because the Kanban DB + worker integration + claim/heartbeat all ship in Hermes)
- **Note**: Our column names (`BACKLOG`/`BRIEFING`/`READY`/`IN_PROGRESS`/`EVALUATION`/`BLOCKED`/`DONE`/`ARCHIVED`) DON'T match Hermes' fixed enum (`triage`/`todo`/`ready`/`running`/`blocked`/`done`/`archived`). Either accept Hermes' names OR add a presentation-layer mapping in the Telegram bridge / dashboard. Recommend: accept Hermes' names to avoid fork.

### P1-6 · Codify failure-handling trichotomy + 24h escalation + 33-mode matrix

- **What**: Formalize the 3-class failure model from spec §8.1 into runnable code. Each class has codified behavior:
  - **Fail-loud**: critical alert (Telegram), halt execution, snapshot state, await `/resume`
  - **Fail-soft**: degrade behavior (e.g., Chroma down → run without vector memory), log, continue, surface degradation in next user-visible message
  - **Self-heal**: exponential backoff with jitter (per limits.yaml), max 3 retries, then promote to fail-loud
- **What** also: enumerate the **33-mode failure matrix** (per `autonomousagent_x_atelier_sweep.md:197-214` — pass-2 enrichment). Each mode maps to one tier and a documented response. Without enumeration, the trichotomy is too abstract for evaluators to reference (P1-2 needs this to score against).
- **What** also: Telegram-blocked tasks (waiting on human input via `/approve` or knowledge-cutoff) auto-escalate to `triage` after 24h of silence (already in `limits.yaml.agent.telegram_escalation_timeout_h: 24` — needs consumer code).
- **Why**: Per `autonomous_agent_principles.md` §5/§7 and `implementation_planV2.md` Tier 2. Without this, the agent silently retries forever or silently fails — not durable.
- **Where (new code)**:
  - `lib/durability/trichotomy.py` — classifier + retry policy + matrix consumer
  - `lib/durability/escalation.py` — Telegram timeout watcher (uses Hermes Kanban's `last_heartbeat_at` field — schema field already exists at `kanban_db.py:559-673`)
  - `docs/architecture/failure-matrix.md` — enumerate all 33 modes per the AA-Atelier sweep, mapping each to fail-loud / fail-soft / self-heal + acceptance criteria. Test ≥10 modes via integration tests.
- **Effort**: **2 days** (was 1.5; +0.5d for the 33-mode enumeration that wasn't in pass 1)
- **Blocks**: P1-2 (evaluators reference matrix), so P1-6 should land BEFORE or concurrent with P1-2.

---

## P2 — GCP cloud-prod migration (effort: ~1 week)

After P1 acceptance proves the agent survives an unsupervised 48h soak on the Mac, lift the same docker-compose stack onto a GCP Compute Engine VM so you can close your PC entirely. Same containers, same networking, same volumes — only the host substrate and secret backend change (this was the original Phase 2 design from the spec).

**Order matters**: P2 follows P1 acceptance, NOT before it. We don't migrate a broken system. But it's not "deferred" either — it's the immediate next gate after P1.

### P2-1 · Codify exponential backoff + concurrency caps + approval/alerts in `limits.yaml`

- **What**: Per `session_gap_analysis.md` enrichments #1/#2/#3 AND `autonomousagent_x_atelier_sweep.md:82-114` (a complete plug-and-play schema we can adapt):
  ```yaml
  retries:
    self_heal:
      max_retries: 3
      backoff_strategy: exponential_with_jitter
      base_delay_ms: 500
      max_delay_ms: 30000
      jitter_range_pct: 25
  concurrency:
    max_parallel_subagents: 6           # bounded by single-LLM throughput
    max_parallel_judge_calls: 5         # multi-judge consensus parallelism
    max_parallel_campaigns: 3           # different TaskSpecs in flight
    queue_overflow_strategy: fifo       # vs priority
  approval:                             # consumed by P1-1 TaskSpec lock
    always_ask_patterns: [...]
    default_for_unknown: ask
    timeout_s: 300
  alerts:                               # consumed by P1-6 trichotomy
    fail_loud_routes: [telegram, log]
    fail_soft_routes: [log]
    rate_limit_per_minute: 6
  ```
- **Why**: Codifies the implicit choices today; lets you tune without code changes. Pass-2 finding: AA's full schema (sweep lines 82-114) is plug-and-play and battle-tested — copy verbatim where applicable.
- **Where**: `config/limits.yaml` + `config/limits-schema.json` + `lib/limits_validator.py` test additions
- **Effort**: 0.5 days

### P2-2 · Build the read-only Kanban dashboard at http://localhost:7878

- **What**: Lightweight FastAPI/HTMX or static HTML+JS that polls the Kanban JSON file every 5s and renders the board. Read-only initially; later phases add card-detail drill-down with trace links to Phoenix.
- **Why**: Walking away for a weekend is much more useful if you can pop up a tab on your phone to glance at progress. This is also `implementation_planV2.md` Tier 3.
- **Where**: New `lib/dashboard/` Python module + `templates/` Jinja2 + dev-mode docker-compose port 7878 mapped.
- **Effort**: 1 day

### P2-3 · 48h walk-away soak test

- **What**: Queue 3 separate non-trivial tasks via Telegram (e.g., "audit my hermes-agent submodule for security issues", "research and summarize the top 5 vector DBs as of 2026", "draft a phase-2 plan for cloud-prod migration"). Walk away. Return after 48h. Verify: tasks in DONE column, `MEMORY/REJECTED.md` updated if any failures, Phoenix shows continuous traces, no critical alerts in Telegram, daily $ spent reasonable.
- **Why**: Empirical proof that the system meets the user's stated north-star outcome.
- **Where**: New `docs/runbooks/48h-soak-protocol.md` documenting the test + acceptance criteria.
- **Effort**: 2 days wall-clock (mostly waiting); 1 hour active work to set up + validate after.

### P2-4 · Re-enable CodeQL + Dependency Review on `main` after Phase 1 merge

- **What**: After P1 lands `lib/anchors/`, `lib/evaluators/`, `lib/durability/`, etc., merging phase/1 → main brings Python source onto main. CodeQL v4 will then succeed (it failed before because main had no Python).
- **Why**: Static analysis is a real production-quality lever; we deferred it because the workflow couldn't run without source.
- **Where**: Restore `.github/workflows/codeql.yml` (delete-and-restore from git history at commit `8a7666e`'s parent) and re-add the required check to branch protection.
- **Effort**: 0.5 days

### P2-5 · Bootstrap GCP cloud-prod VM (Terraform + Compute Engine + Secret Manager)

- **What**: Provision the Phase 2 GCP infrastructure as code. Compute Engine `e2-standard-4` (4 vCPU / 16 GB) with Container-Optimized OS image, attached persistent SSD for `hermes-data`, no public IP (Cloud NAT for egress + IAP tunnel for SSH). Workload Identity Federation replaces the local ADC mount; Secret Manager replaces the sops-encrypted `secrets/*.sops` files at rest. systemd service runs `docker compose up -d` on boot.
- **Why**: Walking-away physically requires the agent to NOT be on your Mac. Same docker-compose stack lifts cleanly per the spec. Without this, "weekend" actually means "weekend with my Mac plugged in and awake."
- **Where**: New `terraform/` directory with `main.tf`, `vm.tf`, `network.tf`, `secrets.tf`, `iam.tf`. New `docs/superpowers/specs/2026-05-16-phase2-cloud-prod-design.md` + plan. New `deploy/systemd/hermes.service`.
- **Effort**: 2 days plan + 3 days execute.

### P2-6 · Cloud Logging / Trace / Monitoring + heartbeat

- **What**: Replace dev Phoenix with **GCP Cloud Trace** for span ingestion, **Cloud Logging** for structured JSON logs, **Cloud Monitoring** for the 5 dashboards from the spec (Cost & Budget, Agent Activity, Model Performance, Sandbox Health, Self-RL Pipeline). Healthchecks.io heartbeat from VM.
- **Why**: Visibility into a 7-day soak from your phone, anywhere. Required for trust in unsupervised operation.
- **Where**: Swap `deploy/otel/collector.dev.yaml` → `collector.prod.yaml` (already exists from spec). New `terraform/monitoring/` for dashboards-as-code.
- **Effort**: 1 day.

### P2-7 · 7-day cloud-prod soak + Phase 2 acceptance

- **What**: Run 5–10 multi-day TaskSpecs through the migrated stack. Verify: VM stays up, daily GCS snapshots succeed, dashboards report normal patterns, no manual interventions, daily $ stays under cap.
- **Why**: Empirical proof Phase 2 is durable. Gate for Phase 2 acceptance + ADR 0006's tag-and-merge protocol.
- **Where**: New `docs/runbooks/phase2-acceptance.md`.
- **Effort**: 7 days wall-clock, ~2h active per day to monitor.

### P2-8 · CodeQL re-enable on `main` after Phase 1 merge

- **What**: After P1 lands `lib/anchors/`, `lib/evaluators/`, `lib/durability/` and merges to `main`, CodeQL v4 will succeed (it failed earlier because main had no Python). Restore the workflow + add to required checks.
- **Effort**: 0.5 days.

---

## P3 — Multi-LLM Specialization Mesh on GCP (effort: ~1.5 weeks)

This is the **cost-optimization + quality-amplification unlock**. Aligns with **GCP ADK** (model abstraction layer), **GEAP / Vertex AI reference architecture** (managed model garden + cost-aware routing), and the **autonomous-agent principle of evaluator diversity** (different model families prevent unanimous-but-wrong consensus collapse).

The original draft of this tier was a single Qwen 3.6 Code endpoint with class-based routing. **User-driven upgrade**: route to a *mesh* of specialized models, each picked for what it's actually best at, with LiteLLM as the routing layer. This composes naturally with our existing LiteLLM proxy and Hermes' multi-agent dispatch.

**Order matters**: P3 follows P2 (cloud-prod) because we need GCP infra to attach GPUs and we want the migrated stack stable before adding new inference paths.

### P3-1 · Define the Specialization Mesh — model garden + routing taxonomy

- **What**: Establish the model registry. Each model in the mesh is paired with the traffic classes it should serve, ordered by preference. **Recommended initial mesh**:

  | Backend | Where it runs | Best at | Routes |
  |---|---|---|---|
  | **`vertex_ai/claude-opus-4-7`** | Vertex AI (existing) | Headline reasoning, hard architecture decisions, complex multi-step planning | `class:reasoning`, `class:orchestrator`, `class:headline` |
  | **`vertex_ai/claude-sonnet-4-6`** | Vertex AI (existing) | Fast Anthropic for routine sub-agent dispatch, safety judging, scope-fit judging | `class:chatter`, `class:judge.safety`, `class:judge.scope` |
  | **`vertex_ai/gemini-2.5-pro`** | Vertex AI (NEW — enable in console) | Long-context analysis (1M tokens — repos, big docs, full session histories), completeness judging | `class:long-context`, `class:judge.completeness` |
  | **`vllm/qwen-coder-32b`** | Self-hosted on GCP A100 80GB | Code generation, code review, code-correctness judging — code-specialized model with low marginal cost | `class:coding`, `class:judge.code-correctness` |
  | **`vllm/qwen-7b-instruct`** *(optional 2nd vLLM)* | Self-hosted on smaller GPU (T4 or L4, ~$200/mo) | Memory curation, vector consolidation, REJECTED.md summarization, routine "non-headline" reasoning at near-zero marginal cost | `class:memory.consolidate`, `class:summary`, `class:fallback.cheap` |

- **Why this composition**:
  - Each judge in the multi-judge consensus runs on a **different model family** → real epistemic diversity (kills the evaluator-collapse risk I flagged in P1-2)
  - High-volume classes (chatter, memory, code) hit cheap/free backends; high-stakes classes (orchestrator decisions) hit Opus
  - Long-context class gets Gemini 2.5 Pro's 1M-token window — important for whole-repo or whole-session analysis Hermes can do
  - LiteLLM's `model_group_alias` + `fallbacks` features cover both routing and degradation paths

- **Where (config / docs)**:
  - `docs/architecture/model-mesh.md` — taxonomy table + routing reasoning, traceable to GCP ADK pattern (https://github.com/google/adk-python) and Vertex AI Model Garden conventions
  - `docs/decisions/0008-multi-llm-specialization-mesh.md` — ADR formalizing the choice
- **Effort**: 1 day.

> **Status update (2026-05-15) — deviation #1: Google-family judge pulled forward from P3 to P1-2.** The completeness judge for the P1-2 evaluator panel now routes to a Google model on Vertex AI in `i-for-ai`, enabled and verified live on 2026-05-15. The actual model is **`vertex_ai/gemini-3.1-pro-preview`** (Preview tier; 1M ctx; thinking model — judges using this axis must allow generous `max_output_tokens` because thoughts count against the budget), reachable only via the `global` endpoint (us-central1 returns 404). The `gemini-2.5-pro` row in the table above remains the documented P3 mesh target — when P3 lands the row may be re-pointed to whatever the latest stable Gemini is at that time. LiteLLM `model_list` entry: `deploy/litellm/config.yaml` (commit `64ccdaf`, PR #16). Routing key in `lib/evaluators/orchestrator_hook.py` — `PER_AXIS_MODEL["completeness"]`.

### P3-2 · Provision GCP A100 80GB + Qwen Coder 32B vLLM service

- **What**: Single `a2-highgpu-1g` (1× A100 80GB) Compute Engine instance. Run `vllm serve Qwen/Qwen2.5-Coder-32B-Instruct` (or "Qwen 3.6 Code" if released by deployment date) — fits 80GB at FP16 with 32K context, ~120 tok/s.
- **Why**: This is the workhorse for `class:coding` and `class:judge.code-correctness` — the two highest-volume classes once P1's multi-judge pattern is amplifying. CSV-grounded: ~$1.5K/mo committed-use captures what would otherwise be 3-5× that on Vertex AI.
- **Where**: New Terraform module `terraform/qwen-coder-vllm/`. Private VPC peering to the orchestrator VM.
- **Effort**: 2 days.
- **Lock-in note**: Start on-demand A100 ($2.7K/mo) for first month while measuring actual amplification; commit to 1-year ($1.5K/mo) only after we confirm sustained utilization >60%.

### P3-3 · (Optional) Provision smaller GPU + Qwen 7B vLLM service

- **What**: Second vLLM endpoint on a smaller GPU (L4 24GB, ~$320/mo on-demand or ~$200/mo committed) running `Qwen/Qwen2.5-7B-Instruct`. Serves the *low-stakes high-volume* classes (`class:memory.consolidate`, `class:summary`, `class:fallback.cheap`).
- **Why**: Memory curation, vector consolidation, and REJECTED.md analysis run on cron schedules — they're high-volume, low-stakes, and shouldn't compete with the Coder 32B for GPU. Splitting them onto a cheaper card frees the A100 for code work.
- **Where**: Second Terraform module `terraform/qwen-cheap-vllm/`. Same VPC.
- **Effort**: 1 day. **Defer until** the A100 saturates (>80% utilization sustained) — until then, route the cheap classes to the A100 too.

### P3-4 · LiteLLM router with task-class metadata + fallback chains

- **What**: Reconfigure LiteLLM router to support the mesh. Per-class routing rules + per-class fallback chain + cost-aware degradation. Following the **GEAP / Vertex AI Application Patterns** convention:
  ```yaml
  router_settings:
    routing_strategy: tag-based
    fallbacks:
      - class:headline:        [vertex_ai/claude-opus-4-7, vertex_ai/claude-sonnet-4-6]
      - class:reasoning:       [vertex_ai/claude-opus-4-7, vertex_ai/gemini-2.5-pro, vllm/qwen-coder-32b]
      - class:long-context:    [vertex_ai/gemini-2.5-pro, vertex_ai/claude-sonnet-4-6]
      - class:coding:          [vllm/qwen-coder-32b, vertex_ai/claude-sonnet-4-6, vertex_ai/claude-opus-4-7]
      - class:chatter:         [vertex_ai/claude-sonnet-4-6, vllm/qwen-coder-32b]
      - class:judge.code:      [vllm/qwen-coder-32b, vertex_ai/claude-sonnet-4-6]
      - class:judge.safety:    [vertex_ai/claude-sonnet-4-6, vllm/qwen-coder-32b]
      - class:judge.scope:     [vertex_ai/claude-sonnet-4-6, vllm/qwen-coder-32b]
      - class:judge.completeness: [vertex_ai/gemini-2.5-pro, vertex_ai/claude-sonnet-4-6]
      - class:memory.consolidate: [vllm/qwen-7b-instruct, vllm/qwen-coder-32b, vertex_ai/claude-sonnet-4-6]
    cost_aware_degradation:
      enabled: true
      monthly_budget_pct_threshold: 75    # at 75% of budget, drop a tier
      degradation_map:
        vertex_ai/claude-opus-4-7: vertex_ai/claude-sonnet-4-6
        vertex_ai/claude-sonnet-4-6: vllm/qwen-coder-32b
        vertex_ai/gemini-2.5-pro: vertex_ai/claude-sonnet-4-6
  ```
- **What also**: Hermes plugin tags each outbound call with the appropriate `class:*` based on call site (orchestrator vs evaluator vs worker vs nudge) and model preference (`x-task-class` header).
- **Why**: This is the actual cost+quality optimization mechanism. Per-class routing maps each call to its best-fit model; fallback chains keep the system functional under partial failure; cost-aware degradation auto-prevents budget runaway.
- **Where**: `deploy/litellm/config.yaml` → `model_list` expansion + `router_settings:` block. New `lib/routing/task_class_tagger.py` Hermes plugin (uses `pre_llm_call` hook from `hermes-agent/AGENTS.md:465-489`).
- **Effort**: 2 days.

### P3-5 · Cost + quality + routing telemetry dashboards

- **What**: Three dashboards on Cloud Monitoring:
  1. **Per-backend cost** — tokens, $ per model, % of monthly budget
  2. **Per-class routing decisions** — which backend served which class, % distribution, fallback-fire rate
  3. **Per-backend quality** — judge agreement rate when this backend was a judge; downstream rejection rate; tracked across model versions
- **Why**: Without per-route quality measurement, you can't validate the mesh is producing better outcomes than single-model routing. Without per-class routing telemetry, you can't tune the fallback chains.
- **Where**: Extends P2-6 dashboards. New `terraform/monitoring/model-mesh-dashboards.tf`.
- **Effort**: 1 day.

### P3-6 · GPU instance lifecycle + emergency scripts

- **What**: Bash scripts for the operator: `scripts/qwen-coder-pause.sh` / `qwen-coder-resume.sh` (start/stop the A100); `scripts/route-emergency-vertex.sh` (force LiteLLM to skip self-hosted endpoints if both GPUs are unhealthy and route everything to Vertex AI for the next N hours).
- **Why**: Insurance. If the A100 has a hardware fault and we can't fix it in <4h, emergency-route to Vertex AI keeps the agent functional (more expensive, but functional).
- **Effort**: 0.5 days.

### P3-7 · Multi-judge evaluator panel rewiring (uses the mesh)

- **What**: Update the P1-2 evaluator code so each of the N judges in the consensus panel is **explicitly routed to a different model family**. Replaces the placeholder "all judges on Anthropic Sonnet" approach with the full mesh:
  - Code-correctness judge → `class:judge.code` → Qwen Coder 32B
  - Safety judge → `class:judge.safety` → Sonnet
  - Scope-fit judge → `class:judge.scope` → Sonnet
  - Completeness judge → `class:judge.completeness` → Gemini 2.5 Pro
- **Why**: This is the actual mechanism that defeats evaluator collapse. The same prompt, scored by 4 different model families, surfaces real disagreement when judgments are uncertain. This was already flagged as a P1-2 risk; P3 finally resolves it.
- **Where**: `lib/evaluators/consensus.py` — replace single-model dispatch with per-judge `task_class` assignment.
- **Effort**: 1 day. **Touches code shipped in P1-2** so this is an "upgrade" commit on phase/3 branch, not new code on phase/1.

---

## P4 — Atropos trajectory pipeline + RL training (effort: weeks, gated by data volume)

This was the original spec's Phase 3 + Phase 4 — now numbered P4 since P3 (Qwen self-host) is more urgent for cost. Triggers automatically once Qwen + multi-judge evaluator are producing enough labeled trajectories.

### P4-1 · Atropos trajectory pipeline → GCS (was original Phase 3)

- **What**: `trajectory-shipper` service tails the agent's session DB + judge votes + accept/reject outcomes; compresses to Atropos JSONL; uploads to `gs://hermes-trajectories-prod/` hourly. DVC tracks dataset versions. Eval suite scaffolded but unused.
- **Why**: Captures the "learning data" that compounds the agent over time. Without this, every weekend's work is throwaway from a training perspective.
- **Effort**: ~1 week (per original spec Phase 3).

### P4-2 · Atropos RL training of Qwen judges via DPO/LoRA (was original Phase 4)

- **What**: Auto-triggered + Telegram-approved DPO/LoRA training runs that fine-tune the Qwen judges on YOUR accept/reject preferences. Per-project LoRA adapters via Vertex AI Endpoints with Multi-Tuning.
- **Why**: After 1K+ trajectories, your judges learn YOUR specific quality bar — the agent's evaluation-loop becomes more aligned with your taste than any off-the-shelf model.
- **Effort**: ~2 weeks (per original spec Phase 4).

---

## Decision matrix — what to do this week

| You have | Do this week | Defer |
|---|---|---|
| ~30 minutes | P0-1 only (verify Telegram path) | Everything else |
| 1 day | P0 entirely (P0-1 through P0-5) | P1+ |
| 1 week | P0 + P1-1 (anchors), P1-6 (matrix), P1-2 (evaluator) | P1-3/4/5, P2-P4 |
| 2 weeks | All of P1 — the 10× transformation + 48h soak proof | P2-P4 |
| 1 month | P0 + P1 + P2 (Mac + cloud-prod migration) | P3-P4 |
| 6 weeks | P0 + P1 + P2 + P3 (Mac + cloud-prod + multi-LLM specialization mesh) — **token-cost + evaluator-quality both optimized** | P4 |
| 2 months | P0 + P1 + P2 + P3 + P4 (full vision: Mac → cloud → multi-LLM mesh → trajectory + DPO of self-hosted judges) | nothing |

---

## Critical risks (called out for explicit user awareness)

1. **TaskSpec rigidity** — locking acceptance criteria too early can prevent the agent from picking up legitimate scope expansions discovered mid-task. Mitigation: TaskSpec versioning with explicit "scope-change" diff that the user must `/approve` via Telegram.
2. **Evaluator collapse** — if all judges share a model (Vertex AI Anthropic), they may share a failure mode and unanimously reject correct work. Mitigation: P3-1's routing — at least one judge runs on a *different model family* (e.g., Qwen 32B Coder for code-correctness judge, Anthropic for safety judge).
3. **Checkpoint-storage explosion** — per-step checkpointing can fill the disk in 48h. Mitigation: rolling retention (keep last 50 checkpoints + every 100th), hourly compression to gzip.
4. **REJECTED.md poisoning** — bad rejection entries can make the agent permanently avoid correct approaches. Mitigation: REJECTED.md TTL of 30 days per entry; user can `/forget <pattern>` via Telegram.
5. **Concurrency vs. quota** — Anthropic Opus 4.7 per-minute token quota on `i-for-ai` is shared with Claude Code. Setting `max_parallel_subagents: 6` will frequently 429. Mitigation: `gcloud quotas update aiplatform.googleapis.com/online_prediction_input_tokens_per_minute_per_base_model` increase request, OR shift to Sonnet for sub-agent traffic.

---

## Changes from pass 1

Pass 2 dispatched 2 parallel Explore subagents — one against the AA-Atelier third sweep, one against the Hermes upstream submodule. Findings:

### Major scope reductions (saving ~1.5 days)
- **P1-5 Kanban** dropped from 1.5 → 0.5 days. Hermes ships the entire Kanban subsystem: SQLite-backed schema with 20+ fields, statuses, programmatic Python API, claim/heartbeat/multi-board (`hermes-agent/hermes_cli/kanban_db.py:559-673`). Our work shrinks to a Telegram bridge + persistent volume.
- **P1-3 Checkpointing** dropped from 1.5 → 1 day. `hermes-agent/batch_runner.py` already has `_load_checkpoint`/`_save_checkpoint`/resume flag for batch jobs — we extend the exact pattern from batch → live agent-loop scope.
- **P1-1 TaskSpec** dropped from 2 → 1.5 days. Hermes' built-in `clarify` tool (`hermes-agent/toolsets.py:126`) gives us the question/answer primitive; we wrap it with the lock state machine + persistence + sha-stamping.
- **P1-2 Evaluator** dropped from 2 → 1.5 days. `tools/delegate_tool.py:1909` (`delegate_task`) is exactly the dispatch primitive — synchronous ThreadPoolExecutor with isolated child contexts, restricted toolsets, model-override support. We don't build dispatch; we build scoring on top.

### Scope additions (adding 0.5 days)
- **P1-6 Failure trichotomy** grew from 1.5 → 2 days. Pass 2 surfaced the **33-mode failure matrix** from `autonomousagent_x_atelier_sweep.md:197-214` that needs explicit enumeration in `docs/architecture/failure-matrix.md`. Without enumeration the trichotomy is too abstract for evaluators to score against.

### Net effect on P1
- **Pass 1 estimated total**: 9 days (P1-1: 2 + P1-2: 2 + P1-3: 1.5 + P1-4: 0.5 + P1-5: 1.5 + P1-6: 1.5)
- **Pass 2 estimated total**: 7 days (P1-1: 1.5 + P1-2: 1.5 + P1-3: 1 + P1-4: 0.5 + P1-5: 0.5 + P1-6: 2)
- **Savings: 2 days (-22%)** by leveraging Hermes' upstream primitives instead of reinventing them.

### Sequencing change
- Pass 1 had P1-6 last. Pass 2 promotes it BEFORE/concurrent-with P1-2 because the failure-matrix is a dependency of evaluator scoring rubrics. New order: **P1-1 → P1-6 (matrix) → P1-2 (uses matrix) → P1-3 → P1-4 → P1-5**.

### P2-1 enrichment
- The full `limits.yaml` schema (`autonomousagent_x_atelier_sweep.md:82-114`) is plug-and-play. Adopt verbatim where applicable; saves design time.

### What got skepticism
- **Chaos tests (quarterly)** — sweep recommends but doesn't justify the cost for a non-production project. Defer to P4 (post-Phase 4).
- **External heartbeat (Healthchecks.io)** — already in Phase 1 deployment; redundant with Cloud Monitoring once Phase 2 lands. Keep as-is, don't expand.

### What got validated unchanged
- All P0 items (P0-1 through P0-5) — pass 2 didn't touch them
- P1-4 (REJECTED.md) — pass-2 confirms it's a blank slate (Hermes has no equivalent)
- All P2 items except P2-1 (refined above)
- All P3 items unchanged (deferred until 100M+ tokens/day)

### Surprises
- **Hermes' Kanban is the single biggest piece of upstream value we weren't using.** It blocks/heartbeats/claims/persists out of the box. P1-5 effectively went from "build" to "wire."
- **`delegate_task` blocks recursive delegation** (no nested subagents) — this is actually a feature for evaluator dispatch since it forces single-hop fan-out and prevents runaway parallelism.
- **The sweep's takeaway** is that AA's contribution is "operational discipline, not architecture" — i.e., the secret-scrubber, CI workflows, conventions docs we already have. Validates the approach we've been taking.

### Citations added
- `autonomousagent_x_atelier_sweep.md:82-114` — complete `limits.yaml` schema template
- `autonomousagent_x_atelier_sweep.md:197-214` — 33-mode failure matrix table
- `hermes-agent/hermes_cli/kanban_db.py:559-673` — Kanban Task schema
- `hermes-agent/hermes_cli/kanban_db.py:93` — Kanban statuses enum
- `hermes-agent/tools/delegate_tool.py:1909` — `delegate_task` primitive
- `hermes-agent/tools/delegate_tool.py:941-963` — child toolset blocklist
- `hermes-agent/AGENTS.md:465-525` — plugin lifecycle hooks
- `hermes-agent/toolsets.py:126` — `clarify` tool

---

## Pass 2.5 — User-driven restructure (Qwen + GCP migration not deferred)

After pass 2 was synthesized, user grounded the plan against real CSV data (`tng - comm-it.cloud - 1_Reports, 2026-05-01 — 2026-05-15.csv`) and corrected two of my deferral calls:

### Token-cost reality check
- 15-day actuals: **744M tokens / $693** ($46/day average)
- Single SKU `Opus 4.7 input cache read`: **487M tokens / $244** in 15d
- 30-day extrapolation: **1.5B tokens / $1,386/mo** — already comparable to a committed-use A100 80GB
- Post-P1 multi-judge evaluators expected to be a **3-5× token amplifier** on sub-agent traffic → cost easily reaches $4-7K/mo of sub-agent overhead at current growth, almost all of which Qwen captures

### Tier restructure
- **Old structure**: P0/P1 + collapsed P2 (5 mixed items) + collapsed P3 (Qwen + Atropos pipeline + RL training all deferred together)
- **New structure**: P0 (unblock) → P1 (10× on Mac + 48h soak) → **P2 (GCP cloud-prod migration, was P2-5; promoted to its own tier with Terraform/Compute Engine/Workload Identity/Cloud Logging+Trace+Monitoring/7-day soak/CodeQL re-enable)** → **P3 (self-hosted Qwen 3.6 Code on GCP A100 + LiteLLM router + cost monitoring + GPU lifecycle scripts; was P3-1; promoted to its own tier as the cost-optimization unlock)** → P4 (Atropos trajectory + RL training; was P3-2/3; renumbered).
- Old P2 items (backoff caps, dashboard, 48h soak, CodeQL re-enable) folded into P1 (where they belong as part of the 10× transformation) and P2 (cloud-prod-specific items).

### Sequencing rationale
- P2 follows P1 acceptance — never migrate a broken system (user confirmed)
- P3 follows P2 — A100 lives in GCP infra, and we want the migrated stack stable BEFORE adding a new inference path
- P4 follows P3 — Qwen needs to be the inference target for trajectory collection (Anthropic-only trajectories don't help us train a Qwen judge)

### Effort updates
- P0: 1 day (unchanged)
- P1: ~7 days (unchanged — Hermes upstream reuse already accounted)
- P2: ~7 days (was implicit in original P2-5; now explicit with 7-day soak)
- P3: ~5 days (was implicit in original P3-1; now broken out with 4 sub-items)
- P4: ~3 weeks (was original P3-2/3; unchanged scope, renumbered)

### What still got skepticism
- Pulling Qwen forward is right per CSV trend, BUT — the CSV's 3-day actual rate (149M) is below the research's 500M/3d threshold. The justification is the **post-P1 amplifier effect** (multi-judge evaluator pattern), not the current rate. If P1 lands and amplification is less than expected, P3 timing can defer 2-4 weeks without economic loss.
- Committed-use A100 lock-in is a 1-year commitment (~$18K). Recommend starting with on-demand A100 ($2.7K/mo) for first month while we measure actual amplification, THEN commit if confirmed.

---

## Pass 2.6 — Multi-LLM Specialization Mesh (user upgrade)

After pass 2.5 was synthesized with P3 = "single self-hosted Qwen + LiteLLM class-based routing", user proposed promoting P3 to a **multi-model mesh**: each LLM specialized for what it's actually best at, with LiteLLM as the routing layer following GCP ADK and Vertex AI Application Patterns conventions.

### Why this is a stronger design than my single-Qwen P3

1. **Resolves the evaluator-collapse risk I flagged in P1-2 risk note** — diversity across model FAMILIES (Anthropic, Gemini, Qwen) is real epistemic diversity. A 4-judge consensus where 4 judges share Anthropic Claude is one judge with redundant compute. A 4-judge consensus across Sonnet + Opus + Gemini 2.5 Pro + Qwen Coder is genuine cross-family validation.
2. **Aligns with GCP ADK** (https://github.com/google/adk-python) — ADK explicitly recommends: model abstraction layer, composable routing, multi-agent dispatch with per-agent model preference. This is the GCP-native production pattern.
3. **Aligns with Vertex AI Model Garden conventions** — Vertex AI's reference architecture for production GenAI uses a similar "model registry + routing taxonomy + fallback chains" pattern. We're not inventing something exotic; we're using the documented best practice.
4. **Better $-per-quality** — high-volume / low-stakes classes (memory consolidation, chatter, summary) hit cheap backends; high-stakes / low-volume classes (orchestrator decisions) hit Opus. Single-backend can't optimize both axes simultaneously.
5. **Failure-isolation** — if one backend fails (Qwen GPU down, Vertex 429), only ONE class is affected; fallback chain handles routing without total outage.

### What changed

- **P3 grew from 4 → 7 sub-items** (+0.5-1 day effort: ~5 days → ~8 days). Worth it.
- **New ADR**: `docs/decisions/0008-multi-llm-specialization-mesh.md` formalizes the mesh choice + GCP ADK alignment.
- **New architecture doc**: `docs/architecture/model-mesh.md` — taxonomy table + routing reasoning.
- **P1-2 risk note resolved** — by P3-7, each judge in the consensus panel routes to a different model family. This was previously listed as an unresolved risk.
- **Vertex AI Gemini 2.5 Pro added to the mesh** — new dependency: enable Gemini in the `i-for-ai` Vertex AI project (no cost until called).
- **Optional Qwen 7B 2nd vLLM** — deferred until A100 saturates; explicit decision rule given.

### What didn't change

- All P0, P1, P2, P4 items unchanged.
- Sequencing P0 → P1 → P2 → P3 → P4 still holds.
- Cost ground-truth from CSV unchanged.

### Open questions to lock in P3 design

1. **Gemini 2.5 Pro tier** — 1M context Gemini 2.5 Pro on Vertex AI is paid-per-token; do you want it routed only to `class:long-context` and `class:judge.completeness`, or open it up to `class:reasoning` as a 3rd diversity option for the orchestrator? Recommendation: **start narrow** (long-context + completeness only) to bound spend; expand later if signal warrants.
2. **Cheap-tier model choice** — Qwen 7B is the safe default for the optional 2nd vLLM. Alternatives: Qwen Coder 7B (smaller code-specialist), Llama 3.3 8B (better general reasoning, less code), Mistral 7B (smallest footprint). **Recommendation: defer this choice** until P3-3 actually fires (after A100 saturation).
3. **Cost-aware degradation thresholds** — proposed 75% of monthly budget triggers tier-down. Aggressive vs conservative? **Recommendation: 75% for first month, tune based on actual spend curve.**

### Citations added in pass 2.6

- GCP ADK pattern reference: https://github.com/google/adk-python
- Vertex AI Model Garden: https://cloud.google.com/model-garden (and the published reference architecture for production GenAI)
- LiteLLM tag-based routing: https://docs.litellm.ai/docs/proxy/reliability (fallback chains + cost-aware degradation)
