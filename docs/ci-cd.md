# CI/CD overview

This document is the deep-dive companion to the README's CI table. It explains what every workflow exists for, exactly which check blocks which merge, and how to reproduce each one locally before pushing.

> **Design intent.** Cheap, fast, informative. Every job runs in parallel where possible, fails loudly with actionable messages, and is independently re-runnable from the GitHub UI. Workflow surface was deliberately minimized in commit [`43efea4`](https://github.com/Manzela/AutonomousAgent/commit/43efea4) to conserve account-wide GitHub Actions minutes — see the comment block at the top of [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) for what was removed and why.

## Workflows at a glance

| Workflow | File | Trigger | Purpose |
|---|---|---|---|
| CI | `.github/workflows/ci.yml` | every PR + manual | Lint + tests + config validation |
| PR Validation | `.github/workflows/pr-validation.yml` | every PR | Title + branch-name conventions |
| Secret Scan | `.github/workflows/secret-scan.yml` | every PR + 1st of every month | gitleaks + detect-secrets |
| Release | `.github/workflows/release.yml` | tag push (`v*` or `phase*-accepted`) | Auto-generated release notes |
| Dependabot | `.github/dependabot.yml` | monthly | Grouped dependency PRs (Actions / pip / Docker base) |
| Dependency Graph | (auto) | always | GitHub-native, no custom config |

`Push` triggers on `main` were removed — PRs cover the same paths and `main` only acquires commits via merged PRs (branch protection enforces).

## Required status checks (the merge contract)

Branch protection on `main` is `strict: true` with **11 required checks**. A PR cannot merge until all are green AND the branch contains main's HEAD.

```
┌──────────────────────────────────────────────────────────────────────┐
│ CI workflow                                                           │
│   • Lint Python                  ruff check + ruff format --check     │
│   • Lint Shell                   shellcheck --severity=warning        │
│   • Lint YAML                    yamllint                             │
│   • Lint Dockerfiles             hadolint (advisory but must complete)│
│   • Unit Tests                   pytest tests/unit/                   │
│   • Validate config/limits.yaml  lib/limits_validator                 │
│   • Validate docker-compose      docker compose -f … config           │
├──────────────────────────────────────────────────────────────────────┤
│ PR Validation workflow                                                │
│   • Conventional Commit title    amannn/action-semantic-pull-request  │
│   • Branch name follows convention   regex against allowed patterns   │
├──────────────────────────────────────────────────────────────────────┤
│ Secret Scan workflow                                                  │
│   • gitleaks                     gitleaks CLI on PR diff              │
│   • detect-secrets               baseline-diff against .secrets.baseline│
└──────────────────────────────────────────────────────────────────────┘
```

## Job-by-job breakdown

### Lint Python

- **What:** `ruff check` (lint) + `ruff format --check` (formatter)
- **Config:** `pyproject.toml` (`[tool.ruff]` section)
- **Pinned version:** `0.6.9` in CI (matches `.pre-commit-config.yaml`). Bump in lockstep.
- **Run locally:**
  ```bash
  ruff check . --exclude hermes-agent --exclude .worktrees
  ruff format --check . --exclude hermes-agent --exclude .worktrees
  ```
- **Auto-fix:** `ruff check --fix .` and `ruff format .`
- **Skips when:** no `*.py` files at the revision (early-bootstrap repos).

### Lint Shell

- **What:** shellcheck on `scripts/*.sh` at warning severity
- **Excluded:** `SC1091` (sourced file paths are dynamic)
- **Run locally:**
  ```bash
  shellcheck --severity=warning --exclude=SC1091 scripts/*.sh
  ```
- **Common gotchas:**
  - `SC2155` — declare and assign separately. `local x="$(cmd)"` masks `cmd`'s exit code.
  - `SC2034` — unused variable. Either use it or delete it.
  - `SC2086` — quote your `$variables` to prevent word-splitting.

### Lint YAML

- **What:** yamllint on every `.yml`/`.yaml` file in the repo (excluding `hermes-agent/`, `.worktrees/`, `.venv/`)
- **Config:** `.yamllint.yml` (2-space indent, 200-col line length)
- **Run locally:**
  ```bash
  yamllint -c .yamllint.yml deploy/
  ```

### Lint Dockerfiles

- **What:** hadolint on `deploy/Dockerfile.hermes` and `deploy/sandboxes/Dockerfile.shell-sandbox`
- **Marked `continue-on-error: true`** until we baseline our findings. Currently advisory.
- **Excluded rules:** `DL3008`, `DL3009`, `DL3015` (apt-pinning + layer-caching warnings — noisy)
- **Run locally:**
  ```bash
  docker run --rm -i hadolint/hadolint < deploy/Dockerfile.hermes
  ```

### Unit Tests

- **What:** `pytest tests/unit/`
- **Python:** 3.11 via `uv venv`
- **Submodule:** Hermes upstream is *not* checked out for unit tests (it isn't needed and bloats the runner)
- **Run locally:**
  ```bash
  ./scripts/test.sh           # unit + integration
  pytest tests/unit/ -v        # unit only
  ```

### Validate config/limits.yaml

- **What:** `lib/limits_validator.py` runs `config/limits.yaml` against `config/limits-schema.json`
- **Run locally:**
  ```bash
  python -m lib.limits_validator
  ```

### Validate docker-compose

- **What:** `docker compose -f deploy/docker-compose.yml config > /dev/null` for the prod stack and the dev/test overrides
- **Why it can fail in CI but pass locally:** the workflow stubs the env-files referenced via `env_file:` (production secrets aren't available to the runner). When a new `env_file:` entry is added to `deploy/docker-compose.yml`, the corresponding `touch secrets/<name>` must be added to the **Stub secrets** step in `ci.yml`.
- **Run locally with stubs:**
  ```bash
  for f in telegram chroma-cloud hermes-provider; do touch "secrets/${f}.env"; done
  for f in honcho-db-password litellm-master-key chroma-token github-pat; do touch "secrets/${f}"; done
  docker compose -f deploy/docker-compose.yml config > /dev/null
  ```

### Conventional Commit title

- **What:** `amannn/action-semantic-pull-request@v6` enforces `type(scope): lowercase subject`
- **Allowed types:** `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `security`, `build`, `ci`, `revert`
- **Subject:** must start with a lowercase letter
- **Skip via labels:** `bot` or `dependencies` (used by Dependabot's own PRs)

### Branch name follows convention

- **What:** regex check on `${{ github.head_ref }}` in `pr-validation.yml`
- **Allowed:** `phase/[1-4]`, `hotfix/<desc>`, `feat/<desc>`, `fix/<desc>`, `chore/<desc>`, `docs/<desc>`, `refactor/<desc>`, `test/<desc>`, `dependabot/<anything>`
- **See [pull-requests.md § Session branches](conventions/pull-requests.md#session-branches)** for the in-flight `session-*/...` exception

### gitleaks

- **What:** scans the PR diff for secret patterns
- **Config:** `.gitleaks.toml` (allowlist for project-specific false positives)
- **Run locally:**
  ```bash
  gitleaks detect --config .gitleaks.toml --no-banner --redact
  ```

### detect-secrets

- **What:** baseline-diff scan; flags any new high-entropy strings or secret keywords not already in `.secrets.baseline`
- **Add a new false positive:**
  ```bash
  detect-secrets scan --baseline .secrets.baseline
  git add .secrets.baseline
  ```
- **Inline pragma to suppress one line:**
  ```python
  api_key = "AKIA..."  # pragma: allowlist secret
  ```

## Non-blocking workflows

### Release (`release.yml`)

Fires on tag push: `v*` (semantic releases) or `phase*-accepted` (phase milestones). Generates release notes from Conventional Commits since the previous tag and creates a GitHub Release. **No CI gate** — it assumes the tag was placed on a green commit.

### Secret Scan (scheduled, monthly)

Runs gitleaks against the full git history on the 1st of each month. PRs already cover per-diff secret-scanning; this catches drift in older history.

### Dependabot

Monthly grouped PRs:
- GitHub Actions (`github_actions` ecosystem)
- Python (`pip` ecosystem) — bumps `pyproject.toml` deps
- Docker base images (`docker` ecosystem) — bumps `FROM` lines in Dockerfiles

PR titles are conventional (`chore(ci)(deps): bump …`). Merge policy: see [pull-requests.md § Dependency-update auto-merge policy](conventions/pull-requests.md#dependency-update-auto-merge-policy).

## What we explicitly chose not to run

| Removed | Reason |
|---|---|
| `markdown-lint` | Advisory only — markdownlint is enforced via local pre-commit instead |
| `CodeQL` | No Python on `main` until Phase 1 merged (now landed; reintroduce at Phase 1 acceptance) |
| `Dependency Review` | Requires GitHub Advanced Security on private repos to be effective |
| `push: main` triggers | PRs already cover the path; re-running on the merge commit duplicates compute |
| Weekly Dependabot / secret-scan cadences | Reduced to monthly — agent isn't on the critical path of any external SLA |

All decisions are inline-commented in the workflow files so the rationale lives next to the code.

## Local reproduction one-liner

The fastest way to mirror CI before pushing:

```bash
# Python
ruff check . --exclude hermes-agent --exclude .worktrees \
  && ruff format --check . --exclude hermes-agent --exclude .worktrees \
  && pytest tests/unit/ -q

# Shell + YAML + compose
shellcheck --severity=warning --exclude=SC1091 scripts/*.sh \
  && yamllint -c .yamllint.yml deploy/ \
  && docker compose -f deploy/docker-compose.yml config > /dev/null
```

If both blocks succeed, every `Lint *` and `Validate *` required check will be green on the PR.

## Related

- [docs/conventions/pull-requests.md](conventions/pull-requests.md) — PR lifecycle including failure-recovery patterns
- [docs/release-process.md](release-process.md) — how the Release workflow gets fired
- [docs/superpowers/session-coordination.md](superpowers/session-coordination.md) — why session-* branches need a regex update
- [.github/workflows/](../.github/workflows/) — the workflow source files
