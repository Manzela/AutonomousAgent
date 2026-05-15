# Changelog

All notable changes to AutonomousAgent are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

> **How this is maintained.** Every PR that adds or changes user-visible behavior also
> appends a bullet under `[Unreleased]` in the relevant section. Release tags
> (`v0.1.0`, `phase1-accepted`, …) cut the unreleased block into a dated section.
> See [docs/conventions/pull-requests.md](docs/conventions/pull-requests.md) for the PR lifecycle.

## [Unreleased]

### Added — SDLC + parallel-session documentation
- [docs/superpowers/session-coordination.md](docs/superpowers/session-coordination.md) — parallel-session model (Session A/B tracks, dispatch, conflict-prevention conventions, session-coordination ledger format)
- [docs/conventions/pull-requests.md](docs/conventions/pull-requests.md) — PR lifecycle (when to rebase, when to merge from main, retitle conventions, stacked-PR pattern, dependency-update auto-merge policy)
- [docs/ci-cd.md](docs/ci-cd.md) — CI/CD deep-dive (each workflow's purpose, required-status-check matrix, branch-protection rules, local-reproduction instructions for every check)
- [docs/release-process.md](docs/release-process.md) — SemVer policy, tag-driven release flow, when to publish vs `phaseN-accepted` tagging
- This `[Unreleased]` section now uses the new sub-section structure (Added / Changed / Fixed / Removed / Security / Documentation) per Keep-a-Changelog 1.1.0.

### Fixed
- CI baseline failures blocking all open PR merges (#17): `tests/unit/test_scrubber.py` not ruff-formatted, `decrypt-secrets.sh:46` SC2155, `smoke.sh:12` SC2034 unused `COMPOSE` array, `secrets/{chroma-cloud,hermes-provider}.env` and `secrets/github-pat` not stubbed for compose validation

### In flight (open PRs against `main`)

Tracked here so contributors can see where the bleeding edge is without opening GitHub.

| PR | Track | Adds | Status |
|---|---|---|---|
| #10 | Session B / P1-2 Task 15 | `scripts/smoke.sh` Gemini 3.1 Pro round-trip check (8th smoke check) | needs rebase + retitle |
| #11 | Session B / P1-2 Task 16 | `lib/evaluators/judge.py` — single-judge dispatch, 4 axes, JSON-strict parser with `unsure` fallback | needs rebase + retitle |
| #12 | Session B / P1-2 Task 17 (stacks on #11) | `lib/evaluators/consensus.py` — 4-judge majority + 5th-judge tiebreak | will retarget to `main` after #11 |
| #13 | Session B / P1-2 Task 19 | `evaluate_after: <bool>` in `config/toolsets.yaml` (gates judge-panel dispatch on cheap reads) | needs rebase + retitle |
| #14 | Session B / P1-2 Task 18 | `lib/evaluators/orchestrator_hook.py` — `PER_AXIS_MODEL` routing constant + per-session feedback queue | needs rebase + retitle |
| #15 | Session A / P1-1 Task 5 | `lib/anchors/__init__.py` — Hermes plugin `register(ctx)` (2 hooks, 4 slash commands, 1 CLI subcommand; handler bodies stubbed for Task 6) | needs rebase + retitle |

See [docs/conventions/pull-requests.md](docs/conventions/pull-requests.md) for what "rebase + retitle" means and why it's needed here.

## [0.0.1-phase1.merge] — 2026-05-15

The Phase/1 branch was merged to `main` as commit [`0f74412`](https://github.com/Manzela/AutonomousAgent/commit/0f74412) (PR #6). This section captures everything that landed in that merge.

### Added — project skeleton
- `.gitignore`, `.gitattributes`, `README.md` initial skeleton
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

### Added — Phase 1 runtime
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
- Phase 1 acceptance runbook ([docs/runbooks/phase1-acceptance.md](docs/runbooks/phase1-acceptance.md))
- Telegram bot setup runbook ([docs/runbooks/telegram-bot-setup.md](docs/runbooks/telegram-bot-setup.md))
- Recovery runbook ([docs/runbooks/recovery.md](docs/runbooks/recovery.md))
- Architecture spec, Phase 1 plan, and session-complete artifact under `docs/superpowers/specs/` and `docs/superpowers/plans/`

### Added — CI/CD + SDLC infrastructure
- GitHub Actions workflows under `.github/workflows/` (deliberately minimal to conserve Actions minutes):
  - `ci.yml` — Python lint (ruff/format), shell lint (shellcheck), YAML lint (yamllint), Dockerfile lint (hadolint), unit tests (pytest), config validation (limits.yaml schema), compose render validation
  - `secret-scan.yml` — gitleaks (full history) + detect-secrets (baseline diff), per-PR + monthly schedule
  - `pr-validation.yml` — Conventional Commits PR title + branch name pattern enforcement
  - `release.yml` — auto-generated release notes on `v*` or `phase*-accepted` tag push
- `.github/dependabot.yml` — monthly grouped dependency updates for GitHub Actions, Python (pip), and Docker base images
- `.github/CODEOWNERS` — automatic reviewer assignment
- `SECURITY.md` — vulnerability disclosure policy with severity tiers and response timelines
- `.gitleaks.toml` — gitleaks configuration with project-specific allowlist
- `.yamllint.yml` — yamllint configuration (2-space indent, 200-col line length, common project conventions)
- `.markdownlint.jsonc` — markdownlint-cli2 configuration tuned for our docs style
- README badges for CI / Secret Scan status, plus expanded sections for Workflow & Branching, CI/CD checklist, Security policy, and a Reference Documentation index

### Changed
- CI workflow surface intentionally minimized in commit [`43efea4`](https://github.com/Manzela/AutonomousAgent/commit/43efea4) to conserve account-wide GitHub Actions minutes — markdownlint, CodeQL, and Dependency Review removed (advisory-only or require Advanced Security on private repos); duplicate `push: main` triggers dropped (PRs already cover the path); weekly cadence reduced to monthly for both Dependabot and the secret-scan schedule. Documented inline in `.github/workflows/ci.yml` header.

### Fixed
- `fix(ci): replace gitleaks-action with direct CLI invocation` — commit [`9901462`](https://github.com/Manzela/AutonomousAgent/commit/9901462), so secret-scan runs without the `gitleaks-action` GHA dependency.

### Dependency updates merged
- `chore(ci)(deps): bump amannn/action-semantic-pull-request from 5 to 6` (#5)
- `chore(ci)(deps): bump actions/setup-python from 5 to 6` (#4)
- `chore(ci)(deps): bump actions/checkout from 4 to 6` (#2)
- `chore(ci)(deps): bump hadolint/hadolint-action` (#1)

[Unreleased]: https://github.com/Manzela/AutonomousAgent/compare/0f74412...HEAD
[0.0.1-phase1.merge]: https://github.com/Manzela/AutonomousAgent/commit/0f74412
