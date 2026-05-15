# AutonomousAgent

> Production deployment of [Hermes Agent](https://github.com/NousResearch/hermes-agent) — a self-improving AI agent built by Nous Research — wrapped with tiered sandboxing, OpenTelemetry observability, sops-encrypted secrets, and a phased path to a Vertex AI–backed cloud deployment + Atropos RL training pipeline.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Phase](https://img.shields.io/badge/Phase-1%20(local)-blue.svg)](docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md)
[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-green.svg)](docs/conventions/commit-messages.md)
[![CI](https://github.com/Manzela/AutonomousAgent/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Manzela/AutonomousAgent/actions/workflows/ci.yml)
[![Secret Scan](https://github.com/Manzela/AutonomousAgent/actions/workflows/secret-scan.yml/badge.svg?branch=main)](https://github.com/Manzela/AutonomousAgent/actions/workflows/secret-scan.yml)
[![Built with Hermes Agent](https://img.shields.io/badge/Built%20on-Hermes%20Agent-blueviolet.svg)](https://github.com/NousResearch/hermes-agent)

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

## Workflow & Branching

Each phase lives on its own long-running branch checked out under `.worktrees/`:

```
main                           ← branch: main      (only accepted, tagged work)
.worktrees/phase1/             ← branch: phase/1   (Phase 1 development)
.worktrees/phase2/             ← branch: phase/2   (created when Phase 2 starts)
.worktrees/phase3/             ← branch: phase/3
.worktrees/phase4/             ← branch: phase/4
```

`main` only contains commits that have passed an acceptance gate. Phase work merges into `main` via `--no-ff` + a `phaseN-accepted` tag. Hotfixes branch from `main` and cherry-pick to active phase branches.

Full details: [docs/conventions/branching.md](docs/conventions/branching.md), [ADR 0007](docs/decisions/0007-worktree-per-phase-branching.md).

## CI/CD

We deliberately keep the workflow surface minimal to conserve GitHub Actions minutes. Every PR runs:

| Check | Tool | Config |
|---|---|---|
| Python lint + format | `ruff` + `ruff-format` | `pyproject.toml` |
| Shell lint | `shellcheck` | strict mode + quoting |
| YAML lint | `yamllint` | [.yamllint.yml](.yamllint.yml) |
| Dockerfile lint | `hadolint` | advisory baseline |
| Unit tests | `pytest` | `tests/unit/` |
| Config validation | `lib/limits_validator` | `config/limits-schema.json` |
| Compose render | `docker compose config` | `deploy/docker-compose.yml` |
| PR title format | Conventional Commits | [.github/workflows/pr-validation.yml](.github/workflows/pr-validation.yml) |
| Branch name format | regex against allowed patterns | same |
| Secret scan | `gitleaks` + `detect-secrets` | [.gitleaks.toml](.gitleaks.toml) + [.secrets.baseline](.secrets.baseline) |

All 11 are required-status-checks for merging into `main` (branch protection enforces).

Plus:

| Workflow | Trigger | Purpose |
|---|---|---|
| Secret scan | 1st of each month | Catches drift between PRs |
| Dependabot | monthly grouped PRs | Action / pip / Docker base image updates |
| Release notes | on `v*` or `phaseN-accepted` tag push | Auto-generated from Conventional Commits |

**Removed to save Actions minutes** (with notes): CodeQL (no Python on `main` until Phase 1 merges; will be reintroduced then); Dependency Review (requires GitHub Advanced Security on private repos to be effective); Markdown lint (advisory only); duplicate `push: main` triggers (PRs already cover the path); weekly cadence reduced to monthly for both Dependabot and the secret-scan schedule.

Workflow definitions: [.github/workflows/](.github/workflows/).

## Security

- All secrets sops-encrypted at rest with age recipients
- Never commit plaintext secrets — pre-commit blocks the obvious patterns; `detect-secrets` baseline + `gitleaks` in CI catch the rest
- Output secret scrubbing on every model response and tool result
- Approval gates for destructive operations (Telegram inline keyboard)
- `panic` mode halts everything immediately; see [docs/runbooks/recovery.md](docs/runbooks/recovery.md)
- Egress allowlist prevents arbitrary outbound traffic

Vulnerability disclosure: see [SECURITY.md](SECURITY.md). Do **not** open public issues for security vulnerabilities.

## Reference Documentation

| Document | Purpose |
|---|---|
| [Architecture spec](docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md) | The complete design (12 sections) |
| [Phase 1 plan](docs/superpowers/plans/2026-05-14-phase1-local-deployment.md) | ~50-task implementation plan for Phase 1 |
| [Session-complete artifact](docs/superpowers/specs/SESSION-COMPLETE-2026-05-15-hermes-agent-full-architecture.md) | Resume-from-cold reference; survives context loss |
| [Architecture index](docs/architecture/README.md) | Reading order for newcomers |
| [Architecture Decision Records](docs/decisions/) | Point-in-time decisions and tradeoffs (MADR format) |
| [Conventions](docs/conventions/) | Commits, branching, logging, code style |
| [Runbooks](docs/runbooks/) | Operational procedures (acceptance, recovery, setup) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to work in this repo |
| [CHANGELOG.md](CHANGELOG.md) | All notable changes |
| [SECURITY.md](SECURITY.md) | Vulnerability disclosure policy |

## License

MIT — see [LICENSE](LICENSE). Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) (also MIT) by Nous Research.
