# Branching & Worktree Convention

This project uses **git worktrees with one branch per phase**. See [ADR 0007](../decisions/0007-worktree-per-phase-branching.md) for the rationale.

## Branches

| Branch | Purpose | Lifecycle |
|---|---|---|
| `main` | Accepted work only | Permanent; `--no-ff` merge per phase + tag `phaseN-accepted` |
| `phase/1` | Phase 1 development | Created on Day 1; merged to `main` at acceptance; deletable after merge |
| `phase/2` | Phase 2 development | Created when Phase 1 is accepted |
| `phase/3` | Phase 3 development | Created when Phase 2 is accepted |
| `phase/4` | Phase 4 development | Created when Phase 3 is accepted |
| `hotfix/<desc>` | Urgent fix to accepted code | Branched from `main`; merged back to `main` + cherry-picked to active phase branch |

## Worktree layout

```
AutonomousAgent/                  ← branch: main
├── .worktrees/                   ← gitignored
│   ├── phase1/                   ← branch: phase/1
│   ├── phase2/                   ← branch: phase/2
│   ├── phase3/                   ← branch: phase/3
│   └── phase4/                   ← branch: phase/4
```

## Creating a phase worktree

```bash
# From the main worktree:
git branch phase/N main                       # create branch from main
git worktree add .worktrees/phaseN phase/N    # create worktree
cd .worktrees/phaseN
git submodule update --init --recursive       # submodule state is per-worktree
```

## Working in a phase

```bash
cd .worktrees/phaseN
# normal git workflow on branch phase/N
git add ...
git commit -m "feat(scope): ..."
```

## Phase acceptance → merge to main

When the phase passes its acceptance protocol:

```bash
# From the main worktree:
cd <project-root>
git checkout main
git merge --no-ff phase/N -m "Merge phase/N into main: <one-line summary>"
git tag -a phaseN-accepted -m "Phase N accepted on $(date -u +%Y-%m-%d). All N criteria passed."
git push origin main --tags    # if there's a remote (Phase 2+)
```

After merging, leave the phase worktree in place if you might still need it; otherwise clean up:

```bash
git worktree remove .worktrees/phaseN
git branch -d phase/N
```

## Hotfixes

```bash
git checkout main
git checkout -b hotfix/short-desc
# fix, test, commit
git checkout main
git merge --no-ff hotfix/short-desc
git push  # if remote exists

# Cherry-pick to active phase branch:
cd .worktrees/phaseN
git cherry-pick <hotfix-sha>
```

## Don'ts

- Don't commit directly to `main` (except for merging accepted phase branches and hotfixes)
- Don't delete `.git/` from a worktree (it's a pointer file; use `git worktree remove`)
- Don't rebase a phase branch after others have based work on it
- Don't force-push to `main`
