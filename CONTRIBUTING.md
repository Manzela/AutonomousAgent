# Contributing

This is a single-developer project, but it follows production conventions so future contributors (or future you) can pick it up cleanly.

## System of Record

The authoritative architectural description for this repository is
[docs/spec/phase2.md](docs/spec/phase2.md) (Phase 2 codification, ISO/IEC/IEEE 42010 §4.1).
Any change that alters architectural decisions, failure-mode contracts (F-codes), or
the durability/observability/security baseline MUST update that spec first.

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
