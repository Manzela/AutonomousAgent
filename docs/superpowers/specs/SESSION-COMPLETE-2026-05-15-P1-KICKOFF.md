---
title: "AutonomousAgent — P1 Kickoff Handoff (resume-from-cold reference)"
date: 2026-05-15
session_dates: [2026-05-14, 2026-05-15]
purpose: "Single doc that lets a fresh Claude Code session pick up at the start of P1 implementation without re-reading the whole prior session"
status: ready-for-p1-implementation
predecessor_artifact: docs/superpowers/specs/SESSION-COMPLETE-2026-05-15-hermes-agent-full-architecture.md
---

# P1 Kickoff Handoff

> **For the next session**: read this doc first. Everything you need to start P1 is here. The 80-page predecessor session-complete artifact (`SESSION-COMPLETE-2026-05-15-hermes-agent-full-architecture.md`) has the deep architectural context if you need it; this doc is the operational brief.

---

## TL;DR — exactly where we are

**P0 is done.** Phase 1 stack is live, healthy, all 6 services up. Telegram bot responds. GitHub MCP works. `audit/` directory contains the locked 5-tier 10× plan and quality-first model mesh decisions.

**P1 is the next gate.** Six items, ~7 days of focused work via subagent-driven development on the `phase/1` worktree. Brainstorming is the first step (interactive with the user).

**Anchors before any code**:
- All design decisions live in `audit/audit-plan.md` (P0–P4 plan) and `audit/model-mesh-decision.md` (locked model picks).
- All conventions live in `docs/conventions/` (commit messages, branching, logging, code style, **new-repo-template**).
- Rationale lives in `docs/decisions/` (7 ADRs).
- The original full architecture is `docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md`.

---

## What's running right now (verify on session start)

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
# Expected: 6 containers — hermes, github-mcp, litellm-proxy (healthy),
#           shell-sandbox, phoenix, otel-collector
```

| Service | What it does | Image | Notes |
|---|---|---|---|
| `hermes` | Agent gateway loop (Telegram polling) | `autonomousagent/hermes:0.1.0` (built locally) | Default model: Opus 4.7 via global Vertex endpoint |
| `litellm-proxy` | OpenAI-format → Vertex AI translation | `ghcr.io/berriai/litellm:v1.84.0` | `vertex_location: global` (24M tok/min Opus quota) |
| `github-mcp` | GitHub MCP server (HTTP, port 8003) | `ghcr.io/github/github-mcp-server:latest` | All toolsets enabled; PAT auth from sops |
| `shell-sandbox` | Tool dispatch target | `autonomousagent/shell-sandbox:0.1.0` | `--cap-drop=ALL --network=none --read-only` |
| `phoenix` | OTel trace UI (dev) | `arizephoenix/phoenix:latest` | http://localhost:6006 |
| `otel-collector` | OTLP receiver | `otel/opentelemetry-collector-contrib:latest` | Distroless, no probe binaries |

Disabled / deferred for Phase 1: Honcho (no public Docker image), Playwright MCP (default cmd issue), Chroma self-hosted (using Chroma Cloud instead).

---

## What to verify before starting work

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent/.worktrees/phase1"

# 1. Branch check
git branch --show-current   # → phase/1
git log --oneline -5

# 2. Service health
docker ps --format "table {{.Names}}\t{{.Status}}"

# 3. Smoke test (uses Sonnet to dodge Opus quota chatter)
./scripts/smoke.sh    # → 7/7 passing

# 4. Telegram bot responsive — DM @Manzelagent_bot

# 5. GitHub MCP reachable via authenticated probe
docker exec autonomous-agent-litellm-proxy-1 /app/.venv/bin/python -c "
import urllib.request
r = urllib.request.urlopen('http://github-mcp:8003/', timeout=3)
print(r.status)" 2>&1 | head -1
# → 401 (means MCP is alive and gating; OK)
```

If any of these fail, fix them BEFORE starting P1. Don't build on a broken stack.

---

## Locked decisions (do not re-litigate)

These are the canonical answers from the prior session — do not re-debate them, even if they seem worth revisiting:

### 1. Architecture path
**5-tier**: P0 (unblock) → **P1 (10× on Mac, ~7d) → P2 (GCP cloud-prod migration, ~7d) → P3 (multi-LLM specialization mesh, ~8d) → P4 (Atropos pipeline + RL training, ~3w)**.

P0 is DONE. P1 is THIS session's work.

### 2. Model mesh (quality-first; budget unlimited initially)

| Class | Primary | Family |
|---|---|---|
| Reasoning / Orchestrator / Headline | Claude Opus 4.7 (Vertex AI **global** endpoint) | Anthropic |
| Long-context (>200K) | Gemini 3.1 Pro (Vertex AI — enable in P3) | Google |
| Code (high-stakes) | Claude Opus 4.7 *(GPT-5.5 Codex deferred — user has no OpenAI account yet)* | Anthropic |
| Code (high-volume) | Qwen3-Coder-Next-FP8 (self-hosted A100, **P3**) | Alibaba |
| Routine chatter | Claude Sonnet 4.6 | Anthropic |
| Memory curation | Claude Sonnet 4.6 | Anthropic |
| Judge: code-correctness | Qwen3-Coder-Next *(P3)* | Alibaba |
| Judge: safety | Claude Opus 4.7 | Anthropic |
| Judge: scope-fit | Gemini 3.1 Pro | Google |
| Judge: completeness | Gemini 3.1 Pro (1M ctx) | Google |

Cost-aware degradation: DISABLED initially per user "unlimited budget" guidance. Multi-judge consensus uses 3 model families (Anthropic + Google + Alibaba self-hosted) for genuine cross-family validation.

### 3. Region
- LiteLLM Anthropic: `vertex_location: global` (24M tok/min Opus quota, currently <1% utilized)
- Anthropic Vertex regional quotas are **sales-managed**, not self-serve — global endpoint sidesteps this entirely
- Future Gemini calls in P3: `me-west1` (Tel Aviv) for lowest latency from user's Israel location
- Future Qwen self-host on P3 A100: `me-west1` (co-located with Gemini)

### 4. P3 GPU mode
On-demand A100 ($2.7K/mo) for first month. Re-evaluate commit ($1.5K/mo) after measuring actual utilization.

### 5. Telegram bot identity
- Bot: `@Manzelagent_bot` (bot id 8911196639)
- User chat ID: `7217166969`
- All Telegram tokens encrypted in `secrets/telegram.env.sops` (env var: `TELEGRAM_ALLOWED_USERS`)

### 6. GitHub MCP
- Sidecar at `http://github-mcp:8003`
- All toolsets enabled (actions, repos, pull_requests, issues, security, etc.)
- PAT encrypted in `secrets/github-pat.sops` (granted scopes per `/user` headers: `admin:org_hook, admin:repo_hook, audit_log, codespace, copilot, delete:packages, gist, notifications, project, read:org, repo, workflow, write:network_configurations, write:packages`)
- **PAT was leaked in chat in the prior session — should be revoked + regenerated by user before any production use**

### 7. New-repo template
The agent's canonical SDLC playbook for any user request to "create a new repo" is at:
- Inside container: `/root/.hermes/new-repo-template.md`
- On host: `docs/conventions/new-repo-template.md`

It defines: repo settings, initial scaffold (16 files), branching, 5 CI workflows, branch protection, sops/age secret management, ADR practice, SDLC phasing, ops scripts, observability, anti-patterns, self-test checklist. The agent MUST consult it before any repo creation work (codified in `config/hermes/AGENTS.md`).

---

## P1 — the actual work (the next 7 days)

Six items, sequence matters (P1-1 + P1-6 must precede P1-2 because evaluators score against the failure matrix):

| # | Title | Effort | Detail |
|---|---|---|---|
| **P1-1** | Dynamic Parameter Locking via `TaskSpec.json` | 1.5d | Wraps Hermes' built-in `clarify` tool (`hermes-agent/toolsets.py:126`) with state machine that locks acceptance criteria → immutable `/data/specs/{slug}.json`. New code: `lib/anchors/{task_spec,clarification_loop,spec_store}.py`. Hermes plugin via `register(ctx)` lifecycle hook (`hermes-agent/AGENTS.md:465-489`). |
| **P1-6** | Failure trichotomy + 33-mode matrix + 24h escalation | 2d | Formalize fail-loud / fail-soft / self-heal model. Enumerate the 33-mode failure matrix from `autonomousagent_x_atelier_sweep.md:197-214`. Telegram-blocked tasks auto-escalate to `triage` after 24h. New code: `lib/durability/{trichotomy,escalation}.py` + `docs/architecture/failure-matrix.md`. |
| **P1-2** | Multi-judge evaluator (worker → evaluator → orchestrator) | 1.5d | Use Hermes' `tools/delegate_tool.py:1909` (`delegate_task`, sync ThreadPoolExecutor, isolated child contexts) as dispatch primitive. N judges score against TaskSpec on different axes. Majority vote → accept / reject-with-feedback / escalate. Each judge routes to a different model family (P3 mesh — initially same-family Anthropic until P3). New code: `lib/evaluators/{judge,consensus,orchestrator_hook}.py`. |
| **P1-3** | Per-step checkpointing + resume-from-last-good | 1d | **70% built** — extend `hermes-agent/batch_runner.py`'s `_load_checkpoint`/`_save_checkpoint` pattern from batch → live agent-loop scope. New code: `lib/durability/{checkpoint,resume}.py`. Hook via Hermes' `on_session_start`. |
| **P1-4** | `MEMORY/REJECTED.md` institutional memory | 0.5d | After 3 evaluator rejections of same approach, append structured failure entry. Agent reads at session start. New code: `lib/memory/rejected.py`. Wire into evaluator. |
| **P1-5** | Kanban orchestrator wiring | 0.5d | **100% built upstream** — Hermes ships SQLite Kanban (`hermes-agent/hermes_cli/kanban_db.py:559-673`). Just need: persistent volume mount + Telegram bridge (cards ↔ messages) + optional read-only HTML dashboard. New code: `lib/kanban/telegram_bridge.py`. |

**Total: ~7 days** of focused work.

### Order rationale
1. **P1-1 first** — every other item depends on TaskSpec existing as the anchor
2. **P1-6 second** — failure matrix is referenced by evaluator scoring rubrics in P1-2
3. **P1-2 third** — uses TaskSpec + matrix from above
4. **P1-3 fourth** — checkpointing wraps the agent loop including evaluator dispatch from P1-2
5. **P1-4 fifth** — institutional memory builds on evaluator rejection signals
6. **P1-5 last** — Kanban wiring is mostly already-built; bolt onto end

### Acceptance gate at end of P1
Per `docs/runbooks/phase1-acceptance.md`:
- 10 real Telegram messages spanning ≥3 task types → coherent replies
- Autonomous skill creation observed (Hermes' built-in nudge fires)
- State persists across container restart
- Phoenix shows traces at http://localhost:6006
- No critical entries in `secret-leak-attempts.log`
- Daily spend recorded in LiteLLM, well under cap

**ADD to acceptance gate** (P1-specific, beyond original spec):
- 1 multi-day TaskSpec successfully completes end-to-end (TaskSpec locked → sub-tasks dispatched → multi-judge evaluator approves → Kanban moves card to `done`)
- Container restart mid-task → resumes from checkpoint without losing work
- Force a rejection scenario (intentionally bad output) → 3 evaluator rejections → `MEMORY/REJECTED.md` updated → agent doesn't retry the same approach

---

## How to start P1 in the new session

### Option A — Subagent-driven (recommended)
Per the workflow we already used for P0:

```
1. Use superpowers:brainstorming with the user to align on:
   - TaskSpec JSON schema (what fields are mandatory / optional?)
   - Clarification loop max-questions threshold
   - Multi-judge axis definitions (4 axes? 5? what each scores)
   - Checkpoint interval (every N steps; trade-off N=3 vs N=5)
   - REJECTED.md retention TTL (default 30 days?)
   - Kanban Telegram bridge UX (one card per message? or one card per project?)

2. Use superpowers:writing-plans to produce
   docs/superpowers/plans/2026-05-15-phase1-10x-implementation.md
   with bite-sized P1-1 through P1-6 tasks, each with full code
   blocks + tests + commit messages.

3. Use superpowers:subagent-driven-development to execute.
   Per-task: implementer subagent → spec reviewer → code quality
   reviewer → mark done → next.
```

### Option B — Inline execution (faster wall-clock)
Skip the brainstorming + plan phase, execute directly with judgment calls and check in with user at acceptance gates. Riskier but faster.

**Recommendation: Option A.** P1 has many small but interlocking decisions (schema fields, axis definitions, retention windows) that benefit from explicit alignment.

---

## Worktree discipline (per user reminder)

User explicitly reminded: **use worktrees for P1**.

We're already on `phase/1` worktree (`.worktrees/phase1/`). For each P1 sub-task that warrants further isolation, you can create nested worktrees:

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"  # main repo root
git branch p1-anchors phase/1
git worktree add .worktrees/p1-anchors p1-anchors

# Work in there
cd .worktrees/p1-anchors

# When done, merge back
cd ../..
git checkout phase/1
git merge --no-ff p1-anchors
```

OR keep all P1 work on `phase/1` directly with feature commits (squash to taste). User's original ADR 0007 is loose enough to support either pattern. Recommendation: **keep P1 work on `phase/1` directly** — six items in 7 days don't need 6 nested worktrees; the per-phase isolation is the protection that matters.

**Phase 2, when it starts**: create `phase/2` worktree per ADR 0007.

---

## Outstanding security followups (do these BEFORE production)

| # | Item | Action |
|---|---|---|
| 1 | **Telegram bot token** leaked in prior session chat | Revoke via `@BotFather` → `/revoke`, regenerate via `/token`, re-encrypt to `secrets/telegram.env.sops` |
| 2 | **Chroma Cloud API key** leaked in prior session chat | Regenerate via Chroma Cloud dashboard, re-encrypt to `secrets/chroma-cloud.env.sops` |
| 3 | **Healthchecks.io ping URL** leaked in prior session chat | Regenerate the project on healthchecks.io, re-encrypt to `secrets/healthchecks-url.sops` |
| 4 | **GitHub PAT** leaked in prior session chat | Revoke at https://github.com/settings/tokens, regenerate with same scopes (repo, workflow, read:org, security_events), re-encrypt to `secrets/github-pat.sops` |

All four are sops-encrypted at rest, but they're in the chat transcript. The Anthropic chat retention policy may keep transcripts for some time; rotate to be safe. None of them are individually catastrophic to leak (they're personal-account-scoped credentials), but rotation is the correct hygiene.

---

## What user has unstaged on phase/1 (don't touch unless they ask)

When you `git status` you'll see:

- `M config/limits.yaml` — user has been editing this with their own additions (raised daily cap to $500, added `dynamic_guardrails: true`, added `telegram_escalation_timeout_h: 24`). Do not overwrite.
- `M docs/conventions/logging.md` — user has been editing this. Leave alone.
- `?? docs/architecture/failure-matrix.md` — user added this themselves (probably their own work on P1-6's matrix). Do not overwrite. Read it before P1-6 to see what they've drafted.

---

## Files most worth reading before brainstorming P1

1. **`audit/audit-plan.md`** — the full 5-tier plan, all P1 items expanded
2. **`audit/model-mesh-decision.md`** — locked model picks (used in P1-2 evaluator design)
3. **`docs/conventions/new-repo-template.md`** — the SDLC playbook (the agent already follows it)
4. **`docs/architecture/failure-matrix.md`** — user's draft (if they've started it)
5. **`hermes-agent/tools/delegate_tool.py`** lines 1909-2234 — the subagent dispatch primitive P1-2 will use
6. **`hermes-agent/hermes_cli/kanban_db.py`** lines 559-673 — the Kanban Task schema P1-5 will wire to Telegram
7. **`hermes-agent/AGENTS.md`** lines 465-525 — Hermes plugin lifecycle hooks (used by P1-1, P1-2, P1-3)

---

## Operational shortcuts (for the next session)

```bash
# Path
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent/.worktrees/phase1"

# Decrypt secrets (idempotent; safe to re-run)
./scripts/decrypt-secrets.sh

# Restart a service
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml restart hermes

# Tail hermes logs
docker logs autonomous-agent-hermes-1 -f

# Force-recreate a service (after config change)
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up -d --force-recreate hermes

# Run smoke
./scripts/smoke.sh

# Run unit tests
.venv/bin/python -m pytest tests/unit/ -v

# Phoenix UI
open http://localhost:6006

# GitHub repo
gh repo view Manzela/AutonomousAgent

# Branch state
git log --all --oneline | head -20
git worktree list
```

---

## Repo state summary at session end

- **Remote**: https://github.com/Manzela/AutonomousAgent (private, GitHub Pro)
- **Branches**: `main` (12 commits, protected, all CI green) + `phase/1` (~30 commits beyond main)
- **Latest commit on phase/1**: `dad982f` (`feat(secrets,deploy): encrypted GitHub PAT + drop github-mcp broken healthcheck`)
- **CI**: 5 active workflows (CI, secret-scan, pr-validation, release, dependabot). 11 required checks on `main`.
- **Tag**: none yet. Will tag `phase1-accepted` when P1 acceptance protocol passes.

---

## End of P1 kickoff handoff

Three things to do at the start of the new session:
1. Read this file
2. Run the verification commands under "What to verify before starting work"
3. Invoke `superpowers:brainstorming` to align with user on P1 design questions, then `superpowers:writing-plans`, then `superpowers:subagent-driven-development`

Good luck. The hard architectural work is done; P1 is mostly applied effort following the plan.
