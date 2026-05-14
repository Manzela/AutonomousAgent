---
title: "AutonomousAgent — Complete Architecture, Decisions, and Session Implementation Log"
date: 2026-05-15
session_dates: [2026-05-14, 2026-05-15]
status: phase-1-implementation-complete-pending-user-manual-steps
artifact_purpose: "Single self-contained record of every architectural decision, design section, configuration block, file inventory, commit log, and pending-action item from the design + implementation session. Survives loss of conversation history."
related:
  - spec: docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md
  - plan: docs/superpowers/plans/2026-05-14-phase1-local-deployment.md
  - upstream: https://github.com/NousResearch/hermes-agent
  - upstream_sha: ddb8d8fa842283ef651a6e4514f8f561f736c72e
---

# AutonomousAgent — Complete Architecture, Decisions, and Session Implementation Log

## 0. How to read this document

This is the **single source of truth** for everything decided and built across the design + implementation session of 2026-05-14 / 2026-05-15. It exists to survive context loss (e.g., new conversations, future engineers, you in 6 months). Where this document and the spec/plan disagree, **the spec/plan files are canonical** and should be updated; this artifact is the snapshot of session knowledge.

Sections:

1. Project goal and scope
2. The 13 architectural decisions captured during brainstorming
3. The eight design sections (architecture, components, data flow, tunable limits, security, self-RL loop, observability, error handling, testing) — full content
4. Build sequencing strategy (phased with acceptance gates)
5. Worktree-per-phase branching model
6. Documentation framework (ADRs, conventions, templates)
7. Implementation log — what was built, in which commits, in which order
8. Test status and verification evidence
9. **Pending user-manual actions** — the gates that block Phase 1 acceptance
10. How to resume (bootstrap procedure once user-blocked items complete)
11. Phase 2-4 forward look
12. Files inventory (full tree)
13. Glossary of project-specific terms

---

## 1. Project Goal and Scope

**Goal**: Stand up a fully managed, autonomous, self-improving AI agent on this Mac (Phase 1) with a clean migration path to a 24/7 GCP Compute Engine deployment (Phase 2), continuous trajectory collection (Phase 3), and gated Atropos-based RL training of a custom open-weight model (Phase 4). Production-grade security best practices throughout (tiered sandboxing, secret management, network isolation, output filtering, approval gates), aggressive observability, and runtime-tunable limits.

**In scope (this session)**:
- Brainstorming + spec authoring
- Phase 1 implementation plan (~50 tasks)
- Phase 1 implementation: documentation framework, worktree setup, configuration layer, deploy stack files, operational scripts, unit + integration tests, runbooks
- Comprehensive ADRs and convention docs
- Worktree-per-phase branching model

**Out of scope (this session, deferred to future plans)**:
- Phase 2 (cloud-prod migration)
- Phase 3 (trajectory pipeline)
- Phase 4 (Atropos RL training)
- Multi-user agent
- Voice memo transcription
- Discord/Slack/WhatsApp/Signal/Email gateways
- Multi-region GCP failover
- Trained-model serving infrastructure
- Multi-agent coordination

**Project root**: `/Users/danielmanzela/RX-Research Project/AutonomousAgent`

---

## 2. The 13 Architectural Decisions

Captured via 10 clarifying questions during brainstorming. All numeric thresholds, intervals, retention windows, and caps are runtime-tunable via `config/limits.yaml`.

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Deployment topology | **Hybrid: local dev → cloud prod** | Iterate fast on Mac (Phase 1), migrate to GCP VM (Phase 2). Same docker-compose stack, different host substrate + secret backend. |
| 2 | Self-RL scope | **Full Atropos RL training pipeline** | Both soft loop (skill creation, memory, user model) for Phase 1+, AND hard loop (model fine-tuning) gated for Phase 4. |
| 3 | LLM provider | **Vertex AI (Anthropic Claude 4.7) via LiteLLM proxy** | Reuses existing GCP project `i-for-ai`. LiteLLM translates OpenAI-format ↔ Vertex AI; matches Claude Code backend. |
| 4 | Messaging gateway | **Telegram only** | Simplest, free, voice-memo support, mobile access. Discord/Slack/WhatsApp/Signal deferred. |
| 5 | Sandboxing strategy | **Tiered: in-process / Docker shell / Modal-Daytona for arbitrary code** | Risk-appropriate isolation per tool class. Best speed/security tradeoff. |
| 6 | Budget cap | **Aggressive: $100/day, no per-task token cap** | User wants serious capacity. LiteLLM enforces daily cap; per-task discretion. |
| 7 | Observability stack | **OpenTelemetry + Cloud Trace (prod) + Phoenix (dev)** | Vendor-agnostic via OTel SDK, native to GCP for prod, Arize Phoenix for visual agent-loop debugging in dev. |
| 8 | Phase 4 GPU strategy | **GCP A100/H100 Compute Engine instances** | Same GCP project, no cross-cloud data transfer, Workload Identity Federation auth. |
| 9 | Persistence | **SQLite (sessions) + Chroma (vectors) + nightly GCS backup** | Pragmatic for Phase 1, low ops burden, GCS-restorable. Cloud SQL deferred until traffic justifies. |
| 10 | Honcho deployment | **Self-hosted via Docker Compose** | Free, owns data, modest RAM (~512MB), part of unified stack. |
| 11 | Extra MCP servers | **GitHub MCP, Playwright MCP, Context7 MCP** | GitHub for repo ops, Playwright for browser automation, Context7 for live library docs. Firecrawl skipped (already as Claude Code skill). |
| 12 | Build sequencing | **Iterative phases with acceptance gates** | Each phase produces working software; spend gated by completion of prior phase. |
| 13 | Phase 4 trigger model | **Auto-detection + Telegram approval gate before spend** | Cron-driven preflight checks dataset readiness; if all gates pass, send Telegram inline keyboard for user approval before any GPU spend. |

---

## 3. The Eight Design Sections

### 3.1 System Architecture

A single docker-compose stack runs identically on Mac (Phase 1) and on a GCP Compute Engine VM (Phase 2). Same containers, same networking, same volumes — only the host substrate and secret backend change.

**Logical layers**:

| Layer | Purpose | Where |
|---|---|---|
| Ingress | Telegram bot (long-poll), CLI socket, optional ACP webhook | `hermes-gateway` |
| Agent core | `hermes` Python process: agent loop, skill invocation, memory writes, tool dispatch, OTel tracing | `hermes-agent` |
| Model gateway | LiteLLM proxy: OpenAI-format → Vertex AI translation, retries, budget enforcement, cost telemetry | `litellm-proxy` |
| State | SQLite + Chroma + Honcho on persistent volume | `chroma`, `honcho`, sqlite file |
| Tool execution (tiered) | Local FS reads in-process; shell → `shell-sandbox` Docker; arbitrary code → Modal/Daytona | three tiers, dispatched by `toolsets.yaml` |
| Observability | OTel SDK in-agent → OTel Collector → Phoenix (dev) or Cloud Trace (prod) | `otel-collector`, `phoenix` |
| Trajectory pipeline (Phase 3+) | Tail agent logs → Atropos format → GCS bucket | `trajectory-shipper` |
| RL orchestration (Phase 3+) | Cron preflight + Telegram approval + GPU instance lifecycle | `rl-orchestrator` |
| RL training (Phase 4) | Out-of-band ephemeral GCP A100/H100 → train → eval → register | not in main stack |

**Key isolation principles**:
- Agent core cannot reach the host network — only whitelisted egress (LiteLLM, Telegram, GitHub MCP, Playwright sidecar, GCS, Healthchecks.io, Context7).
- Shell sandbox container: read-only host FS except `/workspace`; `--network=none`.
- Modal/Daytona sandboxes: ephemeral, network-restricted to per-call allowlist, max 10-minute lifetime.
- All secrets via `.env` (sops-encrypted at rest in dev) → Secret Manager (Phase 2). Never in source control.

### 3.2 Components

**Twelve docker-compose services** across the project lifecycle:
- Phase 1 dev: 10 services (rows 1–10 below, including Phoenix)
- Phase 1/2 prod: 9 services (drop Phoenix)
- Phase 3+ prod: 11 services (add `trajectory-shipper` + `rl-orchestrator`)
- Phase 3+ dev: 12 services (all rows)

| Service | Image | Purpose | Ports | Volumes | Depends on |
|---|---|---|---|---|---|
| `hermes-agent` | `Dockerfile.hermes` (extends upstream) | Agent loop, skill invocation, memory, tool dispatch | 7878 (admin) | `hermes-data:/data`, configs ro | litellm, chroma, honcho, otel |
| `hermes-gateway` | same image, gateway entrypoint | Telegram bot + ACP webhook listener | — (long-poll) | `hermes-data:/data:ro` | hermes-agent |
| `litellm-proxy` | `ghcr.io/berriai/litellm:main-latest` | Vertex AI translation, retries, budget enforcement | 4000 internal | config + gcloud creds | otel |
| `chroma` | `chromadb/chroma:latest` | Vector store for long-term semantic memory | 8000 internal | `chroma-data` | — |
| `honcho` | `plasticlabs/honcho:latest` | Dialectic user modeling | 8001 internal | — | honcho-db, litellm |
| `honcho-db` | `postgres:16-alpine` | Honcho's Postgres backend | 5432 internal | `honcho-db-data` | — |
| `otel-collector` | `otel/opentelemetry-collector-contrib:latest` | OTLP receiver → Phoenix or Cloud Trace | 4317/4318 internal | collector configs ro | — |
| `phoenix` *(dev only)* | `arizephoenix/phoenix:latest` | Local trace visualization | 6006 → host | `phoenix-data` | — |
| `shell-sandbox` | `Dockerfile.shell-sandbox` | Shell tool target; `--cap-drop=ALL --network=none` | — | `workspace:/workspace`, host FS ro | — |
| `playwright-mcp` | `mcr.microsoft.com/playwright/mcp:latest` | Browser automation tool target | 8002 internal | `playwright-data` | — |
| `trajectory-shipper` *(Phase 3+)* | small Python image | Tail sessions → Atropos JSONL → GCS | — | `hermes-data:ro` | hermes-agent |
| `rl-orchestrator` *(Phase 3+)* | small Python image | Preflight + Telegram approval + Compute Engine lifecycle | — | — | trajectory-shipper |

**Volumes**: `hermes-data`, `chroma-data`, `honcho-db-data`, `phoenix-data` (dev only), `workspace`.

**Networks**: `internal` (no egress), `egress` (whitelisted only). Phase 2: GCP VPC firewall enforces same allowlist.

**Resource budget**: ~3.5GB RAM idle, peaks ~6GB during heavy turns. Fits Mac 16GB+ and `e2-standard-2` (8GB) GCP VM.

### 3.3 Data Flow

#### 3.3.1 User turn (Telegram → response)

```
User on phone
  → Telegram servers
  → hermes-gateway (long-poll, allowlist check, allowlist user)
  → hermes-agent (assemble: system + memory + ToM + tools schema + history)
  → litellm-proxy (budget check, retry policy, OTel emit)
  → Vertex AI (Claude 4.7)
  → litellm-proxy → hermes-agent (tool dispatch loop per toolsets.yaml)
      ├─ in-process: read_file, grep, ls
      ├─ shell-sandbox: shell, run_python, git
      ├─ playwright-mcp: browser_*, web_scrape
      ├─ external HTTPS MCPs: github, context7
      └─ Modal/Daytona: arbitrary model-generated code
  → final response → secret scrubber → persist (sessions DB, Chroma, Honcho)
  → hermes-gateway → Telegram → user
```

**Trust boundary sanitization points**:
- Inbound TG: sender allowlist, per-user rate limit, strip Telegram markup
- Tool dispatch: command-class allowlist via `toolsets.yaml`; sandbox enforcement
- Outbound: regex scrubber for AWS/GCP/GitHub/Anthropic/OpenAI/JWT/Telegram-token patterns

#### 3.3.2 Self-learning loop (asynchronous nudges)

| Nudge | Trigger | Action | Tunable |
|---|---|---|---|
| Memory curator | Every 6h | Reread MEMORY.md, score for keep/promote/forget, emit diff | `limits.nudges.memory_curator_interval` |
| Skill extractor | After complex task (≥10 turns OR ≥3 distinct tools) | Reflect on trajectory; if reusable, write `/skills/{slug}/SKILL.md` | `limits.nudges.skill_extractor_min_*` |
| Vector consolidator | Nightly 03:17 UTC | Cluster Chroma embeddings, summarize via LiteLLM, promote to MEMORY.md, prune low-score >90d | `limits.nudges.vector_consolidator_cron` |

All three nudges are themselves Hermes turns — fully observable in OTel, count toward budget.

#### 3.3.3 Trajectory pipeline (Phase 3+)

```
hermes-agent writes turn records to sessions.db
    ↓
trajectory-shipper (hourly):
    tails sessions.db (WAL)
    buffers 100 completed turns
    trajectory_compressor.py → Atropos JSONL with reward annotations
    uploads → gs://hermes-trajectories-{env}/year=YYYY/month=MM/day=DD/hour=HH/batch-{uuid}.jsonl.zst
    on upload failure: exponential backoff, fall back to local /trajectories/queue/, retry
```

DVC tracks dataset versions. Phase 4 reads only from this bucket.

### 3.4 Configuration & Tunable Limits

**Single source of truth**: `config/limits.yaml`. Validated against JSON schema at startup.

The full `limits.yaml` (see implementation file `/Users/danielmanzela/RX-Research Project/AutonomousAgent/.worktrees/phase1/config/limits.yaml`):

```yaml
budget:
  daily_usd_cap: 100
  per_task_input_tokens: null     # null = unlimited
  per_task_output_tokens: null
  per_conversation_context: null
  alert_at_pct: 75

retries:
  litellm_max_attempts: 5
  litellm_initial_backoff_s: 1
  litellm_max_backoff_s: 60
  litellm_jitter_pct: 25

sandboxes:
  shell_timeout_s: 120
  shell_max_output_bytes: 1048576
  modal_max_lifetime_s: 600
  modal_network_allowlist: ["pypi.org", "github.com", "registry.npmjs.org"]
  daytona_max_lifetime_s: 600

agent:
  max_turns_per_task: 50
  max_concurrent_tasks: 3

nudges:
  memory_curator_interval: "0 */6 * * *"
  skill_extractor_min_turns: 10
  skill_extractor_min_distinct_tools: 3
  vector_consolidator_cron: "17 3 * * *"
  vector_prune_age_days: 90

health:
  healthchecks_io_ping_interval_s: 300
  agent_heartbeat_interval_s: 60
  vm_uptime_alert_threshold_s: 300

snapshots:
  gcs_snapshot_cron: "0 4 * * *"
  gcs_retention_days: 30
  local_db_vacuum_cron: "0 5 * * 0"

approval:
  always_ask_patterns: ["rm -rf", "git push --force", "DROP TABLE", "kubectl delete", "*.private*"]
  never_ask_patterns: ["ls *", "cat *", "git status", "git log*", "rg *"]
  default_for_unknown: ask
  timeout_s: 300

rl_rewards:
  weights:
    user_explicit: 1.0
    user_implicit: 0.3
    self_consistency: 0.2
    task_completion: 0.5
  reward_horizon_turns: 20
  exclude_session_if_lt_turns: 5

rl_training:
  enabled: false   # Phase 1 ships disabled; flipped at Phase 4 acceptance
  trigger_check_cron: "0 12 * * *"
  preflight_thresholds:
    min_new_trajectories_since_last_run: 1000
    min_days_since_last_run: 3
    require_dataset_schema_valid: true
    require_eval_baseline_exists: true
    require_reward_sanity_score_min: 0.7
    require_gpu_quota_available: true
    require_monthly_run_budget_available: true
  approval:
    require_telegram_approval: true
    approval_timeout_h: 12
    auto_disable_after_n_consecutive_deferrals: 3
  guardrails:
    max_runs_per_month: 4
    max_run_duration_h: 24
    gpu_type: a100-80gb
    gpu_max_count: 1
    estimated_cost_per_run_usd: 100
    abort_if_actual_cost_exceeds_estimate_pct: 50
    abort_if_eval_regresses_pct: 10
  post_training:
    auto_register_if_eval_improves_pct: 2
    auto_swap_in_litellm_if_eval_improves_pct: null  # null = always ask
    alert_telegram_on: [run_started, run_completed, run_failed, model_registered]

alerts:
  budget_pct_of_daily_cap: [50, 75, 90, 100]
  budget_pct_of_monthly_cap: [75, 90, 100]
  agent_heartbeat_missed_count: 3
  vm_unreachable_min: 5
  litellm_error_rate_5min: 0.20
  sandbox_oom_count_5min: 5
  scrubber_secret_leak_attempts_per_hour: 1
  rl_run_failed: always
  rl_run_cost_overrun_pct: 25
  trajectory_shipper_lag_min: 30

notify_channels:
  telegram_chat_id: null  # set after Telegram bot creation
  cloud_monitoring_email: null
  pagerduty_routing_key: null

log_retention:
  cloud_logging_days: 30
  cloud_logging_to_gcs_coldline_after_days: 30
  cloud_logging_delete_after_days: 365
  trace_sampling:
    head_sample_rate: 1.0
    tail_sample_errors: true
    tail_sample_slow_p99: true

local_logs_dev:
  rotate_size_mb: 100
  keep_files: 5
```

JSON schema at `config/limits-schema.json`; validator at `lib/limits_validator.py` (5 unit tests in `tests/unit/test_limits_schema.py`, all passing).

### 3.5 Security Model

#### 3.5.1 Identity & Secrets

| Secret | Phase 1 | Phase 2 |
|---|---|---|
| Vertex AI auth | ADC via `gcloud auth application-default login` | Workload Identity Federation; SA `hermes-agent-prod@i-for-ai.iam.gserviceaccount.com` |
| Telegram bot token | sops-encrypted `secrets/telegram.env.sops` | Secret Manager `telegram-bot-token` |
| GitHub MCP token | host `~/.config/gh` (RO) | Secret Manager fine-grained PAT |
| LiteLLM master key | sops-encrypted, generated at bootstrap | Secret Manager, rotated quarterly |
| Honcho DB pw | sops-encrypted | Secret Manager |
| Chroma auth token | sops-encrypted | Secret Manager |
| Modal/Daytona tokens | sops-encrypted | Secret Manager |
| Healthchecks.io URL | sops-encrypted | Secret Manager |

`git-secrets` + `detect-secrets` pre-commit hooks block accidental commits.

#### 3.5.2 Network isolation

- Two compose networks: `internal` (no egress), `egress` (whitelisted)
- Phase 2: GCP VPC firewall enforces same allowlist; VM has no public IP; Cloud NAT for egress; IAP tunnel for SSH

**Initial egress allowlist** (will evolve as new MCPs land):
- `generativelanguage.googleapis.com` (Vertex AI)
- `api.telegram.org`
- `api.github.com`
- `storage.googleapis.com` (GCS)
- `hooks.healthchecks.io`
- `api.context7.com`
- `modal.com`, `daytona.io`
- Playwright per-call allowlist

#### 3.5.3 Tool execution isolation

| Tool class | Container | Capabilities | FS | Network | Resources | Timeout |
|---|---|---|---|---|---|---|
| File reads, grep, ls | hermes-agent in-process | none | host RO | n/a | n/a | 30s |
| Shell, git, jq | shell-sandbox | `--cap-drop=ALL` | host RO + writable `/workspace` + tmpfs `/tmp` 100MB | `--network=none` | `--memory=1g --cpus=1.0 --pids-limit=200` | `limits.shell_timeout_s` |
| Browser | playwright-mcp | `--cap-drop=ALL --cap-add=SYS_ADMIN` | tmpfs only | egress per-call allowlist | `--memory=2g --cpus=2.0` | `limits.browser_timeout_s` (300s) |
| Arbitrary code (LLM-gen) | Modal/Daytona | provider-managed microVM | ephemeral tmpfs | per-call allowlist | provider default | `limits.modal_max_lifetime_s` |
| External HTTPS MCPs | hermes-agent in-process via httpx | n/a | n/a | egress, mTLS where avail | n/a | 60s |

#### 3.5.4 Output Filtering

Regex scrubber (10 patterns in `config/scrubber-patterns.yaml`) before any persist or outbound. Implemented in `lib/scrubber.py` with positive/negative test coverage (15 tests in `tests/unit/test_scrubber.py`, all passing).

Patterns: AWS access key ID, AWS secret, OpenAI key, Anthropic key, GitHub PAT, JWT, GCP SA JSON, PEM private keys, Telegram bot token, high-entropy hex (info-only).

Hits replaced with `[REDACTED:reason]`; logged separately to `secret-leak-attempts.log` for audit.

#### 3.5.5 Approval Gates

Hermes' built-in `command_approval`; tunable in `limits.yaml § approval`. In Telegram, ask-prompts come back as inline keyboard (Approve / Deny / Always allow). On deny, agent gets a structured tool error.

#### 3.5.6 Audit trail

Every tool call → OTel as structured event (timestamp, session_id, tool, sanitized args, result-class, latency). Phase 2: 90-day Cloud Logging + 1-year GCS coldline.

### 3.6 Self-RL Loop

#### 3.6.1 Soft loop (Phase 1+, runs continuously, no GPU)

Three nudges (see §3.3.2) shape behavior without updating model weights. Online improvement: every conversation makes future conversations better via skills/memory/user-model.

#### 3.6.2 Hard loop (Phase 3 + Phase 4)

```
Phase 3 (continuous, low cost):
  hermes-agent → sessions.db
  trajectory-shipper (hourly) → trajectory_compressor.py → Atropos JSONL → GCS
  DVC tracks dataset versions

Phase 4 (auto-triggered + human-approved):
  rl-orchestrator daily preflight check (cron `0 12 * * *`):
    if all preflight gates pass:
      send Telegram approval message with cost estimate
      if user approves within 12h:
        spin up GCP A100 80GB instance
        pull latest dataset from GCS
        run Atropos training environment from upstream environments/
        eval against held-out task suite (~50 tasks, scored)
        if eval > prior: register checkpoint in GCS model registry
        if eval ≤ prior: discard, alert
        instance auto-shuts-down after run + 5min grace
      if cost overrun mid-run: auto-kill instance + alert
```

#### 3.6.3 Reward signals

Three reward sources, weighted in `limits.rl_rewards.weights`:
- `user_explicit` (1.0): /thumbs-up, /thumbs-down, "good", "no that's wrong"
- `user_implicit` (0.3): follow-up patterns (re-ask, abandon, retry)
- `self_consistency` (0.2): tool call agreement with later observations
- `task_completion` (0.5): conversation reached natural completion

Reward horizon 20 turns. Sessions <5 turns excluded from training.

#### 3.6.4 Phase 4 safety

- First Phase 4 run is **eval-only** (load base, score on suite, store baseline)
- Reward-sanity preflight (auto + 20-trajectory human spot-check before first training)
- Eval suite versioned in git (`eval/tasks/*.yaml`), ~50 tasks
- Models trainable: open-weight only (Llama, Qwen, DeepSeek)
- Trained model can be optionally swapped into LiteLLM as `vllm/your-trained-model` later
- Phase 4 GPU cost only kicks in when training is approved

### 3.7 Observability

#### 3.7.1 Instrumentation surface

Every span: `session_id`, `user_id`, `turn_id`, `phase`, `env`, `agent_version`, `model_id`.

| Source | Spans | Key metrics |
|---|---|---|
| hermes-agent | `turn.start/end`, `tool.dispatch/complete`, `memory.write`, `skill.invoke/create`, `nudge.fire` | turns/min, avg duration, tools-per-turn, skill creation rate |
| litellm-proxy | `model.call` parent of `model.attempt` | tokens_in/out, latency, cost_usd, retry_count, cache_hit |
| chroma, honcho | DB query spans | latency, result_count |
| shell-sandbox | `sandbox.shell.exec` | command_class, exit_code, stdout_bytes, duration |
| playwright-mcp | `sandbox.browser.action` | action_type, url_class |
| trajectory-shipper | `trajectory.batch.upload` | batch_size, compressed_bytes, upload_duration |
| rl-orchestrator | `rl.preflight`, `rl.approval_sent`, `rl.run_*` | preflight result, run_cost_usd, eval_score |

#### 3.7.2 Pipeline

```
all services → OTLP gRPC → otel-collector → {Phoenix (dev) | Cloud Trace (prod)}
                                          → {local JSON files (dev) | Cloud Logging (prod)}
                                          → {— | Cloud Monitoring (prod, metrics)}
```

#### 3.7.3 Dashboards (Cloud Monitoring prod, Phoenix dev)

Defined as code in `terraform/monitoring/` (Phase 2):
1. Cost & Budget — daily/monthly $ vs cap, 7-day rolling, burn-rate forecast
2. Agent Activity — turns/hour, tool-mix, skill creation, memory growth, top-N skills
3. Model Performance — p50/95/99 latency, retry rate, cache-hit, token throughput
4. Sandbox Health — exec count, error rate, timeout rate, top denied commands
5. Self-RL Pipeline (Phase 3+) — trajectories/hour, GCS bytes, dataset age, reward distribution

#### 3.7.4 Alerts

Tunable in `limits.alerts`. Routes: Telegram (default), Cloud Monitoring email (prod backstop), PagerDuty (optional).

#### 3.7.5 Heartbeat

`hermes-agent` POSTs to private Healthchecks.io URL every 300s. Outside-in liveness.

### 3.8 Error Handling

#### 3.8.1 Failure-handling philosophy

- **Fail-loud** (alert + halt): security failures, budget breach, data corruption, training cost overruns
- **Fail-soft** (degrade + log): tool errors, Honcho/Chroma temporary unavailability, snapshot upload failure, single trajectory drop
- **Self-heal** (retry silently): transient 429/503, rate limits, container restarts within frequency budget

User-facing: agent **always** acknowledges degradation. Trust > apparent capability.

#### 3.8.2 Failure matrix (highlights)

- LLM 429 → exponential backoff per `limits.retries.litellm_*`
- LLM 401 → halt + critical alert
- Malformed tool call → 3 in-turn retries → abandon turn + alert
- Infinite tool-call loop → cap at `limits.agent.max_turns_per_task = 50` → abort
- Context overflow → auto-`/compress`
- Sandbox timeout → tool error to agent
- Modal/Daytona down → 1 retry → fallback to local Docker shell with explicit note
- SQLite lock → 200ms retry → halt writes → degraded read-only mode
- Chroma unreachable → empty vector results + warning
- Honcho unreachable → null ToM, agent continues
- Snapshot upload fails → local queue + retry; alert after 3 consecutive failures
- Disk <10% → emergency vacuum, prune, alert
- VM preempted (Phase 2) → pre-shutdown script flushes state
- VPC firewall blocks egress → fail-loud
- Phase 4 reward-sanity score <threshold → block trigger
- Phase 4 GPU instance fails to start → retry 3 zones → abort
- Phase 4 training cost overrun → auto-kill via Compute Engine API
- Trained model fails eval → don't register, keep checkpoint cold

#### 3.8.3 Panic stop

`hermes panic` (CLI) and `/panic` (Telegram, restricted to user_id):
- Halt all in-flight tool calls
- Drain queued nudges
- Stop accepting new turns (HTTP 503 from gateway)
- Snapshot state to GCS
- Post status to Telegram
- `--teardown` also stops the GCP VM (Phase 2)

Recover via `hermes resume`.

### 3.9 Testing Strategy

#### 3.9.1 Unit tests (pre-commit + CI, fast <30s)

37 tests across:
- `tests/unit/test_limits_schema.py` (5 tests): schema validation, required sections, type checks
- `tests/unit/test_scrubber.py` (15 tests): positive/negative, multi-secret, source attribution
- `tests/unit/test_toolset_router.py` (15 tests): per-tool routing, glob matching, defaults
- `tests/unit/test_healthcheck.py` (2 tests): all-OK and degraded-down scenarios

#### 3.9.2 Integration tests (PR + nightly, ~5min)

8 tests across:
- `tests/integration/test_full_turn.py`: health endpoint + full turn round-trip
- `tests/integration/test_skill_creation.py`: complex session → skill extractor nudge
- `tests/integration/test_sandbox_isolation.py`: shell-sandbox no network, no FS escape
- `tests/integration/test_secret_leak.py`: scrubber catches injected fake secret
- `tests/integration/test_budget_cap.py`: 429 returned when budget cap hit
- `tests/integration/test_chroma_outage.py`: agent degrades gracefully

#### 3.9.3 Smoke tests (`scripts/smoke.sh`, per-deploy, ~3min)

9 checks: containers healthy, internal network, egress allowlist, egress denylist, real LLM, persistence across restart, OTel trace visible, budget endpoint, panic endpoint, limits.yaml valid.

#### 3.9.4 Eval suite (Phase 3+)

`eval/tasks/*.yaml` with ~50 hand-curated tasks; deterministic graders (LLM-as-judge or programmatic). Versioned in git.

#### 3.9.5 Manual verification protocols

- Phase 1 acceptance: `docs/runbooks/phase1-acceptance.md`
- Phase 2 acceptance: 7-day soak on GCP VM
- Phase 3 acceptance: 1K trajectories + 20-sample human spot-check
- Phase 4 first run: dry-run preflight + cost-overrun abort path verification

#### 3.9.6 Chaos tests (quarterly)

Kill -9 mid-turn, block egress 5min, fill disk to 95%, revoke Vertex AI SA mid-run, corrupt last GCS snapshot.

---

## 4. Build Sequencing — Approach B (Iterative Phases)

Each phase has a success gate. Don't move on until the gate passes.

| Phase | Deliverable | Gate |
|---|---|---|
| **1** (this session) | Local Hermes Agent in Docker on Mac, talks to user via Telegram, learns via in-context skill creation | 10 TG msgs spanning ≥3 task types, autonomous skill creation, restart-persistent state, Phoenix traces, no secret leaks |
| **2** | Migration to GCP Compute Engine VM for 24/7 unattended operation | 7-day soak, no manual interventions, no budget breach, all dashboards green |
| **3** | Trajectory pipeline → GCS, dataset versioning via DVC, eval suite scaffolded | 1K+ trajectories, schema valid, sample replay, reward sanity ≥0.7, 20-trajectory human spot-check |
| **4** | Atropos RL training of a custom open-weight model, gated by automated preflight + Telegram approval | One full cycle, evaluated model improves ≥2% on held-out suite vs base |

---

## 5. Worktree-per-Phase Branching Model

`main` holds only **accepted-and-tagged** work. Each phase gets a dedicated long-running branch checked out under `.worktrees/`.

```
AutonomousAgent/                 ← branch: main
├── .worktrees/                  ← gitignored
│   ├── phase1/                  ← branch: phase/1 (where Phase 1 work happens)
│   ├── phase2/                  ← branch: phase/2 (created when Phase 2 starts)
│   ├── phase3/                  ← branch: phase/3
│   └── phase4/                  ← branch: phase/4
```

**Acceptance flow**: After phase passes its acceptance protocol, from the main worktree:

```bash
git checkout main
git merge --no-ff phase/N -m "Merge phase/N into main: <one-line summary>"
git tag -a phaseN-accepted -m "Phase N accepted on $(date -u +%Y-%m-%d). All N criteria passed."
```

**Hotfixes** branch from main, merge back to main, cherry-pick to active phase branch.

See ADR 0007 (`docs/decisions/0007-worktree-per-phase-branching.md`) and `docs/conventions/branching.md`.

---

## 6. Documentation Framework

Every doc lives at one of these paths:

```
README.md                            # comprehensive project intro
LICENSE                              # MIT
CHANGELOG.md                         # Keep-a-Changelog 1.1.0
CONTRIBUTING.md                      # workflow, conventions, branching
.github/PULL_REQUEST_TEMPLATE.md     # PR template
.github/ISSUE_TEMPLATE/{bug,feature}.md
docs/
├── architecture/README.md           # architecture index
├── decisions/                       # MADR Architecture Decision Records
│   ├── README.md                    # ADR index
│   ├── template.md                  # MADR template
│   ├── 0001-use-hermes-agent-as-base.md
│   ├── 0002-vertex-ai-via-litellm-proxy.md
│   ├── 0003-tiered-sandboxing-strategy.md
│   ├── 0004-sops-age-secret-management.md
│   ├── 0005-self-rl-pipeline-architecture.md
│   ├── 0006-iterative-phase-build-with-gates.md
│   └── 0007-worktree-per-phase-branching.md
├── conventions/
│   ├── commit-messages.md           # Conventional Commits 1.0.0
│   ├── branching.md                 # worktree workflow
│   ├── logging.md                   # structured JSON, severity rules
│   └── code-style.md                # Python (ruff), shell (strict mode), YAML, Dockerfiles
├── runbooks/
│   ├── README.md                    # runbook index
│   ├── telegram-bot-setup.md        # how to create bot via BotFather
│   ├── healthcheck-cron-setup.md    # cron entry for HC.io pings
│   ├── recovery.md                  # panic recovery + snapshot restore
│   └── phase1-acceptance.md         # Phase 1 acceptance protocol
└── superpowers/
    ├── specs/2026-05-14-hermes-agent-architecture-design.md
    ├── specs/SESSION-COMPLETE-2026-05-15-hermes-agent-full-architecture.md  ← THIS FILE
    └── plans/2026-05-14-phase1-local-deployment.md
```

**Convention enforcement**:
- Conventional Commits (reviewer discipline)
- Pre-commit hooks: `ruff`, `ruff-format`, `detect-secrets`, `detect-private-key`, `detect-aws-credentials`, `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`, `check-added-large-files`

---

## 7. Implementation Log

### 7.1 Branch state at session end

```
Branch:    Commits   Worktree path
main         9       /Users/danielmanzela/RX-Research Project/AutonomousAgent
phase/1     46       /Users/danielmanzela/RX-Research Project/AutonomousAgent/.worktrees/phase1
```

(phase/1 inherits the 9 commits from main + 37 phase/1-specific commits.)

### 7.2 Commit log on `main` (chronological)

| SHA | Message |
|---|---|
| `dc8778a` | `chore: initialize project skeleton` |
| `be13d60` | `docs: add comprehensive README and MIT LICENSE` |
| `c123480` | `docs: add CHANGELOG.md (Keep-a-Changelog 1.1.0)` |
| `c5ea61c` | `docs: add CONTRIBUTING.md (workflow, conventions, branching)` |
| `246f686` | `docs: add GitHub PR and issue templates` |
| `cc1b8f2` | `docs(adr): add architecture index, ADR template, and ADRs 0001-0007` |
| `97df342` | `docs(conventions): add commit, branching, logging, code-style conventions` |
| `3e38911` | `chore: gitignore .worktrees/` |
| `9223107` | `chore(worktrees): create phase/1 branch and .worktrees/phase1 worktree` |

### 7.3 Commit log on `phase/1` (post-fork, chronological)

| SHA | Task | Message |
|---|---|---|
| `e14ce7d` | T2  | `chore: add hermes-agent as submodule pinned to ddb8d8f` |
| `4745ffc` | T3  | `feat(scripts): add host prereqs verification script` |
| `d705a61` | T5  | `feat(secrets): add sops + age secret management` |
| `bbc8608` | (auto) | `chore: strip trailing whitespace from prior docs` (pre-commit auto-fix) |
| `ace2b80` | T6  | `chore: add pre-commit hooks (secrets scanning + ruff)` |
| `d7264b8` | T7  | `chore: add python project layout` |
| `8e327a7` | T8  | `feat(scripts): add bootstrap.sh skeleton` |
| `bc0fc94` | T9  | `feat(config): add limits.yaml (single source of truth for tunables)` |
| `b9e938e` | T10 | `feat(config): add JSON schema for limits.yaml` |
| `1516fd5` | T11 | `feat(lib): add limits.yaml validator` |
| `b937356` | T12 | `test(unit): add limits.yaml schema validation tests` |
| `e31b9b8` | T13 | `feat(security): add regex-based secret scrubber` |
| `6c39b2c` | T14 | `test(unit): add scrubber positive/negative test suite` |
| `73783ee` | T15 | `feat(security): add toolset → sandbox-tier router` |
| `c310a19` | T16 | `test(unit): add toolset router tests` |
| `33d4278` | T17 | `feat(config): add initial Hermes agent config (cli-config + AGENTS/MEMORY/USER/SOUL)` |
| `15f76e7` | T18 | `feat(deploy): add LiteLLM proxy config for Vertex AI` |
| `bdebf7f` | T19 | `feat(deploy): add OTel collector configs (dev + prod)` |
| `724c39b` | T20 | `feat(deploy): add Dockerfile.hermes (extends upstream + OTel SDK)` |
| `8af7e19` | T21 | `feat(deploy): add shell-sandbox Dockerfile` |
| `a4aeafd` | T22 | `feat(deploy): add chroma auth.json placeholder` |
| `2c63e7a` | T23 | `feat(deploy): add honcho postgres init SQL` |
| `6018cc7` | T24 | `feat(deploy): add main docker-compose stack (10 services)` |
| `ff58612` | T25 | `feat(deploy): add docker-compose.dev.yml override (Phoenix + dev ports)` |
| `89dfc1f` | T26 | `feat(deploy): add docker-compose.test.yml (mocked LLM)` |
| `0c11d59` | T27 | `docs(runbook): Telegram bot setup procedure` |
| `922e01a` | T29 | `feat(secrets): add encrypted random secrets + decrypt script` |
| `5f98978` | T30 | `feat(observability): add Healthchecks.io ping script (placeholder URL)` |
| `59b4836` | T31 | `chore: gitignore logs/ for cron output` |
| `a72b634` | T32 | `feat(lib): add healthcheck helper + tests` |
| `3fec85d` | T33 | `feat(scripts): add 9-check smoke test` |
| `e43d767` | T34 | `feat(scripts): add local snapshot.sh (Phase 1; GCS in Phase 2)` |
| `0e2b5c9` | T35 | `feat(scripts): add panic, teardown, recovery runbook` |
| `42f5d29` | T36 | `feat(scripts): add test.sh (unit + integration runner)` |
| `9865243` | T37 | `test(integration): scaffolding (conftest + fixtures + Prism mock)` |
| `a6b9c01` | T38 | `test(integration): full turn + skill creation tests` |
| `011f88d` | T39 | `test(integration): sandbox isolation, secret leak, budget cap, chroma outage` |
| `b5fbd48` | T41 | `docs(runbook): Phase 1 acceptance protocol` |

### 7.4 Notable in-flight decisions made by implementer subagents

These are deviations from the verbatim plan that were necessary for execution. All approved as faithful to the plan's intent:

1. **`pyproject.toml` (T7)**: Added `[tool.setuptools.packages.find] include = ["lib*"]` to scope discovery to `lib/`. Without this, setuptools tried to package `secrets/` and `trajectories/` as Python packages and failed. The plan should be updated.

2. **sops on macOS (T5+)**: macOS sops looks for keys at `~/Library/Application Support/sops/age/keys.txt` by default, not `~/.config/sops/age/keys.txt` where the plan instructs. `scripts/decrypt-secrets.sh` and `scripts/healthcheck-ping.sh` set `SOPS_AGE_KEY_FILE=$HOME/.config/sops/age/keys.txt` explicitly. Bootstrap.sh inherits this.

3. **Scrubber pattern ordering (T13/T14)**: Plan listed `openai_api_key` (broader regex `\bsk-...`) before `anthropic_api_key` (`\bsk-ant-...`). Iterating destructively, OpenAI matched Anthropic keys first; Anthropic test failed. Fixed by reordering patterns in `config/scrubber-patterns.yaml` so anthropic fires first. TDD caught this.

4. **`.sops.yaml` regex broadening (T29)**: Original `path_regex: secrets/.*\.sops$` only matched encrypted output files. The plan invokes `sops -e secrets/litellm-master-key` (plaintext input with no `.sops` suffix) which triggered `no matching creation rules`. Broadened to `secrets/.+`.

5. **`secrets/.gitignore` rewrite (T29)**: Original was deny-list-style (ignore `*.env`, `*.json`, etc.) and allowed bare-name plaintext files like `litellm-master-key` to leak past. Rewrote as deny-by-default (`*` + whitelist `*.sops`, `README.md`, `.gitignore`, `*.template.txt`).

6. **`# pragma: allowlist secret` markers (T13/T17/T24/T39)**: Pre-commit `detect-secrets` flags fake/dummy secrets in test fixtures and config dummies. Inline `pragma` comments suppress per-file or per-line. Used at: `config/hermes/cli-config.yaml` (dummy LiteLLM API key), `deploy/docker-compose.yml` (dummy OPENAI_API_KEY for Honcho), `tests/integration/test_secret_leak.py` (intentional fake key).

7. **Pre-commit excludes for `tests/unit/test_scrubber.py` (T14)**: The test file has fake keys as positive-match fixtures. `detect-private-key`, `detect-aws-credentials`, `detect-secrets` all flagged them. Added explicit excludes in `.pre-commit-config.yaml`.

8. **Compose env_file blocks bare render (T24)**: Compose loads `env_file:` eagerly at parse time; without `secrets/telegram.env` present, `docker compose config` exits 1. Implementer temp-created an empty file, validated the render, deleted it before commit. **Bootstrap.sh must ensure secrets/telegram.env exists before any compose command in cold-start scenarios.**

9. **Cron registration (T31)**: Worked. Entry `*/5 * * * * cd <project> && ./scripts/healthcheck-ping.sh` registered. `docs/runbooks/healthcheck-cron-setup.md` also added for re-running on other hosts.

10. **CHANGELOG forward-looking (T1.2 → T1.7 duplicate)**: T1.2's `[Unreleased]` lists all upcoming Phase 1 work as "Added" (forward-looking, intentional per spec). T1.7 added a phase-specific bullet. There is now a duplicate that should be cleaned up at Phase 1 acceptance time when work moves from `[Unreleased]` to a versioned `[v0.1.0]` section.

### 7.5 Test status (verified at session end)

- `pytest tests/unit/ -v`: **37 passed in 0.14s**
- `pytest tests/integration/ --collect-only`: **8 tests collected, no errors** (not run; needs running stack)
- `python -m lib.limits_validator config/limits.yaml`: **Validation OK**
- `docker compose -f deploy/docker-compose.yml config`: exit 0
- `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml config`: exit 0
- `bash -n scripts/{smoke,snapshot,panic,teardown,test}.sh`: all parse-clean
- `git status` on phase/1: clean

---

## 8. Pending User-Manual Actions (Phase 1 Gates)

Phase 1 acceptance is **blocked on these four items**, all requiring user action:

### 8.1 T28 — Telegram bot creation

Follow `docs/runbooks/telegram-bot-setup.md`:

1. Open Telegram, message `@BotFather`, `/newbot`
2. Pick a display name + username ending in `bot`
3. Save the token
4. Set `/setprivacy` to `Disable` (so bot reads all messages)
5. Find your numeric Telegram ID via `@userinfobot`
6. Run from project root:
   ```bash
   cat > secrets/telegram.env <<EOF
   TELEGRAM_BOT_TOKEN=<paste-token-here>
   TELEGRAM_ALLOWED_USER_IDS=<your-numeric-id>
   EOF
   sops -e secrets/telegram.env > secrets/telegram.env.sops
   rm secrets/telegram.env
   ```
7. Set `notify_channels.telegram_chat_id: <your-numeric-id>` in `.worktrees/phase1/config/limits.yaml`
8. `(cd .worktrees/phase1 && python -m lib.limits_validator config/limits.yaml)` to confirm
9. Verify: `TOKEN=$(sops -d secrets/telegram.env.sops | grep TELEGRAM_BOT_TOKEN | cut -d= -f2); curl -fsS "https://api.telegram.org/bot${TOKEN}/getMe" | jq .` → JSON describing your bot
10. Commit: `(cd .worktrees/phase1 && git add secrets/telegram.env.sops config/limits.yaml && git commit -m "feat(secrets): add encrypted Telegram bot token + chat_id")`

### 8.2 T30 step 1 — Healthchecks.io account

1. Sign up at https://healthchecks.io (free)
2. Create a project named `hermes-local`
3. Copy the unique ping URL (looks like `https://hc-ping.com/<UUID>`)
4. Encrypt:
   ```bash
   echo "https://hc-ping.com/<YOUR-UUID>" > secrets/healthchecks-url
   sops -e secrets/healthchecks-url > secrets/healthchecks-url.sops
   rm secrets/healthchecks-url
   ```
5. Commit: `(cd .worktrees/phase1 && git add secrets/healthchecks-url.sops && git commit -m "feat(observability): add encrypted Healthchecks.io URL")`

### 8.3 T40 — Run bootstrap.sh end-to-end

After 8.1 and 8.2 are complete:

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent/.worktrees/phase1"
./scripts/verify-prereqs.sh         # all green
./scripts/bootstrap.sh              # builds + brings stack up + smoke tests
```

This step:
- Decrypts secrets
- Validates limits.yaml
- Builds `Dockerfile.hermes` (~5-10 min first time) and `Dockerfile.shell-sandbox`
- Pulls remaining images (litellm, chroma, honcho, postgres, otel-collector, phoenix, playwright-mcp)
- Brings up the docker-compose stack
- Runs `./scripts/smoke.sh` (9 checks)

Open Phoenix at http://localhost:6006 to verify traces.

### 8.4 T42 — Phase 1 acceptance protocol

Follow `docs/runbooks/phase1-acceptance.md` from inside the phase1 worktree:

1. Send 10 real Telegram messages spanning ≥3 task types (file search, GitHub MCP, shell, file read, Context7, etc.)
2. Verify autonomous skill creation: `docker compose -f deploy/docker-compose.yml exec -T hermes-agent ls /app/skills`
3. Restart hermes-agent, ask "What did we just talk about?", verify summary
4. Inspect Phoenix for traces with `service.name=hermes-agent`
5. Verify no critical entries in `secret-leak-attempts.log`
6. Verify daily spend recorded in LiteLLM, well under $100 cap

If all 7 criteria pass: **Phase 1 ACCEPTED**.

Tag and merge:
```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git checkout main
git merge --no-ff phase/1 -m "Merge phase/1 into main: Phase 1 local deployment accepted"
git tag -a phase1-accepted -m "Phase 1 (local Mac deployment) accepted on $(date -u +%Y-%m-%d)"
```

---

## 9. How to Resume After Context Loss

If you (future you, or a new agent) come back to this project with no conversation history:

1. **Read this file first** (`docs/superpowers/specs/SESSION-COMPLETE-2026-05-15-hermes-agent-full-architecture.md`).
2. **Read the design spec**: `docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md`.
3. **Read the Phase 1 plan**: `docs/superpowers/plans/2026-05-14-phase1-local-deployment.md`.
4. **Run `git log --all --oneline`** to confirm the branch state matches §7.1 above.
5. **Run `pytest tests/unit/ -v`** in the phase1 worktree to confirm 37 tests still pass.
6. **Check `docs/runbooks/phase1-acceptance.md`** for the acceptance protocol.
7. **Identify which of §8 items remain pending** and complete them in order.

---

## 10. Phase 2-4 Forward Look

These will get their own brainstorm + spec + plan + execution cycles when Phase 1 acceptance passes.

### Phase 2 — Cloud-prod migration

**Adds**:
- Terraform GCP project setup (`i-for-ai`)
- Compute Engine VM (e2-standard-2, COS image)
- Workload Identity Federation (replaces local ADC)
- Secret Manager (replaces sops decryption)
- Persistent SSD
- Cloud Logging + Cloud Trace + Cloud Monitoring (replaces Phoenix)
- Healthchecks.io heartbeat from VM
- Daily GCS snapshots (`scripts/snapshot.sh` adds `gsutil cp`)
- systemd service for compose stack
- VPC firewall enforcing egress allowlist
- Cloud NAT for outbound, IAP tunnel for SSH

**Gate**: 7-day soak with no manual interventions, no budget breach, all dashboards green.

### Phase 3 — Trajectory pipeline

**Adds**:
- `trajectory-shipper` service (continuously reads sessions.db, ships Atropos JSONL to GCS hourly)
- DVC for dataset versioning over GCS
- `eval/tasks/*.yaml` with ~50 hand-curated tasks
- Eval harness (LLM-as-judge graders)
- `rl-orchestrator` service with `enabled: false`
- Reward sanity checking automation

**Gate**: 1K trajectories collected, schema validated, sample replay matches, reward sanity ≥0.7, 20-trajectory human spot-check.

### Phase 4 — Atropos RL training

**Adds**:
- GCP A100 80GB or H100 80GB instance class (custom AMI with Atropos + deps pre-baked)
- `rl-orchestrator` with `enabled: true`
- Cloud Scheduler trigger for daily preflight check
- Telegram approval inline-keyboard handler
- Training pipeline: pull from GCS → train via Atropos environment → eval → register
- Eval-only baseline run (mandatory first run)
- Mid-run cost-overrun monitor with auto-kill
- Eval-regression abort

**Gate**: One full training cycle, evaluated model improves ≥2% on held-out suite vs base.

**Models trainable**: open-weight only (Llama, Qwen, DeepSeek). Trained model can be optionally swapped into LiteLLM as `vllm/your-trained-model`.

---

## 11. File Inventory (Final State)

### 11.1 Files on `main` branch

```
.gitattributes
.gitignore
.github/
├── ISSUE_TEMPLATE/
│   ├── bug_report.md
│   └── feature_request.md
└── PULL_REQUEST_TEMPLATE.md
CHANGELOG.md
CONTRIBUTING.md
LICENSE
README.md
docs/
├── architecture/README.md
├── conventions/
│   ├── branching.md
│   ├── code-style.md
│   ├── commit-messages.md
│   └── logging.md
├── decisions/
│   ├── 0001-use-hermes-agent-as-base.md
│   ├── 0002-vertex-ai-via-litellm-proxy.md
│   ├── 0003-tiered-sandboxing-strategy.md
│   ├── 0004-sops-age-secret-management.md
│   ├── 0005-self-rl-pipeline-architecture.md
│   ├── 0006-iterative-phase-build-with-gates.md
│   ├── 0007-worktree-per-phase-branching.md
│   ├── README.md
│   └── template.md
└── runbooks/README.md
trajectories/.gitkeep
```

### 11.2 Additional files on `phase/1` branch (.worktrees/phase1)

```
.gitmodules                              # T2: hermes-agent submodule
.pre-commit-config.yaml                  # T6
.secrets.baseline                        # T6
.sops.yaml                               # T5
hermes-agent/                            # T2 submodule, pinned to ddb8d8f
config/
├── hermes/
│   ├── AGENTS.md
│   ├── MEMORY.md
│   ├── SOUL.md
│   ├── USER.md
│   └── cli-config.yaml
├── limits-schema.json
├── limits.yaml
├── scrubber-patterns.yaml
└── toolsets.yaml
deploy/
├── Dockerfile.hermes
├── chroma/auth.json
├── docker-compose.dev.yml
├── docker-compose.test.yml
├── docker-compose.yml
├── honcho/init.sql
├── litellm/config.yaml
├── otel/
│   ├── collector.dev.yaml
│   └── collector.prod.yaml
└── sandboxes/Dockerfile.shell-sandbox
docs/runbooks/
├── healthcheck-cron-setup.md
├── phase1-acceptance.md
├── recovery.md
└── telegram-bot-setup.md
docs/superpowers/specs/
└── SESSION-COMPLETE-2026-05-15-hermes-agent-full-architecture.md   ← THIS FILE
lib/
├── __init__.py
├── healthcheck.py
├── limits_validator.py
├── scrubber.py
└── toolset_router.py
pyproject.toml
scripts/
├── bootstrap.sh
├── decrypt-secrets.sh
├── healthcheck-ping.sh
├── panic.sh
├── smoke.sh
├── snapshot.sh
├── teardown.sh
├── test.sh
└── verify-prereqs.sh
secrets/
├── .gitignore
├── README.md
├── chroma-token.sops
├── healthchecks-url.template.txt        # placeholder; user encrypts real URL
├── honcho-db-password.sops
└── litellm-master-key.sops
                                          # (telegram.env.sops created at T28 by user)
                                          # (healthchecks-url.sops created at §8.2 by user)
tests/
├── __init__.py
├── fixtures/
│   ├── openai-mock.yaml                 # Prism mock for tests
│   └── sample_session.json
├── integration/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_budget_cap.py
│   ├── test_chroma_outage.py
│   ├── test_full_turn.py
│   ├── test_sandbox_isolation.py
│   ├── test_secret_leak.py
│   └── test_skill_creation.py
└── unit/
    ├── __init__.py
    ├── test_healthcheck.py
    ├── test_limits_schema.py
    ├── test_scrubber.py
    └── test_toolset_router.py
```

### 11.3 Host-level resources (NOT in git)

- `~/.config/sops/age/keys.txt` (mode 600) — age private key. **BACK THIS UP TO YOUR PASSWORD MANAGER.** Loss = inability to decrypt all secrets.
- `~/.config/gcloud/` — Application Default Credentials for Vertex AI auth in Phase 1
- `~/.config/gh` — GitHub CLI auth, used by GitHub MCP
- crontab entry: `*/5 * * * * cd <project> && ./scripts/healthcheck-ping.sh >> logs/healthcheck.log 2>&1`
- Homebrew packages: `sops`, `age` (verified with `which`)

---

## 12. Glossary

| Term | Meaning |
|---|---|
| **Hermes Agent** | The upstream open-source agent runtime (NousResearch/hermes-agent) we wrap. Provides agent loop, skills, memory, multi-platform gateway, RL trajectory pipeline. |
| **Atropos** | Nous Research's RL training environment library, used in Phase 4 for fine-tuning trained models on collected trajectories. |
| **Honcho** | Open-source dialectic user modeling service. Maintains a "theory of mind" model of the user across conversations. Self-hosted via Docker in our stack. |
| **LiteLLM** | OSS proxy that translates OpenAI-format chat completions requests to many backend providers (Vertex AI, Anthropic, OpenAI, OpenRouter, etc.). Sits between Hermes and Vertex AI. |
| **Chroma** | OSS vector database. Stores embeddings for the agent's long-term semantic memory. |
| **Phoenix** | Arize AI's local-dev tool for visualizing OpenTelemetry traces of agent loops. Used in Phase 1 dev only. |
| **MADR** | Markdown Architecture Decision Records. The format we use for ADRs in `docs/decisions/`. |
| **sops** | Secrets OPerationS — Mozilla's tool for encrypting/decrypting structured config files. Combined with `age` (key format) for our use. |
| **Soft loop** | The in-context self-improvement: skill creation, memory curation, vector consolidation, user model updating. No GPU; runs continuously from Phase 1. |
| **Hard loop** | The actual RL fine-tuning of model weights from collected trajectories. Phase 4 only; gated by automated preflight + Telegram approval. |
| **Trajectory** | A sequence of (state, action, observation) tuples from one agent task, with reward annotations. Atropos-format JSONL. Used as training data in Phase 4. |
| **Toolset routing** | Each tool name → sandbox tier mapping (in_process / shell_sandbox / browser_sandbox / external_https / cloud_sandbox). Defined in `config/toolsets.yaml`. |
| **Worktree** | A git working tree distinct from the main checkout. We use one per phase under `.worktrees/`. |
| **Conventional Commits** | Commit message standard: `type(scope): subject`. Enables automated changelog generation. |
| **Keep-a-Changelog** | CHANGELOG format with sections (Added, Changed, Deprecated, Removed, Fixed, Security) and `[Unreleased]` rolling section. |
| **Workload Identity Federation** | GCP feature that lets services authenticate without long-lived keys. Replaces ADC in Phase 2. |

---

## End of artifact

This document is the canonical record of session 2026-05-14 / 2026-05-15. Future work should:
1. Update spec/plan files when reality diverges (don't update this artifact in place; write a new dated session-summary if substantial work happens later)
2. Append entries to `CHANGELOG.md` under `[Unreleased]` for every user-visible change
3. Add new ADRs when irreversible architectural decisions are made
4. Tag releases (`phase1-accepted`, `v0.1.0`, etc.) when phase gates pass
