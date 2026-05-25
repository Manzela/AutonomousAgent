# Forensic Findings — AutonomousAgent (Phase 1 → 10× Vision Gap)

**Audited:** 2026-05-15
**Auditor:** Claude Opus 4.7
**Target scope:** `.worktrees/phase1/` (live deployment) measured against the 10× vision documented in `~/Downloads/Optimizing AutonomousAgent/`
**Pass:** 1 (codebase-only, ~15 min budget)

---

## What exists today (observable)

### Live deployment
- **5 containers running, healthy:** `hermes`, `litellm-proxy` (healthy), `phoenix`, `otel-collector`, `shell-sandbox`
- **7/7 smoke checks pass** — see `scripts/smoke.sh`
- **Real LLM round-trip verified** — Hermes CLI inside the container returns `pong` via LiteLLM → Vertex AI → Anthropic Claude Sonnet 4.6 (`docker exec ... hermes -z 'Reply pong' -m vertex_ai/claude-sonnet-4-6 --provider custom`)

### Architecture (current)
- **Single-service agent loop**: `hermes gateway run` IS the agent (the original 2-service spec was wrong; collapsed in commit `408459e`).
- **Inference path**: Telegram → Hermes long-poll → in-process agent loop → LiteLLM proxy → Vertex AI Anthropic Claude (Opus 4.7 default; Sonnet 4.6 fallback).
- **Sandboxing**: shell tool calls dispatch to `shell-sandbox` container (`--cap-drop=ALL --network=none --read-only`); Modal/Daytona cloud sandbox tier deferred.
- **Observability**: OTel SDK in agent + LiteLLM → OTel Collector → Phoenix at http://localhost:6006 (dev). Cloud Trace deferred to Phase 2.
- **Persistence**: SQLite (Hermes session DB) on `hermes-data` named volume. Chroma Cloud for vector memory.
- **Secrets**: sops-encrypted at rest with age recipient at `~/.config/sops/age/keys.txt`.
- **Telegram**: `@Manzelagent_bot` reachable; user_id `7217166969` allowlisted.

### Configuration discipline
- `config/limits.yaml` — single source of truth (budget, retries, sandboxes, agent caps, nudges, health, snapshots, approval, RL rewards, RL training, alerts, notify, log retention)
- `config/scrubber-patterns.yaml` — 10 regex patterns for output secret filtering (15 unit tests)
- `config/toolsets.yaml` — tool→sandbox-tier routing (15 unit tests)
- `lib/limits_validator.py` + JSON schema (5 unit tests)
- 37 unit tests total, all passing
- 8 integration tests scaffolded (require live stack)

### What's documented and committed (on phase/1)
- Architecture spec, Phase 1 plan, session-complete artifact
- 7 ADRs (MADR format)
- 4 conventions (commits, branching, logging, code style)
- 4 runbooks (telegram, healthcheck cron, recovery, phase1 acceptance)
- CHANGELOG (forward-looking [Unreleased])
- CI/CD: 5 workflows on `main`, 11 required checks, branch protection enabled

---

## What does NOT exist today (the 10× gap)

The current Phase 1 is **plumbing only**. It can talk to Vertex AI through Telegram. It does NOT yet implement the autonomous-agent properties from the research:

### A. Multi-agent Kanban orchestrator (NOT WIRED)
- Hermes ships built-in Kanban scheduler (`hermes kanban` subcommand exists in CLI help) — **never configured or invoked**
- No `Orchestrator → Worker → Evaluator` separation of duties — the gateway IS the agent IS the executor IS the evaluator
- No multi-agent dispatch; everything is a single-context turn

### B. Dynamic Parameter Locking (NOT IMPLEMENTED)
- No `TaskSpec.json` generation at project-initiation
- No clarification loop to lock acceptance criteria
- No "immutable anchors" mechanism
- Hermes' built-in skill creation exists but skills are derived FROM completion, not anchored TO a brief

### C. Closed-loop self-evaluation (NOT IMPLEMENTED)
- No worker→evaluator→orchestrator triangle
- No Multi-Agent Consensus pattern (e.g., 3 sub-prompts: Code, Safety, Completeness, majority voting)
- No EvoDesign-style K=N candidates with evolutionary selection
- The "self-improving" claims of Hermes operate at the skills layer (post-task reflection), not the in-task validation layer

### D. Crash resilience / checkpointing (NOT IMPLEMENTED)
- `scripts/snapshot.sh` exists but is **manual + nightly**; no per-step checkpointing
- No `checkpoint.json` written every N steps during a long-running task
- No `resume-from-last-known-good` orchestrator behavior
- No `REJECTED.md` institutional memory (3-cycle dead-end detection)

### E. Long-running operational hardening (PARTIAL)
- `limits.yaml.retries` defines exponential backoff for LiteLLM, but agent-loop, evaluator-loop, snapshot-loop have no codified backoff
- No `concurrency:` block in `limits.yaml` (subagent caps, judge caps, queue overflow strategy)
- No `dynamic_guardrails: true` flag — the spec is static, not BriefSpec-dependent
- No 24h Telegram-escalation timeout for human-blocked tasks
- Failure modes are listed in the spec (§8.2) but not codified as an explicit trichotomy (fail-loud / fail-soft / self-heal) with per-class behavior

### F. Self-hosted inference at high-volume (DEFERRED to Phase 4)
- Spec says Phase 4 = Atropos RL training of an open-weight model
- Research adds: serving the open-weight model at inference time (Qwen 3.6 Code or Llama 3.3 family) for orchestrator/evaluator chatter to dodge per-token API costs at 500M+ tokens/3 days
- Current state: 100% reliant on Vertex AI Anthropic (paid per token; Opus 4.7 quota also throttled by Claude Code's parallel usage on `autonomous-agent-2026`)

### G. Walk-away verification (NOT EXERCISED)
- The system has never run unattended for >1 hour
- No "weekend run" empirical evidence
- Phase 1 acceptance protocol defines 10 messages but doesn't test 48h continuous operation

### H. Honcho / dialectic user modeling (DROPPED)
- The original spec called for Honcho self-hosted; commit `eea96a2` removed it because upstream doesn't publish a public Docker image
- Result: agent has Hermes' built-in MEMORY/USER/SOUL files but no theory-of-mind layer

### I. Provider auth in Telegram gateway path (UNCONFIRMED)
- CLI direct chat works; the user reported "Provider authentication failed" via Telegram earlier
- Last commit `1a284de` mounted cli-config.yaml at the right path + wired `OPENAI_API_KEY` from LiteLLM master key
- **NOT YET RE-VERIFIED via Telegram** — pending user re-test

---

## Architecture comparison: current vs research-recommended

| Dimension | Current (phase/1) | Research-recommended (10×) | Gap |
|---|---|---|---|
| Layers | Gateway = Agent = Executor = Evaluator | Orchestrator → Worker → Evaluator (separation of duties) | **Critical** |
| Project initialization | None — agent just answers messages | PIP / clarification loop produces immutable BriefSpec / TaskSpec | **Critical** |
| Evaluation | None during task; post-task skill reflection | Multi-axis judges + Consensus + Det Gate scoring against TaskSpec | **Critical** |
| Concurrency | Single sequential agent loop | K=6 parallel candidates + priority tiers (Orch > Eval > Worker) | **Major** |
| Crash resilience | Container restart_policy; nightly snapshot | Per-step checkpoint.json + resume-from-last-good + REJECTED.md | **Major** |
| Failure handling | Compose restart-policy + LiteLLM backoff | Trichotomy (fail-loud / fail-soft / self-heal) with codified backoff | **Major** |
| Inference economics | 100% paid Vertex AI | Self-hosted Qwen/Llama for sub-agent chatter; Anthropic for headline | **Major** (Phase 4) |
| UI / visibility | Phoenix (traces only) | Kanban dashboard (TODO/IN_PROGRESS/EVALUATION/DONE/BLOCKED) | **Major** |
| Memory | SQLite + Chroma Cloud + skills | Above + REJECTED.md institutional memory + theory-of-mind | **Moderate** |
| Async escalation | Telegram + 5min approval timeout | Telegram + 24h escalation timeout → auto-backlog | **Moderate** |

---

## Code references (for fix planning)

- `deploy/docker-compose.yml:151-191` — `hermes` service (single-process; needs to spawn subagents)
- `config/hermes/cli-config.yaml:4-15` — `model:` block (provider routing)
- `config/limits.yaml:1-50` — top sections (budget already at $500/day per user edit; agent block has `dynamic_guardrails: true` flag set by user but no consumer code)
- `lib/` — only 4 modules (`limits_validator`, `scrubber`, `toolset_router`, `healthcheck`); no orchestrator, evaluator, checkpointer, kanban
- `scripts/snapshot.sh` — manual snapshot only; no per-step checkpoint
- No `MEMORY/REJECTED.md` exists in workspace
- No UI directory (`ui/`, `dashboard/`, `web/`)
- Hermes upstream submodule `hermes-agent/` has built-in `kanban` subcommand — never invoked from our wrapper

---

## Notable in-flight artifacts from this session

- 16 commits on `phase/1` since session start (plus 12 on `main`)
- 10 quoted-path / version-pin / image-not-found / mount-conflict / health-probe / config-schema fixes documented in commit messages
- User has manually edited `config/limits.yaml` to raise daily cap to $500 and add `dynamic_guardrails: true` + `telegram_escalation_timeout_h: 24` (per system reminder showing modified file)
- User has manually edited `OTEL_EXPORTER_OTLP_ENDPOINT` to port 4318 (HTTP) instead of 4317 (gRPC) in compose

---

## To enrich in pass 2 (read these references)

- `~/Downloads/Optimizing AutonomousAgent/autonomous_agent_principles.md` — 9-section principle map (already in context)
- `~/Downloads/Optimizing AutonomousAgent/implementation_planV2.md` — 3-tier 10× plan with concrete file changes (already in context)
- `~/Downloads/Optimizing AutonomousAgent/session_gap_analysis.md` — 21-requirement coverage matrix (already in context)
- `~/Downloads/Optimizing AutonomousAgent/autonomous_agent_x_atelier_sweep.md` — Atelier-side mapping with 3 enrichments (already in context)
- `~/Downloads/Optimizing AutonomousAgent/autonomousagent_x_atelier_sweep.md` — repo-side sweep (NOT YET READ — pass-2 candidate)
- `~/Downloads/Optimizing AutonomousAgent/Context-Session-Conversation-with-Gemini3.1Pro.md` — full Gemini transcript (already in context)
- `hermes-agent/cli.py` — built-in Hermes subcommands (kanban, computer-use, profile, fallback) — not yet inspected
- `hermes-agent/plans/` and `hermes-agent/AGENTS.md` — upstream's own agent guidance — not yet inspected
