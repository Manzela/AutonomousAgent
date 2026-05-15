# Pull-request conventions

This is the operating manual for opening, reviewing, and merging PRs in this repo. Most of it is enforced by branch protection on `main`; the rest is etiquette that keeps the queue healthy.

## TL;DR

| You're about to… | Do this |
|---|---|
| Open a PR | Use a conventional title (`type(scope): lowercase subject`); branch must match the allowed regex; one logical change per PR |
| See `BEHIND` on a green PR | `gh pr update-branch <N>` — main has moved; CI must re-run on the new base |
| See `CONFLICTING` | Rebase locally onto `origin/main`, force-push `--with-lease`, retest |
| See a failing required check | Fix it — never bypass. Required checks are the contract for what `main` is allowed to contain |
| Merge | Squash-merge by default; `--no-ff` only for phase-integration into `main` |
| Stack PRs | Set the base to the parent PR's head branch; retarget to `main` after the parent merges |

## Branch naming

Allowed by [`pr-validation.yml`](../../.github/workflows/pr-validation.yml):

```
phase/[1-4]
hotfix/<short-desc>
dependabot/<anything>
docs/<short-desc>
chore/<short-desc>
feat/<short-desc>
fix/<short-desc>
test/<short-desc>
refactor/<short-desc>
```

Slug part is `[a-z0-9-]+`.

### Session branches

In-flight parallel sessions (see [session-coordination.md](../superpowers/session-coordination.md)) use `session-<letter>/<phase-tag>-task-<NN>-<slug>`. **This pattern is not yet in the regex.** The widening PR is tracked in the open backlog; until it lands, session PRs need either branch renames or the regex extension before they can merge to `main`.

## PR title format

Enforced by `amannn/action-semantic-pull-request@v6`:

```
<type>(<scope>): <lowercase subject>
```

- **type**: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `security`, `build`, `ci`, `revert`
- **scope** (optional but recommended): `agent`, `gateway`, `litellm`, `otel`, `chroma`, `honcho`, `sandbox`, `secrets`, `config`, `lib`, `scripts`, `deploy`, `tests`, `docs`, `evaluators`, `anchors`, `runbook`
- **subject**: starts with a lowercase letter; imperative mood; no trailing period

✅ `feat(evaluators): add 4-judge consensus + 5th-judge tiebreak`
❌ `[Session B] P1-2 Task 17: 4-judge consensus + 5th-judge tiebreak` *(not a valid type, capitalized subject)*

The PR body should explain **why**, not what — the diff already shows what.

## PR body template

```markdown
## Summary
<2–3 bullets: what this changes and why>

## Test plan
- [ ] <each verification step>

## Dependencies / stacking
<any open PRs this stacks on, and the retarget plan>

## Session attribution
<for parallel-session work: which session, which track>
```

The repo provides this scaffold via [`.github/PULL_REQUEST_TEMPLATE.md`](../../.github/PULL_REQUEST_TEMPLATE.md).

## Required status checks (branch protection on `main`)

All 11 must be green AND the PR must be up-to-date with main (`strict: true`):

1. `Lint Python` — ruff + ruff-format
2. `Lint Shell` — shellcheck warning-or-stricter
3. `Lint YAML` — yamllint
4. `Lint Dockerfiles` — hadolint (advisory, but the action must complete)
5. `Unit Tests` — pytest under `tests/unit/`
6. `Validate config/limits.yaml` — `lib/limits_validator`
7. `Validate docker-compose` — `docker compose config` against the production stack and dev/test overrides
8. `Conventional Commit title` — semantic-pull-request action
9. `Branch name follows convention` — regex check
10. `gitleaks` — secret scan over the full PR diff
11. `detect-secrets` — baseline diff

See [docs/ci-cd.md](../ci-cd.md) for what each check does, where to find its config, and how to reproduce locally.

## Three patterns you'll hit constantly

### 1. The "behind main" pattern

Branch protection requires `strict: true` (PR branch must contain main's HEAD before merging). When main moves, your PR shows `mergeStateStatus: BEHIND`.

```bash
gh pr update-branch <N>     # merges main into the PR branch; CI re-runs
```

If the resulting merge has conflicts, the command fails — fall back to local rebase:

```bash
git fetch origin
git checkout <branch>
git rebase origin/main
git push --force-with-lease
```

### 2. The "stale base" pattern

Sometimes a long-lived integration branch (e.g., `phase/1`) merges to `main` while you're working on `feat/x` whose base is the *old* main. GitHub's PR diff suddenly explodes with thousands of "added" lines that already exist in main — these are the integration's files, now reachable from main but not yet in your branch's history.

Symptom: PR shows 18k+ additions, all duplicating files already in `main`, status `CONFLICTING`.

Fix: rebase your branch onto current `main` so git can collapse the duplicates:

```bash
git fetch origin
git checkout <branch>
git rebase origin/main
# resolve any conflicts (usually trivial — keep main's version)
git push --force-with-lease
```

The PR diff will shrink to the actual task-specific delta.

### 3. The stacked-PR pattern

Sometimes Task B depends on Task A's code that hasn't merged yet. Don't wait — open Task B with its base set to Task A's head branch:

```bash
gh pr create --base session-b/p1-2-task-16-judge --head session-b/p1-2-task-17-consensus
```

GitHub's diff shows only B's changes (good).

When A merges to main:
```bash
gh pr edit <B> --base main         # retarget
gh pr update-branch <B>            # main now contains A's commits, refresh your branch
```

If A's commits get squashed on merge, you'll need a `git rebase --onto main A` to drop the now-duplicated commits.

## Merge styles

| Source → Target | Style | Reason |
|---|---|---|
| Task PR → integration branch (`phase/N`) | Squash | Keep integration history one-commit-per-task |
| Phase integration → `main` | `--no-ff` merge commit | Preserve the per-task history; tag the merge commit `phaseN-accepted` |
| Hotfix → `main` | Squash | Single self-contained fix |
| Hotfix → active phase | Cherry-pick | Apply the same fix without dragging unrelated history |
| Dependabot → `main` | Squash | Single-commit dependency bump |
| Docs-only → `main` | Squash | Single coherent doc change |

## Dependency-update auto-merge policy

Dependabot PRs merge automatically when **all** of these hold:

- ✅ All 11 required checks green
- ✅ No major-version bump in a runtime dependency (advisory dependencies like CI actions can major-bump if CI passes)
- ✅ No conflicts; `mergeable: MERGEABLE` and `mergeStateStatus` not `BLOCKED` for any reason other than `BEHIND` (which auto-resolves on rebase)

Operationally: comment `@dependabot rebase` if `BEHIND`; merge with `gh pr merge <N> --squash --auto --delete-branch`. Major bumps to runtime deps (anything in `pyproject.toml`'s `[project.dependencies]`, anything in `deploy/Dockerfile.*` base images, or LiteLLM proxy versions) require a human-readable upgrade note in the PR description before merging.

## Pre-merge checklist (for the merger)

- [ ] PR title is conventional and summarizes the change
- [ ] CHANGELOG.md has an `[Unreleased]` entry (skip for chore-only PRs that don't change user-visible behavior)
- [ ] All required checks green
- [ ] No comments left unaddressed
- [ ] Squash commit message rewritten if GitHub chose a noisy default

## When something needs to be reverted

```bash
gh pr revert <N>     # opens a clean revert PR
```

Don't `git push --force` to `main` — branch protection forbids it; even admins should not bypass.

## Related

- [docs/conventions/commit-messages.md](commit-messages.md) — Conventional Commits format
- [docs/conventions/branching.md](branching.md) — long-running phase branches
- [docs/superpowers/session-coordination.md](../superpowers/session-coordination.md) — how parallel sessions cohabit a repo
- [docs/ci-cd.md](../ci-cd.md) — what each required check does
- [docs/release-process.md](../release-process.md) — how releases get cut from `main`
