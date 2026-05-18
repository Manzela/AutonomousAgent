---
title: "Session E brief — P1-5 Kanban → Telegram bridge"
created: 2026-05-18
owner: session-e
track: P1-5
plan: docs/superpowers/plans/2026-05-15-phase1-10x-implementation.md §P1-5 (Tasks 34-38)
spec: docs/superpowers/specs/2026-05-15-phase1-design-alignment.md §P1-5
integration_branch: phase/1-completion
---

# Session E — P1-5 Kanban → Telegram bridge

## Your goal

Bridge Hermes' built-in Kanban subsystem with the Telegram gateway. Each inbound
Telegram message becomes a Kanban card (at TaskSpec lock-time). Card status
transitions trigger Telegram notifications per the locked policy.

## Hermes reuse — START HERE

**Hermes ships THE ENTIRE Kanban subsystem.** Your work is the Telegram bridge
+ notification mapping, NOT building Kanban from scratch. Verified at pin
`ddb8d8f` by audit Pass 2:

- `hermes-agent/hermes_cli/kanban_db.py:559-673` — Task schema (20+ fields)
- `hermes-agent/hermes_cli/kanban_db.py:93` — `VALID_STATUSES = {"triage", "todo", "ready", "running", "blocked", "done", "archived"}`
- `hermes-agent/hermes_cli/kanban_db.py:61-68` — SQLite WAL + BEGIN IMMEDIATE + CAS pattern (already concurrency-safe)
- Programmatic API: `create_task`, `claim_task`, `complete_task`

## Naming convention decision (P2-B from audit)

**Accept Hermes' status names verbatim.** Don't fork. The user-facing labels in
Telegram notifications can differ from internal status names if needed (presentation
layer mapping in `lib/kanban/notification_policy.py`), but DON'T rename Hermes'
enum.

## Files you own (greenfield)

- `lib/kanban/__init__.py` — plugin entry: `register(ctx)` wires `pre_tool_call` (create card at TaskSpec lock) + `post_tool_call` (update status)
- `lib/kanban/telegram_bridge.py` — Telegram → Kanban (`telegram_msg_to_card`) and Kanban → Telegram (`status_transition_to_notification`)
- `lib/kanban/notification_policy.py` — locked policy table (see spec L362-370). 1 user message = 1 card. Sub-cards via Hermes' `Task.workflow_template_id`.

## Files you must touch (shared)

- `lib/anchors/__init__.py:55` — **replace the `TODO(P1-5)` stub** in the `/cancel` slash command handler. Currently returns a placeholder string; needs to dispatch by argument: `/cancel` (no arg) → existing draft-spec cancel logic in P1-1; `/cancel <id>` → call `lib/kanban/telegram_bridge.cancel_card(id)`. Keep the existing draft-cancel branch intact.
- `deploy/docker-compose.yml` — add a volume mount on the hermes service: `hermes-data:/root/.hermes/kanban` so the Kanban SQLite DB persists across container restarts.
- `config/limits.yaml` — APPEND a new top-level `kanban:` section:
  ```yaml
  kanban:
    db_path: /root/.hermes/kanban/kanban.db
    notification_rate_limit_per_minute: 6
    use_hermes_status_names: true   # accept Hermes' enum, don't fork
  ```

## Files you MUST NOT touch

- `lib/durability/`, `lib/memory/`, `lib/evaluators/`
- The `register()` function in `lib/anchors/__init__.py` — just fill the TODO at line 55

## Notification policy (locked by spec L362-370)

| Status transition | Notification |
|---|---|
| `triage` → `todo` | silent |
| `todo` → `ready` | "Started: <title>" |
| `ready` → `running` | silent (heartbeat-only via OTel) |
| `running` → `blocked` | **PRIORITY ALERT**: "Blocked on: <reason>. Use `/resume <id>` to unblock" |
| `running` → `done` | "Done: <title>\n\nResult: <summary>" |
| `running` → failure | **ALERT**: "Card <id> failed: <consecutive_failures>x — <last_failure_error>" |
| any → `archived` | silent |

## Integration tests this PR should make green

- (optional) `tests/integration/test_full_turn.py` — a successful turn should now create a Kanban card + emit a Telegram notification. You may need to extend the test to assert these side-effects.

## Branch + PR convention

- Branch: `session-e/p1-5-task-NN-<slug>`
- Worktree: `.worktrees/session-e-task-NN/`
- PR base: `phase/1-completion`

## Update the ledger before starting

Add to §"Active sessions (Phase 1 completion)" in session-coordination.md:
```
| E | P1-5 (Kanban→Telegram) | 2026-MM-DD | in-flight | session-e/p1-5-* | Replaces TODO(P1-5) in lib/anchors/__init__.py:55 |
```

## Phase 1 completion design spec

For full context: `docs/superpowers/specs/2026-05-18-phase1-completion-coordination-design.md` §5.3 + §5.4.
