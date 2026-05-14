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
- Worktree-per-phase branching: `phase/1` branch created from `main`, checked out at `.worktrees/phase1/`. See [ADR 0007](docs/decisions/0007-worktree-per-phase-branching.md) and [docs/conventions/branching.md](docs/conventions/branching.md).

### Notes
- All entries above are part of Phase 1; nothing has yet been merged to `main` until Phase 1 acceptance passes.
- See [docs/superpowers/plans/2026-05-14-phase1-local-deployment.md](docs/superpowers/plans/2026-05-14-phase1-local-deployment.md) for the implementation plan that produces these changes.

[Unreleased]: https://github.com/<owner>/AutonomousAgent/compare/v0.0.0...HEAD
