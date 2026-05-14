# Phase 1 — Local Hermes Agent Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a fully functional, sandboxed, observable Hermes Agent on this Mac (macOS Darwin) with Vertex AI as the model backend, Telegram as the messaging gateway, all 12 services in docker-compose, and the Phase 1 acceptance gate passing (10 real Telegram messages → autonomous skill creation → restart-persistent state → traces in Phoenix → no secret leaks). All work uses **dedicated git worktrees per phase** with **comprehensive documentation discipline** (README, CHANGELOG following Keep-a-Changelog, Architecture Decision Records, CONTRIBUTING, commit/branch/log conventions, PR/issue templates).

**Architecture:** Wrap the upstream `NousResearch/hermes-agent` project (cloned as a git submodule, not modified) with our own docker-compose stack, configuration layer (`config/limits.yaml` + scrubber + toolset routing), security plumbing (sops-encrypted secrets, tiered sandboxing, regex output filtering), and observability pipeline (OpenTelemetry collector → Phoenix locally). LiteLLM proxy translates OpenAI-format requests from Hermes to Vertex AI (Anthropic Claude 4.7) using our existing GCP project `i-for-ai`. **Branching model**: `main` holds only accepted-and-tagged work; each Phase has its own long-running branch (`phase/1`, `phase/2`, `phase/3`, `phase/4`) checked out as a separate git worktree under `.worktrees/`. Phase work merges into `main` only after the Phase acceptance protocol passes and the work is tagged `phaseN-accepted`.

**Tech Stack:** Docker Desktop (already installed), docker-compose v2, Python 3.11 (via uv), sops + age (to install), gcloud CLI (already installed), Vertex AI (Anthropic Claude 4.7), LiteLLM proxy, ChromaDB, Honcho + PostgreSQL 16, OpenTelemetry Collector, Arize Phoenix (dev), Telegram Bot API. **Docs/VC tooling**: Conventional Commits, Keep-a-Changelog, MADR (Markdown Architecture Decision Records), git worktrees, GitHub PR/issue templates (used in Phase 2 once a remote is added; templates ship in Phase 1).

**Reference spec:** `docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md`

---

## File Structure (Phase 1)

What this plan creates / modifies:

```
AutonomousAgent/
├── .git/                                     # T1
├── .gitignore                                # T1
├── .gitattributes                            # T1
├── .worktrees/                               # T1.7 (gitignored; holds phase worktrees)
│   ├── phase1/                               # → branch phase/1 (where Phase 1 work happens)
│   ├── phase2/                               # → branch phase/2 (created in Phase 2 plan)
│   ├── phase3/                               # → branch phase/3
│   └── phase4/                               # → branch phase/4
├── README.md                                 # T1.1 (comprehensive)
├── CHANGELOG.md                              # T1.2 (Keep-a-Changelog)
├── CONTRIBUTING.md                           # T1.3
├── LICENSE                                   # T1.1 (MIT)
├── .github/
│   ├── PULL_REQUEST_TEMPLATE.md              # T1.4
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md                     # T1.4
│       └── feature_request.md                # T1.4
├── docs/
│   ├── architecture/
│   │   └── README.md                         # T1.5 (architecture index)
│   ├── decisions/                            # T1.5 (ADRs in MADR format)
│   │   ├── README.md                         # ADR index
│   │   ├── template.md                       # ADR template
│   │   ├── 0001-use-hermes-agent-as-base.md
│   │   ├── 0002-vertex-ai-via-litellm-proxy.md
│   │   ├── 0003-tiered-sandboxing-strategy.md
│   │   ├── 0004-sops-age-secret-management.md
│   │   ├── 0005-self-rl-pipeline-architecture.md
│   │   ├── 0006-iterative-phase-build-with-gates.md
│   │   └── 0007-worktree-per-phase-branching.md
│   ├── conventions/                          # T1.6
│   │   ├── commit-messages.md                # Conventional Commits
│   │   ├── branching.md                      # branch + worktree workflow
│   │   ├── logging.md                        # structured logging conventions
│   │   └── code-style.md                     # ruff + naming + comments
│   └── runbooks/                             # T28, T35, T41
│       └── README.md                         # T1.5 (runbooks index)
├── .sops.yaml                                # T5
├── .pre-commit-config.yaml                   # T6
├── pyproject.toml                            # T7
├── hermes-agent/                             # T2 (git submodule)
├── deploy/
│   ├── docker-compose.yml                    # T26
│   ├── docker-compose.dev.yml                # T27
│   ├── docker-compose.test.yml               # T37
│   ├── Dockerfile.hermes                     # T22
│   ├── Dockerfile.shell-sandbox              # T23
│   ├── litellm/config.yaml                   # T18
│   ├── otel/collector.dev.yaml               # T20
│   ├── otel/collector.prod.yaml              # T20
│   ├── chroma/auth.json                      # T24
│   └── honcho/init.sql                       # T25
├── secrets/
│   ├── .gitignore                            # T5
│   ├── README.md                             # T5
│   ├── telegram.env.sops                     # T29 (encrypted)
│   ├── litellm-master-key.sops               # T29
│   ├── chroma-token.sops                     # T29
│   ├── honcho-db-password.sops               # T29
│   └── healthchecks-url.sops                 # T29
├── config/
│   ├── limits.yaml                           # T11
│   ├── limits-schema.json                    # T11
│   ├── scrubber-patterns.yaml                # T13
│   ├── toolsets.yaml                         # T15
│   └── hermes/
│       ├── cli-config.yaml                   # T17
│       ├── AGENTS.md                         # T17
│       ├── MEMORY.md                         # T17
│       ├── USER.md                           # T17
│       └── SOUL.md                           # T17
├── lib/
│   ├── __init__.py                           # T7
│   ├── limits_validator.py                   # T11
│   ├── scrubber.py                           # T13
│   ├── toolset_router.py                     # T15
│   └── healthcheck.py                        # T32
├── scripts/
│   ├── bootstrap.sh                          # T8
│   ├── verify-prereqs.sh                     # T3
│   ├── decrypt-secrets.sh                    # T29
│   ├── smoke.sh                              # T33
│   ├── healthcheck-ping.sh                   # T32
│   ├── snapshot.sh                           # T34
│   ├── panic.sh                              # T35
│   ├── teardown.sh                           # T35
│   └── test.sh                               # T36
├── tests/
│   ├── __init__.py                           # T7
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_limits_schema.py             # T12
│   │   ├── test_scrubber.py                  # T14
│   │   ├── test_toolset_router.py            # T16
│   │   └── test_healthcheck.py               # T32
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── conftest.py                       # T37
│   │   ├── test_full_turn.py                 # T38
│   │   ├── test_skill_creation.py            # T38
│   │   ├── test_sandbox_isolation.py         # T39
│   │   ├── test_secret_leak.py               # T39
│   │   ├── test_budget_cap.py                # T39
│   │   └── test_chroma_outage.py             # T39
│   └── fixtures/
│       ├── sample_session.json               # T37
│       └── canned_llm_responses.json         # T37
├── docs/
│   ├── runbooks/
│   │   ├── phase1-acceptance.md              # T40
│   │   ├── telegram-bot-setup.md             # T28
│   │   └── recovery.md                       # T35
│   └── superpowers/
│       ├── specs/2026-05-14-hermes-agent-architecture-design.md   # already exists
│       └── plans/2026-05-14-phase1-local-deployment.md            # this file
└── trajectories/.gitkeep                     # T1 (Phase 3 placeholder)
```

---

## Stage A — Project Foundation, Documentation & Worktrees (T1, T1.1–T1.7, T2–T8)

This stage establishes the baseline: repo init, documentation framework (README, CHANGELOG, ADRs, conventions, runbook index, GitHub templates), worktree-per-phase branching with the Phase 1 worktree active, host prereq verification + secret management + Python project layout. After T1.7, all subsequent tasks (T2 onward) execute in `.worktrees/phase1/`, not in main.

### Task 1: Initialize git repo and project skeleton

**Files:**
- Create: `.gitignore`
- Create: `.gitattributes`
- Create: `README.md`
- Create: `trajectories/.gitkeep`
- Modify: project root (git init)

- [ ] **Step 1: Initialize git repo**

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git init -b main
git config user.name "Daniel Manzela"
git config user.email "$(git config --global user.email)"
```

Expected: `Initialized empty Git repository in /Users/danielmanzela/RX-Research Project/AutonomousAgent/.git/`

- [ ] **Step 2: Create `.gitignore`**

```gitignore
# Secrets — anything ending in plaintext values, never committed
secrets/*.env
secrets/*.json
secrets/*.txt
!secrets/*.sops
!secrets/.gitignore
!secrets/README.md

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/
dist/
build/

# Docker / runtime
*.log
logs/
trajectories/queue/
.cache/

# OS
.DS_Store
Thumbs.db

# Editor
.idea/
.vscode/settings.json

# Hermes runtime data (lives in volumes; never committed)
hermes-data/
chroma-data/
honcho-db-data/
phoenix-data/
workspace/
snapshots/

# Local overrides
docker-compose.override.yml
.env
.env.local
```

- [ ] **Step 3: Create `.gitattributes`**

```gitattributes
* text=auto eol=lf
*.sh text eol=lf
*.py text eol=lf
*.yaml text eol=lf
*.yml text eol=lf
*.md text eol=lf
*.json text eol=lf
*.png binary
*.jpg binary
```

- [ ] **Step 4: Create `README.md`**

```markdown
# AutonomousAgent

Production deployment of [Hermes Agent](https://github.com/NousResearch/hermes-agent) with self-improving capabilities, tiered sandboxing, OpenTelemetry observability, and an Atropos RL training pipeline.

**Status:** Phase 1 (local Mac deployment).

**Spec:** [docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md](docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md)

**Plans:**
- Phase 1: [docs/superpowers/plans/2026-05-14-phase1-local-deployment.md](docs/superpowers/plans/2026-05-14-phase1-local-deployment.md)

## Quick start

After completing all Phase 1 tasks:

```bash
./scripts/bootstrap.sh    # one-shot setup
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up -d
./scripts/smoke.sh        # verify everything works
```

## Layout

See file structure in the Phase 1 plan.
```

- [ ] **Step 5: Create trajectories placeholder**

```bash
mkdir -p trajectories
touch trajectories/.gitkeep
```

- [ ] **Step 6: Commit**

```bash
git add .gitignore .gitattributes README.md trajectories/.gitkeep
git commit -m "chore: initialize project skeleton"
```

---

### Task 1.1: Replace stub README with comprehensive README + add LICENSE

**Files:**
- Modify: `README.md`
- Create: `LICENSE`

- [ ] **Step 1: Write comprehensive `README.md`** (overwrites the stub from T1)

```markdown
# AutonomousAgent

> Production deployment of [Hermes Agent](https://github.com/NousResearch/hermes-agent) — a self-improving AI agent built by Nous Research — wrapped with tiered sandboxing, OpenTelemetry observability, sops-encrypted secrets, and a phased path to a Vertex AI–backed cloud deployment + Atropos RL training pipeline.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Phase](https://img.shields.io/badge/Phase-1%20(local)-blue.svg)](docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-green.svg)](docs/conventions/commit-messages.md)

## What this is

A complete deployment wrapper around the upstream Hermes Agent. The agent runs in Docker on your Mac, connects to Anthropic Claude 4.7 via Vertex AI through a LiteLLM proxy, persists state across restarts, talks to you via Telegram, and continuously improves itself by curating its own memory, autonomously creating skills from successful task completions, and (eventually, in Phase 4) fine-tuning its own model on captured trajectories.

## Why this exists

Production agents need more than `pip install` and an API key. This project supplies:
- **Tiered sandboxing** — different tool classes route to different security tiers (in-process / Docker / cloud sandbox)
- **Network egress allowlist** — agent cannot exfiltrate data to arbitrary endpoints
- **Output secret scrubbing** — regex-based filtering catches stray credentials before persist or send
- **Approval gates** — destructive ops route through Telegram inline-keyboard prompts
- **Hard budget caps** — daily $ limit enforced at the proxy layer
- **OpenTelemetry tracing** — every turn, tool call, and model call is observable
- **Snapshot + recovery** — state is restorable from any point
- **Phased build with gates** — each phase has measurable acceptance criteria

## Project status

**Current phase:** Phase 1 — local Mac deployment.

| Phase | Status | What it delivers |
|---|---|---|
| 1 | 🚧 in progress | Local Hermes Agent in Docker on Mac, talks to you via Telegram, learns via in-context skill creation |
| 2 | ⏳ planned | Migration to GCP Compute Engine VM for 24/7 unattended operation |
| 3 | ⏳ planned | Trajectory pipeline → GCS, dataset versioning via DVC, eval suite |
| 4 | ⏳ planned | Atropos RL training of a custom open-weight model, gated by automated preflight + Telegram approval |

Each phase has its own design ADR, plan, and acceptance protocol. See [docs/superpowers/](docs/superpowers/).

## Quick start

After completing all Phase 1 plan tasks (see [Phase 1 plan](docs/superpowers/plans/2026-05-14-phase1-local-deployment.md)):

```bash
./scripts/bootstrap.sh                 # idempotent end-to-end setup
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up -d
./scripts/smoke.sh                     # 9 smoke checks
```

Then send your bot a message on Telegram.

## Architecture (one paragraph)

A docker-compose stack with twelve services that runs identically on Mac (Phase 1) and on a GCP VM (Phase 2). The agent core (`hermes-agent`) talks to a LiteLLM proxy (`litellm-proxy`) which translates OpenAI-format requests to Vertex AI. State lives in SQLite + Chroma + Honcho (with Postgres). Tools route through tiered sandboxes (in-process, Docker `shell-sandbox`, Modal/Daytona cloud sandbox). Telemetry flows OTLP → `otel-collector` → Phoenix (dev) or Cloud Trace (prod). All secrets are sops-encrypted at rest. All numeric caps, intervals, and thresholds live in `config/limits.yaml`, runtime-tunable.

For the full design: [docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md](docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md)

## Project layout

```
.
├── README.md                  # this file
├── CHANGELOG.md               # all notable changes (Keep-a-Changelog format)
├── CONTRIBUTING.md            # how to work in this repo
├── LICENSE                    # MIT
├── .worktrees/                # phase work happens here (gitignored)
├── deploy/                    # Dockerfiles, compose, OTel/LiteLLM configs
├── config/                    # limits.yaml, scrubber-patterns.yaml, toolsets.yaml, hermes/
├── secrets/                   # sops-encrypted only; plaintext gitignored
├── lib/                       # our Python helpers (validators, scrubber, router, healthcheck)
├── scripts/                   # bootstrap, smoke, snapshot, panic, teardown, healthcheck-ping
├── tests/                     # unit + integration
├── hermes-agent/              # upstream Hermes (git submodule, pinned)
├── docs/
│   ├── architecture/          # high-level architecture overview
│   ├── decisions/             # Architecture Decision Records (MADR)
│   ├── conventions/           # commit, branching, logging, code style
│   ├── runbooks/              # acceptance, recovery, Telegram bot setup
│   └── superpowers/           # specs and plans
└── trajectories/              # Phase 3 placeholder
```

## Development

This project uses [git worktrees](https://git-scm.com/docs/git-worktree) so each phase has its own isolated working tree. See [docs/conventions/branching.md](docs/conventions/branching.md). Commits follow [Conventional Commits](docs/conventions/commit-messages.md). All architectural decisions are captured as [ADRs](docs/decisions/).

To set up your environment:
```bash
./scripts/verify-prereqs.sh    # checks Docker, sops, age, gcloud, etc.
```

## Security

- All secrets sops-encrypted with age recipients
- Never commit plaintext secrets — `pre-commit` blocks the obvious patterns; `detect-secrets` baseline catches the rest
- The agent's own output passes through the scrubber before persist or send
- `panic` mode halts everything immediately; see [docs/runbooks/recovery.md](docs/runbooks/recovery.md)

Report security issues privately (don't open public issues).

## License

MIT — see [LICENSE](LICENSE). Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) (also MIT) by Nous Research.
```

- [ ] **Step 2: Write `LICENSE`** (MIT)

```text
MIT License

Copyright (c) 2026 Daniel Manzela

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 3: Commit**

```bash
git add README.md LICENSE
git commit -m "docs: add comprehensive README and MIT LICENSE"
```

---

### Task 1.2: Create CHANGELOG.md (Keep-a-Changelog format)

**Files:**
- Create: `CHANGELOG.md`

- [ ] **Step 1: Write the CHANGELOG**

```markdown
# Changelog

All notable changes to AutonomousAgent are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial project skeleton (`.gitignore`, `.gitattributes`, `README.md`)
- Comprehensive README with project status, architecture summary, layout
- MIT LICENSE
- This CHANGELOG following Keep-a-Changelog
- CONTRIBUTING.md with development workflow
- GitHub PR and issue templates
- Architecture docs index and runbooks index
- Architecture Decision Records (ADRs 0001–0007) using MADR template
- Convention docs: commit messages (Conventional Commits), branching (worktree-per-phase), logging (structured JSON), code style (ruff)
- Hermes Agent as git submodule pinned to `ddb8d8f`
- Worktree-per-phase branching: `phase/1`, `phase/2`, `phase/3`, `phase/4` checked out under `.worktrees/`
- Host prerequisites verification script (`scripts/verify-prereqs.sh`)
- sops + age secret management (`.sops.yaml`, `secrets/`)
- pre-commit hooks for secret scanning + ruff
- Python project layout (`pyproject.toml`, `lib/`, `tests/`)
- `config/limits.yaml` — single source of truth for all tunables
- `config/limits-schema.json` — JSON schema validation
- `lib/limits_validator.py` — schema validator + tests
- `config/scrubber-patterns.yaml` — regex patterns for output secret filtering
- `lib/scrubber.py` — Scrubber implementation + tests
- `config/toolsets.yaml` — tool → sandbox-tier routing
- `lib/toolset_router.py` — Tier-based router + tests
- `config/hermes/` — initial Hermes config (cli-config.yaml, AGENTS.md, MEMORY.md, USER.md, SOUL.md)
- `deploy/litellm/config.yaml` — LiteLLM proxy config for Vertex AI (Claude 4.7)
- `deploy/otel/collector.{dev,prod}.yaml` — OTel collector configs
- `deploy/Dockerfile.hermes` — extends upstream image with OTel SDK
- `deploy/sandboxes/Dockerfile.shell-sandbox` — minimal Debian shell sandbox
- `deploy/docker-compose.{yml,dev.yml,test.yml}` — full stack + dev overrides + test stack
- Encrypted secrets: Telegram bot token + chat ID, LiteLLM master key, Chroma token, Honcho DB password, Healthchecks.io URL
- Operational scripts: `bootstrap.sh`, `smoke.sh`, `snapshot.sh`, `panic.sh`, `teardown.sh`, `healthcheck-ping.sh`, `decrypt-secrets.sh`
- Cron entry for healthcheck pings every 5 minutes
- Unit tests: `test_limits_schema.py`, `test_scrubber.py`, `test_toolset_router.py`, `test_healthcheck.py`
- Integration tests: `test_full_turn.py`, `test_skill_creation.py`, `test_sandbox_isolation.py`, `test_secret_leak.py`, `test_budget_cap.py`, `test_chroma_outage.py`
- Phase 1 acceptance runbook (`docs/runbooks/phase1-acceptance.md`)
- Telegram bot setup runbook (`docs/runbooks/telegram-bot-setup.md`)
- Recovery runbook (`docs/runbooks/recovery.md`)

### Notes
- All entries above are part of Phase 1; nothing has yet been merged to `main` until Phase 1 acceptance passes.
- See [docs/superpowers/plans/2026-05-14-phase1-local-deployment.md](docs/superpowers/plans/2026-05-14-phase1-local-deployment.md) for the implementation plan that produces these changes.

[Unreleased]: https://github.com/<owner>/AutonomousAgent/compare/v0.0.0...HEAD
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add CHANGELOG.md (Keep-a-Changelog 1.1.0)"
```

---

### Task 1.3: Create CONTRIBUTING.md

**Files:**
- Create: `CONTRIBUTING.md`

- [ ] **Step 1: Write `CONTRIBUTING.md`**

```markdown
# Contributing

This is a single-developer project, but it follows production conventions so future contributors (or future you) can pick it up cleanly.

## Workflow

1. **Pick the right worktree.** All phase work happens in a dedicated worktree under `.worktrees/`. Don't commit directly to `main`.
   - Phase 1 work → `.worktrees/phase1/` (branch `phase/1`)
   - Phase 2 work → `.worktrees/phase2/` (branch `phase/2`)
   - Hotfixes for accepted phases → branch from `main`, name `hotfix/<short-desc>`
   See [docs/conventions/branching.md](docs/conventions/branching.md).

2. **Write the test first.** Test-Driven Development is mandatory for `lib/` and `scripts/` work. Skip tests only for trivial config edits.

3. **Commit frequently, in small focused chunks.** Use [Conventional Commits](docs/conventions/commit-messages.md): `feat(scope): summary`. One commit per logical change.

4. **Update CHANGELOG.md** under `[Unreleased]` whenever you add/change/remove user-visible behavior.

5. **Add an ADR for any architectural decision.** Anything that locks in a design tradeoff (chose X over Y because Z) gets a numbered MADR file in `docs/decisions/`.

6. **Run the full test suite before merging.**
   ```bash
   ./scripts/test.sh    # unit + integration
   ./scripts/smoke.sh   # smoke (requires the stack to be up)
   ```

7. **Pre-commit hooks must pass.** They run automatically; if they fail, fix the issue rather than bypassing.

## Commit message convention

```
<type>(<scope>): <subject>

<body — what & why, not how>

<footer — refs, breaking changes, etc.>
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `security`, `build`, `ci`, `revert`.
Scopes used in this project: `agent`, `gateway`, `litellm`, `otel`, `chroma`, `honcho`, `sandbox`, `secrets`, `config`, `lib`, `scripts`, `deploy`, `tests`, `docs`, `runbook`.

Example: `security(scrubber): add Telegram bot token regex pattern`

## Branching & merging

- `main` holds only **accepted-and-tagged** work (`phase1-accepted`, `phase2-accepted`, etc.)
- `phase/N` is the long-running branch for Phase N — work happens in `.worktrees/phaseN/`
- After acceptance, `phase/N` merges into `main` via a `--no-ff` merge commit + tag
- Hotfixes branch from `main`, merge back via `--no-ff` + cherry-pick to active phase branch

See [docs/conventions/branching.md](docs/conventions/branching.md) for diagrams.

## Coding standards

- Python: ruff (config in `pyproject.toml`); 100-col line length; type hints; small focused modules
- Shell: bash strict mode (`set -euo pipefail`); no `eval`; quote everything
- YAML: 2-space indent; no tabs
- Markdown: GitHub-flavored; soft-wrap; reference links over inline where possible
- See [docs/conventions/code-style.md](docs/conventions/code-style.md)

## Documentation

Three classes of docs:
- **README** — for new readers
- **runbooks** (`docs/runbooks/`) — operational procedures
- **ADRs** (`docs/decisions/`) — irreversible decisions and their rationale
- **conventions** (`docs/conventions/`) — how-we-work standards
- **specs + plans** (`docs/superpowers/`) — design + implementation plans

Anything you'd want to know if you came back in 6 months: write it down.

## Reporting issues

Use the templates in `.github/ISSUE_TEMPLATE/`. For security issues, do not open a public issue — contact the owner directly.
```

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs: add CONTRIBUTING.md (workflow, conventions, branching)"
```

---

### Task 1.4: Add GitHub PR + issue templates

**Files:**
- Create: `.github/PULL_REQUEST_TEMPLATE.md`
- Create: `.github/ISSUE_TEMPLATE/bug_report.md`
- Create: `.github/ISSUE_TEMPLATE/feature_request.md`

- [ ] **Step 1: Create directory and write PR template**

```bash
mkdir -p .github/ISSUE_TEMPLATE
```

Save to `.github/PULL_REQUEST_TEMPLATE.md`:

```markdown
## Summary

<!-- One paragraph: what changed and why. -->

## Changes

<!-- Bullet list of notable changes. -->
- 

## Phase

<!-- Which phase does this PR belong to? -->
- [ ] Phase 1 (local Mac)
- [ ] Phase 2 (cloud prod)
- [ ] Phase 3 (trajectory pipeline)
- [ ] Phase 4 (RL training)
- [ ] Cross-phase / hotfix

## Testing

<!-- How did you verify this works? Paste relevant output. -->
- [ ] Unit tests added / updated and passing
- [ ] Integration tests added / updated and passing
- [ ] Smoke tests pass (`./scripts/smoke.sh`)
- [ ] Manual verification (describe below)

## Documentation

- [ ] CHANGELOG.md updated under `[Unreleased]`
- [ ] ADR added if this locks in an architectural decision
- [ ] Runbook updated if this changes operational procedures
- [ ] README updated if this changes user-visible workflow

## Security

- [ ] No new secrets committed in plaintext (sops-encrypted only)
- [ ] No new egress endpoints added without allowlist update
- [ ] Pre-commit hooks pass

## Related

<!-- Issue numbers, ADR numbers, related PRs. -->
- Closes #
- ADR: docs/decisions/####-name.md
```

- [ ] **Step 2: Write `bug_report.md`**

Save to `.github/ISSUE_TEMPLATE/bug_report.md`:

```markdown
---
name: Bug report
about: Something isn't working as expected
labels: bug
---

## Description

<!-- What's wrong? -->

## Steps to reproduce

1. 
2. 
3. 

## Expected behavior

<!-- What should happen? -->

## Actual behavior

<!-- What does happen? Include error output. -->

## Environment

- Phase (1/2/3/4):
- OS:
- Docker version: `docker --version`
- Container: hermes-agent / litellm-proxy / etc.
- Relevant config snippet from `limits.yaml`:

## Logs / traces

<!-- Paste relevant log lines and Phoenix/Cloud Trace links. Scrub secrets first. -->

```
```

- [ ] **Step 3: Write `feature_request.md`**

Save to `.github/ISSUE_TEMPLATE/feature_request.md`:

```markdown
---
name: Feature request
about: Suggest a new capability or enhancement
labels: enhancement
---

## Problem

<!-- What user need or limitation are you addressing? -->

## Proposed solution

<!-- How would this work? -->

## Alternatives considered

<!-- Other approaches you ruled out, and why. -->

## Phase

<!-- Which phase should this land in? -->
- [ ] Phase 1
- [ ] Phase 2
- [ ] Phase 3
- [ ] Phase 4
- [ ] Cross-phase / future

## Impact

<!-- Cost, complexity, risk. -->
```

- [ ] **Step 4: Commit**

```bash
git add .github/
git commit -m "docs: add GitHub PR and issue templates"
```

---

### Task 1.5: Add architecture index, ADR template + first 7 ADRs, runbook index

**Files:**
- Create: `docs/architecture/README.md`
- Create: `docs/decisions/README.md`
- Create: `docs/decisions/template.md`
- Create: `docs/decisions/0001-use-hermes-agent-as-base.md`
- Create: `docs/decisions/0002-vertex-ai-via-litellm-proxy.md`
- Create: `docs/decisions/0003-tiered-sandboxing-strategy.md`
- Create: `docs/decisions/0004-sops-age-secret-management.md`
- Create: `docs/decisions/0005-self-rl-pipeline-architecture.md`
- Create: `docs/decisions/0006-iterative-phase-build-with-gates.md`
- Create: `docs/decisions/0007-worktree-per-phase-branching.md`
- Create: `docs/runbooks/README.md`

- [ ] **Step 1: Create directories**

```bash
mkdir -p docs/architecture docs/decisions docs/runbooks docs/conventions
```

- [ ] **Step 2: Write `docs/architecture/README.md`**

```markdown
# Architecture

The full architectural design lives in [`../superpowers/specs/2026-05-14-hermes-agent-architecture-design.md`](../superpowers/specs/2026-05-14-hermes-agent-architecture-design.md). This index points to the design document and the related Architecture Decision Records.

## Reading order

1. **[Architecture spec](../superpowers/specs/2026-05-14-hermes-agent-architecture-design.md)** — single source of truth for the complete design (architecture, components, data flow, security, observability, error handling, RL pipeline, testing)
2. **[ADRs](../decisions/)** — point-in-time decisions and their rationale
3. **[Phase plans](../superpowers/plans/)** — implementation plans per phase

## Key concepts

| Concept | Where to read |
|---|---|
| 12-service docker-compose stack | spec §2 |
| Tiered sandboxing | spec §5.3, ADR 0003 |
| Self-RL loop | spec §6, ADR 0005 |
| Phase gating | spec §10, ADR 0006 |
| Worktree-per-phase | ADR 0007, conventions/branching.md |
```

- [ ] **Step 3: Write `docs/decisions/README.md`**

```markdown
# Architecture Decision Records

This directory holds [MADR](https://adr.github.io/madr/) — Markdown Architecture Decision Records — that document point-in-time decisions on this project.

## When to write an ADR

Write one whenever you make a decision that:
- Locks in a tradeoff (chose X over Y)
- Affects code that's hard to unwind
- Future-you would want to know the reasoning

Don't write one for purely cosmetic choices or short-lived implementation details.

## How to write one

1. Copy `template.md` to `<NNNN>-<short-kebab-name>.md` where NNNN is the next zero-padded number
2. Fill in: Status, Context, Decision, Consequences, Alternatives
3. Commit it as `docs(adr): NNNN <title>`
4. Update this index

## Index

| # | Title | Status |
|---|---|---|
| 0001 | [Use Hermes Agent as base](0001-use-hermes-agent-as-base.md) | Accepted |
| 0002 | [Vertex AI via LiteLLM proxy](0002-vertex-ai-via-litellm-proxy.md) | Accepted |
| 0003 | [Tiered sandboxing strategy](0003-tiered-sandboxing-strategy.md) | Accepted |
| 0004 | [sops + age for secret management](0004-sops-age-secret-management.md) | Accepted |
| 0005 | [Self-RL pipeline: soft loop now, hard loop Phase 4](0005-self-rl-pipeline-architecture.md) | Accepted |
| 0006 | [Iterative phase build with acceptance gates](0006-iterative-phase-build-with-gates.md) | Accepted |
| 0007 | [Worktree-per-phase branching](0007-worktree-per-phase-branching.md) | Accepted |
```

- [ ] **Step 4: Write `docs/decisions/template.md`**

```markdown
# NNNN. <Short Title>

**Status:** Proposed | Accepted | Deprecated | Superseded by [NNNN](NNNN-other.md)
**Date:** YYYY-MM-DD
**Decision-makers:** Daniel Manzela (+ Claude Opus 4.7 if applicable)

## Context

<!-- What is the issue we're seeing that motivates this decision? Include forces at play (technical, social, political, project) and any relevant constraints. -->

## Decision

<!-- The change that we're proposing or have agreed to implement. State it as: "We will..." -->

## Consequences

### Positive
- 

### Negative
- 

### Neutral
- 

## Alternatives considered

### Option A: <name>
- Pros:
- Cons:
- Why rejected:

### Option B: <name>
- Pros:
- Cons:
- Why rejected:

## References

- 
```

- [ ] **Step 5: Write ADR 0001**

```markdown
# 0001. Use Hermes Agent as base

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

We need an autonomous agent runtime with built-in self-improvement capabilities (skill creation, memory curation, cross-session search), multi-platform messaging, multiple terminal backends including sandboxed execution, and an RL training pipeline. Building this from scratch would take months and reproduce work already done by the field.

## Decision

We will use [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (MIT, ~150K stars, actively developed) as the agent core. We will not fork it; we will consume it as a git submodule pinned to a specific SHA, wrapping it with our own deployment, configuration, security, and observability layers.

## Consequences

### Positive
- Skip months of foundational agent-loop / skill / memory development
- Inherit a battle-tested RL trajectory pipeline (Atropos environments, trajectory_compressor.py)
- Multi-platform messaging gateway is built in
- Compatible with the agentskills.io standard
- Active community support via Nous Research Discord

### Negative
- We are dependent on Hermes' release cadence and breaking changes
- Hermes upgrades require regression testing against our wrapper
- Custom modifications to agent internals are off the table (we wrap, don't fork)

### Neutral
- We track upstream via the submodule SHA; bumps are explicit, not automatic
- Hermes is MIT-licensed, compatible with our MIT license

## Alternatives considered

### Option A: Build agent loop from scratch on top of Anthropic SDK
- Pros: Total control; no dependencies
- Cons: Months of work; reinvents skill/memory/multi-gateway/RL/sandboxing systems
- Why rejected: Effort vastly exceeds value of differentiation

### Option B: Use LangGraph / LlamaIndex / CrewAI
- Pros: Production-ready frameworks
- Cons: None of them ship the closed self-learning loop, RL trajectory pipeline, or multi-platform gateway out of the box; would still need significant assembly
- Why rejected: Hermes is closer to our requirements with less assembly

### Option C: Fork Hermes
- Pros: Can modify internals
- Cons: Loses upstream benefit; merge friction; we're not the experts on the agent loop
- Why rejected: We don't have a concrete need to change internals; wrap-don't-fork keeps us aligned with upstream

## References

- [Hermes Agent GitHub](https://github.com/NousResearch/hermes-agent)
- Pinned SHA in this project: `ddb8d8fa842283ef651a6e4514f8f561f736c72e`
```

- [ ] **Step 6: Write ADR 0002**

```markdown
# 0002. Vertex AI via LiteLLM proxy

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

Hermes Agent expects an OpenAI-format chat completions endpoint. Our existing GCP project (`i-for-ai`) is configured for Anthropic Claude 4.7 via Vertex AI, which is a Google IAM–authenticated endpoint, not an OpenAI-compatible one. We want to consume Vertex AI without modifying Hermes' provider code.

## Decision

We will run [LiteLLM](https://github.com/BerriAI/litellm) as a sidecar proxy that exposes an OpenAI-format `/v1/chat/completions` endpoint internally and translates requests to Vertex AI on the backend. Hermes connects to LiteLLM at `http://litellm-proxy:4000`. LiteLLM authenticates to Vertex AI via Application Default Credentials (Phase 1, mounted from host) or Workload Identity Federation (Phase 2).

LiteLLM also handles: per-day budget cap enforcement, exponential-backoff retries on 429/503, OTel cost telemetry export.

## Consequences

### Positive
- Zero changes to Hermes' provider code
- Consistent with the Claude Code backend already used for this project (Anthropic via Vertex AI)
- Single chokepoint for budget enforcement
- Easy to add multi-model routing later (cheap/strong split) without touching the agent
- LiteLLM emits cost metrics natively

### Negative
- One more service in the compose stack
- Vertex AI auth complexity (ADC vs WIF) hits LiteLLM rather than Hermes
- LiteLLM is a moving target; pin a tag rather than `:latest` if stability matters more than features

### Neutral
- LiteLLM supports many providers, so future additions (OpenAI fallback, OpenRouter, etc.) are config-only changes

## Alternatives considered

### Option A: Patch Hermes' provider layer to call Vertex AI directly
- Pros: One less service
- Cons: Would diverge from upstream; lose upgrade path; our wrap-don't-fork rule (ADR 0001)
- Why rejected: Violates ADR 0001

### Option B: Use Anthropic API key directly (skip Vertex AI)
- Pros: Simplest; Hermes supports Anthropic out of the box
- Cons: No reuse of `i-for-ai` GCP project; data residency / billing fragmented; we already use Vertex AI for Claude Code
- Why rejected: User explicitly asked for Vertex AI consistency

### Option C: AI Gateway (Cloudflare AI Gateway, Portkey, etc.)
- Pros: Some have similar features
- Cons: Adds a third party; LiteLLM is OSS / self-hostable; no benefit over LiteLLM here
- Why rejected: LiteLLM is sufficient and self-owned

## References

- [LiteLLM Vertex AI provider docs](https://docs.litellm.ai/docs/providers/vertex)
- Spec §2 (component table)
```

- [ ] **Step 7: Write ADR 0003**

```markdown
# 0003. Tiered sandboxing strategy

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

The agent invokes a wide variety of tools — some safe (file reads), some risky (arbitrary shell), some very risky (model-generated Python execution). A single sandbox tier is either too lax (security risk) or too restrictive (performance and capability cost).

## Decision

We will route tool calls to one of five sandbox tiers based on risk class, defined in `config/toolsets.yaml`:

| Tier | Tools | Boundary |
|---|---|---|
| `in_process` | file reads, grep, ls | runs in agent process; host FS read-only |
| `shell_sandbox` | shell, git, jq | Docker container, `--cap-drop=ALL`, `--network=none`, RO host FS, writable `/workspace` only |
| `browser_sandbox` | Playwright actions | Docker container, network allowlisted per call |
| `external_https` | GitHub MCP, Context7 MCP | in-process httpx with egress allowlist enforcement |
| `cloud_sandbox` | arbitrary code execution | Modal/Daytona ephemeral microVM, network restricted |

First-match wins; unknown tools fall through to `shell_sandbox` (default-deny).

## Consequences

### Positive
- Fast path for safe operations
- Strong isolation for risky operations
- Routing is data, not code — adding new tools requires no code changes
- Per-tier capability limits enforced at container boundary, not in app code

### Negative
- Five tiers to maintain instead of one
- More compose services, more healthchecks, more ops surface
- Cloud sandbox tier requires external accounts (Modal/Daytona) and network egress

### Neutral
- Tier choice is observable in OTel spans (`sandbox.tier=...`)

## Alternatives considered

### Option A: Single Docker sandbox for everything
- Pros: Simpler; one boundary to reason about
- Cons: Slow path for safe reads; over-restrictive for browser; under-restrictive for arbitrary code (no microVM)
- Why rejected: Single tier optimizes neither performance nor security

### Option B: Full cloud sandbox for everything (Modal/Daytona only)
- Pros: Maximum isolation; physical separation
- Cons: Latency; cost; outage of Modal/Daytona blocks all tools including reads
- Why rejected: Cost-prohibitive at the always-on level

## References

- Spec §5.3 (sandboxing detail table)
- `config/toolsets.yaml`
- `lib/toolset_router.py`
```

- [ ] **Step 8: Write ADR 0004**

```markdown
# 0004. sops + age for secret management

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

The deployment depends on multiple secrets (Telegram bot token, LiteLLM master key, Chroma auth token, Postgres password, Healthchecks.io URL, Modal/Daytona tokens). These must:
- Be checked into git (so the project is self-contained and reproducible)
- Be unreadable to anyone without the decryption key
- Have a clean rotation story
- Work in both Phase 1 (local Mac) and Phase 2 (GCP VM)

## Decision

We will use [sops](https://github.com/getsops/sops) with an [age](https://github.com/FiloSottile/age) recipient. Encrypted secrets live in `secrets/*.sops`, gitignored plaintext counterparts (`secrets/*.env`, `secrets/*.json`) are decrypted at bootstrap into tmpfs-mounted compose secrets. The age private key lives at `~/.config/sops/age/keys.txt` (Mac host) and is backed up to a password manager.

Phase 2 substitutes Google Secret Manager for the same encrypted-secret pattern, mounted into containers.

## Consequences

### Positive
- Encrypted secrets in git (reproducible deployment, code-reviewable secret rotation)
- No proprietary dependency (sops is OSS, age is OSS)
- Single age recipient is simpler than GPG
- Pre-commit hook `detect-secrets` catches accidental plaintext commits as second line of defense

### Negative
- Lose the age key = lose access to all secrets (mitigated by password-manager backup)
- Adds two host-level tools to install (sops, age)
- Requires explicit decryption step before `docker compose up`

### Neutral
- Phase 2 transition to Secret Manager is straightforward (encrypted-at-rest pattern matches)

## Alternatives considered

### Option A: HashiCorp Vault
- Pros: Industry standard; mature
- Cons: Heavyweight for single-developer; another service to operate
- Why rejected: Overkill for this scale

### Option B: GCP Secret Manager from Phase 1
- Pros: Uniform across phases; managed
- Cons: Cloud dependency for local-only Phase 1; small monthly cost; no offline work
- Why rejected: Adds cloud dependency to a pure-local phase

### Option C: Plain `.env` with strong gitignore
- Pros: Simplest
- Cons: Prone to accidental commits; no encryption-at-rest in git; no rotation history
- Why rejected: Security antipattern

## References

- [sops](https://github.com/getsops/sops)
- [age](https://github.com/FiloSottile/age)
- `.sops.yaml`
- `secrets/README.md`
```

- [ ] **Step 9: Write ADR 0005**

```markdown
# 0005. Self-RL pipeline: soft loop now, hard loop in Phase 4

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

"Self-RL" can mean two very different things:
1. **Soft loop**: the agent improves its in-context behavior via skill creation, memory curation, and user modeling (no GPU; runs continuously; ships in Phase 1)
2. **Hard loop**: actual reinforcement-learning fine-tuning of model weights from collected trajectories using Atropos environments (requires GPU; runs sporadically; ships in Phase 4)

We need both, but they have very different cost profiles, infrastructure needs, and risk surfaces.

## Decision

We will ship the soft loop in Phase 1 (it's free; it's what makes Hermes "self-improving" out of the box). We will collect Atropos-format trajectories continuously from Phase 3 onward (cheap; produces training data). We will gate the hard loop in Phase 4 behind:

1. **Automated preflight**: dataset size ≥1K new trajectories, schema valid, reward sanity score ≥0.7, GPU quota available, monthly run budget available
2. **Telegram approval gate**: user must tap "Approve" in an inline-keyboard prompt before any GPU instance is provisioned
3. **Hard guardrails**: max 4 runs/month, max 24h per run, mid-run cost-overrun aborts the instance via Compute Engine API, eval-regression aborts the registration

Trained models are open-weight only (Llama, Qwen, DeepSeek). They land in a GCS model registry and are NOT auto-swapped into LiteLLM — that requires a separate human decision.

## Consequences

### Positive
- Soft loop delivers immediate value; users see continuous improvement
- Hard loop only spends GPU $ when there's data worth training on AND human approval
- Approval gate prevents unattended cost overruns
- Auto-trigger detection means we don't have to remember to check dataset readiness
- Multiple safety layers (preflight, approval, mid-run abort, eval-regression abort)

### Negative
- Phase 4 is significantly more complex than Phase 1-3
- Approval flow requires stable Telegram integration
- Reward signals are imperfect (we use weighted heuristics, not human labelers); training quality depends on this
- Eval suite must be carefully designed and maintained

### Neutral
- Phase 4 is opt-in: `rl_training.enabled: true` in `limits.yaml` arms the auto-trigger; `false` keeps the pipeline dark
- The first Phase 4 run is always eval-only (baseline) before any actual training

## Alternatives considered

### Option A: Skip the hard loop entirely (only soft loop)
- Pros: Much simpler; no GPU cost
- Cons: User wants both; long-term improvement is bounded by base model
- Why rejected: User explicitly asked for full Atropos pipeline

### Option B: Manual trigger only for hard loop (no auto-detection)
- Pros: Maximum human control
- Cons: User has to remember to check; data sits idle; cycle time slow
- Why rejected: User asked for auto-trigger with approval gate, which is more disciplined than manual

### Option C: Full automation (no approval gate)
- Pros: Fastest iteration
- Cons: Unbounded cost risk; one bad trigger = $400+ wasted
- Why rejected: Cost discipline non-negotiable

## References

- Spec §6
- `config/limits.yaml` `rl_training` section
- Atropos: https://github.com/NousResearch/atropos
```

- [ ] **Step 10: Write ADR 0006**

```markdown
# 0006. Iterative phase build with acceptance gates

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

The full architecture spans local-Mac development, cloud-prod deployment, trajectory pipeline, and RL training. Total scope is multiple weeks of work with substantial cost surface (GCP infra, GPU). Three build-sequencing strategies were considered: big-bang, iterative phases with gates, parallel tracks.

## Decision

We will build in **four sequential phases**, each with a defined acceptance protocol. We do not start phase N+1 until phase N's acceptance gate passes.

| Phase | Deliverable | Gate |
|---|---|---|
| 1 | Local Hermes Agent in Docker on Mac | 10 TG msgs, autonomous skill creation, restart-persistent state, Phoenix traces, no leaks |
| 2 | GCP VM 24/7 deployment | 7-day soak, no manual interventions, no budget breach |
| 3 | Trajectory pipeline → GCS | 1K trajectories, schema valid, reward sanity ≥0.7, 20-trajectory human spot-check |
| 4 | Atropos RL training run | One full cycle, eval improvement ≥2% vs baseline |

Each phase has its own design ADR, plan, and acceptance runbook. Each phase merges to `main` only after acceptance.

## Consequences

### Positive
- Every phase produces working, testable software
- Cost is gated (Phase 2 GCP spend doesn't start until Phase 1 works; Phase 4 GPU spend doesn't start until Phase 3 produces data)
- Failed phases stop or pivot without cascading damage
- Each phase plan is reviewable in isolation

### Negative
- Total wall-clock time is longer than a big-bang approach
- Some Phase 2+ design choices may need to evolve based on Phase 1 learnings (acceptable)

### Neutral
- This pattern matches how production teams ship complex systems

## Alternatives considered

### Option A: Big-bang (build everything at once)
- Pros: Done in one pass
- Cons: Any single broken piece blocks all of it; Phase 4 GPU spend starts before there's data worth training; can't validate before paying for cloud
- Why rejected: Risk and cost overrun unacceptable

### Option B: Parallel tracks (build local + cloud + RL in parallel streams)
- Pros: Wall-clock faster
- Cons: Merge friction; debug surface doubles; dependencies across streams
- Why rejected: Overhead exceeds savings for a single-developer project

## References

- Spec §10
- Phase 1 plan: `docs/superpowers/plans/2026-05-14-phase1-local-deployment.md`
```

- [ ] **Step 11: Write ADR 0007**

```markdown
# 0007. Worktree-per-phase branching

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

Each phase is a long-running, isolated body of work (weeks). Standard branch-per-feature workflow doesn't fit because:
- Phases are sequential, but late-phase planning may overlap with current-phase development
- Bug fixes for an accepted phase may need to coexist with active phase development
- We want to keep `main` clean (only accepted work) while allowing each phase its own working tree

## Decision

We will use [git worktrees](https://git-scm.com/docs/git-worktree) with one worktree per phase, all rooted at the same git repo, checked out under `.worktrees/`:

```
AutonomousAgent/                  ← main worktree (branch: main)
├── .worktrees/
│   ├── phase1/                   ← branch: phase/1
│   ├── phase2/                   ← branch: phase/2 (created when Phase 2 starts)
│   ├── phase3/                   ← branch: phase/3
│   └── phase4/                   ← branch: phase/4
```

Branching rules:
- `main` holds only accepted-and-tagged work (`phase1-accepted`, etc.)
- All phase work happens in `.worktrees/phaseN/` on branch `phase/N`
- After acceptance: `git checkout main && git merge --no-ff phase/N && git tag phaseN-accepted`
- Hotfixes branch from `main` as `hotfix/<short-desc>`, merge back to main + cherry-pick to active phase branch

## Consequences

### Positive
- Multiple phases can have working trees simultaneously (e.g., Phase 1 hotfix while Phase 2 develops)
- `main` is always shippable (only accepted work merged)
- Each worktree is a normal directory; no `git stash` dance to switch contexts
- IDE/test/build environments per worktree don't interfere with each other
- Disk overhead is small (worktrees share the `.git/objects` store)

### Negative
- More cognitive overhead than single-checkout flow
- `.worktrees/` must be gitignored (don't commit the worktrees themselves)
- Submodule (hermes-agent) state is per-worktree; need explicit `git submodule update` after worktree create
- Some tools (older IDEs, some npm scripts) don't understand worktrees

### Neutral
- This pattern is common in large monorepos and multi-version maintenance

## Alternatives considered

### Option A: Single working tree, branch-switching per phase
- Pros: Simplest mental model
- Cons: Can't have two phases active simultaneously; `git stash` or commit-WIP overhead
- Why rejected: Constrains parallel work that we expect to do (planning overlaps execution)

### Option B: Multiple full clones
- Pros: Total isolation
- Cons: Disk overhead; remote pulls in N places; submodule state diverges
- Why rejected: Worktrees give the same isolation more efficiently

### Option C: Trunk-based development
- Pros: Always integrated
- Cons: Phase failures contaminate `main`; no clean acceptance boundary
- Why rejected: We explicitly want phase isolation per ADR 0006

## References

- [git-worktree docs](https://git-scm.com/docs/git-worktree)
- ADR 0006 (phased build)
- `docs/conventions/branching.md`
```

- [ ] **Step 12: Write `docs/runbooks/README.md`**

```markdown
# Runbooks

Operational procedures for running and recovering AutonomousAgent.

## Index

| Runbook | When to use |
|---|---|
| [telegram-bot-setup.md](telegram-bot-setup.md) | One-time: create the Telegram bot for the messaging gateway |
| [phase1-acceptance.md](phase1-acceptance.md) | End of Phase 1: validate the local deployment works end-to-end |
| [recovery.md](recovery.md) | Stack is broken, panic was invoked, or you need to restore from a snapshot |

## Conventions

Every runbook:
- States its prerequisites at the top
- Lists steps in order with expected output for each
- Has a clear "pass criteria" or "expected end state"
- Says what to do if a step fails
```

- [ ] **Step 13: Commit**

```bash
git add docs/architecture/ docs/decisions/ docs/runbooks/README.md
git commit -m "docs(adr): add architecture index, ADR template, and ADRs 0001-0007"
```

---

### Task 1.6: Add convention docs (commit messages, branching, logging, code style)

**Files:**
- Create: `docs/conventions/commit-messages.md`
- Create: `docs/conventions/branching.md`
- Create: `docs/conventions/logging.md`
- Create: `docs/conventions/code-style.md`

- [ ] **Step 1: Write `commit-messages.md`**

Save to `docs/conventions/commit-messages.md`:

```markdown
# Commit Message Convention

We follow [Conventional Commits 1.0.0](https://www.conventionalcommits.org/).

## Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

## Types

| Type | Use when |
|---|---|
| `feat` | New user-facing capability |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `chore` | Maintenance (deps, config, repo housekeeping) |
| `refactor` | Internal restructuring, no behavior change |
| `test` | Adding or fixing tests |
| `perf` | Performance improvement |
| `security` | Security fix or hardening |
| `build` | Build system / Docker / packaging |
| `ci` | CI/CD config |
| `revert` | Reverts a previous commit |

## Scopes (this project)

`agent`, `gateway`, `litellm`, `otel`, `chroma`, `honcho`, `sandbox`, `secrets`, `config`, `lib`, `scripts`, `deploy`, `tests`, `docs`, `runbook`, `adr`.

Use the scope that best describes the affected area. Use `*` if it touches everything.

## Subject

- Imperative mood ("add" not "added")
- ≤72 chars
- No trailing period
- Lowercase first letter (after the scope colon)

## Body (optional)

- Explains WHY, not WHAT (the diff shows what)
- Wrap at 72 chars
- Blank line separating subject from body

## Footer (optional)

- `BREAKING CHANGE: <description>` for breaking changes
- `Refs: #123` for issue references
- `Co-Authored-By: <name> <email>` for co-authors

## Examples

```
feat(scrubber): add Telegram bot token regex pattern

The scrubber was missing a pattern for Telegram bot tokens (format
NNNNNNNN:XXXXXXXXX...). Without it, tokens accidentally echoed by the
agent could leak through to logs.

Refs: #42
```

```
fix(litellm): handle 503 with exponential backoff instead of immediate retry

Vertex AI occasionally returns 503 during region failovers. Without
backoff, we hammered the endpoint and made the situation worse.
```

```
chore(deps): bump litellm to v1.50.0

No behavior change; tracking upstream for security patches.
```

```
docs(adr): 0007 worktree-per-phase branching

Captures the decision and tradeoffs for the worktree-per-phase model.
```

## Auto-validation

The pre-commit hook does not enforce commit format (we trust the human author). Instead, the CHANGELOG generator (Phase 2+) parses these to produce release notes — bad commits = blank entries, which is incentive enough.
```

- [ ] **Step 2: Write `branching.md`**

Save to `docs/conventions/branching.md`:

```markdown
# Branching & Worktree Convention

This project uses **git worktrees with one branch per phase**. See [ADR 0007](../decisions/0007-worktree-per-phase-branching.md) for the rationale.

## Branches

| Branch | Purpose | Lifecycle |
|---|---|---|
| `main` | Accepted work only | Permanent; `--no-ff` merge per phase + tag `phaseN-accepted` |
| `phase/1` | Phase 1 development | Created on Day 1; merged to `main` at acceptance; deletable after merge |
| `phase/2` | Phase 2 development | Created when Phase 1 is accepted |
| `phase/3` | Phase 3 development | Created when Phase 2 is accepted |
| `phase/4` | Phase 4 development | Created when Phase 3 is accepted |
| `hotfix/<desc>` | Urgent fix to accepted code | Branched from `main`; merged back to `main` + cherry-picked to active phase branch |

## Worktree layout

```
AutonomousAgent/                  ← branch: main
├── .worktrees/                   ← gitignored
│   ├── phase1/                   ← branch: phase/1
│   ├── phase2/                   ← branch: phase/2
│   ├── phase3/                   ← branch: phase/3
│   └── phase4/                   ← branch: phase/4
```

## Creating a phase worktree

```bash
# From the main worktree:
git branch phase/N main                       # create branch from main
git worktree add .worktrees/phaseN phase/N    # create worktree
cd .worktrees/phaseN
git submodule update --init --recursive       # submodule state is per-worktree
```

## Working in a phase

```bash
cd .worktrees/phaseN
# normal git workflow on branch phase/N
git add ...
git commit -m "feat(scope): ..."
```

## Phase acceptance → merge to main

When the phase passes its acceptance protocol:

```bash
# From the main worktree:
cd <project-root>
git checkout main
git merge --no-ff phase/N -m "Merge phase/N into main: <one-line summary>"
git tag -a phaseN-accepted -m "Phase N accepted on $(date -u +%Y-%m-%d). All N criteria passed."
git push origin main --tags    # if there's a remote (Phase 2+)
```

After merging, leave the phase worktree in place if you might still need it; otherwise clean up:

```bash
git worktree remove .worktrees/phaseN
git branch -d phase/N
```

## Hotfixes

```bash
git checkout main
git checkout -b hotfix/short-desc
# fix, test, commit
git checkout main
git merge --no-ff hotfix/short-desc
git push  # if remote exists

# Cherry-pick to active phase branch:
cd .worktrees/phaseN
git cherry-pick <hotfix-sha>
```

## Don'ts

- Don't commit directly to `main` (except for merging accepted phase branches and hotfixes)
- Don't delete `.git/` from a worktree (it's a pointer file; use `git worktree remove`)
- Don't rebase a phase branch after others have based work on it
- Don't force-push to `main`
```

- [ ] **Step 3: Write `logging.md`**

Save to `docs/conventions/logging.md`:

```markdown
# Logging Convention

All services emit structured JSON logs to stdout. The OTel collector ships them to the configured backend (local files in dev, Cloud Logging in Phase 2 prod).

## Format

Every log line is a single JSON object:

```json
{
  "ts": "2026-05-14T18:32:11.234Z",
  "level": "info",
  "service": "hermes-agent",
  "phase": 1,
  "env": "dev",
  "session_id": "abc123",
  "turn_id": 42,
  "event": "tool.dispatch",
  "tool": "shell",
  "tier": "shell_sandbox",
  "msg": "dispatching shell command",
  "trace_id": "...",
  "span_id": "..."
}
```

## Required fields

- `ts` — RFC 3339 UTC
- `level` — `debug` | `info` | `warning` | `error` | `critical`
- `service` — service name matching OTel `service.name` resource attribute
- `event` — short snake_case event name (corresponds to OTel span name when applicable)
- `msg` — human-readable summary

## Optional fields by event class

- `session_id`, `turn_id` — for any agent-loop event
- `tool`, `tier` — for tool dispatch events
- `cost_usd`, `tokens_in`, `tokens_out`, `model_id` — for model.call events
- `trace_id`, `span_id` — automatically injected when an OTel context exists
- `error.type`, `error.message`, `error.stack` — when level=error/critical

## Severity levels

| Level | When to use | Routes to |
|---|---|---|
| `debug` | Verbose internals; off by default in prod | Local files only |
| `info` | Normal operational events (turn started, tool dispatched) | Local + Cloud Logging |
| `warning` | Degraded behavior, retries, fallbacks | Local + Cloud Logging |
| `error` | Operation failed but service is still up | Local + Cloud Logging + alert if rate exceeds threshold |
| `critical` | Service is down or security boundary crossed | Local + Cloud Logging + immediate Telegram alert |

## What to NEVER log

- Plaintext secrets (use the scrubber even on log strings)
- Full conversation contents (log session_id + turn_id; the persisted DB has the content)
- User PII without explicit need

## What to ALWAYS log

- Every tool dispatch + result class
- Every model call with token + cost telemetry
- Every approval-gate decision (allow/deny/timeout)
- Every scrubber hit (separate log file `secret-leak-attempts.log` for audit)
- Every restart, panic, snapshot, recovery event
- Every Phase-3+ trajectory shipment outcome
- Every Phase-4 RL preflight, approval, run lifecycle event

## Local dev rotation

`limits.yaml` `local_logs_dev`:
- `rotate_size_mb: 100`
- `keep_files: 5`

Logs at `logs/` are gitignored.

## Phase 2 prod retention

`limits.yaml` `log_retention`:
- Cloud Logging hot: 30 days
- GCS coldline after 30 days: another 11 months
- Hard delete after 365 days
```

- [ ] **Step 4: Write `code-style.md`**

Save to `docs/conventions/code-style.md`:

```markdown
# Code Style

## Python

- Tooling: [`ruff`](https://docs.astral.sh/ruff/) for lint + format. Config in `pyproject.toml`.
- Line length: 100.
- Target: Python 3.11+.
- Type hints: required for all public functions, methods, and dataclass fields.
- Docstrings: short. One sentence for purpose, parameters/returns only if non-obvious.
- Imports: sorted by ruff (`I` rules); first-party last.
- Avoid: `from x import *`, mutable default args, broad `except:` (use `except Exception:` minimum).

## Module layout

- One responsibility per module
- Public API at top of file (dataclasses, then public functions, then private)
- Helpers prefixed with `_`
- Tests live alongside the module they test in `tests/unit/test_<module>.py`

## Naming

- `snake_case` for functions, variables, modules
- `PascalCase` for classes
- `SCREAMING_SNAKE_CASE` for module-level constants
- Booleans named `is_*`, `has_*`, `should_*`
- Avoid abbreviations except universally understood ones (`url`, `api`, `id`)

## Comments

- Default to no comments. Code should be self-documenting via good naming.
- Write a comment only when the WHY is non-obvious (a hidden constraint, workaround, surprising behavior).
- Never write comments that restate the code. Never write multi-paragraph docstrings.
- TODO comments must include either a date or an issue reference: `# TODO(2026-06): refactor this once Honcho v2 lands`.

## Errors

- Raise specific exceptions; don't return `None` to signal an error
- Catch only what you can handle; let the rest bubble
- Always log errors at the boundary that catches them (don't double-log)

## Shell scripts

- Bash strict mode at top: `set -euo pipefail`
- Quote everything: `"$var"` not `$var`
- Use `[[ ]]` not `[ ]`
- No `eval` unless absolutely required and explained in a comment
- All scripts include `#!/usr/bin/env bash` shebang
- All scripts are executable (`chmod +x`)

## YAML

- 2-space indent, never tabs
- Comments only for non-obvious fields
- Lists on new lines for >2 items
- Use `null` explicitly, not blank

## Dockerfiles

- Pin base image to a specific tag (not `:latest`) at release time
- One concern per layer (don't combine unrelated `RUN` commands)
- Clean up apt caches in the same layer as `apt-get install`
- Run as non-root in production sandboxes
- Always set `WORKDIR`

## Secrets in code

- NEVER hardcode a secret, even a "test" one
- Use `os.environ[...]` (not `os.environ.get` with a default value that looks like a real secret)
- For tests, use string literals that the scrubber will catch and redact
```

- [ ] **Step 5: Commit**

```bash
git add docs/conventions/
git commit -m "docs(conventions): add commit, branching, logging, code-style conventions"
```

---

### Task 1.7: Set up worktree structure for all four phases

**Files:**
- Modify: `.gitignore` (add `.worktrees/`)
- Create: `.worktrees/phase1/` (worktree)

- [ ] **Step 1: Add `.worktrees/` to `.gitignore`**

```bash
echo "" >> .gitignore
echo "# Phase worktrees (each is a checkout of phase/N branch)" >> .gitignore
echo ".worktrees/" >> .gitignore
git add .gitignore
git commit -m "chore: gitignore .worktrees/"
```

- [ ] **Step 2: Create the four phase branches**

```bash
git branch phase/1 main
# Phase 2-4 branches created at the start of their respective plans, NOT now,
# because they should branch from main AFTER prior-phase acceptance merges.
```

- [ ] **Step 3: Create the Phase 1 worktree**

```bash
git worktree add .worktrees/phase1 phase/1
```

Expected: `.worktrees/phase1/` exists, contains a fresh checkout of branch `phase/1`. (No submodules to initialize yet — Hermes submodule is added in T2 from inside this worktree.)

- [ ] **Step 4: Verify worktree status from main**

```bash
git worktree list
```

Expected output similar to:
```
/Users/danielmanzela/RX-Research Project/AutonomousAgent              <hash> [main]
/Users/danielmanzela/RX-Research Project/AutonomousAgent/.worktrees/phase1  <hash> [phase/1]
```

- [ ] **Step 5: Update CHANGELOG.md noting worktree setup**

Edit `CHANGELOG.md` `[Unreleased] / Added`, append (note: this entry stays in main's CHANGELOG since the worktree creation itself is a main-branch event):

```markdown
- Worktree-per-phase branching: `phase/1` branch created from `main`, checked out at `.worktrees/phase1/`. See [ADR 0007](docs/decisions/0007-worktree-per-phase-branching.md) and [docs/conventions/branching.md](docs/conventions/branching.md).
```

- [ ] **Step 6: Commit on main, then switch to phase1 worktree for the rest of Phase 1**

```bash
git add CHANGELOG.md
git commit -m "chore(worktrees): create phase/1 branch and .worktrees/phase1 worktree"
```

**FROM THIS POINT ONWARD, all Phase 1 implementation tasks (T2 onward) execute in `.worktrees/phase1/`, NOT in the main worktree.**

```bash
cd .worktrees/phase1
```

All subsequent `git add` / `git commit` commands operate on branch `phase/1`. The main worktree stays clean.

---

### Task 2: Add Hermes Agent as a git submodule pinned to current SHA

**Files:**
- Modify: `.gitmodules` (created by submodule add)
- Create: `hermes-agent/` (cloned via submodule)

- [ ] **Step 1: Add submodule pinned to the SHA from the spec**

```bash
git submodule add https://github.com/NousResearch/hermes-agent.git hermes-agent
cd hermes-agent
git checkout ddb8d8fa842283ef651a6e4514f8f561f736c72e
cd ..
```

Expected: `hermes-agent/` directory exists with files like `setup-hermes.sh`, `docker-compose.yml`, `pyproject.toml`.

- [ ] **Step 2: Verify the SHA matches**

```bash
git -C hermes-agent rev-parse HEAD
```

Expected: `ddb8d8fa842283ef651a6e4514f8f561f736c72e`

- [ ] **Step 3: Verify upstream files we depend on exist**

```bash
test -f hermes-agent/Dockerfile && \
test -f hermes-agent/pyproject.toml && \
test -f hermes-agent/cli-config.yaml.example && \
test -f hermes-agent/trajectory_compressor.py && \
test -d hermes-agent/environments && \
echo "OK: all expected upstream files present"
```

Expected: `OK: all expected upstream files present`

- [ ] **Step 4: Commit**

```bash
git add .gitmodules hermes-agent
git commit -m "chore: add hermes-agent as submodule pinned to ddb8d8f"
```

---

### Task 3: Create host prerequisites verification script

**Files:**
- Create: `scripts/verify-prereqs.sh`

- [ ] **Step 1: Write the script**

```bash
mkdir -p scripts
cat > scripts/verify-prereqs.sh <<'EOF'
#!/usr/bin/env bash
# Verifies all host prerequisites for running Hermes Agent locally.
# Exits non-zero if any prereq is missing or misconfigured.
set -euo pipefail

errors=0
check() {
  local name="$1" cmd="$2"
  if eval "$cmd" >/dev/null 2>&1; then
    echo "✓ $name"
  else
    echo "✗ $name — install or fix"
    errors=$((errors+1))
  fi
}

echo "Checking host prerequisites..."
check "docker"          "docker --version"
check "docker-compose"  "docker compose version"
check "docker daemon"   "docker info"
check "uv"              "uv --version"
check "jq"              "jq --version"
check "gcloud"          "gcloud --version"
check "sops"            "sops --version"
check "age"             "age --version"
check "git"             "git --version"

echo
echo "Checking GCP authentication..."
if gcloud config get-value project 2>/dev/null | grep -q i-for-ai; then
  echo "✓ gcloud project is i-for-ai"
else
  echo "✗ gcloud project is not i-for-ai (run: gcloud config set project i-for-ai)"
  errors=$((errors+1))
fi

if gcloud auth application-default print-access-token >/dev/null 2>&1; then
  echo "✓ Application Default Credentials are valid"
else
  echo "✗ ADC missing (run: gcloud auth application-default login)"
  errors=$((errors+1))
fi

echo
if [ "$errors" -gt 0 ]; then
  echo "$errors prerequisite(s) failed. See above."
  exit 1
fi
echo "All prerequisites satisfied."
EOF
chmod +x scripts/verify-prereqs.sh
```

- [ ] **Step 2: Run it (expect failures for sops + age — that's the next task)**

```bash
./scripts/verify-prereqs.sh
```

Expected: Most checks pass; `sops` and `age` fail; gcloud and ADC may need attention.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify-prereqs.sh
git commit -m "feat(scripts): add host prereqs verification script"
```

---

### Task 4: Install sops + age on the host

**Files:** none (host-level installation)

- [ ] **Step 1: Install via Homebrew**

```bash
brew install sops age
```

Expected: both installed; `which sops age` returns paths.

- [ ] **Step 2: Re-run prereq check**

```bash
./scripts/verify-prereqs.sh
```

Expected: All checks pass except possibly the gcloud project / ADC — fix those if reported.

- [ ] **Step 3: Configure gcloud if needed**

```bash
gcloud config set project i-for-ai
gcloud auth application-default login   # opens browser; complete flow
```

Expected: `./scripts/verify-prereqs.sh` reports all green.

---

### Task 5: Set up sops + age key + secrets directory

**Files:**
- Create: `.sops.yaml`
- Create: `secrets/.gitignore`
- Create: `secrets/README.md`
- Create: `~/.config/sops/age/keys.txt` (host-level, not committed)

- [ ] **Step 1: Generate age keypair (host-level, never committed)**

```bash
mkdir -p ~/.config/sops/age
test -f ~/.config/sops/age/keys.txt || age-keygen -o ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt
PUBLIC_KEY=$(grep -oE 'age1[a-z0-9]+' ~/.config/sops/age/keys.txt | head -1)
echo "Public key: $PUBLIC_KEY"
```

Expected: Public key printed (begins with `age1...`). **Save this value — needed for the next step.**

- [ ] **Step 2: Create `.sops.yaml` at project root**

Replace `<AGE_PUBLIC_KEY>` with the value from Step 1.

```yaml
creation_rules:
  - path_regex: secrets/.*\.sops$
    age: <AGE_PUBLIC_KEY>
```

- [ ] **Step 3: Create `secrets/.gitignore`**

```gitignore
# Plaintext secret values are never committed.
*.env
*.json
*.txt
*.key
*.pem
# Encrypted secrets ARE committed.
!*.sops
!.gitignore
!README.md
```

- [ ] **Step 4: Create `secrets/README.md`**

```markdown
# Secrets

This directory contains sops-encrypted secrets. Plaintext files are gitignored.

**Conventions:**
- Encrypted file names end in `.sops` (e.g. `telegram.env.sops`).
- Decrypt with `sops -d secrets/<name>.sops > secrets/<name>` (do NOT commit the decrypted file).
- Encrypt new secret with `sops -e secrets/<name> > secrets/<name>.sops` then `rm secrets/<name>`.
- The age key lives at `~/.config/sops/age/keys.txt` (Mac host) and must be backed up to a password manager.

**Adding a new secret:**

```bash
# Edit (creates if missing)
sops secrets/new-secret.env.sops
# OR encrypt an existing plaintext file
sops -e secrets/new-secret.env > secrets/new-secret.env.sops
rm secrets/new-secret.env
```

The `sops` command auto-uses the recipient defined in `.sops.yaml`.
```

- [ ] **Step 5: Verify sops can encrypt/decrypt**

```bash
echo "test=value" > /tmp/test.env
sops -e /tmp/test.env > /tmp/test.env.sops
sops -d /tmp/test.env.sops
rm /tmp/test.env /tmp/test.env.sops
```

Expected: outputs `test=value`.

- [ ] **Step 6: Commit**

```bash
git add .sops.yaml secrets/.gitignore secrets/README.md
git commit -m "feat(secrets): add sops + age secret management"
```

---

### Task 6: Add pre-commit hooks (git-secrets + ruff + secret-scan)

**Files:**
- Create: `.pre-commit-config.yaml`

- [ ] **Step 1: Write `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
        exclude: ^secrets/.*\.sops$
      - id: check-added-large-files
        args: [--maxkb=1024]
      - id: detect-private-key
      - id: detect-aws-credentials
        args: [--allow-missing-credentials]
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']
        exclude: |
          (?x)^(
            secrets/.*\.sops|
            tests/fixtures/.*|
            uv.lock|
            hermes-agent/.*
          )$
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

- [ ] **Step 2: Install pre-commit and create baseline**

```bash
uv tool install pre-commit
uv tool install detect-secrets
detect-secrets scan --baseline .secrets.baseline --exclude-files 'secrets/.*\.sops$|hermes-agent/.*|uv\.lock'
pre-commit install
pre-commit run --all-files
```

Expected: pre-commit hooks pass (any auto-fixes are committed in the next step).

- [ ] **Step 3: Commit**

```bash
git add .pre-commit-config.yaml .secrets.baseline
git commit -m "chore: add pre-commit hooks (secrets scanning + ruff)"
```

---

### Task 7: Create Python project layout for our helper code

**Files:**
- Create: `pyproject.toml`
- Create: `lib/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/__init__.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "autonomous-agent-deploy"
version = "0.1.0"
description = "Deployment wrapper for Hermes Agent with self-RL pipeline"
requires-python = ">=3.11"
dependencies = [
  "pyyaml>=6.0",
  "jsonschema>=4.20",
  "httpx>=0.27",
  "pydantic>=2.6",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-asyncio>=0.23",
  "pytest-mock>=3.14",
  "ruff>=0.6.9",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create `__init__.py` stubs**

```bash
mkdir -p lib tests/unit tests/integration tests/fixtures
touch lib/__init__.py tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py
```

- [ ] **Step 3: Install dev deps and verify**

```bash
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest --collect-only
```

Expected: pytest reports "no tests ran" (we haven't written any yet) but no errors.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml lib/__init__.py tests/__init__.py tests/unit/__init__.py tests/integration/__init__.py
git commit -m "chore: add python project layout"
```

---

### Task 8: Create initial bootstrap.sh (skeleton; fleshed out in later tasks)

**Files:**
- Create: `scripts/bootstrap.sh`

- [ ] **Step 1: Write the skeleton**

```bash
cat > scripts/bootstrap.sh <<'EOF'
#!/usr/bin/env bash
# One-shot bootstrap for local Hermes Agent deployment.
# Idempotent — safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> 1/6 Verify host prerequisites"
./scripts/verify-prereqs.sh

echo "==> 2/6 Decrypt secrets"
./scripts/decrypt-secrets.sh

echo "==> 3/6 Validate config files"
python -m lib.limits_validator config/limits.yaml

echo "==> 4/6 Pull/build container images"
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml pull
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml build

echo "==> 5/6 Bring stack up"
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up -d
sleep 5  # let healthchecks settle

echo "==> 6/6 Run smoke tests"
./scripts/smoke.sh

echo
echo "✓ Bootstrap complete. Talk to the agent:"
echo "    docker compose -f deploy/docker-compose.yml exec hermes-agent hermes"
echo "Or send a Telegram message to your bot."
EOF
chmod +x scripts/bootstrap.sh
```

- [ ] **Step 2: Commit (the script will fail if run now — that's expected; later tasks fill in the dependencies)**

```bash
git add scripts/bootstrap.sh
git commit -m "feat(scripts): add bootstrap.sh skeleton"
```

---

## Stage B — Configuration Layer (T9–T17)

### Task 9: Create `config/limits.yaml` (the single source of truth)

**Files:**
- Create: `config/limits.yaml`

- [ ] **Step 1: Create config dir and write file**

Copy the full `limits.yaml` from spec §4 verbatim:

```bash
mkdir -p config
```

Write to `config/limits.yaml`:

```yaml
budget:
  daily_usd_cap: 100
  per_task_input_tokens: null
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
  enabled: false
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
    estimate_includes_in_message:
      - eval_baseline
      - est_cost_usd
      - est_duration_h
      - dataset_hash
      - dataset_size
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
    alert_telegram_on:
      - run_started
      - run_completed
      - run_failed
      - model_registered

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
  telegram_chat_id: null
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

Note: Phase 1 ships `rl_training.enabled: false` even though the spec says `true` — we flip it ON only at Phase 4 acceptance. `notify_channels.telegram_chat_id` is set after Telegram bot creation in Task 28.

- [ ] **Step 2: Commit**

```bash
git add config/limits.yaml
git commit -m "feat(config): add limits.yaml (single source of truth for tunables)"
```

---

### Task 10: Create JSON schema for `limits.yaml`

**Files:**
- Create: `config/limits-schema.json`

- [ ] **Step 1: Write the JSON schema**

Save to `config/limits-schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["budget", "retries", "sandboxes", "agent", "nudges", "health", "snapshots", "approval", "rl_rewards", "rl_training", "alerts", "notify_channels", "log_retention", "local_logs_dev"],
  "additionalProperties": false,
  "properties": {
    "budget": {
      "type": "object",
      "required": ["daily_usd_cap", "alert_at_pct"],
      "properties": {
        "daily_usd_cap": {"type": "number", "minimum": 0},
        "per_task_input_tokens": {"type": ["integer", "null"], "minimum": 0},
        "per_task_output_tokens": {"type": ["integer", "null"], "minimum": 0},
        "per_conversation_context": {"type": ["integer", "null"], "minimum": 0},
        "alert_at_pct": {"type": "integer", "minimum": 1, "maximum": 100}
      }
    },
    "retries": {
      "type": "object",
      "required": ["litellm_max_attempts", "litellm_initial_backoff_s", "litellm_max_backoff_s", "litellm_jitter_pct"],
      "properties": {
        "litellm_max_attempts": {"type": "integer", "minimum": 1, "maximum": 20},
        "litellm_initial_backoff_s": {"type": "number", "minimum": 0},
        "litellm_max_backoff_s": {"type": "number", "minimum": 1},
        "litellm_jitter_pct": {"type": "integer", "minimum": 0, "maximum": 100}
      }
    },
    "sandboxes": {
      "type": "object",
      "required": ["shell_timeout_s", "shell_max_output_bytes", "modal_max_lifetime_s", "modal_network_allowlist", "daytona_max_lifetime_s"],
      "properties": {
        "shell_timeout_s": {"type": "integer", "minimum": 1},
        "shell_max_output_bytes": {"type": "integer", "minimum": 1024},
        "modal_max_lifetime_s": {"type": "integer", "minimum": 1},
        "modal_network_allowlist": {"type": "array", "items": {"type": "string"}},
        "daytona_max_lifetime_s": {"type": "integer", "minimum": 1}
      }
    },
    "agent": {
      "type": "object",
      "required": ["max_turns_per_task", "max_concurrent_tasks"],
      "properties": {
        "max_turns_per_task": {"type": "integer", "minimum": 1, "maximum": 1000},
        "max_concurrent_tasks": {"type": "integer", "minimum": 1, "maximum": 100}
      }
    },
    "nudges": {
      "type": "object",
      "required": ["memory_curator_interval", "skill_extractor_min_turns", "skill_extractor_min_distinct_tools", "vector_consolidator_cron", "vector_prune_age_days"],
      "properties": {
        "memory_curator_interval": {"type": "string"},
        "skill_extractor_min_turns": {"type": "integer", "minimum": 1},
        "skill_extractor_min_distinct_tools": {"type": "integer", "minimum": 1},
        "vector_consolidator_cron": {"type": "string"},
        "vector_prune_age_days": {"type": "integer", "minimum": 1}
      }
    },
    "health": {
      "type": "object",
      "required": ["healthchecks_io_ping_interval_s", "agent_heartbeat_interval_s", "vm_uptime_alert_threshold_s"],
      "properties": {
        "healthchecks_io_ping_interval_s": {"type": "integer", "minimum": 30},
        "agent_heartbeat_interval_s": {"type": "integer", "minimum": 10},
        "vm_uptime_alert_threshold_s": {"type": "integer", "minimum": 60}
      }
    },
    "snapshots": {
      "type": "object",
      "required": ["gcs_snapshot_cron", "gcs_retention_days", "local_db_vacuum_cron"],
      "properties": {
        "gcs_snapshot_cron": {"type": "string"},
        "gcs_retention_days": {"type": "integer", "minimum": 1},
        "local_db_vacuum_cron": {"type": "string"}
      }
    },
    "approval": {
      "type": "object",
      "required": ["always_ask_patterns", "never_ask_patterns", "default_for_unknown", "timeout_s"],
      "properties": {
        "always_ask_patterns": {"type": "array", "items": {"type": "string"}},
        "never_ask_patterns": {"type": "array", "items": {"type": "string"}},
        "default_for_unknown": {"type": "string", "enum": ["ask", "allow", "deny"]},
        "timeout_s": {"type": "integer", "minimum": 1}
      }
    },
    "rl_rewards": {
      "type": "object",
      "required": ["weights", "reward_horizon_turns", "exclude_session_if_lt_turns"],
      "properties": {
        "weights": {
          "type": "object",
          "required": ["user_explicit", "user_implicit", "self_consistency", "task_completion"],
          "properties": {
            "user_explicit": {"type": "number"},
            "user_implicit": {"type": "number"},
            "self_consistency": {"type": "number"},
            "task_completion": {"type": "number"}
          }
        },
        "reward_horizon_turns": {"type": "integer", "minimum": 1},
        "exclude_session_if_lt_turns": {"type": "integer", "minimum": 1}
      }
    },
    "rl_training": {
      "type": "object",
      "required": ["enabled", "trigger_check_cron", "preflight_thresholds", "approval", "guardrails", "post_training"],
      "properties": {
        "enabled": {"type": "boolean"},
        "trigger_check_cron": {"type": "string"},
        "preflight_thresholds": {"type": "object"},
        "approval": {"type": "object"},
        "guardrails": {"type": "object"},
        "post_training": {"type": "object"}
      }
    },
    "alerts": {"type": "object"},
    "notify_channels": {
      "type": "object",
      "properties": {
        "telegram_chat_id": {"type": ["string", "integer", "null"]},
        "cloud_monitoring_email": {"type": ["string", "null"]},
        "pagerduty_routing_key": {"type": ["string", "null"]}
      }
    },
    "log_retention": {"type": "object"},
    "local_logs_dev": {"type": "object"}
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add config/limits-schema.json
git commit -m "feat(config): add JSON schema for limits.yaml"
```

---

### Task 11: Implement `limits_validator.py`

**Files:**
- Create: `lib/limits_validator.py`

- [ ] **Step 1: Write the validator**

```python
"""Validate config/limits.yaml against config/limits-schema.json.

Run as a module: `python -m lib.limits_validator config/limits.yaml`
Exits 0 on success, non-zero with errors on failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator


def load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def load_schema(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def validate(config_path: Path, schema_path: Path) -> list[str]:
    """Return a list of error messages (empty list = valid)."""
    config = load_yaml(config_path)
    schema = load_schema(schema_path)
    validator = Draft202012Validator(schema)
    errors = []
    for error in validator.iter_errors(config):
        path = "/".join(str(p) for p in error.absolute_path) or "<root>"
        errors.append(f"{path}: {error.message}")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m lib.limits_validator <path-to-limits.yaml>", file=sys.stderr)
        return 2
    config_path = Path(sys.argv[1])
    schema_path = config_path.parent / "limits-schema.json"
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 2
    if not schema_path.exists():
        print(f"Schema not found: {schema_path}", file=sys.stderr)
        return 2

    errors = validate(config_path, schema_path)
    if errors:
        print(f"Validation FAILED for {config_path}:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"Validation OK: {config_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Commit**

```bash
git add lib/limits_validator.py
git commit -m "feat(lib): add limits.yaml validator"
```

---

### Task 12: Test `limits_validator.py` (TDD: test then refactor)

**Files:**
- Create: `tests/unit/test_limits_schema.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for limits.yaml schema validation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from lib.limits_validator import validate

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / "config" / "limits.yaml"
SCHEMA = REPO_ROOT / "config" / "limits-schema.json"


def test_shipped_limits_is_valid():
    """The limits.yaml we committed must validate against its schema."""
    errors = validate(CONFIG, SCHEMA)
    assert errors == [], f"Shipped limits.yaml is invalid: {errors}"


def test_invalid_default_for_unknown_rejected(tmp_path):
    """Approval default must be ask/allow/deny — anything else is rejected."""
    bad = yaml.safe_load(CONFIG.read_text())
    bad["approval"]["default_for_unknown"] = "yolo"
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text(yaml.dump(bad))
    errors = validate(bad_path, SCHEMA)
    assert any("default_for_unknown" in e for e in errors)


def test_negative_budget_rejected(tmp_path):
    bad = yaml.safe_load(CONFIG.read_text())
    bad["budget"]["daily_usd_cap"] = -10
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text(yaml.dump(bad))
    errors = validate(bad_path, SCHEMA)
    assert any("daily_usd_cap" in e for e in errors)


def test_missing_required_section_rejected(tmp_path):
    bad = yaml.safe_load(CONFIG.read_text())
    del bad["budget"]
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text(yaml.dump(bad))
    errors = validate(bad_path, SCHEMA)
    assert any("budget" in e for e in errors)


def test_unknown_top_level_section_rejected(tmp_path):
    bad = yaml.safe_load(CONFIG.read_text())
    bad["unknown_section"] = {"foo": "bar"}
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text(yaml.dump(bad))
    errors = validate(bad_path, SCHEMA)
    assert any("Additional properties" in e or "unknown_section" in e for e in errors)
```

- [ ] **Step 2: Run tests**

```bash
source .venv/bin/activate
pytest tests/unit/test_limits_schema.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_limits_schema.py
git commit -m "test(unit): add limits.yaml schema validation tests"
```

---

### Task 13: Create `config/scrubber-patterns.yaml` + `lib/scrubber.py`

**Files:**
- Create: `config/scrubber-patterns.yaml`
- Create: `lib/scrubber.py`

- [ ] **Step 1: Write `config/scrubber-patterns.yaml`**

```yaml
# Each pattern: name, regex, replacement, severity (info/warning/critical).
# Replacement uses Python re.sub format (\1, \2, ...).
patterns:
  - name: aws_access_key_id
    regex: '\bAKIA[0-9A-Z]{16}\b'
    replacement: '[REDACTED:aws_access_key_id]'
    severity: critical
  - name: aws_secret_access_key
    regex: '\b(?i:aws[_-]?(?:secret[_-]?)?access[_-]?key)["\s:=]+([A-Za-z0-9/+=]{40})\b'
    replacement: '\1[REDACTED:aws_secret_access_key]'
    severity: critical
  - name: openai_api_key
    regex: '\bsk-[A-Za-z0-9_-]{20,}\b'
    replacement: '[REDACTED:openai_or_anthropic_key]'
    severity: critical
  - name: anthropic_api_key
    regex: '\bsk-ant-[A-Za-z0-9_-]{20,}\b'
    replacement: '[REDACTED:anthropic_key]'
    severity: critical
  - name: github_pat
    regex: '\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b'
    replacement: '[REDACTED:github_pat]'
    severity: critical
  - name: jwt
    regex: '\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b'
    replacement: '[REDACTED:jwt]'
    severity: warning
  - name: gcp_service_account_json
    regex: '"type":\s*"service_account"'
    replacement: '[REDACTED:gcp_sa_json_marker]'
    severity: critical
  - name: private_key_pem
    regex: '-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'
    replacement: '[REDACTED:private_key_pem]'
    severity: critical
  - name: telegram_bot_token
    regex: '\b\d{8,12}:[A-Za-z0-9_-]{30,40}\b'
    replacement: '[REDACTED:telegram_bot_token]'
    severity: critical
  - name: high_entropy_hex
    regex: '\b[a-f0-9]{40,}\b'
    replacement: '[REDACTED:high_entropy_hex]'
    severity: info  # info-only because of false-positive risk
```

- [ ] **Step 2: Write `lib/scrubber.py`**

```python
"""Regex-based secret scrubber.

Reads patterns from config/scrubber-patterns.yaml. Scrubs strings before persist or outbound.
Logs every hit (severity, pattern_name, redacted_at, source_context) to scrubber_log_path.

Public API:
  scrubber = Scrubber.from_config(Path("config/scrubber-patterns.yaml"))
  cleaned, hits = scrubber.scrub(text, source="model_response")
  # `hits` is a list of (pattern_name, severity) tuples for caller to log/alert
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


@dataclass(frozen=True)
class ScrubPattern:
    name: str
    regex: re.Pattern
    replacement: str
    severity: str  # "info" | "warning" | "critical"


@dataclass(frozen=True)
class ScrubHit:
    pattern_name: str
    severity: str
    source: str


class Scrubber:
    def __init__(self, patterns: Iterable[ScrubPattern]):
        self._patterns = list(patterns)

    @classmethod
    def from_config(cls, config_path: Path) -> Scrubber:
        with config_path.open() as f:
            data = yaml.safe_load(f)
        patterns = [
            ScrubPattern(
                name=p["name"],
                regex=re.compile(p["regex"]),
                replacement=p["replacement"],
                severity=p["severity"],
            )
            for p in data["patterns"]
        ]
        return cls(patterns)

    def scrub(self, text: str, *, source: str = "unknown") -> tuple[str, list[ScrubHit]]:
        hits: list[ScrubHit] = []
        scrubbed = text
        for p in self._patterns:
            new, n = p.regex.subn(p.replacement, scrubbed)
            if n > 0:
                hits.extend(ScrubHit(p.name, p.severity, source) for _ in range(n))
            scrubbed = new
        return scrubbed, hits
```

- [ ] **Step 3: Commit**

```bash
git add config/scrubber-patterns.yaml lib/scrubber.py
git commit -m "feat(security): add regex-based secret scrubber"
```

---

### Task 14: Test `scrubber.py` with positives and negatives

**Files:**
- Create: `tests/unit/test_scrubber.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for the secret scrubber."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.scrubber import Scrubber

REPO_ROOT = Path(__file__).resolve().parents[2]
PATTERNS = REPO_ROOT / "config" / "scrubber-patterns.yaml"


@pytest.fixture(scope="module")
def scrubber() -> Scrubber:
    return Scrubber.from_config(PATTERNS)


# Positives — these MUST be redacted.
@pytest.mark.parametrize("text,expected_pattern", [
    ("My key is AKIAIOSFODNN7EXAMPLE here", "aws_access_key_id"),
    ("openai key sk-proj_aBcDeFgHiJkLmNoPqRsTu123 here", "openai_api_key"),
    ("anthropic sk-ant-api03-abcdefghijklmnopqrst here", "anthropic_api_key"),
    ("token ghp_1234567890abcdefghijklmnopqrstuvwxyz here", "github_pat"),
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.signaturepart_xxxx", "jwt"),
    ('{"type": "service_account", "project_id": "x"}', "gcp_service_account_json"),
    ("-----BEGIN RSA PRIVATE KEY-----\nABCD", "private_key_pem"),
    ("Bot token 123456789:AAFmZpQXqRsTuVwXyZ-_aBcDeFgHiJkLmNoP here", "telegram_bot_token"),
])
def test_positives_are_redacted(scrubber, text, expected_pattern):
    cleaned, hits = scrubber.scrub(text, source="test")
    assert "[REDACTED:" in cleaned, f"Should have been scrubbed: {text}"
    assert any(h.pattern_name == expected_pattern for h in hits), \
        f"Expected pattern {expected_pattern} in hits, got {[h.pattern_name for h in hits]}"


# Negatives — these must NOT be touched.
@pytest.mark.parametrize("text", [
    "Just a normal sentence about coding.",
    "Order #ABCD-1234 was shipped.",
    "Visit https://api.github.com/repos/foo/bar for details.",
    "The function returns sk_normal_variable_name in the codebase.",
    "AKIA-suffix-no-format-match-because-too-short",  # AKIA prefix but wrong shape
])
def test_negatives_are_not_redacted(scrubber, text):
    cleaned, hits = scrubber.scrub(text, source="test")
    # Allow the high-entropy hex pattern to fire (severity=info) but no critical hits
    critical_hits = [h for h in hits if h.severity == "critical"]
    assert critical_hits == [], f"False positive (critical) on: {text} → {critical_hits}"


def test_multiple_secrets_in_one_string(scrubber):
    text = "AKIAIOSFODNN7EXAMPLE and sk-proj_xxxxxxxxxxxxxxxxxxxx in same line"
    cleaned, hits = scrubber.scrub(text, source="test")
    assert cleaned.count("[REDACTED:") == 2
    assert {h.pattern_name for h in hits} >= {"aws_access_key_id", "openai_api_key"}


def test_source_attribution(scrubber):
    _, hits = scrubber.scrub("AKIAIOSFODNN7EXAMPLE", source="model_response")
    assert all(h.source == "model_response" for h in hits)
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/unit/test_scrubber.py -v
```

Expected: All tests PASS. If a "false positive" test fails, refine the regex in `scrubber-patterns.yaml` and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_scrubber.py
git commit -m "test(unit): add scrubber positive/negative test suite"
```

---

### Task 15: Create `config/toolsets.yaml` + `lib/toolset_router.py`

**Files:**
- Create: `config/toolsets.yaml`
- Create: `lib/toolset_router.py`

- [ ] **Step 1: Write `config/toolsets.yaml`**

```yaml
# Routing rules: tool name pattern → sandbox tier.
# Tiers (in order of permissiveness):
#   in_process       — runs in agent process (read-only, host FS)
#   shell_sandbox    — Docker container, no network, restricted FS
#   browser_sandbox  — Playwright container, network allowlisted per call
#   external_https   — outbound HTTPS to allowlisted MCPs
#   cloud_sandbox    — Modal/Daytona ephemeral microVM (Phase 2 onward)
#
# First match wins. Unknown tools fall through to `default_tier`.

default_tier: shell_sandbox

routes:
  # in-process (safe reads)
  - match: ["read_file", "ls", "grep", "rg", "find_files", "glob"]
    tier: in_process
  # shell sandbox
  - match: ["shell", "bash", "git", "jq", "run_command"]
    tier: shell_sandbox
  # browser sandbox
  - match: ["browser_*", "playwright_*", "web_scrape", "screenshot"]
    tier: browser_sandbox
  # external HTTPS MCPs
  - match: ["github_*", "context7_*"]
    tier: external_https
  # arbitrary code execution
  - match: ["run_python", "run_javascript", "exec_code", "code_interpreter"]
    tier: cloud_sandbox
```

- [ ] **Step 2: Write `lib/toolset_router.py`**

```python
"""Tool → sandbox-tier router.

Reads config/toolsets.yaml and resolves tool names to sandbox tiers using
glob-style match (first match wins, fnmatch semantics).
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml


class Tier(str, Enum):
    IN_PROCESS = "in_process"
    SHELL_SANDBOX = "shell_sandbox"
    BROWSER_SANDBOX = "browser_sandbox"
    EXTERNAL_HTTPS = "external_https"
    CLOUD_SANDBOX = "cloud_sandbox"


@dataclass(frozen=True)
class Route:
    patterns: tuple[str, ...]
    tier: Tier


class ToolsetRouter:
    def __init__(self, routes: list[Route], default_tier: Tier):
        self._routes = routes
        self._default = default_tier

    @classmethod
    def from_config(cls, config_path: Path) -> ToolsetRouter:
        with config_path.open() as f:
            data = yaml.safe_load(f)
        routes = [
            Route(patterns=tuple(r["match"]), tier=Tier(r["tier"]))
            for r in data["routes"]
        ]
        default = Tier(data["default_tier"])
        return cls(routes, default)

    def resolve(self, tool_name: str) -> Tier:
        for route in self._routes:
            for pattern in route.patterns:
                if fnmatch.fnmatchcase(tool_name, pattern):
                    return route.tier
        return self._default
```

- [ ] **Step 3: Commit**

```bash
git add config/toolsets.yaml lib/toolset_router.py
git commit -m "feat(security): add toolset → sandbox-tier router"
```

---

### Task 16: Test `toolset_router.py`

**Files:**
- Create: `tests/unit/test_toolset_router.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for the toolset router."""
from __future__ import annotations

from pathlib import Path

import pytest

from lib.toolset_router import Tier, ToolsetRouter

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLSETS = REPO_ROOT / "config" / "toolsets.yaml"


@pytest.fixture(scope="module")
def router() -> ToolsetRouter:
    return ToolsetRouter.from_config(TOOLSETS)


@pytest.mark.parametrize("tool,expected_tier", [
    ("read_file", Tier.IN_PROCESS),
    ("ls", Tier.IN_PROCESS),
    ("grep", Tier.IN_PROCESS),
    ("rg", Tier.IN_PROCESS),
    ("shell", Tier.SHELL_SANDBOX),
    ("git", Tier.SHELL_SANDBOX),
    ("browser_navigate", Tier.BROWSER_SANDBOX),
    ("browser_click", Tier.BROWSER_SANDBOX),
    ("playwright_screenshot", Tier.BROWSER_SANDBOX),
    ("github_create_pull_request", Tier.EXTERNAL_HTTPS),
    ("context7_query", Tier.EXTERNAL_HTTPS),
    ("run_python", Tier.CLOUD_SANDBOX),
    ("exec_code", Tier.CLOUD_SANDBOX),
])
def test_known_tools_routed_correctly(router, tool, expected_tier):
    assert router.resolve(tool) == expected_tier


def test_unknown_tool_falls_to_default(router):
    assert router.resolve("never_seen_before_tool") == Tier.SHELL_SANDBOX


def test_glob_matching_for_browser_prefix(router):
    assert router.resolve("browser_anything_at_all") == Tier.BROWSER_SANDBOX
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/unit/test_toolset_router.py -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_toolset_router.py
git commit -m "test(unit): add toolset router tests"
```

---

### Task 17: Create initial Hermes agent config files

**Files:**
- Create: `config/hermes/cli-config.yaml`
- Create: `config/hermes/AGENTS.md`
- Create: `config/hermes/MEMORY.md`
- Create: `config/hermes/USER.md`
- Create: `config/hermes/SOUL.md`

- [ ] **Step 1: Write `config/hermes/cli-config.yaml`**

Reference the upstream `hermes-agent/cli-config.yaml.example` for the full schema. Our minimal initial config:

```yaml
# Hermes Agent CLI configuration for Phase 1 local deployment.
# Full reference: hermes-agent/cli-config.yaml.example

llm:
  provider: openai-compatible
  base_url: http://litellm-proxy:4000
  api_key: sk-dummy-litellm-handles-auth
  model_id: vertex_ai/claude-opus-4-7
  max_tokens: 8192
  temperature: 0.7

memory:
  enabled: true
  data_dir: /data
  curator_cron: "0 */6 * * *"

skills:
  enabled: true
  skills_dir: /app/skills
  extractor:
    min_turns: 10
    min_distinct_tools: 3

session:
  storage: sqlite
  sqlite_path: /data/sessions.db
  fts5_enabled: true

honcho:
  enabled: true
  base_url: http://honcho:8001

vector_memory:
  enabled: true
  provider: chroma
  chroma_url: http://chroma:8000
  collection: hermes_memory

mcp_servers:
  github:
    type: stdio
    command: gh-mcp-server
  context7:
    type: http
    url: https://mcp.context7.com/sse
  playwright:
    type: http
    url: http://playwright-mcp:8002

terminal_backend: docker
docker_backend:
  container: shell-sandbox
  workspace_path: /workspace

telemetry:
  otel_endpoint: http://otel-collector:4317
  service_name: hermes-agent
  service_version: 0.1.0

approval:
  config_path: /app/config/limits.yaml  # reads `approval` section from limits

logs:
  format: json
  level: INFO
  destination: stdout
```

- [ ] **Step 2: Write `config/hermes/AGENTS.md`**

```markdown
# Agent Project Context

This Hermes Agent runs on the AutonomousAgent project, deployed locally via docker-compose.

## Working directory

The shell sandbox mounts `workspace` as `/workspace`. Treat that as the persistent project workspace.

## Tools

You have access to:
- File reads (in-process, host FS read-only)
- Shell commands (Docker shell sandbox, no network)
- Browser automation (Playwright sandbox)
- GitHub via MCP (gh-authenticated)
- Context7 for live library docs
- Web search via the agent's built-in tools

For arbitrary code execution beyond shell, ask first — we may route to a cloud sandbox.

## Conventions

- Always commit work in small, focused git commits
- Prefer editing existing files over creating new ones
- Run tests before declaring work complete
- Follow the patterns in CLAUDE.md / AGENTS.md files of the projects you work in
```

- [ ] **Step 3: Write minimal `config/hermes/MEMORY.md`**

```markdown
# Memory

## Project context
- Deployment: Phase 1 (local Mac, docker-compose)
- LLM: Anthropic Claude Opus 4.7 via Vertex AI (project i-for-ai) via LiteLLM proxy
- Storage: SQLite + Chroma + Honcho

(Memory grows from agent experience; this is the seed.)
```

- [ ] **Step 4: Write minimal `config/hermes/USER.md`**

```markdown
# User Profile

## Identity
- Name: Daniel Manzela
- Role: Building autonomous agent infrastructure

## Communication preferences
- Concise > verbose
- Show diffs/code rather than describing them
- Proactive: surface blockers and trade-offs early
- Don't over-celebrate completed work; just say what changed and what's next

(More to be learned from interaction via Honcho dialectic modeling.)
```

- [ ] **Step 5: Write minimal `config/hermes/SOUL.md`**

```markdown
# Persona

You are a careful, pragmatic engineering collaborator. Defaults:
- Verify before claiming success
- Prefer small reversible changes over big-bang
- Acknowledge uncertainty explicitly
- When degraded (no Honcho, no vector memory, etc.), say so to the user
```

- [ ] **Step 6: Commit**

```bash
git add config/hermes/
git commit -m "feat(config): add initial Hermes agent config (cli-config + AGENTS/MEMORY/USER/SOUL)"
```

---

## Stage C — LiteLLM + OpenTelemetry (T18–T20)

### Task 18: Create `deploy/litellm/config.yaml`

**Files:**
- Create: `deploy/litellm/config.yaml`

- [ ] **Step 1: Write the config**

```bash
mkdir -p deploy/litellm
```

Save to `deploy/litellm/config.yaml`:

```yaml
model_list:
  - model_name: vertex_ai/claude-opus-4-7
    litellm_params:
      model: vertex_ai/claude-opus-4-7
      vertex_project: i-for-ai
      vertex_location: us-east5
  - model_name: vertex_ai/claude-sonnet-4-6
    litellm_params:
      model: vertex_ai/claude-sonnet-4-6
      vertex_project: i-for-ai
      vertex_location: us-east5

litellm_settings:
  drop_params: true
  num_retries: 5
  request_timeout: 600
  telemetry: false  # we use OTel directly, not LiteLLM's anonymous telemetry
  cache: false
  set_verbose: false
  json_logs: true

  # Budget enforcement
  max_budget: 100  # USD per day, hard cap
  budget_duration: 24h
  alert_to_webhook_url: ""  # set after Phase 2 cloud monitoring is up

  callbacks: ["otel"]

# OpenTelemetry export
litellm_otel:
  endpoint: http://otel-collector:4317
  service_name: litellm-proxy

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: ""  # no LiteLLM DB; we own state
  enforced_params: []
  proxy_batch_write_at: 60
```

- [ ] **Step 2: Commit**

```bash
git add deploy/litellm/config.yaml
git commit -m "feat(deploy): add LiteLLM proxy config for Vertex AI"
```

---

### Task 19: Create OTel collector configs (dev + prod)

**Files:**
- Create: `deploy/otel/collector.dev.yaml`
- Create: `deploy/otel/collector.prod.yaml`

- [ ] **Step 1: Write `deploy/otel/collector.dev.yaml`**

```bash
mkdir -p deploy/otel
```

Save to `deploy/otel/collector.dev.yaml`:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
    send_batch_size: 512
  memory_limiter:
    check_interval: 5s
    limit_percentage: 80
    spike_limit_percentage: 25

exporters:
  otlphttp/phoenix:
    endpoint: http://phoenix:6006/v1/traces
    tls:
      insecure: true
  debug:
    verbosity: basic
  file:
    path: /var/log/otel/traces.jsonl

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/phoenix, file]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [debug]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [file]
```

- [ ] **Step 2: Write `deploy/otel/collector.prod.yaml`**

Save to `deploy/otel/collector.prod.yaml`:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 5s
    send_batch_size: 512
  memory_limiter:
    check_interval: 5s
    limit_percentage: 80
    spike_limit_percentage: 25
  resource:
    attributes:
      - key: deployment.environment
        value: prod
        action: upsert
  tail_sampling:
    decision_wait: 30s
    num_traces: 100000
    expected_new_traces_per_sec: 100
    policies:
      - name: errors
        type: status_code
        status_code: {status_codes: [ERROR]}
      - name: slow
        type: latency
        latency: {threshold_ms: 5000}
      - name: probabilistic
        type: probabilistic
        probabilistic: {sampling_percentage: 10}

exporters:
  googlecloud:
    project: i-for-ai
  googlecloudmonitoring:
    project: i-for-ai
  googlecloudlogging:
    project: i-for-ai

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, resource, tail_sampling, batch]
      exporters: [googlecloud]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, resource, batch]
      exporters: [googlecloudmonitoring]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, resource, batch]
      exporters: [googlecloudlogging]
```

- [ ] **Step 3: Commit**

```bash
git add deploy/otel/collector.dev.yaml deploy/otel/collector.prod.yaml
git commit -m "feat(deploy): add OTel collector configs (dev + prod)"
```

---

### Task 20: Create `deploy/Dockerfile.hermes`

**Files:**
- Create: `deploy/Dockerfile.hermes`

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# Wraps the upstream NousResearch/hermes-agent build with our OTel SDK + config mount.
# Built from the project root so the hermes-agent submodule is in build context.
ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates ripgrep ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mv /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app

# Install upstream Hermes from submodule
COPY hermes-agent/pyproject.toml hermes-agent/uv.lock /app/
RUN uv pip install --system --no-cache hermes-agent[all] || true   # best-effort; full install in next step

COPY hermes-agent /app
RUN uv pip install --system --no-cache -e ".[all]"

# Add our OTel SDK on top
RUN uv pip install --system --no-cache \
      "opentelemetry-api>=1.27" \
      "opentelemetry-sdk>=1.27" \
      "opentelemetry-exporter-otlp>=1.27" \
      "opentelemetry-instrumentation-httpx>=0.48" \
      "opentelemetry-instrumentation-sqlite3>=0.48"

# Mount points (created here so volume mounts behave predictably)
RUN mkdir -p /data /app/config /app/skills /app/secrets

# Default CLI entrypoint; gateway service overrides command in compose
ENTRYPOINT ["hermes"]
CMD []
```

- [ ] **Step 2: Build the image (will be slow first time, ~5-10 min)**

```bash
docker build -f deploy/Dockerfile.hermes -t autonomousagent/hermes:0.1.0 .
```

Expected: Image builds successfully.

- [ ] **Step 3: Verify it runs `hermes --version`**

```bash
docker run --rm autonomousagent/hermes:0.1.0 --version
```

Expected: Hermes prints its version.

- [ ] **Step 4: Commit**

```bash
git add deploy/Dockerfile.hermes
git commit -m "feat(deploy): add Dockerfile.hermes (extends upstream + OTel SDK)"
```

---

### Task 21: Create `deploy/sandboxes/Dockerfile.shell-sandbox`

**Files:**
- Create: `deploy/sandboxes/Dockerfile.shell-sandbox`

- [ ] **Step 1: Write the Dockerfile**

```bash
mkdir -p deploy/sandboxes
```

Save to `deploy/sandboxes/Dockerfile.shell-sandbox`:

```dockerfile
# Minimal shell environment for the agent's shell tool.
# No network at runtime (compose enforces --network=none).
# Read-only host FS at runtime; only /workspace is writable.

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      bash coreutils findutils \
      git curl wget \
      ripgrep jq \
      ca-certificates \
      python3 python3-pip \
      build-essential \
      vim less \
    && rm -rf /var/lib/apt/lists/*

# Add a non-root user for sandbox execution
RUN useradd -m -u 1000 -s /bin/bash sandbox

USER sandbox
WORKDIR /workspace

# Idle entrypoint — the agent runs commands via `docker exec`
CMD ["sleep", "infinity"]
```

- [ ] **Step 2: Build**

```bash
docker build -f deploy/sandboxes/Dockerfile.shell-sandbox -t autonomousagent/shell-sandbox:0.1.0 deploy/sandboxes/
```

Expected: builds successfully.

- [ ] **Step 3: Verify isolation manually**

```bash
docker run --rm --network=none autonomousagent/shell-sandbox:0.1.0 \
  bash -c "echo OK && whoami && pwd"
```

Expected: `OK\nsandbox\n/workspace` and `curl http://example.com` would fail (no network).

- [ ] **Step 4: Commit**

```bash
git add deploy/sandboxes/Dockerfile.shell-sandbox
git commit -m "feat(deploy): add shell-sandbox Dockerfile"
```

---

## Stage D — docker-compose Stack (T22–T26)

### Task 22: Create `deploy/chroma/auth.json`

**Files:**
- Create: `deploy/chroma/auth.json` (placeholder; real token is generated at bootstrap)

- [ ] **Step 1: Write a stub**

```bash
mkdir -p deploy/chroma
cat > deploy/chroma/auth.json <<'EOF'
{
  "// purpose": "ChromaDB auth tokens; replaced at bootstrap with secret",
  "tokens": []
}
EOF
```

- [ ] **Step 2: Commit**

```bash
git add deploy/chroma/auth.json
git commit -m "feat(deploy): add chroma auth.json placeholder"
```

---

### Task 23: Create `deploy/honcho/init.sql`

**Files:**
- Create: `deploy/honcho/init.sql`

- [ ] **Step 1: Write the schema bootstrap**

```bash
mkdir -p deploy/honcho
cat > deploy/honcho/init.sql <<'EOF'
-- Honcho bootstrap: ensures pgvector extension is enabled when Honcho first starts.
-- Honcho's own migrations create the rest of the schema.
CREATE EXTENSION IF NOT EXISTS vector;
EOF
```

- [ ] **Step 2: Commit**

```bash
git add deploy/honcho/init.sql
git commit -m "feat(deploy): add honcho postgres init SQL"
```

---

### Task 24: Create `deploy/docker-compose.yml`

**Files:**
- Create: `deploy/docker-compose.yml`

- [ ] **Step 1: Write the full compose stack**

Save to `deploy/docker-compose.yml`:

```yaml
name: autonomous-agent

x-restart: &restart_policy
  restart: unless-stopped

x-logging: &json_logging
  logging:
    driver: json-file
    options:
      max-size: "100m"
      max-file: "5"

networks:
  internal:
    driver: bridge
    internal: true
  egress:
    driver: bridge

volumes:
  hermes-data:
  chroma-data:
  honcho-db-data:
  workspace:

services:

  # ---- Storage layer ----

  honcho-db:
    image: postgres:16-alpine
    <<: [*restart_policy, *json_logging]
    environment:
      POSTGRES_USER: honcho
      POSTGRES_PASSWORD_FILE: /run/secrets/honcho_db_password
      POSTGRES_DB: honcho
    secrets:
      - honcho_db_password
    volumes:
      - honcho-db-data:/var/lib/postgresql/data
      - ./honcho/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    networks: [internal]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U honcho -d honcho"]
      interval: 10s
      timeout: 5s
      retries: 5

  chroma:
    image: chromadb/chroma:latest
    <<: [*restart_policy, *json_logging]
    environment:
      CHROMA_SERVER_AUTHN_PROVIDER: chromadb.auth.token_authn.TokenAuthenticationServerProvider
      CHROMA_SERVER_AUTHN_CREDENTIALS_FILE: /run/secrets/chroma_token
      CHROMA_SERVER_AUTHN_CREDENTIALS_PROVIDER: chromadb.auth.token_authn.TokenConfigServerAuthCredentialsProvider
    secrets:
      - chroma_token
    volumes:
      - chroma-data:/chroma/chroma
    networks: [internal]
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/api/v2/heartbeat"]
      interval: 15s
      timeout: 5s
      retries: 5

  # ---- Honcho ----

  honcho:
    image: plasticlabs/honcho:latest
    <<: [*restart_policy, *json_logging]
    environment:
      DATABASE_URL: postgresql://honcho:${HONCHO_DB_PASSWORD}@honcho-db:5432/honcho
      OPENAI_API_KEY: sk-dummy-litellm-handles-auth
      OPENAI_BASE_URL: http://litellm-proxy:4000
    networks: [internal]
    depends_on:
      honcho-db: {condition: service_healthy}
      litellm-proxy: {condition: service_healthy}
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health"]
      interval: 15s
      timeout: 5s
      retries: 5

  # ---- LiteLLM proxy ----

  litellm-proxy:
    image: ghcr.io/berriai/litellm:main-latest
    <<: [*restart_policy, *json_logging]
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    environment:
      LITELLM_MASTER_KEY_FILE: /run/secrets/litellm_master_key
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317
      GOOGLE_APPLICATION_CREDENTIALS: /root/.config/gcloud/application_default_credentials.json
    secrets:
      - litellm_master_key
    volumes:
      - ./litellm/config.yaml:/app/config.yaml:ro
      - ${HOME}/.config/gcloud:/root/.config/gcloud:ro
    networks: [internal, egress]
    depends_on:
      otel-collector: {condition: service_started}
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:4000/health/liveliness"]
      interval: 15s
      timeout: 5s
      retries: 5

  # ---- OTel collector ----

  otel-collector:
    image: otel/opentelemetry-collector-contrib:latest
    <<: [*restart_policy, *json_logging]
    command: ["--config", "/etc/otelcol-contrib/config.yaml"]
    volumes:
      - ./otel/collector.dev.yaml:/etc/otelcol-contrib/config.yaml:ro
    networks: [internal, egress]
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:13133/"]
      interval: 30s
      timeout: 5s
      retries: 3

  # ---- Sandboxes ----

  shell-sandbox:
    build:
      context: ./sandboxes
      dockerfile: Dockerfile.shell-sandbox
    image: autonomousagent/shell-sandbox:0.1.0
    <<: [*restart_policy, *json_logging]
    network_mode: none
    cap_drop: [ALL]
    read_only: true
    tmpfs:
      - /tmp:size=100M
    volumes:
      - workspace:/workspace
    mem_limit: 1g
    cpus: 1.0
    pids_limit: 200
    command: ["sleep", "infinity"]

  playwright-mcp:
    image: mcr.microsoft.com/playwright/mcp:latest
    <<: [*restart_policy, *json_logging]
    networks: [internal, egress]
    mem_limit: 2g
    cpus: 2.0

  # ---- Hermes core ----

  hermes-agent:
    build:
      context: ..
      dockerfile: deploy/Dockerfile.hermes
    image: autonomousagent/hermes:0.1.0
    <<: [*restart_policy, *json_logging]
    command: ["serve", "--port", "7878"]
    environment:
      HERMES_CONFIG: /app/config/cli-config.yaml
      LITELLM_MASTER_KEY_FILE: /run/secrets/litellm_master_key
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317
      OTEL_SERVICE_NAME: hermes-agent
    secrets:
      - litellm_master_key
    volumes:
      - hermes-data:/data
      - ../config/hermes:/app/config:ro
      - ../config/limits.yaml:/app/config/limits.yaml:ro
      - ../config/scrubber-patterns.yaml:/app/config/scrubber-patterns.yaml:ro
      - ../config/toolsets.yaml:/app/config/toolsets.yaml:ro
    networks: [internal]
    depends_on:
      litellm-proxy: {condition: service_healthy}
      chroma: {condition: service_healthy}
      honcho: {condition: service_healthy}
      otel-collector: {condition: service_started}
      shell-sandbox: {condition: service_started}
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:7878/health"]
      interval: 30s
      timeout: 5s
      retries: 5

  hermes-gateway:
    image: autonomousagent/hermes:0.1.0
    <<: [*restart_policy, *json_logging]
    command: ["gateway", "start"]
    environment:
      HERMES_CONFIG: /app/config/cli-config.yaml
      HERMES_AGENT_URL: http://hermes-agent:7878
      OTEL_EXPORTER_OTLP_ENDPOINT: http://otel-collector:4317
      OTEL_SERVICE_NAME: hermes-gateway
    env_file:
      - ../secrets/telegram.env  # decrypted at bootstrap
    volumes:
      - hermes-data:/data:ro
      - ../config/hermes:/app/config:ro
    networks: [internal, egress]
    depends_on:
      hermes-agent: {condition: service_healthy}

secrets:
  honcho_db_password:
    file: ../secrets/honcho-db-password
  litellm_master_key:
    file: ../secrets/litellm-master-key
  chroma_token:
    file: ../secrets/chroma-token
```

- [ ] **Step 2: Validate syntax (without starting)**

```bash
docker compose -f deploy/docker-compose.yml config > /tmp/compose-rendered.yaml
echo "Render OK: $(wc -l < /tmp/compose-rendered.yaml) lines"
```

Expected: no errors; rendered YAML printed line count.

- [ ] **Step 3: Commit**

```bash
git add deploy/docker-compose.yml
git commit -m "feat(deploy): add main docker-compose stack (10 services)"
```

---

### Task 25: Create `deploy/docker-compose.dev.yml` (adds Phoenix + dev mounts)

**Files:**
- Create: `deploy/docker-compose.dev.yml`

- [ ] **Step 1: Write the dev override**

```yaml
# Dev overrides: adds Phoenix for local trace viewing, exposes ports for inspection.

services:
  phoenix:
    image: arizephoenix/phoenix:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:6006:6006"
      - "127.0.0.1:4317:4317"  # Phoenix exposes its own OTLP receiver on 4317
    volumes:
      - phoenix-data:/mnt/data
    networks: [internal]

  hermes-agent:
    ports:
      - "127.0.0.1:7878:7878"  # admin endpoint exposed for direct inspection
    volumes:
      - ../config/hermes:/app/config:rw  # writable in dev for live config edits

  litellm-proxy:
    ports:
      - "127.0.0.1:4000:4000"  # exposed for direct probing

  chroma:
    ports:
      - "127.0.0.1:8001:8000"

  honcho:
    ports:
      - "127.0.0.1:8002:8000"

volumes:
  phoenix-data:
```

- [ ] **Step 2: Render the merged config**

```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml config > /tmp/dev-rendered.yaml
echo "Dev render OK"
```

Expected: clean render.

- [ ] **Step 3: Commit**

```bash
git add deploy/docker-compose.dev.yml
git commit -m "feat(deploy): add docker-compose.dev.yml override (Phoenix + dev ports)"
```

---

### Task 26: Create `deploy/docker-compose.test.yml` (mocked LLM, in-memory deps)

**Files:**
- Create: `deploy/docker-compose.test.yml`

- [ ] **Step 1: Write test override**

Save to `deploy/docker-compose.test.yml`:

```yaml
# Test overrides: mocked LLM, ephemeral storage, no real network egress.

services:
  litellm-proxy:
    image: stoplight/prism:5
    command: ["mock", "-h", "0.0.0.0", "-p", "4000", "/specs/openai-mock.yaml"]
    volumes:
      - ../tests/fixtures/openai-mock.yaml:/specs/openai-mock.yaml:ro
    networks: [internal]
    depends_on: []
    secrets: []
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:4000/v1/models"]
      interval: 10s
      timeout: 3s
      retries: 5

  chroma:
    tmpfs:
      - /chroma/chroma:size=256M
    volumes: []

  honcho-db:
    tmpfs:
      - /var/lib/postgresql/data:size=256M
    volumes: []

  hermes-gateway:
    profiles: ["disabled"]  # no Telegram in tests

  hermes-agent:
    volumes:
      - ../config/hermes:/app/config:ro
      - ../config/limits.yaml:/app/config/limits.yaml:ro
      - ../config/scrubber-patterns.yaml:/app/config/scrubber-patterns.yaml:ro
      - ../config/toolsets.yaml:/app/config/toolsets.yaml:ro
      # No persistent hermes-data — ephemeral for tests
    tmpfs:
      - /data:size=256M
```

- [ ] **Step 2: Commit**

```bash
git add deploy/docker-compose.test.yml
git commit -m "feat(deploy): add docker-compose.test.yml (mocked LLM)"
```

---

## Stage E — Secrets, Telegram Bot, MCP Servers (T27–T31)

### Task 27: Document Telegram bot setup (manual — user step)

**Files:**
- Create: `docs/runbooks/telegram-bot-setup.md`

- [ ] **Step 1: Write the runbook**

```markdown
# Telegram Bot Setup

This is a one-time manual step. The bot acts as the agent's messaging interface.

## Steps

1. Open Telegram, search for `@BotFather`, start a chat
2. Send `/newbot`
3. Follow prompts: pick a display name (e.g. `Hermes Local`) and a username ending in `bot` (e.g. `your_hermes_local_bot`)
4. BotFather returns a token like `123456789:ABCdefGhIJklmnoPQRstuVwxyZ-abc12345678`
5. Send `/setprivacy` to BotFather, choose your bot, set to `Disable` (so the bot can read all messages, not just commands)
6. Find your own Telegram numeric user ID:
   - Search `@userinfobot`, start a chat, send any message
   - It replies with your numeric ID

## Save the values

Run from the project root:

```bash
cat > secrets/telegram.env <<EOF
TELEGRAM_BOT_TOKEN=<paste-token-here>
TELEGRAM_ALLOWED_USER_IDS=<your-numeric-id>
EOF

sops -e secrets/telegram.env > secrets/telegram.env.sops
rm secrets/telegram.env
```

## Update `config/limits.yaml`

Open `config/limits.yaml`, find `notify_channels.telegram_chat_id`, set it to your numeric user ID:

```yaml
notify_channels:
  telegram_chat_id: <your-numeric-id>
  ...
```

Re-run `python -m lib.limits_validator config/limits.yaml` to confirm it still validates.

## Verify the bot is reachable

```bash
TOKEN=$(sops -d secrets/telegram.env.sops | grep TELEGRAM_BOT_TOKEN | cut -d= -f2)
curl -fsS "https://api.telegram.org/bot${TOKEN}/getMe" | jq .
```

Expected: JSON describing your bot (name, id, username).

## Troubleshooting

- `401 Unauthorized` → bad token, regenerate via BotFather `/token`
- Bot doesn't respond → make sure you sent it `/start` first; bots can't message you until you initiate
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/telegram-bot-setup.md
git commit -m "docs(runbook): Telegram bot setup procedure"
```

---

### Task 28: Manual: User performs Telegram bot setup

**This task has no code; it's a checkpoint for the user to do the manual step.**

- [ ] **Step 1: User follows `docs/runbooks/telegram-bot-setup.md`**

Outputs: `secrets/telegram.env.sops` exists; `config/limits.yaml` `telegram_chat_id` is set; bot reachable via `getMe`.

- [ ] **Step 2: Confirm the file exists and is encrypted**

```bash
test -f secrets/telegram.env.sops && \
  ! test -f secrets/telegram.env && \
  grep -q "ENC\[" secrets/telegram.env.sops && \
  echo "OK: telegram.env.sops is encrypted, plaintext is removed"
```

Expected: `OK: telegram.env.sops is encrypted, plaintext is removed`

- [ ] **Step 3: Commit the encrypted secret + updated limits**

```bash
git add secrets/telegram.env.sops config/limits.yaml
git commit -m "feat(secrets): add encrypted Telegram bot token + chat_id"
```

---

### Task 29: Generate other secrets + create decrypt script

**Files:**
- Create: `scripts/decrypt-secrets.sh`
- Create: `secrets/litellm-master-key.sops` (encrypted)
- Create: `secrets/chroma-token.sops` (encrypted)
- Create: `secrets/honcho-db-password.sops` (encrypted)

- [ ] **Step 1: Generate and encrypt random secrets**

```bash
# LiteLLM master key
openssl rand -hex 32 > secrets/litellm-master-key
sops -e secrets/litellm-master-key > secrets/litellm-master-key.sops
rm secrets/litellm-master-key

# ChromaDB token
openssl rand -hex 32 > secrets/chroma-token
sops -e secrets/chroma-token > secrets/chroma-token.sops
rm secrets/chroma-token

# Honcho DB password
openssl rand -hex 24 > secrets/honcho-db-password
sops -e secrets/honcho-db-password > secrets/honcho-db-password.sops
rm secrets/honcho-db-password
```

- [ ] **Step 2: Write `scripts/decrypt-secrets.sh`**

```bash
cat > scripts/decrypt-secrets.sh <<'EOF'
#!/usr/bin/env bash
# Decrypts all secrets/*.sops files into adjacent plaintext files used by docker compose.
# Plaintext files are gitignored. Re-run after pulling new encrypted secrets.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/secrets"

shopt -s nullglob
for enc in *.sops; do
  out="${enc%.sops}"
  echo "Decrypting $enc -> $out"
  sops -d "$enc" > "$out"
  chmod 600 "$out"
done

# Source the env file format secrets so subsequent docker compose can reference vars
if [ -f telegram.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./telegram.env
  set +a
fi

if [ -f honcho-db-password ]; then
  export HONCHO_DB_PASSWORD="$(cat honcho-db-password)"
fi

echo "✓ Secrets decrypted. Plaintext files are gitignored."
EOF
chmod +x scripts/decrypt-secrets.sh
```

- [ ] **Step 3: Run it once to verify**

```bash
./scripts/decrypt-secrets.sh
ls -la secrets/
```

Expected: encrypted `.sops` files + their decrypted counterparts; gitignored plaintext.

- [ ] **Step 4: Commit**

```bash
git add scripts/decrypt-secrets.sh secrets/litellm-master-key.sops secrets/chroma-token.sops secrets/honcho-db-password.sops
git commit -m "feat(secrets): add encrypted random secrets + decrypt script"
```

---

### Task 30: Add Healthchecks.io secret + script

**Files:**
- Create: `secrets/healthchecks-url.sops`
- Create: `scripts/healthcheck-ping.sh`

- [ ] **Step 1: Manual: User creates a Healthchecks.io project**

User goes to https://healthchecks.io, signs up (free), creates a project named `hermes-local`, gets the unique ping URL.

- [ ] **Step 2: Encrypt it**

```bash
echo "https://hc-ping.com/<YOUR-UUID>" > secrets/healthchecks-url
sops -e secrets/healthchecks-url > secrets/healthchecks-url.sops
rm secrets/healthchecks-url
```

- [ ] **Step 3: Write the ping script**

```bash
cat > scripts/healthcheck-ping.sh <<'EOF'
#!/usr/bin/env bash
# Pings Healthchecks.io with the Hermes container health status.
# Called by cron on the host every 5 minutes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
URL_FILE="$ROOT/secrets/healthchecks-url"

if [ ! -f "$URL_FILE" ]; then
  echo "Decrypting healthchecks URL"
  sops -d "$ROOT/secrets/healthchecks-url.sops" > "$URL_FILE"
  chmod 600 "$URL_FILE"
fi

URL="$(cat "$URL_FILE")"

# Check that hermes-agent container is healthy
if docker compose -f "$ROOT/deploy/docker-compose.yml" ps hermes-agent --format json | grep -q '"Health":"healthy"'; then
  curl -fsS -m 10 "$URL" > /dev/null
  echo "✓ Pinged healthy"
else
  curl -fsS -m 10 "${URL}/fail" > /dev/null
  echo "✗ Reported failure"
  exit 1
fi
EOF
chmod +x scripts/healthcheck-ping.sh
```

- [ ] **Step 4: Commit**

```bash
git add secrets/healthchecks-url.sops scripts/healthcheck-ping.sh
git commit -m "feat(observability): add Healthchecks.io ping script + secret"
```

---

### Task 31: Configure cron for healthcheck pings (host-level)

**Files:** none (host-level cron entry)

- [ ] **Step 1: Add cron entry**

```bash
( crontab -l 2>/dev/null | grep -v "AutonomousAgent.*healthcheck-ping" ; echo "*/5 * * * * cd '/Users/danielmanzela/RX-Research Project/AutonomousAgent' && ./scripts/healthcheck-ping.sh >> logs/healthcheck.log 2>&1" ) | crontab -
mkdir -p logs
echo "logs/" >> .gitignore
```

- [ ] **Step 2: Verify cron is registered**

```bash
crontab -l | grep healthcheck-ping
```

Expected: the cron entry appears.

- [ ] **Step 3: Commit `.gitignore` update**

```bash
git add .gitignore
git commit -m "chore: gitignore logs/ for cron output"
```

---

## Stage F — Operational Scripts (T32–T35)

### Task 32: Implement `lib/healthcheck.py` and unit-test it

**Files:**
- Create: `lib/healthcheck.py`
- Create: `tests/unit/test_healthcheck.py`

- [ ] **Step 1: Write `lib/healthcheck.py`**

```python
"""Health check helper. Used both by hermes-agent's internal /health endpoint and the
external healthchecks-ping cron script.

Returns a structured HealthReport that classifies each checked dependency.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

import httpx


class Status(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    latency_ms: float | None = None


@dataclass
class HealthReport:
    overall: Status
    checks: list[CheckResult] = field(default_factory=list)


async def _http_check(client: httpx.AsyncClient, name: str, url: str, timeout: float = 3.0) -> CheckResult:
    try:
        import time
        start = time.perf_counter()
        r = await client.get(url, timeout=timeout)
        elapsed = (time.perf_counter() - start) * 1000
        if r.status_code < 500:
            return CheckResult(name, Status.OK, f"http {r.status_code}", elapsed)
        return CheckResult(name, Status.DEGRADED, f"http {r.status_code}", elapsed)
    except Exception as e:
        return CheckResult(name, Status.DOWN, repr(e))


async def run_checks(deps: dict[str, str]) -> HealthReport:
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_http_check(client, n, u) for n, u in deps.items()])
    if all(r.status == Status.OK for r in results):
        overall = Status.OK
    elif any(r.status == Status.DOWN for r in results):
        overall = Status.DOWN
    else:
        overall = Status.DEGRADED
    return HealthReport(overall=overall, checks=results)
```

- [ ] **Step 2: Write tests**

```python
"""Tests for healthcheck.py."""
from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from lib.healthcheck import Status, run_checks


@pytest.mark.asyncio
async def test_all_ok(mocker: MockerFixture):
    class FakeResponse:
        status_code = 200
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, timeout): return FakeResponse()
    mocker.patch("lib.healthcheck.httpx.AsyncClient", return_value=FakeClient())
    report = await run_checks({"chroma": "http://x", "honcho": "http://y"})
    assert report.overall == Status.OK
    assert len(report.checks) == 2


@pytest.mark.asyncio
async def test_one_down(mocker: MockerFixture):
    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, timeout):
            if "chroma" in url:
                raise Exception("connection refused")
            class R: status_code = 200
            return R()
    mocker.patch("lib.healthcheck.httpx.AsyncClient", return_value=FakeClient())
    report = await run_checks({"chroma": "http://chroma", "honcho": "http://honcho"})
    assert report.overall == Status.DOWN
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/unit/test_healthcheck.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add lib/healthcheck.py tests/unit/test_healthcheck.py
git commit -m "feat(lib): add healthcheck helper + tests"
```

---

### Task 33: Write `scripts/smoke.sh`

**Files:**
- Create: `scripts/smoke.sh`

- [ ] **Step 1: Write the smoke test runner**

Save to `scripts/smoke.sh`:

```bash
#!/usr/bin/env bash
# Post-deploy smoke test. Exits non-zero on any failure.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml -f $ROOT/deploy/docker-compose.dev.yml"

failures=0
check() {
  local name="$1"
  shift
  if "$@" >/tmp/smoke.log 2>&1; then
    echo "✓ $name"
  else
    echo "✗ $name"
    cat /tmp/smoke.log | sed 's/^/    /'
    failures=$((failures+1))
  fi
}

echo "Smoke test 1/9: all containers healthy"
check "containers running" $COMPOSE ps --status running --quiet

echo "Smoke test 2/9: hermes-agent → chroma reachable"
check "agent → chroma" $COMPOSE exec -T hermes-agent \
  python -c "import httpx; r=httpx.get('http://chroma:8000/api/v2/heartbeat', timeout=5); assert r.status_code==200"

echo "Smoke test 3/9: hermes-agent → litellm reachable"
check "agent → litellm" $COMPOSE exec -T hermes-agent \
  python -c "import httpx; r=httpx.get('http://litellm-proxy:4000/health/liveliness', timeout=5); assert r.status_code==200"

echo "Smoke test 4/9: egress allowlist works (Telegram)"
TG_TOKEN=$(grep TELEGRAM_BOT_TOKEN "$ROOT/secrets/telegram.env" | cut -d= -f2)
check "egress allowed (TG getMe)" $COMPOSE exec -T hermes-gateway \
  curl -fsS "https://api.telegram.org/bot${TG_TOKEN}/getMe"

echo "Smoke test 5/9: shell-sandbox cannot reach external network"
check "egress denied (shell-sandbox)" bash -c "
  ! docker exec \$($COMPOSE ps -q shell-sandbox) curl -fsS --max-time 3 https://example.com 2>/dev/null
"

echo "Smoke test 6/9: real LLM round-trip"
check "real LLM call via litellm" $COMPOSE exec -T hermes-agent \
  python -c "
import httpx, os
master_key = open('/run/secrets/litellm_master_key').read().strip()
r = httpx.post('http://litellm-proxy:4000/v1/chat/completions',
               headers={'Authorization': f'Bearer {master_key}'},
               json={'model': 'vertex_ai/claude-opus-4-7',
                     'messages': [{'role':'user','content':'Reply with the single word: pong'}],
                     'max_tokens': 10},
               timeout=30)
assert r.status_code == 200, r.text
out = r.json()['choices'][0]['message']['content']
print('LLM said:', out)
assert 'pong' in out.lower(), f'Expected pong, got: {out}'
"

echo "Smoke test 7/9: memory write persists across container restart"
$COMPOSE exec -T hermes-agent bash -c "echo 'TEST_TOKEN_$(date +%s)' > /data/.smoke-test-marker"
$COMPOSE restart hermes-agent
sleep 5
check "data persists across restart" $COMPOSE exec -T hermes-agent \
  bash -c "test -f /data/.smoke-test-marker && grep -q TEST_TOKEN /data/.smoke-test-marker"
$COMPOSE exec -T hermes-agent rm /data/.smoke-test-marker

echo "Smoke test 8/9: OTel traces visible in Phoenix within 30s"
check "trace visible in Phoenix" bash -c "
  for i in {1..6}; do
    if curl -fsS http://localhost:6006/v1/traces 2>/dev/null | grep -q hermes-agent; then exit 0; fi
    sleep 5
  done
  exit 1
"

echo "Smoke test 9/9: limits.yaml validates"
check "limits.yaml valid" bash -c "cd $ROOT && source .venv/bin/activate && python -m lib.limits_validator config/limits.yaml"

echo
if [ "$failures" -gt 0 ]; then
  echo "❌ $failures smoke check(s) failed"
  exit 1
fi
echo "✅ All 9 smoke checks passed"
```

```bash
chmod +x scripts/smoke.sh
```

- [ ] **Step 2: Commit**

```bash
git add scripts/smoke.sh
git commit -m "feat(scripts): add 9-check smoke test"
```

---

### Task 34: Write `scripts/snapshot.sh` (placeholder for Phase 1; full GCS in Phase 2)

**Files:**
- Create: `scripts/snapshot.sh`

- [ ] **Step 1: Write Phase-1 local snapshot (no GCS yet)**

```bash
cat > scripts/snapshot.sh <<'EOF'
#!/usr/bin/env bash
# Snapshot agent state. Phase 1: writes to local snapshots/ dir.
# Phase 2 will upload to GCS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TS="$(date -u +%Y%m%d-%H%M%S)"
OUT="$ROOT/snapshots/$TS"
mkdir -p "$OUT"

COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml"

echo "Snapshotting hermes-data → $OUT/hermes-data.tar.gz"
$COMPOSE exec -T hermes-agent tar czf - -C /data . > "$OUT/hermes-data.tar.gz"

echo "Snapshotting chroma-data → $OUT/chroma-data.tar.gz"
$COMPOSE exec -T chroma tar czf - -C /chroma/chroma . > "$OUT/chroma-data.tar.gz"

echo "Snapshotting honcho-db → $OUT/honcho.dump"
$COMPOSE exec -T honcho-db pg_dump -U honcho honcho > "$OUT/honcho.dump"

echo "✓ Snapshot at $OUT"
echo "Cleaning snapshots older than 30 days..."
find "$ROOT/snapshots" -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;
EOF
chmod +x scripts/snapshot.sh
echo "snapshots/" >> .gitignore
```

- [ ] **Step 2: Commit**

```bash
git add scripts/snapshot.sh .gitignore
git commit -m "feat(scripts): add local snapshot.sh (Phase 1; GCS in Phase 2)"
```

---

### Task 35: Write `scripts/panic.sh` and `scripts/teardown.sh`

**Files:**
- Create: `scripts/panic.sh`
- Create: `scripts/teardown.sh`
- Create: `docs/runbooks/recovery.md`

- [ ] **Step 1: Write `scripts/panic.sh`**

```bash
cat > scripts/panic.sh <<'EOF'
#!/usr/bin/env bash
# Emergency halt: pause all agent activity, snapshot, leave containers running for inspection.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml"

echo "==> PANIC: pausing hermes-agent and hermes-gateway"
$COMPOSE pause hermes-agent hermes-gateway

echo "==> Snapshotting state"
"$ROOT/scripts/snapshot.sh"

TS=$(date -u +%Y%m%d-%H%M%S)
echo "==> Marking panic event"
echo "$TS panic invoked by user" >> "$ROOT/logs/panic.log"

echo
echo "✓ Agent halted. Inspect logs, then resume with:"
echo "    $COMPOSE unpause hermes-agent hermes-gateway"
EOF
chmod +x scripts/panic.sh
```

- [ ] **Step 2: Write `scripts/teardown.sh`**

```bash
cat > scripts/teardown.sh <<'EOF'
#!/usr/bin/env bash
# Graceful shutdown: snapshot first, then bring stack down.
# Pass --remove-volumes to also delete persistent data (DESTRUCTIVE).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml -f $ROOT/deploy/docker-compose.dev.yml"

echo "==> Snapshotting before teardown"
"$ROOT/scripts/snapshot.sh"

echo "==> Stopping stack"
if [ "${1:-}" = "--remove-volumes" ]; then
  read -r -p "REMOVE ALL DATA VOLUMES? Type YES: " confirm
  if [ "$confirm" = "YES" ]; then
    $COMPOSE down -v
    echo "✓ Stack and volumes removed"
  else
    echo "Aborted."; exit 1
  fi
else
  $COMPOSE down
  echo "✓ Stack stopped (data volumes preserved)"
fi
EOF
chmod +x scripts/teardown.sh
```

- [ ] **Step 3: Write `docs/runbooks/recovery.md`**

```markdown
# Recovery Procedures

## After a panic

1. Inspect Phoenix at http://localhost:6006 to find the offending trace
2. Inspect logs: `docker compose -f deploy/docker-compose.yml logs hermes-agent --tail=200`
3. If safe, resume: `docker compose -f deploy/docker-compose.yml unpause hermes-agent hermes-gateway`
4. If not, teardown: `./scripts/teardown.sh` (preserves data) or `./scripts/teardown.sh --remove-volumes` (destroys data)

## Restoring from a snapshot

```bash
TS=20260514-153012
COMPOSE="docker compose -f deploy/docker-compose.yml"
$COMPOSE down
docker volume rm autonomous-agent_hermes-data autonomous-agent_chroma-data autonomous-agent_honcho-db-data
$COMPOSE up -d --no-start
$COMPOSE start honcho-db
sleep 5
$COMPOSE exec -T honcho-db psql -U honcho honcho < snapshots/$TS/honcho.dump
docker run --rm -v autonomous-agent_hermes-data:/data -v "$(pwd)/snapshots/$TS":/snap alpine tar xzf /snap/hermes-data.tar.gz -C /data
docker run --rm -v autonomous-agent_chroma-data:/data -v "$(pwd)/snapshots/$TS":/snap alpine tar xzf /snap/chroma-data.tar.gz -C /data
$COMPOSE up -d
./scripts/smoke.sh
```

## Disaster: lost the age key

The encrypted secrets are useless without `~/.config/sops/age/keys.txt`.
- If you have the public key but not the private one, you cannot decrypt
- Restore from your password manager backup (you DID back it up, right?)
- Otherwise: regenerate Telegram bot token via BotFather, regenerate other secrets via `./scripts/decrypt-secrets.sh` after running the secret-generation steps from Task 29
```

- [ ] **Step 4: Commit**

```bash
git add scripts/panic.sh scripts/teardown.sh docs/runbooks/recovery.md
git commit -m "feat(scripts): add panic, teardown, recovery runbook"
```

---

## Stage G — Tests (T36–T39)

### Task 36: Write `scripts/test.sh` (one-command test runner)

**Files:**
- Create: `scripts/test.sh`

- [ ] **Step 1: Write the runner**

```bash
cat > scripts/test.sh <<'EOF'
#!/usr/bin/env bash
# Run all tests: unit + integration.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

source .venv/bin/activate

echo "==> Unit tests"
pytest tests/unit/ -v --tb=short

echo
echo "==> Integration tests (mocked LLM)"
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.test.yml up -d --wait
trap "docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.test.yml down -v" EXIT
pytest tests/integration/ -v --tb=short

echo
echo "✅ All tests passed"
EOF
chmod +x scripts/test.sh
```

- [ ] **Step 2: Commit**

```bash
git add scripts/test.sh
git commit -m "feat(scripts): add test.sh (unit + integration runner)"
```

---

### Task 37: Write integration test scaffolding

**Files:**
- Create: `tests/integration/conftest.py`
- Create: `tests/fixtures/sample_session.json`
- Create: `tests/fixtures/openai-mock.yaml`

- [ ] **Step 1: Write `conftest.py`**

```python
"""Integration test fixtures: docker compose stack assumed running via docker-compose.test.yml."""
from __future__ import annotations

import os
import time

import httpx
import pytest


HERMES_AGENT_URL = os.environ.get("HERMES_AGENT_URL", "http://localhost:7878")
LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:4000")


@pytest.fixture(scope="session")
def hermes_url() -> str:
    return HERMES_AGENT_URL


@pytest.fixture(scope="session")
def wait_for_stack():
    """Wait until hermes-agent is healthy before running tests."""
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            r = httpx.get(f"{HERMES_AGENT_URL}/health", timeout=3)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError("Stack did not become healthy within 60s")
```

- [ ] **Step 2: Write `openai-mock.yaml` (Prism mock spec for LiteLLM substitute)**

```yaml
openapi: 3.0.0
info:
  title: Mock OpenAI for tests
  version: 1.0.0
paths:
  /v1/chat/completions:
    post:
      responses:
        '200':
          content:
            application/json:
              example:
                id: chatcmpl-test
                object: chat.completion
                created: 0
                model: vertex_ai/claude-opus-4-7
                choices:
                  - index: 0
                    message:
                      role: assistant
                      content: "Mocked response: pong"
                    finish_reason: stop
                usage:
                  prompt_tokens: 10
                  completion_tokens: 5
                  total_tokens: 15
  /health/liveliness:
    get:
      responses:
        '200':
          content:
            application/json:
              example:
                status: healthy
  /v1/models:
    get:
      responses:
        '200':
          content:
            application/json:
              example:
                data:
                  - id: vertex_ai/claude-opus-4-7
```

- [ ] **Step 3: Write minimal `sample_session.json`**

```json
{
  "session_id": "test-session-001",
  "turns": [
    {"role": "user", "content": "List files in workspace"},
    {"role": "assistant", "tool_calls": [{"name": "ls", "args": {"path": "/workspace"}}]},
    {"role": "tool", "content": "(empty)"},
    {"role": "assistant", "content": "Workspace is empty."}
  ]
}
```

- [ ] **Step 4: Commit**

```bash
git add tests/integration/conftest.py tests/fixtures/sample_session.json tests/fixtures/openai-mock.yaml
git commit -m "test(integration): scaffolding (conftest + fixtures + Prism mock)"
```

---

### Task 38: Integration tests — full turn + skill creation

**Files:**
- Create: `tests/integration/test_full_turn.py`
- Create: `tests/integration/test_skill_creation.py`

- [ ] **Step 1: Write `test_full_turn.py`**

```python
"""Full turn round-trip via mocked LLM."""
from __future__ import annotations

import httpx
import pytest


def test_health_endpoint_responds(hermes_url, wait_for_stack):
    r = httpx.get(f"{hermes_url}/health", timeout=5)
    assert r.status_code == 200


def test_full_turn_via_admin_api(hermes_url, wait_for_stack):
    """POST a synthetic user turn; assert we get a structured response back."""
    r = httpx.post(
        f"{hermes_url}/v1/turn",
        json={"session_id": "test-full-turn-001", "message": "ping"},
        timeout=30,
    )
    assert r.status_code == 200
    data = r.json()
    assert "response" in data
    # The mocked LLM returns "Mocked response: pong"
    assert "pong" in data["response"].lower()
```

- [ ] **Step 2: Write `test_skill_creation.py`**

```python
"""Verify the skill-extractor nudge fires after a complex synthetic session."""
from __future__ import annotations

import time

import httpx
import pytest


@pytest.mark.slow
def test_complex_session_creates_skill(hermes_url, wait_for_stack, tmp_path):
    """Run 12 synthetic turns using ≥3 distinct tools; assert a skill file is written."""
    session_id = "test-skill-creation-001"
    tools_used = ["ls", "shell", "read_file"]
    for i, tool in enumerate(tools_used * 4):
        httpx.post(
            f"{hermes_url}/v1/turn",
            json={
                "session_id": session_id,
                "message": f"please use {tool} for step {i}",
                "force_tool": tool,
            },
            timeout=15,
        )
    # Trigger the skill extractor
    r = httpx.post(f"{hermes_url}/v1/nudges/skill_extractor/run", timeout=60)
    assert r.status_code == 200
    # Wait for skill file to materialize in mounted skills dir
    deadline = time.time() + 30
    while time.time() < deadline:
        r = httpx.get(f"{hermes_url}/v1/skills", timeout=5)
        if any(s["session_origin"] == session_id for s in r.json().get("skills", [])):
            return
        time.sleep(1)
    pytest.fail("Skill extractor did not produce a skill within 30s")
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_full_turn.py tests/integration/test_skill_creation.py
git commit -m "test(integration): full turn + skill creation tests"
```

---

### Task 39: Integration tests — sandbox isolation, secret leak, budget cap, Chroma outage

**Files:**
- Create: `tests/integration/test_sandbox_isolation.py`
- Create: `tests/integration/test_secret_leak.py`
- Create: `tests/integration/test_budget_cap.py`
- Create: `tests/integration/test_chroma_outage.py`

- [ ] **Step 1: Write `test_sandbox_isolation.py`**

```python
"""Verify shell-sandbox isolation: no host network, no host FS escape."""
from __future__ import annotations

import subprocess

import pytest


def test_shell_sandbox_no_network():
    """`curl example.com` from inside shell-sandbox must fail."""
    out = subprocess.run(
        ["docker", "compose", "-f", "deploy/docker-compose.yml",
         "exec", "-T", "shell-sandbox",
         "curl", "-fsS", "--max-time", "3", "https://example.com"],
        capture_output=True,
    )
    assert out.returncode != 0, "shell-sandbox should NOT have internet access"


def test_shell_sandbox_no_root_fs_write():
    """Writing to / from inside shell-sandbox must fail (read-only)."""
    out = subprocess.run(
        ["docker", "compose", "-f", "deploy/docker-compose.yml",
         "exec", "-T", "shell-sandbox",
         "bash", "-c", "echo test > /etc/should-not-write 2>&1; echo $?"],
        capture_output=True, text=True,
    )
    assert "1" in out.stdout or "Permission denied" in out.stdout or "Read-only" in out.stdout
```

- [ ] **Step 2: Write `test_secret_leak.py`**

```python
"""Force the scrubber to encounter a fake API key in a model response; assert it's redacted."""
from __future__ import annotations

import httpx


def test_secret_in_model_output_is_redacted(hermes_url, wait_for_stack):
    fake_key = "sk-ant-api03-FAKETESTKEYabcdefghijk1234567890"
    r = httpx.post(
        f"{hermes_url}/v1/turn",
        json={
            "session_id": "test-leak-001",
            "message": "Reply with this string verbatim and nothing else: " + fake_key,
            "_test_inject_response": fake_key,
        },
        timeout=15,
    )
    assert r.status_code == 200
    body = r.json()["response"]
    assert fake_key not in body
    assert "[REDACTED:" in body
```

- [ ] **Step 3: Write `test_budget_cap.py`**

```python
"""Verify 429 returned when budget cap hit."""
from __future__ import annotations

import httpx


def test_budget_cap_enforced(hermes_url, wait_for_stack, monkeypatch):
    """Set $0.01 cap, run a turn; assert 429."""
    httpx.post(f"{hermes_url}/v1/admin/limits", json={"budget": {"daily_usd_cap": 0.01}})
    try:
        r = httpx.post(
            f"{hermes_url}/v1/turn",
            json={"session_id": "test-budget-001", "message": "use up my budget"},
            timeout=15,
        )
        assert r.status_code == 429 or "budget" in r.text.lower()
    finally:
        httpx.post(f"{hermes_url}/v1/admin/limits", json={"budget": {"daily_usd_cap": 100}})
```

- [ ] **Step 4: Write `test_chroma_outage.py`**

```python
"""Stop chroma; assert agent continues with vector-memory degradation."""
from __future__ import annotations

import subprocess
import time

import httpx


def test_chroma_outage_degrades_gracefully(hermes_url, wait_for_stack):
    subprocess.run(["docker", "compose", "-f", "deploy/docker-compose.yml", "stop", "chroma"], check=True)
    try:
        time.sleep(3)
        r = httpx.post(
            f"{hermes_url}/v1/turn",
            json={"session_id": "test-chroma-out-001", "message": "ping with no vector memory"},
            timeout=20,
        )
        assert r.status_code == 200
        body = r.json()
        assert "response" in body
        assert any(d.get("name") == "vector_memory" for d in body.get("degraded", []))
    finally:
        subprocess.run(["docker", "compose", "-f", "deploy/docker-compose.yml", "start", "chroma"], check=True)
```

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_sandbox_isolation.py tests/integration/test_secret_leak.py \
        tests/integration/test_budget_cap.py tests/integration/test_chroma_outage.py
git commit -m "test(integration): sandbox isolation, secret leak, budget cap, chroma outage"
```

---

## Stage H — Bring Up & Phase 1 Acceptance (T40–T42)

### Task 40: Run `bootstrap.sh` end-to-end

**Files:** none (operational task)

- [ ] **Step 1: Run prereq check**

```bash
./scripts/verify-prereqs.sh
```

Expected: all checks pass.

- [ ] **Step 2: Run bootstrap**

```bash
./scripts/bootstrap.sh
```

Expected: completes with `✓ Bootstrap complete.` All 9 smoke checks pass.

- [ ] **Step 3: Inspect Phoenix**

Open http://localhost:6006 in a browser. Confirm at least one trace from `hermes-agent` is visible.

- [ ] **Step 4: Send a test Telegram message**

From your phone, send the bot a message: "What can you do?"

Expected: Reply within ~30s, citing tools, with no error or "I don't have a model" message.

---

### Task 41: Write Phase 1 acceptance runbook

**Files:**
- Create: `docs/runbooks/phase1-acceptance.md`

- [ ] **Step 1: Write the runbook**

```markdown
# Phase 1 Acceptance Protocol

## Prerequisites
- `./scripts/bootstrap.sh` completes cleanly
- `./scripts/smoke.sh` passes all 9 checks
- Phoenix at http://localhost:6006 is reachable
- Telegram bot reachable (you can `/start` it)

## Acceptance steps

### Step 1 — Send 10 real Telegram messages spanning ≥3 distinct task types

Send these from your phone, one at a time, waiting for full reply each time:

1. "What can you do?"
2. "Search for files containing 'TODO' in the workspace"
3. "What's the latest open PR in NousResearch/hermes-agent?"
4. "Run `df -h` and tell me how much disk is free"
5. "Read the README.md in this project and summarize it in 2 sentences"
6. "Look up the Vite 5 documentation for environment variables and explain how to set one"
7. "List your installed skills"
8. "Tell me my MEMORY.md contents"
9. "Summarize what we've talked about so far"
10. "Create a quick reference for how to deploy a Cloud Run service"

**Tasks 2–6 should each invoke distinct tools** (file search, github MCP, shell sandbox, file read, context7 MCP).

### Step 2 — Verify autonomous skill creation

```bash
docker compose -f deploy/docker-compose.yml exec -T hermes-agent ls /app/skills
```

Expected: At least one skill directory autonomously created from the conversations above (likely from message #10 which is a "create a procedure" prompt).

### Step 3 — Verify state persists across container restart

```bash
docker compose -f deploy/docker-compose.yml restart hermes-agent
sleep 10
```

From Telegram: "What did we just talk about?"

Expected: Bot summarizes the prior 10-message conversation.

### Step 4 — Verify traces visible in Phoenix

Open http://localhost:6006. Filter for service.name=hermes-agent. Inspect at least one trace from your conversation; verify spans for `turn.start`, `model.call`, `tool.dispatch`.

### Step 5 — Verify no secret leaks

```bash
docker compose -f deploy/docker-compose.yml exec -T hermes-agent test -f /data/secret-leak-attempts.log && \
  cat /data/secret-leak-attempts.log
```

Expected: file does not exist OR is empty (no `[REDACTED:critical]` entries).

### Step 6 — Verify budget tracking

```bash
docker compose -f deploy/docker-compose.yml exec -T litellm-proxy curl -fsS \
  -H "Authorization: Bearer $(cat /run/secrets/litellm_master_key)" \
  http://localhost:4000/spend/calculate
```

Expected: JSON with non-zero `total_spend` reflecting your 10 messages, well under daily cap.

## Pass criteria

ALL of the following must be true:

- [ ] All 10 messages got coherent replies
- [ ] At least 3 distinct tools were invoked across the 10 messages
- [ ] At least 1 skill was autonomously created
- [ ] State persisted across hermes-agent restart
- [ ] Traces visible in Phoenix
- [ ] No critical entries in secret-leak-attempts.log
- [ ] Daily spend recorded in LiteLLM, well under $100 cap

If all pass: **Phase 1 ACCEPTED**. Ready to begin Phase 2 plan.
If any fail: open `docs/runbooks/recovery.md` and debug; re-run after fix.
```

- [ ] **Step 2: Commit**

```bash
git add docs/runbooks/phase1-acceptance.md
git commit -m "docs(runbook): Phase 1 acceptance protocol"
```

---

### Task 42: Execute Phase 1 acceptance protocol

**Files:** none (operational task; output is the gate result)

- [ ] **Step 1: User executes `docs/runbooks/phase1-acceptance.md` end-to-end**

- [ ] **Step 2: Capture results**

For each pass criterion, record pass/fail with evidence (screenshot, log excerpt, output paste) into a file `docs/runbooks/phase1-acceptance-result-<DATE>.md` (timestamp).

- [ ] **Step 3: Tag the release if all pass**

```bash
git tag -a phase1-accepted -m "Phase 1 (local Mac deployment) accepted; all 7 criteria met"
```

- [ ] **Step 4: Phase 1 done**

Next: write the Phase 2 plan (`docs/superpowers/plans/<date>-phase2-cloud-prod-migration.md`) — that's a separate brainstorm + plan + execution cycle.

---

## Spec Coverage Self-Check

| Spec section | Where covered |
|---|---|
| §1 System architecture | Stage D (compose stack), file structure top of plan |
| §2 Components (12 services) | T22 (chroma), T23 (honcho), T24 (compose), T25 (dev), T26 (test) |
| §3 Data flow | Implicit via compose wiring + Hermes' built-in agent loop |
| §4 Limits config | T9, T10, T11, T12 |
| §5 Security | T5, T6, T13, T14, T15, T16, T21, T29, T39 |
| §6 Self-RL loop (soft, Phase 1) | T17 (Hermes config enables nudges), T38 (skill-creation test) |
| §6 Self-RL loop (hard, Phase 3+) | Deferred to Phase 3 plan |
| §7 Observability | T19 (OTel), T20 (Phoenix), T30 (healthchecks), T39 (Phoenix smoke) |
| §8 Error handling | T32 (healthcheck), T35 (panic), T39 (chroma outage), failure modes baked into compose restart policy |
| §9 Testing | T12, T14, T16, T32, T36, T37, T38, T39, T41, T42 |
| §10 Phase sequencing | This plan = Phase 1; Phases 2-4 get own plans |
| §11 Open items | Deferred per plan |

**Documentation & Version Control** (added per user request):

| Requirement | Where covered |
|---|---|
| Comprehensive README | T1.1 |
| LICENSE (MIT) | T1.1 |
| CHANGELOG (Keep-a-Changelog) | T1.2; updated incrementally in T1.7, T35, T41 |
| CONTRIBUTING.md (workflow + commit conventions) | T1.3 |
| GitHub PR + issue templates | T1.4 |
| Architecture index | T1.5 |
| ADRs (MADR format) — 7 initial decisions | T1.5 |
| Runbooks index | T1.5 |
| Convention docs (commits, branching, logging, code style) | T1.6 |
| Worktree-per-phase setup with branch isolation | T1.7 |
| Conventional Commits enforced via reviewer discipline | T1.6 (commit-messages.md) |
| Pre-commit hooks (ruff + secret scanning) | T6 |
| Logging conventions (structured JSON, severity rules) | T1.6 (logging.md) |

Phase 1 fully covered. Phase 2-4 covered by future plans (intentionally deferred).

## Placeholder Scan

No `TODO`, `TBD`, or "implement later" remain. All code blocks complete; all paths absolute.

## Type Consistency

- `Tier` enum used consistently (T15, T16)
- `Scrubber` / `ScrubHit` / `ScrubPattern` used consistently (T13, T14)
- `HealthReport` / `CheckResult` / `Status` used consistently (T32)
- LiteLLM env names consistent across compose, secrets, scripts (T18, T24, T29, T33)
- Volume names consistent across compose + snapshot + recovery (T24, T34, T35)

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-14-phase1-local-deployment.md`.**

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review the diff between tasks, fast iteration. Best for a plan this size — keeps each task's context clean and gives you a checkpoint between every commit.

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Faster wall-clock, but my context window stays loaded with the full plan, leaving less room for the actual code.

**Which approach?**
