---
title: Hermes Agent — Fully Managed Autonomous Agent with Self-RL Capabilities
date: 2026-05-14
status: approved
author: Daniel Manzela + Claude Opus 4.7
project_dir: /Users/danielmanzela/RX-Research Project/AutonomousAgent
upstream: https://github.com/NousResearch/hermes-agent
upstream_sha_at_design: ddb8d8fa842283ef651a6e4514f8f561f736c72e
related_specs: []
---

# Hermes Agent — Architecture Design

## Goal

Stand up a fully managed, autonomous, self-improving agent on this Mac (Phase 1) with a clean migration path to a 24/7 GCP Compute Engine deployment (Phase 2), continuous trajectory collection (Phase 3), and gated Atropos-based RL training of a custom open-weight model (Phase 4). All with security best practices (tiered sandboxing, secret management, network isolation, output filtering, approval gates), aggressive observability, and runtime-tunable limits.

## Decisions captured

| # | Decision | Choice |
|---|---|---|
| 1 | Deployment topology | **Hybrid: local dev → cloud prod** |
| 2 | Self-RL scope | **Full Atropos RL training pipeline** |
| 3 | LLM provider | **Vertex AI (Anthropic Claude 4.7) via LiteLLM proxy** |
| 4 | Messaging gateway | **Telegram only** |
| 5 | Sandboxing strategy | **Tiered: local read / Docker shell / cloud sandbox for arbitrary code** |
| 6 | Budget cap | **Aggressive: $100/day, no per-task token cap** |
| 7 | Observability stack | **OpenTelemetry + Cloud Trace (prod) + Phoenix (dev)** |
| 8 | GPU strategy for Phase 4 | **GCP A100/H100 Compute Engine instances** |
| 9 | Persistence | **SQLite (sessions) + Chroma (vectors) + nightly GCS backup** |
| 10 | Honcho deployment | **Self-hosted via Docker Compose** |
| 11 | Extra MCP servers | **GitHub MCP, Playwright MCP, Context7 MCP** |
| 12 | Build sequencing | **Iterative phases with validation gates** (Approach B) |
| 13 | Phase 4 trigger model | **Auto-detection + Telegram approval gate before spend** |

All numeric thresholds, intervals, retention windows, and caps are runtime-tunable via `config/limits.yaml`.

---

## 1. System Architecture

A single docker-compose stack runs identically on Mac (Phase 1) and on a GCP Compute Engine VM (Phase 2). Same containers, same networking, same volumes — only the host substrate and secret backend change.

### Logical layers

| Layer | Purpose | Where |
|---|---|---|
| Ingress | Telegram bot (long-poll), CLI socket, optional ACP webhook | `hermes-gateway` |
| Agent core | `hermes` Python process: agent loop, skill invocation, memory writes, tool dispatch, OTel tracing | `hermes-agent` |
| Model gateway | LiteLLM proxy: OpenAI-format → Vertex AI translation, retries, budget enforcement, cost telemetry | `litellm-proxy` |
| State | SQLite + Chroma + Honcho on a single persistent volume | `chroma`, `honcho`, sqlite file |
| Tool execution (tiered) | Local FS reads in-process; shell → `shell-sandbox` Docker; arbitrary code → Modal/Daytona | three tiers, dispatched by `toolsets.yaml` |
| Observability | OTel SDK in-agent → OTel Collector → Phoenix (dev) or Cloud Trace (prod). Structured JSON logs → stdout → Cloud Logging | `otel-collector`, `phoenix` |
| Trajectory pipeline (Phase 3+) | Tail agent logs → Atropos format → GCS bucket | `trajectory-shipper` |
| RL orchestration (Phase 3+) | Cron-driven preflight + Telegram approval + GPU instance lifecycle | `rl-orchestrator` |
| RL training (Phase 4) | Out-of-band: ephemeral GCP A100/H100 → train → eval → register | not in main stack |

### Key isolation principles

- Agent core cannot reach the host network — only whitelisted egress (LiteLLM, Telegram, GitHub MCP, Playwright sidecar, GCS, Healthchecks.io, Context7).
- Shell sandbox container: read-only host FS except `/workspace`; `--network=none`.
- Modal/Daytona sandboxes: ephemeral, network-restricted to a per-call allowlist, max 10-minute lifetime.
- All secrets via `.env` (sops-encrypted at rest in dev) → Secret Manager (Phase 2). Never in source control.

### Workspace layout

```
AutonomousAgent/
├── .claude/                    # Claude Code project state (already exists)
├── docs/
│   ├── superpowers/specs/      # this design + future specs
│   └── runbooks/               # operational manual procedures
├── hermes-agent/               # cloned upstream (NousResearch/hermes-agent)
├── deploy/
│   ├── docker-compose.yml      # full prod stack
│   ├── docker-compose.dev.yml  # dev overrides (Phoenix, host bind-mounts)
│   ├── docker-compose.test.yml # CI/test stack (mocked LLM, in-memory deps)
│   ├── Dockerfile.hermes       # extends upstream image, adds OTel SDK + our config
│   ├── litellm/config.yaml     # provider/model routing rules
│   ├── otel/collector.dev.yaml
│   ├── otel/collector.prod.yaml
│   ├── sandboxes/Dockerfile.shell-sandbox
│   └── systemd/hermes.service  # Phase 2: keeps stack running on VM
├── terraform/                  # Phase 2: GCP infra (compute, secrets, GCS, IAM, alerts, monitoring)
├── secrets/                    # gitignored, sops-encrypted
├── config/
│   ├── limits.yaml             # SINGLE SOURCE OF TRUTH for tunables
│   ├── scrubber-patterns.yaml  # regex patterns for output secret filtering
│   ├── toolsets.yaml           # tool → sandbox-tier routing rules
│   ├── hermes/                 # cli-config.yaml, AGENTS.md, MEMORY.md, SOUL.md
│   └── eval/tasks/             # Phase 3+: evaluation suite
├── scripts/
│   ├── bootstrap.sh            # one-shot local Phase 1 setup
│   ├── healthcheck.sh          # called by Healthchecks.io ping
│   ├── snapshot.sh             # nightly GCS snapshot (state + vectors)
│   ├── smoke.sh                # post-deploy smoke test
│   └── test.sh                 # unit + integration runner
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── trajectories/               # local trajectory cache before GCS upload
```

---

## 2. Components

Twelve docker-compose services across the project lifecycle:
- Phase 1 dev: 10 services (rows 1–10 below, including Phoenix)
- Phase 1/2 prod: 9 services (drop Phoenix)
- Phase 3+ prod: 11 services (add `trajectory-shipper` + `rl-orchestrator`)
- Phase 3+ dev: 12 services (all rows)

| Service | Image | Purpose | Ports | Volumes | Depends on |
|---|---|---|---|---|---|
| `hermes-agent` | `Dockerfile.hermes` (extends upstream) | Main agent loop, skill invocation, memory, tool dispatch | 7878 (admin) | `hermes-data:/data`, `./config/hermes:/app/config:ro`, `./skills:/app/skills` | litellm, chroma, honcho, otel |
| `hermes-gateway` | same image, gateway entrypoint | Telegram bot + ACP webhook listener | — (long-poll) | `hermes-data:/data:ro` | hermes-agent |
| `litellm-proxy` | `ghcr.io/berriai/litellm:main-latest` | Vertex AI translation, retries, budget enforcement | 4000 internal | `./deploy/litellm/config.yaml:ro`, gcloud creds | otel |
| `chroma` | `chromadb/chroma:latest` | Vector store for long-term semantic memory | 8000 internal | `chroma-data` | — |
| `honcho` | `plasticlabs/honcho:latest` | Dialectic user modeling | 8001 internal | — | honcho-db, litellm |
| `honcho-db` | `postgres:16-alpine` | Honcho's Postgres backend | 5432 internal | `honcho-db-data` | — |
| `otel-collector` | `otel/opentelemetry-collector-contrib:latest` | OTLP receiver → Phoenix or Cloud Trace | 4317/4318 internal | `./deploy/otel/collector.{dev|prod}.yaml:ro` | — |
| `phoenix` *(dev only)* | `arizephoenix/phoenix:latest` | Local trace visualization | 6006 → host | `phoenix-data` | — |
| `shell-sandbox` | `Dockerfile.shell-sandbox` | Hermes' shell tool target; `--cap-drop=ALL --network=none` | — | `workspace:/workspace`, host FS read-only | — |
| `playwright-mcp` | `mcr.microsoft.com/playwright/mcp:latest` | Browser automation tool target | 8002 internal | `playwright-data` | — |
| `trajectory-shipper` *(Phase 3+)* | small Python image | Tail sessions → Atropos JSONL → GCS | — | `hermes-data:ro` | hermes-agent |
| `rl-orchestrator` *(Phase 3+)* | small Python image | Preflight + Telegram approval + Compute Engine instance lifecycle | — | — | trajectory-shipper |

### Volumes

- `hermes-data` — sessions DB, MEMORY/USER/SOUL, skills cache, FTS5 index
- `chroma-data` — vector embeddings + metadata
- `honcho-db-data` — Postgres data dir
- `phoenix-data` — dev-only trace storage
- `workspace` — agent's working dir for shell sandbox

### Networks

- `internal` — all services, no external access
- `egress` — only `litellm-proxy`, `hermes-gateway`, `playwright-mcp`, `trajectory-shipper`, `rl-orchestrator` attached; egress-restricted via firewall in Phase 2

### Resource budget

~3.5GB RAM idle, peaks ~6GB during heavy turns. Fits on Mac 16GB+ and on `e2-standard-2` (8GB) GCP VM.

---

## 3. Data Flow

### 3.1 User turn (Telegram → response)

```
User on phone
  → Telegram servers
  → hermes-gateway (long-poll, allowlist check, allowlist user)
  → hermes-agent (assemble prompt: system + memory + ToM + tools schema + history)
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

**Trust boundaries (sanitization points):**
- Inbound TG: sender allowlist, per-user rate limit, strip Telegram markup
- Tool dispatch: command-class allowlist via `toolsets.yaml`; sandbox enforcement
- Outbound: regex scrubber for AWS/GCP/GitHub/Anthropic/OpenAI/JWT-shaped secrets

### 3.2 Self-learning loop (asynchronous nudges)

| Nudge | Trigger | Action | Tunable |
|---|---|---|---|
| Memory curator | Every 6h | Agent rereads MEMORY.md, scores facts for keep/promote/forget, emits diff | `limits.nudges.memory_curator_interval` |
| Skill extractor | After complex task (≥10 turns OR ≥3 distinct tools) | Reflect on trajectory; if reusable, write `/skills/{slug}/SKILL.md` | `limits.nudges.skill_extractor_min_*` |
| Vector consolidator | Nightly 03:17 UTC | Cluster Chroma embeddings, summarize via LiteLLM, promote to MEMORY.md, prune low-score >90d | `limits.nudges.vector_consolidator_cron` |

All three nudges are themselves Hermes turns — fully observable in OTel, count toward budget, inspectable in Phoenix.

### 3.3 Trajectory pipeline (Phase 3+)

```
hermes-agent writes turn records to sessions.db
    ↓
trajectory-shipper (hourly):
    tails sessions.db (WAL)
    buffers 100 completed turns
    trajectory_compressor.py → Atropos JSONL format with reward annotations
    uploads → gs://hermes-trajectories-{env}/year=YYYY/month=MM/day=DD/hour=HH/batch-{uuid}.jsonl.zst
    ← write watermark in hermes-data/.shipped
    on upload failure: exponential backoff, fall back to local /trajectories/queue/, retry next iteration
```

DVC tracks dataset versions. Phase 4 reads only from this bucket — never from the live agent's DB.

---

## 4. Configuration & Tunable Limits

Single source of truth: `config/limits.yaml`. Validated against JSON schema at startup; bad values fail fast. Phase 2 mirrors to Secret Manager.

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
  always_ask_patterns:
    - "rm -rf"
    - "git push --force"
    - "DROP TABLE"
    - "kubectl delete"
    - "*.private*"
  never_ask_patterns:
    - "ls *"
    - "cat *"
    - "git status"
    - "git log*"
    - "rg *"
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
  enabled: true
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
    estimate_includes_in_message: [eval_baseline, est_cost_usd, est_duration_h, dataset_hash, dataset_size]
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
    auto_swap_in_litellm_if_eval_improves_pct: null
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
  telegram_chat_id: <from secrets>
  cloud_monitoring_email: <from secrets>
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

---

## 5. Security Model

Defense in depth, four layers.

### 5.1 Identity & Secrets

| Secret | Phase 1 | Phase 2 |
|---|---|---|
| Vertex AI auth | ADC via `gcloud auth application-default login`, mounted RO into LiteLLM | Workload Identity Federation; SA `hermes-agent-prod@i-for-ai.iam.gserviceaccount.com` with `roles/aiplatform.user` |
| Telegram bot token | sops-encrypted `secrets/telegram.env` | Secret Manager `telegram-bot-token` |
| GitHub MCP token | host `~/.config/gh` (RO) | Secret Manager `github-mcp-token`, fine-grained PAT |
| LiteLLM master key | sops-encrypted, generated at bootstrap | Secret Manager, rotated quarterly |
| Honcho DB pw | sops-encrypted | Secret Manager |
| Chroma auth token | sops-encrypted | Secret Manager |
| Modal/Daytona tokens | sops-encrypted | Secret Manager |
| Healthchecks.io URL | sops-encrypted | Secret Manager |

`git-secrets` pre-commit hook blocks accidental commits. No plaintext secrets on disk outside `/run/secrets` tmpfs or Secret Manager fetch cache.

### 5.2 Network isolation

- Two compose networks: `internal` (no egress) and `egress` (whitelisted only)
- Phase 2: GCP VPC firewall enforces same allowlist; VM has no public IP; Cloud NAT for egress; IAP tunnel for SSH

Allowed egress endpoints (initial — will evolve):
- `generativelanguage.googleapis.com` (Vertex AI)
- `api.telegram.org`
- `api.github.com`
- `storage.googleapis.com` (GCS)
- `hooks.healthchecks.io`
- `api.context7.com`
- `modal.com`, `daytona.io`
- Playwright per-call allowlist

### 5.3 Tool execution isolation

| Tool class | Container | Capabilities | FS | Network | Resources | Timeout |
|---|---|---|---|---|---|---|
| File reads, grep, ls | hermes-agent in-process | none | host RO mounts | n/a | n/a | 30s |
| Shell, git, jq | shell-sandbox | `--cap-drop=ALL` | host RO + writable `/workspace` + `/tmp` 100MB tmpfs | `--network=none` | `--memory=1g --cpus=1.0 --pids-limit=200` | `limits.shell_timeout_s` |
| Browser | playwright-mcp | `--cap-drop=ALL --cap-add=SYS_ADMIN` | tmpfs only | egress per-call allowlist | `--memory=2g --cpus=2.0` | `limits.browser_timeout_s` (300s) |
| Arbitrary code (LLM-generated) | Modal/Daytona ephemeral | provider-managed microVM | ephemeral tmpfs | per-call allowlist | provider default | `limits.modal_max_lifetime_s` |
| External HTTPS MCPs | hermes-agent in-process via `httpx` | n/a | n/a | egress, host-only DNS, mTLS where avail | n/a | 60s |

### 5.4 Output Filtering

Regex-based scrubber before any persist or outbound. Patterns versioned in `config/scrubber-patterns.yaml`. Hits replaced with `[REDACTED:reason]` and logged to `secret-leak-attempts.log` for audit. Patterns include AWS `AKIA*`, GCP SA JSON shapes, Anthropic/OpenAI `sk-*`, GitHub `ghp_*`/`gho_*`, JWTs, generic high-entropy hex.

### 5.5 Approval Gates

Hermes' built-in `command_approval` system; tunable in `limits.yaml` (§4). In Telegram, ask-prompts come back as inline keyboard buttons (Approve / Deny / Always allow). On deny, agent gets a structured tool error.

### 5.6 Audit trail

Every tool call logged with timestamp, session_id, tool, sanitized args, result-class, latency. To OTel as structured events. Phase 2: 90-day Cloud Logging + 1-year GCS coldline.

---

## 6. Self-RL Loop

### 6.1 Soft loop (Phase 1+, runs continuously, no GPU)

Three nudges (see §3.2) shape behavior without updating model weights. Online improvement: every conversation makes future conversations better via skills/memory/user-model.

### 6.2 Hard loop (Phase 3 + Phase 4)

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

### 6.3 Reward signals

Three reward sources, weighted in `limits.rl_rewards.weights`:
- `user_explicit` (1.0): /thumbs-up, /thumbs-down, "good", "no that's wrong"
- `user_implicit` (0.3): follow-up patterns (re-ask, abandon, retry)
- `self_consistency` (0.2): tool call agreement with later observations
- `task_completion` (0.5): conversation reached natural completion

Reward horizon 20 turns. Sessions <5 turns excluded from training.

### 6.4 Phase 4 safety

- First Phase 4 run is **eval-only** (load base, score on suite, store baseline)
- Reward-sanity preflight (auto + 20-trajectory human spot-check before first training run)
- Eval suite versioned in git (`eval/tasks/*.yaml`), ~50 tasks
- Models trainable: open-weight only (Llama, Qwen, DeepSeek). Trained model can be optionally swapped into LiteLLM as `vllm/your-trained-model` later.
- Phase 4 GPU cost only kicks in when training is approved — not as standing infra.

---

## 7. Observability

### 7.1 Instrumentation surface

Every span: `session_id`, `user_id`, `turn_id`, `phase`, `env`, `agent_version`, `model_id`.

| Source | Spans | Key metrics |
|---|---|---|
| hermes-agent | `turn.start/end`, `tool.dispatch/complete`, `memory.write`, `skill.invoke/create`, `nudge.fire` | turns/min, avg duration, tools-per-turn, skill creation rate |
| litellm-proxy | `model.call` parent of `model.attempt` | tokens_in/out, latency, cost_usd, retry_count, cache_hit |
| chroma, honcho | DB query spans | latency, result_count |
| shell-sandbox | `sandbox.shell.exec` | command_class, exit_code, stdout_bytes, duration |
| playwright-mcp | `sandbox.browser.action` | action_type, url_class |
| trajectory-shipper | `trajectory.batch.upload` | batch_size, compressed_bytes, upload_duration |
| rl-orchestrator | `rl.preflight`, `rl.approval_sent`, `rl.run_*` | preflight result fields, run_cost_usd, eval_score |

### 7.2 Pipeline

```
all services → OTLP gRPC → otel-collector → {Phoenix (dev) | Cloud Trace (prod)}
                                          → {local JSON files (dev) | Cloud Logging (prod)}
                                          → {— | Cloud Monitoring (prod, metrics)}
```

### 7.3 Dashboards (Cloud Monitoring prod, Phoenix dev)

Defined as code in `terraform/monitoring/`:

1. Cost & Budget — daily/monthly $ vs cap, 7-day rolling, burn-rate forecast
2. Agent Activity — turns/hour, tool-mix, skill creation, memory growth, top-N skills
3. Model Performance — p50/95/99 latency, retry rate, cache-hit, token throughput
4. Sandbox Health — exec count, error rate, timeout rate, top denied commands, restarts
5. Self-RL Pipeline (Phase 3+) — trajectories/hour, GCS bytes, dataset age, reward distribution, training history

### 7.4 Alerts

Tunable in `limits.alerts`:
- Budget threshold trips (50/75/90/100% daily, 75/90/100% monthly)
- Heartbeat missed (3×60s)
- VM unreachable (5min)
- LiteLLM error rate >20% in 5min
- Sandbox OOM >5/5min
- Scrubber leak attempt (any)
- RL run failed / cost overrun >25%
- Trajectory shipper lag >30min

Routes: Telegram (default), Cloud Monitoring email (prod backstop), PagerDuty (optional, off).

### 7.5 Heartbeat

`hermes-agent` POSTs to private Healthchecks.io URL every 300s. Healthchecks alerts to Telegram on missed ping. Outside-in liveness — independent of self-reporting.

### 7.6 Log retention

- Cloud Logging 30d hot → 30d more in GCS coldline → delete after 365d
- Trace head-sample 100% (small volume); tail-sample on errors and slow-p99
- Local dev logs rotated at 100MB, keep 5 files

---

## 8. Error Handling

### 8.1 Failure-handling philosophy

- **Fail-loud** (alert + halt): security failures, budget breach, data corruption, training cost overruns
- **Fail-soft** (degrade + log): tool errors, Honcho/Chroma temporary unavailability, snapshot upload failure, single trajectory drop
- **Self-heal** (retry silently): transient 429/503, rate limits, container restarts within frequency budget

User-facing: agent **always** acknowledges degradation. Trust > apparent capability.

### 8.2 Failure matrix (summary)

Comprehensive matrix in design discussion. Highlights:
- LLM 429 → exponential backoff per `limits.retries.litellm_*`
- LLM 401 (auth) → halt + critical alert
- Malformed tool call → 3 in-turn retries → abandon turn + alert
- Infinite tool-call loop → cap at `limits.agent.max_turns_per_task = 50` → abort + memo
- Context overflow → auto-`/compress`
- Sandbox timeout → tool error to agent, agent decides next action
- Modal/Daytona down → 1 retry → fall back to local Docker shell with explicit note
- SQLite lock → 200ms retry → halt writes → degraded read-only mode
- Chroma unreachable → empty vector results + warning, agent operates without long-term memory
- Honcho unreachable → null ToM, agent operates without dialectic profile
- Snapshot upload fails → local queue + retry; alert after 3 consecutive failures
- Disk <10% → emergency vacuum, prune, alert
- VM preempted → pre-shutdown script flushes state, instance group respawns
- VPC firewall blocks egress → fail-loud, no auto-response
- Phase 4 reward-sanity score below threshold → block trigger + alert
- Phase 4 GPU instance fails to start → retry 3 zones → abort + alert
- Phase 4 training cost overrun → auto-kill via Compute Engine API + alert
- Trained model fails eval → don't register, keep checkpoint in cold storage

### 8.3 Panic stop

`hermes panic` (CLI) and `/panic` (Telegram, restricted to your user_id):
- Halt all in-flight tool calls
- Drain queued nudges
- Stop accepting new turns (HTTP 503 from gateway)
- Snapshot state to GCS
- Post status to Telegram
- `--teardown` also stops the GCP VM (Phase 2)

Recover via `hermes resume`.

---

## 9. Testing Strategy

### 9.1 Unit tests (`tests/unit/`)

Pre-commit + CI. Fast (<30s).

- `limits.yaml` schema validates, defaults present, types correct
- Secret scrubber regexes (positives + negatives)
- Toolset routing logic per `toolsets.yaml`
- Trajectory compressor produces valid Atropos JSONL
- RL preflight checks
- Reward signal computation
- Approval-gate state machine

### 9.2 Integration tests (`tests/integration/`)

`docker-compose.test.yml` with mocked LLM. PR + nightly. ~5min.

- Full TG → response happy path
- Skill creation after complex task
- Memory curator nudge
- Sandbox isolation (FS escape, external HTTP, env exfiltration all blocked)
- Secret leak attempt → scrubber + alert
- Budget cap enforcement
- LiteLLM retry on 429
- Chroma outage → graceful degradation
- Snapshot round-trip
- Trajectory pipeline → mock-GCS

### 9.3 Smoke tests (`scripts/smoke.sh`)

Per-deploy. ~3min. Blocks deploy on failure.

- All containers healthy
- Internal network reachability
- Egress allowlist works (TG bot getMe → 200)
- Egress denylist works (example.com → blocked)
- Synthetic turn through CLI → real LLM → response
- Memory write persists across container restart
- OTel trace appears in Phoenix/Cloud Trace within 30s
- Budget endpoint returns expected state
- Panic endpoint reachable

### 9.4 Eval suite (`eval/tasks/*.yaml`, Phase 3+)

~50 hand-curated tasks; deterministic graders (LLM-as-judge with rubric or programmatic). Versioned in git for cross-time comparison.

Schema example:
```yaml
id: pr-summarize-001
category: code-research
description: Summarize the most recent open PR
input:
  message: "Summarize the latest open PR in NousResearch/hermes-agent"
expected_behavior:
  - tool_used: github_mcp.list_pull_requests
  - tool_used: github_mcp.get_pull_request
  - response_contains_any: [PR number, PR title, author]
  - response_word_count: [50, 500]
grader:
  type: llm_judge
  rubric: |
    Score 0-10 based on real PR identification, summary content, accuracy, conciseness.
  passing_score: 6
```

Runs:
- Manual: `hermes eval run --suite all`
- Pre-Phase-4: rl-orchestrator runs eval as preflight, computes baseline
- Post-Phase-4: same eval against new model, compare
- Weekly drift check (Phase 2 optional)

### 9.5 Manual verification protocols (`docs/runbooks/`)

| Protocol | When | What |
|---|---|---|
| Phase 1 acceptance | End of Phase 1 | Send 10 real TG messages spanning ≥3 distinct task types; observe agent autonomously creates ≥1 skill via the skill-extractor nudge (not manually); restart container and confirm memory/sessions persist; verify traces visible in Phoenix; verify no entries in `secret-leak-attempts.log` |
| Phase 2 acceptance | After Phase 2 | 7-day soak: VM up, snapshots succeed, budget under cap, TG responsive, no manual interventions |
| Phase 3 acceptance | At 1K trajectories | Spot-check 20 random: rewards sane, schema valid, no PII, replay matches |
| Phase 4 first run | Before flipping `enabled: true` | Dry-run preflight, TG approval renders correctly, cost estimate accurate, abort path verified |

### 9.6 Chaos tests (quarterly)

- Kill -9 hermes-agent mid-turn → restart in 30s, conversation resumes
- Block egress 5min → agent surfaces error, queues turn, recovers
- Fill disk to 95% → vacuum + prune, alert, read-only if needed
- Revoke Vertex AI SA mid-run → halt within 1 turn, alert
- Corrupt last GCS snapshot → fail-loud, fall back to previous good

---

## 10. Build Sequencing — Approach B (Iterative Phases)

Each phase has a success gate. Don't move on until the gate passes.

### Phase 1: local-first (Mac)

Hermes Agent in Docker on Mac. LiteLLM → Vertex AI proxy. SQLite + Chroma + Honcho self-hosted. Telegram gateway. Tiered sandboxing. OTel → local Phoenix. Structured logs. Budget caps. Secret management via sops + .env.

**Gate**: agent answers from Telegram, learns ≥1 skill, persists across container restart, traces visible in Phoenix, smoke tests pass.

### Phase 2: cloud-prod migration

Terraform GCP project. Compute Engine VM (e2-standard-2, COS image). Secret Manager. Persistent SSD. Cloud Logging + Cloud Trace + Cloud Monitoring. Healthchecks.io heartbeat. Daily GCS snapshots. systemd service. Deploy same docker-compose stack.

**Gate**: agent runs 24/7 unattended on GCP for 7 days, recovers from VM preemption, no budget breach, all dashboards green.

### Phase 3: trajectory pipeline

`trajectory_compressor.py` writes Atropos-format trajectories to GCS continuously. DVC dataset versioning. Evaluation harness scaffolded but unused. rl-orchestrator with `enabled: false`.

**Gate**: 1K+ clean trajectories collected, schema validated, sample replay works, reward sanity score ≥0.7, 20-trajectory human spot-check passes.

### Phase 4: Atropos RL training (auto-trigger + approval gate)

Provision GPU instance class (A100 80GB or H100). Atropos environments configured. Training scripts. Eval loop. Model registry in GCS. rl-orchestrator with `enabled: true` and Telegram approval.

**Gate**: one full training cycle completes, evaluated model improves ≥2% on held-out suite vs base.

---

## 11. Open Items / Will-Evolve

- Egress allowlist (will grow as new MCPs are added)
- Approval-gate `always_ask_patterns` (refine based on observed false negatives)
- Toolset routing (`config/toolsets.yaml`) (refine as new tools enter the agent's toolset)
- Eval suite (`eval/tasks/`) — start with 10, grow to 50+ across Phase 3
- Reward weights (calibrate after first 1K trajectories)
- Model routing in LiteLLM (Phase 1 config ships single-model `vertex_ai/claude-opus-4-7`; cheap/strong split deferred until Phase 2 traffic patterns are observable)

## 12. Out of Scope (this spec)

- Multi-user agent (single-user only — your TG account)
- Voice memo transcription (Hermes supports it; defer until needed)
- Discord/Slack/WhatsApp gateways (defer)
- Multi-region GCP failover (single zone, single VM)
- Trained-model serving infrastructure (vLLM cluster — Phase 5+ if ever)
- Multi-agent coordination (Hermes' subagent feature stays available but unused in initial deployment)

---

## Appendix A — Upstream Hermes Agent reference

- Repo: https://github.com/NousResearch/hermes-agent
- License: MIT
- Default branch: main
- SHA at design time: `ddb8d8fa842283ef651a6e4514f8f561f736c72e`
- Built-in capabilities used: agent loop, skills system, MEMORY/USER/SOUL files, FTS5 session search, Honcho integration, multi-platform gateway, 7 terminal backends, batch_runner.py, trajectory_compressor.py, environments/ for Atropos
- Built-in capabilities NOT used: Discord/Slack/WhatsApp/Signal/Email gateways, voice transcription, Modal/Daytona via Hermes' built-in (we manage these via toolset routing instead), Singularity backend

## Appendix B — Cost projection (rough)

| Phase | Monthly cost (USD) |
|---|---|
| 1 (local Mac) | ~$50-200 LLM + ~$0 infra |
| 2 (GCP VM, 24/7) | +$30-50 VM + $5 storage + $10 logging = ~$45-65 infra |
| 3 (+ trajectory pipeline) | +$1-5 GCS storage |
| 4 (per training run, ~4/mo) | +$50-400 GPU per run, ~$200-1600/mo if max |

Worst case with daily $100 LLM cap maxed and 4 training runs/mo: ~$3000-4500/mo. Realistic ongoing ~$200-500/mo through Phase 3.
