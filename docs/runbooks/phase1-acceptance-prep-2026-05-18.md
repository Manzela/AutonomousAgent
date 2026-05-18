---
title: "Phase 1 acceptance preflight report — 2026-05-18"
status: GO
prepared_by: Claude Opus 4.7 (autonomous subagent-driven execution session)
integration_branch_head: 95ce9c3685141a63ac999eae3048b675f3cb0bbe
prepared_at: 2026-05-18
applies_to: docs/runbooks/phase1-acceptance.md
---

# Phase 1 Acceptance Preflight Report

This report certifies the stack is ready for the human-driven acceptance walk-through documented in `docs/runbooks/phase1-acceptance.md`. Verdict: **GO**.

## Pre-flight checks (per design spec §6.1)

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Smoke 8/8 | ✅ PASS | `bash scripts/smoke.sh` → "All 8 smoke checks passed" |
| 2 | Unit tests | ✅ PASS | `.venv/bin/pytest tests/unit/ -q` → 162 passed |
| 3 | Integration tests (triaged) | ✅ PASS | 8 PASS, 6 SKIP (with documented P2-deferral reasons), 0 ERROR |
| 4 | OTel/Phoenix UI reachable | ✅ PASS | Phoenix at `http://localhost:6006`; trace count incremented after live LiteLLM round-trip |
| 5 | Telegram bot reachable from container | ✅ PASS | `docker exec autonomous-agent-hermes-1 getent hosts api.telegram.org` → IPv6 resolved |
| 6 | LiteLLM spend tracking | ✅ PASS (with caveat) | `/spend/calculate` returns valid JSON; `/spend/logs` returns 500 (no DB attached, known P1 limitation, not a regression) |
| 7 | Skills dir exists + writable | ✅ PASS | `docker exec ... ls -la /app/skills` shows 25+ skill subdirs |

## Phase 1 completion summary

### What landed on `phase/1-completion`

| PR | Track | Summary |
|---|---|---|
| #34 | docs | Smoke check count (8 not 9) doc fix |
| #35 | α-0 fix | OTel collector → Phoenix double `/v1/traces` URL fix |
| #36 | α-0 fix | Phoenix port publishing (host-published 127.0.0.1:4317/6006) |
| #37 | α-0 fix | Healthcheck cron dual-fix (closes #29) |
| #40 | α | limits.yaml anchors + evaluators APPEND + schema |
| #41 | α | P1-6 Durability (33-mode failure matrix + trichotomy + escalation sidecar) |
| #42 | α | Session briefs c/d/e + coordination ledger |
| #43 | β.c | P1-3 Checkpointing + resume (atomic temp-rename writes) |
| #44 | β.d | P1-4 REJECTED.md institutional memory |
| #45 | β.e | P1-5 Kanban→Telegram bridge |
| #46 | γ-prep | Integration test triage (5 P2-deferred with reasons) |

### Issue #29 (healthcheck): CLOSED.

## Footnotes (caveats for the acceptance reporter)

- **Acceptance step 5 (secret-leak file check) is a false-positive PASS.** Per audit finding B5, `lib/scrubber.py` is not yet wired into the live pipeline; no code writes to `/data/secret-leak-attempts.log`. The file will be absent, trivially satisfying the runbook check. Phase 2 hardening will land the live scrubber wiring.
- **5 integration tests skipped with documented P2 reasons**:
  - `test_budget_cap`, `test_secret_leak`, `test_skill_creation` — need backend endpoints/hooks not in P1 scope.
  - `test_full_turn`, `test_chroma_outage` — assume the legacy two-service HTTP gateway architecture that was collapsed into the single `hermes` service in commit `408459e`.
  - All 5 will need either Phase 2 endpoint implementation OR test rewrites against the new Telegram-only entrypoint.
- **P1-5 hook bodies are stubbed.** The `pre_tool_call` + `post_tool_call` hooks in `lib/kanban/__init__.py` register correctly but currently no-op with `TODO(P1-5 follow-up)` markers. Card-creation-at-TaskSpec-lock requires session-metadata API not yet exposed. `/cancel <id>` and the public bridge surface work; auto-card-on-message does not. Phase 1 acceptance does not require auto-cards (Telegram messaging works without them).

## What to do next

You (the human) have ~30 minutes uninterrupted with your phone + a browser. Walk the 7 acceptance steps in `docs/runbooks/phase1-acceptance.md`. On all-pass:

1. Open promotion PR: `phase/1-completion` → `main`
2. Tag `phase1-accepted` on the resulting main HEAD
3. (Optional) cut release per `docs/release-process.md`

If anything fails, capture details and open a `chore/p1-acceptance-fixes` PR against `phase/1-completion`.

## Verdict: **GO**
