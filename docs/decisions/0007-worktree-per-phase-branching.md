# 0007. Worktree-per-phase branching

**Status:** Accepted
**Date:** 2026-05-14
**Decision-makers:** Daniel Manzela

## Context

Each phase is a long-running, isolated body of work (weeks). Standard branch-per-feature workflow doesn't fit because:
- Phases are sequential, but late-phase planning may overlap with current-phase development
- Bug fixes for an accepted phase may need to coexist with active phase development
- We want to keep `main` clean (only accepted work) while allowing each phase its own working tree

## Decision

We will use [git worktrees](https://git-scm.com/docs/git-worktree) with one worktree per phase, all rooted at the same git repo, checked out under `.worktrees/`:

```
AutonomousAgent/                  ← main worktree (branch: main)
├── .worktrees/
│   ├── phase1/                   ← branch: phase/1
│   ├── phase2/                   ← branch: phase/2 (created when Phase 2 starts)
│   ├── phase3/                   ← branch: phase/3
│   └── phase4/                   ← branch: phase/4
```

Branching rules:
- `main` holds only accepted-and-tagged work (`phase1-accepted`, etc.)
- All phase work happens in `.worktrees/phaseN/` on branch `phase/N`
- After acceptance: `git checkout main && git merge --no-ff phase/N && git tag phaseN-accepted`
- Hotfixes branch from `main` as `hotfix/<short-desc>`, merge back to main + cherry-pick to active phase branch

## Consequences

### Positive
- Multiple phases can have working trees simultaneously (e.g., Phase 1 hotfix while Phase 2 develops)
- `main` is always shippable (only accepted work merged)
- Each worktree is a normal directory; no `git stash` dance to switch contexts
- IDE/test/build environments per worktree don't interfere with each other
- Disk overhead is small (worktrees share the `.git/objects` store)

### Negative
- More cognitive overhead than single-checkout flow
- `.worktrees/` must be gitignored (don't commit the worktrees themselves)
- Submodule (hermes-agent) state is per-worktree; need explicit `git submodule update` after worktree create
- Some tools (older IDEs, some npm scripts) don't understand worktrees

### Neutral
- This pattern is common in large monorepos and multi-version maintenance

## Alternatives considered

### Option A: Single working tree, branch-switching per phase
- Pros: Simplest mental model
- Cons: Can't have two phases active simultaneously; `git stash` or commit-WIP overhead
- Why rejected: Constrains parallel work that we expect to do (planning overlaps execution)

### Option B: Multiple full clones
- Pros: Total isolation
- Cons: Disk overhead; remote pulls in N places; submodule state diverges
- Why rejected: Worktrees give the same isolation more efficiently

### Option C: Trunk-based development
- Pros: Always integrated
- Cons: Phase failures contaminate `main`; no clean acceptance boundary
- Why rejected: We explicitly want phase isolation per ADR 0006

## References

- [git-worktree docs](https://git-scm.com/docs/git-worktree)
- ADR 0006 (phased build)
- `docs/conventions/branching.md`
