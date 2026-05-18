---
title: "Session C brief — P1-3 Per-step checkpointing + resume"
created: 2026-05-18
owner: session-c
track: P1-3
plan: docs/superpowers/plans/2026-05-15-phase1-10x-implementation.md §P1-3 (Tasks 22-28)
spec: docs/superpowers/specs/2026-05-15-phase1-design-alignment.md §P1-3
integration_branch: phase/1-completion
---

# Session C — P1-3 Per-step checkpointing + resume

## Your goal

Build the checkpoint + resume subsystem so a 48h weekend job survives container
restart / OOM / OS update without losing in-flight work. Every N steps
(configurable, default N=5), serialize state to `/data/checkpoints/{session}/step-{N}.json`.
On container restart, find the latest checkpoint per incomplete session and resume.

## Hermes reuse — START HERE

Hermes' `batch_runner.py` already has `_load_checkpoint()` at line 688 and
`_save_checkpoint()` at line 715, plus a `--resume` flag handler. **Verified
present at submodule pin `ddb8d8f` by audit Pass 2.** Your task is to extend
that pattern from batch-script context to live agent-loop context (every N
steps during a session, not just at batch start/end).

Read `hermes-agent/batch_runner.py` lines 680-770 first to understand the pattern.

## Files you own (greenfield)

- `lib/durability/checkpoint.py` — checkpoint writer (every N steps, JSON-serialize state)
- `lib/durability/resume.py` — checkpoint scanner + state rehydrator

## Files you must touch (shared)

- `lib/durability/__init__.py` — **replace ONLY the `_p1_3_resume_session(ctx)` function body** with a real call to `resume.rehydrate_latest_for_session(ctx)`. DO NOT touch the `register()` function, the `_p1_4_inject_rejected` stub, or anything else in this file. Session D edits a different stub in the same file in parallel.
- `config/limits.yaml` — APPEND a `durability.checkpoint.*` extension to the existing `durability:` key that P1-6 already added. The existing schema is:
  ```yaml
  durability:
    checkpoint:
      interval_steps: 5
      retention_count: 50
      keep_every_nth: 100
      autoresume_enabled: true
  ```
  Add any new keys you need under `durability.checkpoint.*` here.

## Files you MUST NOT touch

- `lib/anchors/`, `lib/evaluators/`, `lib/memory/` (session-d owns memory)
- `lib/kanban/` (session-e owns kanban)
- `lib/durability/failure_matrix.py`, `trichotomy.py`, `escalation.py` (settled by P1-6 PR)
- The `register()` function body in `lib/durability/__init__.py` (just fill the stub)

## Hermes upstream symbols (verified at pin `ddb8d8f`)

- `hermes-agent/batch_runner.py:688` — `_load_checkpoint()` method (your model)
- `hermes-agent/batch_runner.py:715` — `_save_checkpoint()` method (your model)
- `hermes-agent/batch_runner.py:17` — `--resume` flag docs
- `hermes-agent/AGENTS.md:325` — `checkpoints:` config section in hermes config.yaml (already implemented upstream)
- `hermes-agent/AGENTS.md:465-489` — plugin `register(ctx)` contract reference

## Integration tests this PR should make green

- `tests/integration/test_chroma_outage.py` — needs `degraded[]` field in turn response + fail-soft resume path. Your checkpoint + P1-6 trichotomy together make this pass.

## Branch + PR convention

- Branch: `session-c/p1-3-task-NN-<slug>` (e.g., `session-c/p1-3-task-22-checkpoint-write`)
- Worktree: `.worktrees/session-c-task-NN/`
- PR base: `phase/1-completion` (NOT main)
- PR title: Conventional Commits — `feat(durability): ...` or `feat(checkpoint): ...`

## Update the ledger before starting

Open `docs/superpowers/session-coordination.md`, find §"Active sessions (Phase 1 completion)", add a row:
```
| C | P1-3 (checkpointing) | 2026-MM-DD | in-flight | session-c/p1-3-* | Fills _p1_3_resume_session stub |
```

When your last PR merges, update the row's Status to `done`.

## How to run integration tests

```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
.venv/bin/pytest tests/integration/test_chroma_outage.py -v
```
Most integration tests need the live docker-compose stack. `tests/integration/conftest.py` provides shared fixtures.

## Phase 1 completion design spec

For full context read: `docs/superpowers/specs/2026-05-18-phase1-completion-coordination-design.md` (especially §5.3 ownership map + §5.4 conflict-prevention rules).
