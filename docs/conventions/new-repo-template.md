# New Repository Template — SDLC + Security Conventions

> **For the agent**: when the user asks you to create a new GitHub repository for an end-to-end project, you MUST apply every section below in order. This is the standard the AutonomousAgent project itself uses (see [our own ADRs](../decisions/) for rationale). Do not deviate without explicit user approval and an ADR documenting the reason.

This document is your **canonical playbook**. Read it once at the start of any new-project task; reference it for every decision.

---

## 1. Repository creation

Use the `github-mcp` tool to create the repo. Defaults that MUST be applied:

| Setting | Value | Why |
|---|---|---|
| Visibility | **Private** (unless user explicitly asks for public) | Default-deny; user explicitly opts into public exposure |
| Default branch | `main` | Industry standard |
| Allow merge commit | **No** | Squash + rebase only — keeps `main` history linear |
| Allow squash merge | **Yes** | Preferred for PR merges |
| Allow rebase merge | **Yes** | For trivial fast-forwards |
| Auto-merge | **Enabled** | Lets Dependabot PRs land after green CI without manual intervention |
| Delete branch on merge | **Enabled** | Keeps branch list clean |
| Issues | **Enabled** | We use them |
| Wiki | **Disabled** | Use `docs/` in repo instead |
| Projects | **Disabled** | Use Hermes Kanban instead |
| Discussions | **Disabled unless user asks** | Lightweight |
| Topics | Add 5–10 relevant topics | Discoverability |
| Description | One sentence describing what the project does | First-impression matters |

After creation:
- Configure **vulnerability alerts**: `PUT /repos/{owner}/{repo}/vulnerability-alerts`
- Configure **automated security fixes**: `PUT /repos/{owner}/{repo}/automated-security-fixes`
- (We do this in our own repo at https://github.com/Manzela/AutonomousAgent — the screenshots in audit/ confirm.)

---

## 2. Initial file scaffold (commit 1 of every new repo)

Every new repo starts with these files, following the AutonomousAgent pattern:

```
.gitignore                 # secrets, build artifacts, OS files, editor files
.gitattributes             # text=auto eol=lf; binary markers
README.md                  # status badges + project intent + quickstart + reference docs index
LICENSE                    # MIT default unless user specifies otherwise
CHANGELOG.md               # Keep-a-Changelog 1.1.0 with [Unreleased] section
CONTRIBUTING.md            # workflow + commit conventions + branching + coding standards
SECURITY.md                # vulnerability disclosure policy
.github/
├── CODEOWNERS             # ownership map (use @<owner>)
├── dependabot.yml         # weekly grouped updates for relevant ecosystems
└── workflows/             # CI/CD (see §4)
docs/
├── architecture/
│   └── README.md          # architecture index
├── decisions/
│   ├── README.md          # ADR index
│   └── template.md        # MADR template
├── conventions/
│   ├── commit-messages.md # Conventional Commits
│   ├── branching.md       # branching strategy
│   ├── logging.md         # structured logging
│   └── code-style.md      # tech-stack-specific style
└── runbooks/
    └── README.md
```

Every file MUST be filled with real content (no placeholder stubs that say "TBD"). For exact templates, mirror the AutonomousAgent project's equivalents.

---

## 3. Branching model

| Branch | Lifecycle | Rules |
|---|---|---|
| `main` | Permanent. Holds only **accepted, tagged, CI-green** work | Protected. PR + CI required. Squash/rebase merges only. No force push. No deletion. |
| `phase/N` *(if multi-phase project)* | Long-running per phase | Created from `main`. Worktree-per-phase under `.worktrees/`. Merges back via `--no-ff` + tag `phaseN-accepted` after acceptance protocol passes. |
| `feat/<short-desc>` *(if simple project)* | Short-lived feature work | PR back to `main` after CI green |
| `fix/<short-desc>` | Short-lived bug fix | Same as feat |
| `docs/<short-desc>` | Doc-only changes | Same as feat |
| `hotfix/<short-desc>` | Urgent production fix | Branch from `main`; merge back to `main`; cherry-pick to active phase branches if multi-phase |
| `dependabot/...` | Auto-managed by Dependabot | Auto-merged after CI green if minor/patch |
| `chore/<short-desc>` | Maintenance | Same as feat |

**Worktrees**: when the project is multi-phase, set up `.worktrees/phaseN/` checkouts. Add `.worktrees/` to `.gitignore`. Each phase worktree is isolated. See `docs/conventions/branching.md` (mirror our own).

---

## 4. CI/CD workflows (under `.github/workflows/`)

Every new repo gets these 5 workflows minimum (based on the AutonomousAgent pattern):

### `ci.yml` (per PR + push to main)
Per-stack lint + test + validation. Includes:
- Language-specific lint + format check (e.g., `ruff` for Python, `eslint+prettier` for JS, `gofmt+golangci-lint` for Go)
- `shellcheck` if any `*.sh` exists
- `yamllint` if any `*.yml`/`*.yaml` exists
- `hadolint` if any `Dockerfile` exists
- Test runner appropriate to stack (pytest, jest, go test, etc.)
- `markdownlint-cli2` (advisory)

### `secret-scan.yml` (per PR + monthly schedule)
- `gitleaks` (full history, direct CLI invocation — NOT the action wrapper which breaks on initial pushes)
- `detect-secrets` (baseline diff, if `.secrets.baseline` exists)
- See AutonomousAgent's `.gitleaks.toml` for the canonical config

### `pr-validation.yml` (per PR)
- `amannn/action-semantic-pull-request` enforcing Conventional Commits PR title (types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `security`, `build`, `ci`, `revert`)
- Branch name regex enforcement: `^(phase/[1-9]|hotfix/[a-z0-9-]+|feat/[a-z0-9-]+|fix/[a-z0-9-]+|docs/[a-z0-9-]+|chore/[a-z0-9-]+|refactor/[a-z0-9-]+|test/[a-z0-9-]+|dependabot/.+)$`

### `release.yml` (on tag push: `v*` or `phase*-accepted`)
- Auto-generate release notes from Conventional Commits
- Use `softprops/action-gh-release@v2`

### `dependabot.yml` (in `.github/`, not `workflows/`)
- Monthly schedule (NOT weekly — burns Action minutes)
- Cover all ecosystems present: github-actions, npm/pip/cargo/go/etc., docker
- Grouped updates by dependency type

### Optional (add when warranted)
- `codeql.yml` — only if the codebase has source files in supported language
- `dependency-review.yml` — requires GHAS on private repos to enforce
- Custom workflows (deploy, e2e, etc.) — per project

**Trigger discipline**: do NOT add `push: branches: [main]` triggers — PRs are the only path to main (branch protection enforces it), so the merge-commit re-run is wasted compute. Only PR + workflow_dispatch + (for some) schedule.

---

## 5. Branch protection on `main`

Apply via `gh api -X PUT /repos/{owner}/{repo}/branches/main/protection`:

| Setting | Value |
|---|---|
| `required_status_checks.strict` | `true` |
| `required_status_checks.contexts` | The 11 context names from `ci.yml` + `pr-validation.yml` + `secret-scan.yml` |
| `enforce_admins` | `false` (so user can bypass for emergencies) |
| `required_pull_request_reviews.required_approving_review_count` | `0` (single-dev) or `1` (multi-dev) |
| `required_pull_request_reviews.dismiss_stale_reviews` | `true` |
| `required_pull_request_reviews.require_code_owner_reviews` | `true` |
| `restrictions` | `null` (no push restrictions; protection comes from required checks) |
| `allow_force_pushes.enabled` | `false` |
| `allow_deletions.enabled` | `false` |
| `required_conversation_resolution.enabled` | `true` |

For **public repos on free tier**, branch protection works. For **private repos on free tier**, branch protection is GitHub-Pro-only — fall back to **rulesets** if the user is on free tier (we did this initially in AutonomousAgent before they upgraded).

---

## 6. Conventional Commits + commit hygiene

Every commit (by you or a human contributor) follows this format:

```
<type>(<scope>): <subject — imperative, ≤72 chars, lowercase first letter, no trailing period>

<body — explains WHY, not WHAT (the diff shows what); wrap at 72 chars>

<footer — refs/breaking changes/co-authors>
```

Types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `security`, `build`, `ci`, `revert`.

When YOU (the agent) create commits, ALWAYS:
- Use Conventional Commits format
- Explain WHY in the body if non-obvious
- Co-author with the user when relevant: `Co-Authored-By: <name> <email>`

---

## 7. Secret management (sops + age)

**Never commit plaintext secrets.** Always:

1. Generate per-repo age key: `age-keygen -o ~/.config/sops/age/<repo-slug>.txt`. Back up to user's password manager (recommend they do this).
2. Add `.sops.yaml` at repo root mapping `secrets/.+` paths to that age recipient
3. Encrypt every secret: `sops -e secrets/<name> > secrets/<name>.sops`; immediately `rm secrets/<name>`
4. Add `secrets/` deny-by-default `.gitignore`: `*` + whitelist `*.sops`, `README.md`, `.gitignore`, `*.template.txt`
5. Provide `scripts/decrypt-secrets.sh` that decrypts all `*.sops` to adjacent plaintext (gitignored) at bootstrap
6. Document: which secrets exist, how to rotate each, who is the recipient

**Pre-commit hooks** (per repo): install `pre-commit` + `detect-secrets` baseline + ruff/format + standard hygiene (trailing whitespace, EOF, large file check).

**Phase 2 (cloud)**: secrets migrate from sops/age → GCP Secret Manager (or equivalent). Same encryption-at-rest pattern; different backend.

---

## 8. Architecture Decision Records (ADRs)

Every irreversible architectural decision MUST be captured as a numbered MADR file in `docs/decisions/`. Format:

```markdown
# NNNN. <Short Title>

**Status:** Accepted | Proposed | Deprecated | Superseded by [NNNN]
**Date:** YYYY-MM-DD
**Decision-makers:** <names>

## Context
What problem motivates this decision? Constraints + forces.

## Decision
"We will <X>." Concrete and unambiguous.

## Consequences
### Positive: <bullets>
### Negative: <bullets>
### Neutral: <bullets>

## Alternatives considered
### Option A: <name>
- Pros / Cons / Why rejected

### Option B: <name>
- Pros / Cons / Why rejected

## References
- <links + commits>
```

Update `docs/decisions/README.md` index every time you add an ADR.

For multi-phase projects, ADR for the phasing strategy (mirror our ADR 0006) and worktree branching (mirror our ADR 0007) are mandatory.

---

## 9. Documentation discipline

Every new repo gets:

- **README.md** — comprehensive: badges, what/why, status, quickstart, project layout, workflow + branching, CI/CD checklist, security policy, reference documentation index
- **CHANGELOG.md** — Keep-a-Changelog 1.1.0; populate `[Unreleased]/Added` as you commit
- **CONTRIBUTING.md** — workflow + Conventional Commits + branching + coding standards
- **SECURITY.md** — vulnerability disclosure policy with severity tiers + response timelines
- **docs/conventions/{commit-messages,branching,logging,code-style}.md** — codified standards
- **docs/runbooks/** — operational procedures (acceptance protocols, recovery, setup)
- **docs/architecture/README.md** + **docs/decisions/** — design rationale

When generating README content: real content with real cross-links, no placeholder stubs.

---

## 10. SDLC phasing (for multi-phase projects)

Default to phased delivery with acceptance gates (mirror AutonomousAgent ADR 0006):

```
Phase 1: <local/dev — prove core works>
  ↓ acceptance gate (specific measurable criteria)
Phase 2: <prod migration / scaling>
  ↓ acceptance gate
Phase 3: <feature expansion>
  ↓ acceptance gate
Phase 4: <optimization / advanced features>
```

Each phase has:
- Its own design ADR
- Its own implementation plan in `docs/plans/`
- Its own acceptance runbook in `docs/runbooks/`
- Its own `phase/N` branch + worktree
- Tag `phaseN-accepted` on `main` after merge

---

## 11. Operational scripts

Every new repo gets these scripts in `scripts/`:

- `bootstrap.sh` — one-shot setup (verify prereqs, decrypt secrets, install deps, build, smoke test)
- `verify-prereqs.sh` — host-level prerequisite checks
- `decrypt-secrets.sh` — sops decrypt all `secrets/*.sops` to adjacent plaintext
- `smoke.sh` — post-deploy smoke tests (under 60s; fail-fast)
- `test.sh` — one-command test runner (unit + integration)
- `snapshot.sh` — state snapshot (local for dev, GCS for prod)
- `panic.sh` — emergency halt
- `teardown.sh` — graceful shutdown

All shell scripts: `set -euo pipefail` + quote everything + shellcheck-clean.

---

## 12. Observability discipline

Even small projects benefit from:

- **Structured JSON logs** to stdout (per `docs/conventions/logging.md`)
- **OpenTelemetry tracing** if the project has any service-to-service calls (lib for the language: `opentelemetry-sdk` Python, `@opentelemetry/sdk-node` JS, etc.)
- **Health check endpoint** (`/health` returning JSON `{status: "ok"|"degraded"|"down", checks: [...]}`)
- **Heartbeat** to an external service (Healthchecks.io for free; Cloud Monitoring for prod)

For dev: Phoenix/Arize for traces (free, runs locally). For prod: Cloud Trace / Cloud Logging / Cloud Monitoring.

---

## 13. The AutonomousAgent reference implementation

You're operating WITHIN the AutonomousAgent project — every pattern above is implemented here. When in doubt, refer to:

- `~/RX-Research Project/AutonomousAgent/README.md` — the canonical README structure
- `~/RX-Research Project/AutonomousAgent/CHANGELOG.md` — Keep-a-Changelog example
- `~/RX-Research Project/AutonomousAgent/CONTRIBUTING.md` — workflow + conventions
- `~/RX-Research Project/AutonomousAgent/SECURITY.md` — security policy template
- `~/RX-Research Project/AutonomousAgent/.github/workflows/` — 5 reference workflows
- `~/RX-Research Project/AutonomousAgent/.github/dependabot.yml` — monthly grouped updates pattern
- `~/RX-Research Project/AutonomousAgent/.github/CODEOWNERS` — ownership pattern
- `~/RX-Research Project/AutonomousAgent/docs/decisions/` — 7 reference ADRs
- `~/RX-Research Project/AutonomousAgent/docs/conventions/` — 4 reference convention docs
- `~/RX-Research Project/AutonomousAgent/scripts/` — 9 operational script references

When you're about to create the Nth file in a new repo and aren't sure of the format: open the equivalent file in this project, mirror it, adapt the project-specific bits.

---

## 14. Anti-patterns (do NOT do)

- ❌ Commit secrets in plaintext (use sops)
- ❌ Force-push to `main` (branch protection blocks; don't try to bypass)
- ❌ Skip `--no-ff` on phase merges (loses traceability of phase boundaries)
- ❌ Use `latest` tags for production Docker images (pin to specific versions)
- ❌ Skip `--ignore-pull-failures` on `compose pull` in bootstrap (lets one bad image hang the whole stack)
- ❌ Add `push: main` triggers to CI workflows (duplicate of PR-side runs)
- ❌ Use `gitleaks-action@v2` directly (breaks on initial pushes; use direct CLI invocation)
- ❌ Add Dependabot at `weekly` cadence (burns Action minutes; use monthly)
- ❌ Skip the `[Unreleased]` CHANGELOG section (every commit-worthy change goes there until tagged)
- ❌ Create ADR-worthy decisions without ADRs (silent decisions become tech debt)
- ❌ Make `MERGE_COMMIT` the merge style (linear history is non-negotiable)
- ❌ Run all tests on every commit including markdown-only changes (use path filters thoughtfully — but careful, paths-skipped jobs need workarounds for required status checks)

---

## 15. When to deviate

Deviate from this template ONLY when:

1. The user explicitly requests a deviation (then add an ADR for the deviation)
2. The project's specific tech stack makes a section non-applicable (e.g., no `.gitignore` for a Go module → still apply, with Go-specific patterns)
3. You discover a section is broken or stale (then update THIS file, get user approval, and apply consistently going forward)

When deviating, ALWAYS:
- Document the deviation in the new repo's `docs/decisions/` as an ADR
- Note in the new repo's `CONTRIBUTING.md` what differs from the canonical template
- If the deviation is broadly useful, propose an update to this template

---

## 16. Self-test before declaring "repo ready"

Before reporting back "the new repo is ready," verify:

- [ ] Repo created with all required settings (visibility, default branch, merge methods, topics, description)
- [ ] Initial scaffold committed (all 14 root + nested files)
- [ ] All 5 (+ optional) CI workflows landed under `.github/workflows/`
- [ ] Dependabot config landed at `.github/dependabot.yml`
- [ ] CODEOWNERS landed at `.github/CODEOWNERS`
- [ ] Branch protection (or rulesets if free-tier private) applied to `main`
- [ ] Vulnerability alerts + automated security fixes enabled
- [ ] `.sops.yaml` + `secrets/.gitignore` + `secrets/README.md` landed; example encrypted secret round-trips through `decrypt-secrets.sh`
- [ ] `.pre-commit-config.yaml` + `.secrets.baseline` landed; `pre-commit run --all-files` passes
- [ ] First push to `main` triggered all CI workflows; **all green** (or known-failing with reason documented)
- [ ] README badges resolve (CI status, license, language, etc.)
- [ ] CHANGELOG `[Unreleased]/Added` lists the scaffolded items
- [ ] Tag the initial state as `v0.0.0` (so the CHANGELOG `[Unreleased]` link target exists)

If any item fails the self-test, FIX IT before reporting completion. Do not declare "ready" with known broken state.
