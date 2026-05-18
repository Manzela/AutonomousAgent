---
title: "Session D brief — P1-4 REJECTED.md institutional memory"
created: 2026-05-18
owner: session-d
track: P1-4
plan: docs/superpowers/plans/2026-05-15-phase1-10x-implementation.md §P1-4 (Tasks 29-33)
spec: docs/superpowers/specs/2026-05-15-phase1-design-alignment.md §P1-4
integration_branch: phase/1-completion
---

# Session D — P1-4 REJECTED.md institutional memory

## Your goal

When the evaluator loop rejects an approach 3 times for the same TaskSpec, append
a structured entry to `/data/MEMORY/REJECTED.md` (workspace-shared). On every
session start, inject category-filtered entries as context so the agent doesn't
repeat dead-end approaches across sessions.

## Files you own (greenfield)

- `lib/memory/__init__.py` — plugin entry: registers `/forget` + `/rejections` Telegram slash commands (delegates to the Telegram bridge plugin in P1-5; session-e provides the bridge surface)
- `lib/memory/rejected.py` — append/dedupe/load + intent-category filter
- `lib/memory/intent_classifier.py` — classify TaskSpec intent_category using Sonnet 4.6 (model id `vertex_ai/claude-sonnet-4-6` per limits.yaml `memory.intent_classifier_model`)

## Files you must touch (shared)

- `lib/durability/__init__.py` — **replace ONLY the `_p1_4_inject_rejected(ctx)` function body** with a real implementation. DO NOT touch `register()`, the `_p1_3_resume_session` stub, or anything else. Session C edits a different stub in parallel.
- `lib/evaluators/consensus.py` — add the call to `lib/memory/rejected.append_entry(...)` when `consecutive_rejections >= 3` for the same approach fingerprint, per design-alignment spec L333.
- `config/limits.yaml` — APPEND a new top-level `memory:` section:
  ```yaml
  memory:
    rejected_md_path: /data/MEMORY/REJECTED.md
    rejected_default_ttl_days: 30
    rejected_max_inject_per_session: 10
    intent_categories: [coding, audit, research, writing, ops, data, unknown]
    intent_classifier_model: vertex_ai/claude-sonnet-4-6
  ```

## "Same approach" definition (locked by spec L337-339)

`approach_fingerprint = sha256(json.dumps([{"tool": tc.tool_name, "first_arg": _truncate(tc.first_arg, 80)} for tc in session.tool_calls_since_last_taskspec_lock], sort_keys=True))`

Two attempts share an `approach_fingerprint` iff their tool-call sequences (tool name + first-arg-truncated-to-80-chars) match. `consecutive_rejections` increments against this fingerprint.

## Files you MUST NOT touch

- `lib/anchors/`, `lib/evaluators/judge.py`, `lib/evaluators/orchestrator_hook.py`
- `lib/durability/{failure_matrix,trichotomy,escalation}.py`
- `lib/kanban/` (session-e)
- The `register()` function body in `lib/durability/__init__.py`

## Branch + PR convention

- Branch: `session-d/p1-4-task-NN-<slug>` (e.g., `session-d/p1-4-task-29-rejected-append`)
- Worktree: `.worktrees/session-d-task-NN/`
- PR base: `phase/1-completion`

## Update the ledger before starting

Add to §"Active sessions (Phase 1 completion)" in session-coordination.md:
```
| D | P1-4 (REJECTED.md) | 2026-MM-DD | in-flight | session-d/p1-4-* | Fills _p1_4_inject_rejected stub |
```

## How to run unit tests

```bash
.venv/bin/pytest tests/unit/test_rejected.py -v   # you'll create this
```

## Phase 1 completion design spec

For full context: `docs/superpowers/specs/2026-05-18-phase1-completion-coordination-design.md` §5.3 + §5.4.
