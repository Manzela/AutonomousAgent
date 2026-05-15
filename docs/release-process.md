# Release process

This repo uses two parallel release tracks because it has two parallel notions of "done."

| Track | Tag form | When | Triggers |
|---|---|---|---|
| **Phase milestones** | `phaseN-accepted` (e.g., `phase1-accepted`) | A phase has passed its acceptance protocol and merged to `main` | Release workflow generates phase-accepted notes |
| **Semantic releases** | `vMAJOR.MINOR.PATCH` (e.g., `v0.1.0`) | A coherent set of changes is ready to ship as a versioned release | Release workflow generates SemVer release notes |

Both tag patterns fire `.github/workflows/release.yml`, which auto-generates release notes from Conventional Commits since the previous tag and publishes a GitHub Release.

## SemVer policy

We follow [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html), with these project-specific definitions:

| Bump | When |
|---|---|
| **MAJOR** | Backward-incompatible change to the agent's runtime contract: removed/renamed configuration keys, removed CLI subcommands, removed slash commands, removed observability fields, breaking schema changes to `config/limits.yaml` |
| **MINOR** | Backward-compatible feature addition: new tools/sandboxes, new evaluators, new runbooks, new optional config keys |
| **PATCH** | Backward-compatible fix: bugfix without behavior change, doc fix, dependency bump, CI tweak |

Until `v1.0.0`, the project may make breaking changes in MINOR bumps (per SemVer §4) — we still document those clearly in CHANGELOG.

## Release cadence

There is no fixed cadence. Cut a release when there's a coherent thing to communicate:

- A phase has been accepted (always tag `phaseN-accepted` first; SemVer release later)
- A security fix is ready to be highlighted
- Several user-visible features have stacked up under `[Unreleased]`

## Cutting a release — step by step

### Phase milestone (`phaseN-accepted`)

```bash
# 1. Confirm phase acceptance protocol passed (see docs/runbooks/phaseN-acceptance.md)

# 2. Update CHANGELOG: rename [Unreleased] header to [phaseN-accepted] - YYYY-MM-DD,
#    add a fresh empty [Unreleased] block above
$EDITOR CHANGELOG.md

# 3. Commit
git add CHANGELOG.md
git commit -m "chore(release): phase N acceptance"
git push

# 4. Tag the merge commit on main
git tag -a phaseN-accepted -m "Phase N: <one-line summary>"
git push origin phaseN-accepted
```

The Release workflow fires on the tag push and creates a GitHub Release.

### Semantic release (`vX.Y.Z`)

```bash
# 1. Decide the bump (MAJOR/MINOR/PATCH per the policy above)

# 2. Update CHANGELOG:
#    - Rename [Unreleased] header to [X.Y.Z] - YYYY-MM-DD
#    - Add a fresh empty [Unreleased] block above
#    - Update the link refs at the bottom (Unreleased: …compare/X.Y.Z...HEAD; X.Y.Z: …/releases/tag/vX.Y.Z)
$EDITOR CHANGELOG.md

# 3. Commit
git add CHANGELOG.md
git commit -m "chore(release): vX.Y.Z"
git push

# 4. Tag
git tag -a vX.Y.Z -m "vX.Y.Z: <one-line summary>"
git push origin vX.Y.Z
```

## Release notes format

The Release workflow's auto-generated notes group commits by Conventional Commit type:

```
## What's Changed

### 🚀 Features
- feat(evaluators): add 4-judge consensus + 5th-judge tiebreak (#12)

### 🐛 Fixes
- fix(ci): replace gitleaks-action with direct CLI invocation (#3)

### 📚 Documentation
- docs(conventions): add pull-requests guide (#X)

### 🔧 Maintenance
- chore(ci)(deps): bump astral-sh/setup-uv from 3 to 7 (#9)
```

Edit the GitHub Release after publication to add a top-level **Highlights** section if the auto-generated grouping leaves the most important changes buried.

## What never gets tagged

- Forks and personal branches
- Internal tooling experiments
- `[Unreleased]` content that hasn't reached a coherent shippable state

## Recovery: a release went out broken

```bash
# 1. Mark the GitHub Release as a pre-release (so installs prefer the previous one)
gh release edit vX.Y.Z --prerelease

# 2. Cut a vX.Y.(Z+1) with the fix following the normal flow

# 3. Once the patch is out, mark the broken release as deleted (don't delete the tag —
#    that breaks anyone who pinned to it)
gh release edit vX.Y.Z --notes "⚠️ Replaced by vX.Y.(Z+1) — see release notes there."
```

We don't delete tags. Tags are part of the public history; making them disappear breaks dependents.

## Related

- [CHANGELOG.md](../CHANGELOG.md) — the source of truth for what changed
- [docs/conventions/commit-messages.md](conventions/commit-messages.md) — how commit messages drive release-note generation
- [docs/runbooks/phase1-acceptance.md](runbooks/phase1-acceptance.md) — what "phase accepted" means in practice
- [.github/workflows/release.yml](../.github/workflows/release.yml) — the workflow source
