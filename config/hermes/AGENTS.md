# Agent Project Context

This Hermes Agent runs on the AutonomousAgent project, deployed locally via docker-compose.

## Working directory

The shell sandbox mounts `workspace` as `/workspace`. Treat that as the persistent project workspace.

## Tools

You have access to:
- **File reads** (in-process, host FS read-only mounts)
- **Shell commands** via the `shell-sandbox` Docker container (`--cap-drop=ALL --network=none --read-only`; only `/workspace` is writable)
- **GitHub MCP** via `github-mcp` sidecar (HTTP, port 8003) — authenticated via PAT with `repo` + `workflow` + `read:org` + `security_events` scopes. ALL toolsets enabled: actions, code_security, copilot, dependabot, discussions, gists, git, issues, labels, notifications, orgs, projects, pull_requests, repos, secret_protection, security_advisories, stargazers, users.
- **Context7 MCP** for live library/framework documentation
- LLM access: routed through LiteLLM proxy → Vertex AI Anthropic Claude (Opus 4.7 default; Sonnet 4.6 fallback; Gemini 3.1 Pro for long-context once mesh ships)

For arbitrary code execution beyond shell, ask first — we may route to a cloud sandbox.

## CRITICAL: When asked to create a new GitHub repository or build a new project end-to-end

You MUST consult `~/.hermes/new-repo-template.md` BEFORE taking any action. It is the canonical SDLC + security playbook for this user. It defines:
- Repository creation defaults (visibility, branch protection, merge methods, vulnerability alerts)
- Initial file scaffold (16 root + nested files including README, CHANGELOG, CONTRIBUTING, SECURITY, docs/, .github/)
- Branching model (main + phase/N + worktrees + Conventional Commits)
- 5 mandatory CI/CD workflows + Dependabot pattern
- Branch protection / rulesets per Pro vs free tier
- sops + age secret management discipline
- ADR (Architecture Decision Records) practice
- Self-test checklist before declaring "ready"

This file is mounted at `/root/.hermes/new-repo-template.md` inside this container; you can also reference it as `docs/conventions/new-repo-template.md` in the AutonomousAgent project root if you have host-FS access.

DO NOT skip steps. DO NOT use placeholder content. The template specifies real, production-grade conventions because the user is operating at production scale (500M+ tokens / 3 days, multi-week unattended autonomous runs).

When in doubt about a specific file's format, mirror the equivalent in the AutonomousAgent project itself — that's the canonical reference implementation.

## Conventions for ALL work (not just new-repo creation)

- **Conventional Commits** for every commit you make: `<type>(<scope>): <subject>` where type ∈ {feat, fix, chore, docs, refactor, test, perf, security, build, ci, revert}
- **Small, focused commits** — one logical change per commit
- **Prefer editing existing files** over creating new ones
- **Run tests before declaring work complete** — never claim success without verification evidence
- **Follow the patterns in CLAUDE.md / AGENTS.md** of the project you're working in
- **Capture architectural decisions as ADRs** (`docs/decisions/NNNN-<title>.md`) when you make a tradeoff that's hard to reverse
- **Never commit secrets in plaintext** — always sops-encrypt
- **Never use `latest` Docker tags in production** — pin specific versions
- **Failure handling**: classify into fail-loud (alert+halt), fail-soft (degrade+continue+log), or self-heal (retry with exponential backoff). Never silently retry forever.
