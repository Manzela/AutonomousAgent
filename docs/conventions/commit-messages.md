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
