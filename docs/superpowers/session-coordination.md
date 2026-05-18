# Parallel-session coordination

This project routinely runs **two or more Claude Code sessions in parallel**, each owning a slice of the in-flight phase plan. This document explains how those sessions stay out of each other's way, where the contract surfaces live, and what conventions make merges painless when the sessions converge.

> **TL;DR.** Each session owns a numbered task track from the phase plan. Each task lives on its own branch, named `session-<letter>/<phase-tag>-task-<NN>-<slug>`, in its own worktree under `.worktrees/session-<letter>-task-<NN>/`. Sessions never share branches, never edit shared baseline files in the same task PR, and announce intent in the **session-coordination ledger** (this file's appendix) before claiming a track.

## Why parallel sessions

Phase plans (see [`docs/superpowers/plans/`](plans/)) decompose into ~50 tasks. Many tasks are independent — Task 16 (`lib/evaluators/judge.py`) doesn't touch the same files as Task 5 (`lib/anchors/__init__.py`). Running them serially would take 5–10× longer than needed.

The model is borrowed from production engineering: *track-level ownership* with *branch-level isolation*. Each track has a single owner; each task within a track is one branch + one PR.

## The track-naming convention

| Element | Form | Example | Lives on |
|---|---|---|---|
| Session | `session-<letter>` | `session-b` | one Claude Code session, one human |
| Track | `<phase-tag>` | `p1-2` (Phase 1, slice 2) | one task subgroup in the phase plan |
| Task | `task-<NN>-<slug>` | `task-16-judge` | one PR; one branch |
| Branch | `session-<letter>/<phase-tag>-task-<NN>-<slug>` | `session-b/p1-2-task-16-judge` | one worktree |
| Worktree | `.worktrees/session-<letter>-task-<NN>/` | `.worktrees/session-b-task-16/` | gitignored |

Letters increment per active session (A, B, C, …). Reuse a letter when a session retires.

> ✅ **Branch-name conformance.** The `branch-validation` check in [`.github/workflows/pr-validation.yml`](../../.github/workflows/pr-validation.yml) accepts `session-<letter>/<path>` where `<path>` is one or more kebab-case segments separated by `/`. Both the canonical form (`session-b/p1-2-task-16-judge`) and the legacy form (`session-a/task-05-anchors-register`, no phase-tag) are allowed. PR titles still need to follow the Conventional Commits convention separately (`type(scope): lowercase subject`) — see [pull-requests.md](../conventions/pull-requests.md).

## The session-coordination ledger

This is a single file (this one) that every session reads at the start of work and updates when claiming or releasing a track. It is the authoritative answer to "who owns what right now."

Each entry is one line:

```
| Session | Track | Owner-since | Status | Branch | Notes |
```

Append-only during a phase; pruned when a phase is accepted into `main`.

### Active sessions (Phase 1)

| Session | Track | Owner-since | Status | Branch | Notes |
|---|---|---|---|---|---|
| A | P1-1 (anchors plugin) | 2026-05-15 | in-flight | `session-a/task-05-anchors-register` | Task 5 wires `register(ctx)`; Task 6 fills the stubs |
| B | P1-2 (evaluator panel) | 2026-05-15 | in-flight | `session-b/p1-2-*` | Tasks 14–19 (LiteLLM Gemini, smoke, judge, consensus, hook, toolsets) |

### Retired sessions

_(none yet — section grows as phases accept)_

### Active sessions (Phase 1 completion)

| Session | Track | Owner-since | Status | Branch | Notes |
|---|---|---|---|---|---|
| C | P1-3 (checkpointing) | _(set on claim)_ | not-yet-claimed | session-c/p1-3-* | Fills `_p1_3_resume_session` stub in `lib/durability/__init__.py` |
| D | P1-4 (REJECTED.md) | _(set on claim)_ | not-yet-claimed | session-d/p1-4-* | Fills `_p1_4_inject_rejected` stub in `lib/durability/__init__.py` |
| E | P1-5 (Kanban→Telegram) | _(set on claim)_ | not-yet-claimed | session-e/p1-5-* | Replaces `TODO(P1-5)` in `lib/anchors/__init__.py:55` |

## Conflict-prevention rules

These are the conventions that make 5+ parallel sessions actually work without nightly rebase parties.

1. **Each task PR touches as few shared files as possible.** Adding a new module under `lib/<task-name>/` is ideal. Editing a shared registry, a top-level `__init__.py`, or a global config is a yellow flag: coordinate with other live sessions before doing it.

2. **Shared-file edits go on the track's *integration* PR, not on individual task PRs.** Phase 1's integration vehicle was `phase/1` — multiple sessions added per-task changes to that branch, and the merge to `main` was a single `--no-ff` PR (`Phase/1 (#6)` = commit `0f74412`).

3. **Session branches fork from a known integration commit, not from `main` HEAD.** Phase 1 sessions all forked from `3e38911 chore: gitignore .worktrees/`. When `phase/1` later merged, every session branch ended up "behind" main with an artificial 18k-line diff (the entire phase/1 baseline reappearing). The fix is a clean `git rebase` onto current `main`, but it's avoidable by either (a) using a single integration branch per phase or (b) rebasing session branches onto each new shared-file change.

4. **No silent overlap.** If two sessions need to touch the same file, the second one waits until the first merges (or to phase-integration), or they negotiate a single combined commit.

5. **Session IDs are visible in the trail.** Branch name (`session-b/...`), worktree path (`.worktrees/session-b-task-NN/`), and PR description (`### Session attribution: Session B — executed in dedicated worktree …`) all carry the session label so a human reviewer can trace any artifact back to its source session.

## Worktree mechanics

Each task gets its own worktree to isolate IDE state, test caches, and accidental cross-task edits.

```bash
# Create
git worktree add .worktrees/session-b-task-16 -b session-b/p1-2-task-16-judge main

# Use
cd .worktrees/session-b-task-16
# ...do work...

# Done (after PR merges)
cd ../..
git worktree remove .worktrees/session-b-task-16
git branch -D session-b/p1-2-task-16-judge   # if you want to clean up the branch too
```

`.worktrees/` is gitignored. The Superpowers `using-git-worktrees` skill automates the create/remove cycle when invoked inside a session.

## Dispatching subagents

A session may dispatch parallel **subagents** for independent task slices. The Superpowers `dispatching-parallel-agents` skill is designed for the 2+ independent tasks-without-shared-state case. Subagent dispatch happens *inside* a session — the subagent uses the session's worktree(s), and any branches it creates inherit the session's letter.

## When sessions converge

At the end of a track, the phase-integration PR collects all task PRs into one merge to `main`:

1. All task PRs in the track merge into the integration branch (e.g., `phase/1`)
2. Integration branch runs the **full acceptance protocol** ([`docs/runbooks/phase1-acceptance.md`](../runbooks/phase1-acceptance.md))
3. Integration → `main` via `--no-ff` + tag `phaseN-accepted`
4. Session ledger is pruned; session worktrees are removed
5. Long-running session branches stay in the remote for archeology, but are no longer integrated

## Rules-of-thumb cheat sheet

| Situation | Do | Don't |
|---|---|---|
| Starting a new task | Update the ledger above; create branch + worktree with the conventional name | Create branches like `my-fix-attempt-2` |
| Two sessions need the same file | Coordinate on a single combined commit | Both edit it independently and rebase later |
| A task's PR is taking days | Mark `in-flight (slow)` in the ledger; consider splitting | Block other sessions silently |
| Phase merges to `main` | Rebase any in-flight session branches; expect a one-time `git rebase --onto main <old-base>` | Force-push without `--with-lease` |

## Related

- [docs/conventions/branching.md](../conventions/branching.md) — long-running phase branches
- [docs/conventions/pull-requests.md](../conventions/pull-requests.md) — PR lifecycle including the rebase-and-retitle pattern
- [docs/conventions/commit-messages.md](../conventions/commit-messages.md) — Conventional Commits
- [ADR 0007](../decisions/0007-worktree-per-phase-branching.md) — why worktrees per phase
- [Superpowers `dispatching-parallel-agents`](https://github.com/Manzela/superpowers) skill — used inside a session
