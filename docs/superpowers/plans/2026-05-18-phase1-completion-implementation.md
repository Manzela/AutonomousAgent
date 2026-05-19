# Phase 1 Completion Implementation Plan

> ## ⚠ DRIFT NOTICE — 2026-05-19
>
> **This implementation plan is preserved as historical context.** It was used to drive Phase 1 + Phase 1.0.1 execution; line numbers + path references + threshold values in the plan have since drifted from the live code in 4 places that affect re-implementation. Where the plan disagrees with current code, **trust the code**.
>
> Surfaced by the HANDOFF-2026-05-19 forensic audit — see `audit/handoff-doc-2026-05-19-review/audit-plan.md` (P1-2) for full evidence.
>
> ### Confirmed drifts in this plan
>
> | Plan says | Live code says | Where to look |
> |---|---|---|
> | `lib/anchors/__init__.py:55` is `TODO(P1-5)` stub — lines 1903, 1944, 1963 | Line 55 is error-handling code; `/cancel` handler is at line ~259; `TODO(P1-5)` is fully implemented | `lib/anchors/__init__.py` `_slash_cancel` |
> | `KANBAN_DB_PATH = "/root/.hermes/kanban/kanban.db"` — line 1267-1269 | `KANBAN_DB_PATH = "/home/hermes/.hermes/kanban.db"` (no `kanban/` subdir, post PR #60 HOME rebase) | `lib/durability/escalation.py:21`, `lib/kanban/telegram_bridge.py:57` |
> | `accept_threshold` / `reject_threshold` as integer counts | Both are `float = 0.75` percentages | `config/limits.yaml:153-154`, `lib/evaluators/consensus.py:59-60` |
> | Phase α-0 was 4 isolation PRs | 8 hotfix PRs #56-#63 | `git log` |
>
> ---

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the assistant-driven portion of Phase 1 completion — Phase α-0 (4 live-stack defect fixes), Phase α (pre-work + Tasks 6/20b + P1-6 Durability subsystem + 3 session briefs for parallel work), Phase γ-prep (acceptance preflight), and Phase γ promotion (`--no-ff`/squash PR to `main` + `phase1-accepted` tag). Phase β (sessions c/d/e) executes against per-session briefs and is OUT OF SCOPE for this plan.

**Architecture:** Per the design spec at `docs/superpowers/specs/2026-05-18-phase1-completion-coordination-design.md`. All work converges on `phase/1-completion` integration branch (created in Task 1). PRs base against that branch, not `main`, until promotion (Task 17). The Hermes-agent submodule pin (`ddb8d8f`) is frozen for the duration; all upstream symbols verified by audit Pass 2.

**Tech Stack:** Python 3.x (asyncio, pydantic), Docker Compose v2, LiteLLM proxy (v1.84.0), Hermes Agent (submodule @ `ddb8d8f`), Phoenix (OTel observability), OpenTelemetry collector, gh CLI (authenticated as `Manzela`), sops + age (secrets), pytest, ruff, pre-commit hooks (trim whitespace, fix EOF, detect-secrets, gitleaks).

---

## Pre-flight (one-time verifications before any code work)

### Task 0: Verify starting state matches spec assumptions

**Files:** none (read-only checks)

- [ ] **Step 0.1: Confirm local main is synced to origin**

Run:
```bash
cd "/Users/danielmanzela/RX-Research Project/AutonomousAgent"
git fetch origin --prune
git rev-parse origin/main
git rev-parse main
```
Expected: both SHAs match (or local main fast-forwardable). If diverged, STOP and resolve before continuing.

- [ ] **Step 0.2: Confirm spec PR #33 exists**

Run:
```bash
gh pr view 33 --json state,headRefName,baseRefName
```
Expected: `{"state":"OPEN","headRefName":"docs/spec-phase1-completion-design","baseRefName":"main"}` (or `MERGED` if user has already merged it).

- [ ] **Step 0.3: Confirm `phase/1-completion` branch does NOT yet exist on origin**

Run:
```bash
git ls-remote origin phase/1-completion
```
Expected: empty output (branch absent). If present, STOP — investigate who created it.

- [ ] **Step 0.4: Confirm live stack is the running stack the spec audited**

Run:
```bash
docker ps --format "table {{.Names}}\t{{.Status}}" | grep autonomous-agent-
```
Expected: 5 containers up: `litellm-proxy`, `hermes`, `phoenix`, `otel-collector`, `shell-sandbox`. (The `github-mcp` is optional.)

---

## Phase α: Pre-work cleanup + integration branch

### Task 1: Create the `phase/1-completion` integration branch

**Files:** none (git operations only)

- [ ] **Step 1.1: Fast-forward local main**

Run:
```bash
git checkout main
git pull --ff-only origin main
```
Expected: `Already up to date` or `Updating <oldsha>..<newsha>` with a fast-forward, no merge commit.

- [ ] **Step 1.2: Create + push the integration branch**

Run:
```bash
git checkout -b phase/1-completion
git push -u origin phase/1-completion
```
Expected: `Switched to a new branch 'phase/1-completion'`; push succeeds; remote tracking set.

- [ ] **Step 1.3: Verify the branch is on origin**

Run:
```bash
git ls-remote origin phase/1-completion
```
Expected: non-empty SHA matching local `git rev-parse phase/1-completion`.

### Task 2: Local cleanup — archive old audit dir + worktree pruning

**Files:**
- Move: `audit/phase1-unblock-2026-05-15/` → `/Users/danielmanzela/RX-Research Project/AutonomousAgent-archives/phase1-unblock-2026-05-15/`
- Remove: 20 `.worktrees/` entries

NOTE: no PR. This is local file-system housekeeping. Verify before each destructive command.

- [ ] **Step 2.1: Create the archives parent dir**

Run:
```bash
mkdir -p "/Users/danielmanzela/RX-Research Project/AutonomousAgent-archives"
ls -la "/Users/danielmanzela/RX-Research Project/AutonomousAgent-archives"
```
Expected: empty dir present.

- [ ] **Step 2.2: Move the old audit dir outside the repo**

Run:
```bash
mv "audit/phase1-unblock-2026-05-15" \
   "/Users/danielmanzela/RX-Research Project/AutonomousAgent-archives/phase1-unblock-2026-05-15"
ls "audit/" "/Users/danielmanzela/RX-Research Project/AutonomousAgent-archives/"
```
Expected: `audit/` no longer contains `phase1-unblock-2026-05-15`; archive parent contains the directory with both files (`findings.md`, `spec-compliance.md`).

- [ ] **Step 2.3: Verify `.worktrees/phase1` dirty-file content vs. main**

Run:
```bash
git -C .worktrees/phase1 status --porcelain
git -C .worktrees/phase1 diff HEAD -- docs/architecture/failure-matrix.md docs/superpowers/session-coordination.md | head -40
```
Expected: 2 untracked files listed. Diff inspection shows whether the local versions differ from what's on main (PR #31 committed the failure-matrix; HANDOFF noted the 216-line session-coordination.md variant is obsolete vs. main's 113-line canonical).

- [ ] **Step 2.4: Discard the obsolete drafts in `.worktrees/phase1` (only if confirmed obsolete in Step 2.3)**

Run:
```bash
rm -f .worktrees/phase1/docs/architecture/failure-matrix.md.local \
      .worktrees/phase1/docs/superpowers/session-coordination.md.local 2>/dev/null
# Use rm only on the untracked files identified in Step 2.3, not tracked files.
git -C .worktrees/phase1 clean -fd -n   # dry-run preview first
```
Expected: `clean -n` preview lists what would be removed. If preview matches expectations, re-run without `-n`:
```bash
git -C .worktrees/phase1 clean -fd
```

- [ ] **Step 2.5: Remove `.worktrees/phase1` and prune**

Run:
```bash
git worktree remove .worktrees/phase1
git worktree prune
```
Expected: `phase1` no longer in `git worktree list`.

- [ ] **Step 2.6: Prune remaining 19 worktrees** (all verified clean by audit Pass 1)

Run (one-liner per HANDOFF §5):
```bash
for wt in \
  .worktrees/handoff .worktrees/handoff-clarify .worktrees/reconcile \
  .worktrees/sa-task-01-polish .worktrees/sa-task-02-polish .worktrees/sa-task-02-polish-2 \
  .worktrees/sa-task-03-polish .worktrees/sa-task-04-polish .worktrees/sa-task-04-polish-2 \
  .worktrees/sa-task-05-anchors-register \
  .worktrees/session-b-audit-plan .worktrees/session-b-task-14-fix \
  .worktrees/session-b-task-15 .worktrees/session-b-task-16 .worktrees/session-b-task-17 \
  .worktrees/session-b-task-18 .worktrees/session-b-task-19 .worktrees/session-b-task-20a \
  .worktrees/session-b-task-21; do
  git worktree remove --force "$wt" 2>/dev/null && echo "removed $wt"
done
git worktree prune
git worktree list
```
Expected: only the main worktree remains in the list.

### Task 3: PR — Smoke-doc drift fix (P1-B)

**Files:**
- Modify: `README.md` (replace "9 smoke checks" with "7 smoke checks")
- Modify: `docs/superpowers/HANDOFF-2026-05-17.md` ("Smoke checks on main" row: 7 not 9)
- Modify: `docs/runbooks/phase1-acceptance.md:5` ("smoke.sh passes all 9 checks" → "all 7 checks")

- [ ] **Step 3.1: Create branch**

Run:
```bash
git checkout phase/1-completion
git checkout -b docs/smoke-7-not-9-checks
```

- [ ] **Step 3.2: Grep for stale "9 checks" references**

Run:
```bash
grep -rn "9 smoke checks\|all 9 checks\|9/9 PASS\|smoke 9/9" \
  README.md docs/ scripts/ deploy/ 2>/dev/null
```
Expected: 3-4 hits across README, HANDOFF, and acceptance runbook. Update each to "7".

- [ ] **Step 3.3: Edit `README.md`**

Replace any `./scripts/smoke.sh # 9 smoke checks` with `./scripts/smoke.sh # 7 smoke checks`. Use Edit tool with exact line context.

- [ ] **Step 3.4: Edit `docs/superpowers/HANDOFF-2026-05-17.md`**

In the §1 table, change the "Unit tests on main" / "Live integration test" rows if they mention "9 smoke checks" anywhere. Search "9" occurrences in §1-3 and update.

- [ ] **Step 3.5: Edit `docs/runbooks/phase1-acceptance.md`**

Line 5: `./scripts/smoke.sh passes all 9 checks` → `./scripts/smoke.sh passes all 7 checks` (or just `passes all checks` — count-agnostic is safer).

- [ ] **Step 3.6: Verify edits with diff**

Run:
```bash
git diff --stat
git diff
```
Expected: only the 3-4 expected lines changed. No incidental changes.

- [ ] **Step 3.7: Commit**

Run:
```bash
git add README.md docs/superpowers/HANDOFF-2026-05-17.md docs/runbooks/phase1-acceptance.md
git commit -m "$(cat <<'EOF'
docs: align smoke check count (7, not 9) across README + HANDOFF + acceptance runbook

scripts/smoke.sh contains 7 numbered checks (smoke 1/7..7/7). README +
HANDOFF + phase1-acceptance.md all claim 9 — doc drift. This corrects
the count so acceptance reports don't have to footnote the discrepancy.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3.8: Push + open PR against `phase/1-completion`**

Run:
```bash
git push -u origin docs/smoke-7-not-9-checks
gh pr create --base phase/1-completion --head docs/smoke-7-not-9-checks \
  --title "docs: align smoke check count (7, not 9)" \
  --body "$(cat <<'EOF'
## Summary
- scripts/smoke.sh has 7 checks; README + HANDOFF + acceptance runbook said 9. Doc drift only.

## Test plan
- [ ] CI (lint + secret scan) green
- [ ] grep on this branch returns 0 hits for "9 smoke checks" or "all 9 checks"

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
Expected: PR URL printed.

- [ ] **Step 3.9: After CI green, squash-merge into `phase/1-completion`**

Run:
```bash
gh pr merge --squash --delete-branch
git checkout phase/1-completion && git pull --ff-only
```

---

## Phase α-0: Live-stack defect fixes (4 small PRs)

### Task 4: PR α-0.1 — OTel collector double `/v1/traces` URL fix

**Files:**
- Modify: `deploy/otel/collector.dev.yaml:21` (endpoint config)
- Modify: `deploy/otel/collector.prod.yaml` (same key if present)

- [ ] **Step 4.1: Create branch**

Run:
```bash
git checkout phase/1-completion
git checkout -b fix/otel-double-traces-url
```

- [ ] **Step 4.2: Inspect current endpoint config**

Run:
```bash
grep -nE "endpoint:|exporters:" deploy/otel/collector.dev.yaml
```
Expected: see `endpoint: http://phoenix:6006/v1/traces` (the defect — SDK auto-appends `/v1/traces`).

- [ ] **Step 4.3: Edit `collector.dev.yaml` to drop the trailing `/v1/traces`**

Use the Edit tool:
```
old_string: "endpoint: http://phoenix:6006/v1/traces"
new_string: "endpoint: http://phoenix:6006"
```

- [ ] **Step 4.4: Repeat for `collector.prod.yaml` if it has the same line**

Run:
```bash
grep -n "endpoint: http://phoenix:6006/v1/traces" deploy/otel/collector.prod.yaml
```
If hit, apply the same Edit. If no hit, skip.

- [ ] **Step 4.5: Restart otel-collector + verify no more 405 errors**

Run:
```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml restart otel-collector
sleep 5
docker logs autonomous-agent-otel-collector-1 --since 30s 2>&1 | grep -i "405\|error" | head -20
```
Expected: no `405` lines after the restart.

- [ ] **Step 4.6: Trigger a live agent turn to flush a trace through**

Run:
```bash
MASTER_KEY=$(cat secrets/litellm-master-key 2>/dev/null)
curl -fsS -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer ${MASTER_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"vertex_ai/claude-sonnet-4-6","messages":[{"role":"user","content":"Reply with just: pong"}],"max_tokens":10}' \
  | python3 -m json.tool
```
Expected: response contains `"pong"`.

- [ ] **Step 4.7: Verify Phoenix now has traces**

Run:
```bash
docker exec autonomous-agent-litellm-proxy-1 \
  curl -fsS http://phoenix:6006/v1/projects 2>&1 | python3 -m json.tool
```
Expected: `traceCount` > 0 for the `default` project.

- [ ] **Step 4.8: Commit + push + open PR**

Run:
```bash
git add deploy/otel/collector.dev.yaml deploy/otel/collector.prod.yaml 2>/dev/null
git commit -m "$(cat <<'EOF'
fix(otel): drop /v1/traces suffix in collector endpoint to avoid doubling

The OTLP HTTP exporter SDK auto-appends /v1/traces to the configured
endpoint. With endpoint=http://phoenix:6006/v1/traces, requests went
to /v1/traces/v1/traces → HTTP 405 → 0 traces in Phoenix → acceptance
step 4 (verify traces in Phoenix UI) impossible. Fix is to set the
endpoint to http://phoenix:6006 (just the host).

Verified: a single chat completion through the LiteLLM proxy now
increments Phoenix's traceCount.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push -u origin fix/otel-double-traces-url
gh pr create --base phase/1-completion --head fix/otel-double-traces-url \
  --title "fix(otel): drop /v1/traces suffix in collector endpoint" \
  --body "$(cat <<'EOF'
## Summary
- OTLP HTTP exporter appends /v1/traces to endpoint config; URL was doubling to /v1/traces/v1/traces → HTTP 405 → 0 traces in Phoenix.
- Fix: set endpoint to http://phoenix:6006 in collector.dev.yaml (+ collector.prod.yaml if same).

## Test plan
- [x] Collector logs show no 405 after restart
- [x] One live chat completion increments Phoenix traceCount
- [ ] CI green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4.9: After CI green, squash-merge**

Run:
```bash
gh pr merge --squash --delete-branch
git checkout phase/1-completion && git pull --ff-only
```

### Task 5: PR α-0.2 — Publish Phoenix ports `4317` + `6006` from base compose

**Files:**
- Modify: `deploy/docker-compose.yml` (add `ports:` to phoenix service)
- Modify: `deploy/docker-compose.dev.yml` (remove now-redundant port publish entries for phoenix)

- [ ] **Step 5.1: Create branch**

Run:
```bash
git checkout phase/1-completion
git checkout -b fix/phoenix-port-publish-in-base-compose
```

- [ ] **Step 5.2: Inspect current phoenix service in both compose files**

Run:
```bash
awk '/^  phoenix:/,/^  [a-z][a-z-]*:/' deploy/docker-compose.yml | head -30
echo "---"
awk '/^  phoenix:/,/^  [a-z][a-z-]*:/' deploy/docker-compose.dev.yml | head -30
```
Expected: base compose has no `ports:` for phoenix; dev override has `ports: ["4317:4317", "6006:6006"]` or similar.

- [ ] **Step 5.3: Add `ports:` block to phoenix service in `deploy/docker-compose.yml`**

Use Edit:
```yaml
# Look for the existing phoenix: service block. Add under it:
  phoenix:
    image: arizephoenix/phoenix:latest
    # ... existing config ...
    ports:
      - "4317:4317"   # OTLP gRPC receiver
      - "6006:6006"   # Phoenix UI + OTLP HTTP receiver
```

- [ ] **Step 5.4: Remove redundant phoenix port entries from `deploy/docker-compose.dev.yml`**

Use Edit. The dev override's `phoenix.ports` block is no longer needed if base publishes them.

- [ ] **Step 5.5: Re-up the stack to apply**

Run:
```bash
docker compose -f deploy/docker-compose.yml up -d phoenix
sleep 3
docker port autonomous-agent-phoenix-1
```
Expected: `4317/tcp -> 0.0.0.0:4317` and `6006/tcp -> 0.0.0.0:6006`.

- [ ] **Step 5.6: Verify Phoenix UI reachable from host**

Run:
```bash
curl -fsS http://localhost:6006/ | head -20
```
Expected: HTTP 200; HTML containing `<title>Phoenix</title>` or similar.

- [ ] **Step 5.7: Commit + push + open PR**

Run:
```bash
git add deploy/docker-compose.yml deploy/docker-compose.dev.yml
git commit -m "$(cat <<'EOF'
fix(deploy): publish phoenix ports in base compose so acceptance can reach UI

Phase 1 acceptance step 4 requires the human to open Phoenix at
localhost:6006 in a browser. Previously the port publish lived in
docker-compose.dev.yml only; stacks brought up without -f dev had
no host port published → curl localhost:6006 = connection refused.

Move both 4317 (OTLP gRPC) and 6006 (UI + OTLP HTTP) into base
compose; remove redundant entries from the dev override.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push -u origin fix/phoenix-port-publish-in-base-compose
gh pr create --base phase/1-completion --head fix/phoenix-port-publish-in-base-compose \
  --title "fix(deploy): publish phoenix ports in base compose" \
  --body "$(cat <<'EOF'
## Summary
- Phoenix ports 4317 + 6006 only published via dev override; base compose stack made the UI unreachable from host.
- Moves both ports into base compose; removes redundant dev-override entries.

## Test plan
- [x] docker port autonomous-agent-phoenix-1 shows both ports bound to 0.0.0.0
- [x] curl localhost:6006 returns Phoenix UI shell HTML
- [ ] CI green (compose render check should pass since we're not changing service shape)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5.8: After CI green, squash-merge**

Run:
```bash
gh pr merge --squash --delete-branch
git checkout phase/1-completion && git pull --ff-only
```

### Task 6: PR α-0.3 — Attach `egress` network to hermes service (Telegram DNS fix)

**Files:**
- Modify: `deploy/docker-compose.yml` (hermes service `networks:` list)

- [ ] **Step 6.1: Create branch**

Run:
```bash
git checkout phase/1-completion
git checkout -b fix/hermes-egress-network-for-telegram
```

- [ ] **Step 6.2: Inspect current network attachments for hermes service**

Run:
```bash
awk '/^  hermes:/,/^  [a-z][a-z-]*:/' deploy/docker-compose.yml | grep -A 5 "networks:"
echo "--- top-level networks: ---"
awk '/^networks:/,/^[a-z]/' deploy/docker-compose.yml | head -20
```
Expected: hermes is on `internal` only; top-level `networks:` includes `egress` (used by litellm-proxy and others that need outbound).

- [ ] **Step 6.3: Add `egress` to hermes service's networks list**

Use Edit:
```yaml
  hermes:
    # ... existing config ...
    networks:
      - internal
      - egress     # NEW: needed for Telegram bot api.telegram.org DNS
```

- [ ] **Step 6.4: Recreate hermes container with new network**

Run:
```bash
docker compose -f deploy/docker-compose.yml up -d --force-recreate hermes
sleep 5
```

- [ ] **Step 6.5: Verify DNS resolves to api.telegram.org from inside hermes**

Run:
```bash
docker exec autonomous-agent-hermes-1 nslookup api.telegram.org 2>&1 | head -10
```
Expected: `Address:` line with a real IP (not `server can't find` or `NXDOMAIN`).

- [ ] **Step 6.6: Verify hermes logs no longer show DNS errors**

Run:
```bash
docker logs autonomous-agent-hermes-1 --since 1m 2>&1 | grep -iE "telegram|dns|name.*not.*known" | head -10
```
Expected: no recent `Name or service not known` lines. May see successful Telegram polling activity.

- [ ] **Step 6.7: Optional liveness probe (only if user is online to test) — send `/start` to bot**

Manual: open Telegram on phone, send `/start` to `@Manzelagent_bot`. Expect a reply within ~5s.

- [ ] **Step 6.8: Commit + push + open PR**

Run:
```bash
git add deploy/docker-compose.yml
git commit -m "$(cat <<'EOF'
fix(deploy): attach egress network to hermes so bot can reach api.telegram.org

Hermes container was on the 'internal' network only; the 'egress'
network exists for exactly this case (outbound to external APIs) but
wasn't attached. Result: DNS resolution for api.telegram.org failed
with "Name or service not known" → Telegram bot non-functional →
Phase 1 acceptance step 1 (10 manual Telegram messages) impossible.

Verified: nslookup api.telegram.org from inside the container now
returns an IP; DNS error stream in hermes logs has stopped.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push -u origin fix/hermes-egress-network-for-telegram
gh pr create --base phase/1-completion --head fix/hermes-egress-network-for-telegram \
  --title "fix(deploy): attach egress network to hermes for Telegram DNS" \
  --body "$(cat <<'EOF'
## Summary
- Hermes container couldn't resolve api.telegram.org (only on internal network).
- Attaches the existing egress network to the hermes service.

## Test plan
- [x] nslookup api.telegram.org succeeds from inside container
- [x] No more "Name or service not known" in hermes logs
- [ ] Optional: send /start to @Manzelagent_bot from phone, expect reply
- [ ] CI green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6.9: After CI green, squash-merge**

Run:
```bash
gh pr merge --squash --delete-branch
git checkout phase/1-completion && git pull --ff-only
```

### Task 7: PR α-0.4 — Healthcheck cron dual-fix (closes issue #29)

**Files:**
- Modify: `scripts/healthcheck-ping.sh:29` (service name)
- Modify: `scripts/healthcheck-ping.sh` (add `mkdir -p logs` prelude)
- Modify: `deploy/docker-compose.yml` (add healthcheck block to hermes service)
- Modify: `.gitignore` (ensure `logs/` is ignored)

- [ ] **Step 7.1: Create branch**

Run:
```bash
git checkout phase/1-completion
git checkout -b fix/healthcheck-cron-dual-fix
```

- [ ] **Step 7.2: Inspect current script line 29 + compose hermes healthcheck**

Run:
```bash
sed -n '25,35p' scripts/healthcheck-ping.sh
echo "---"
awk '/^  hermes:/,/^  [a-z][a-z-]*:/' deploy/docker-compose.yml | head -30
echo "---"
grep "^logs" .gitignore || echo "(logs/ not in .gitignore)"
```
Expected: script line 29 references `hermes-agent`; hermes compose service has no `healthcheck:` block; `logs/` absent from `.gitignore`.

- [ ] **Step 7.3: Edit script — fix service name + add mkdir prelude**

Use Edit twice on `scripts/healthcheck-ping.sh`:
```
old_string: "docker compose ps hermes-agent"
new_string: "docker compose ps hermes"
```

Then add at the top of the script (after the shebang + first comment block) a `mkdir -p` line to ensure logs dir exists:
```bash
# Use Edit to insert after the existing `set -euo pipefail` line:
old_string: "set -euo pipefail"
new_string: "set -euo pipefail
mkdir -p \"$(dirname \"$0\")/../logs\""
```

- [ ] **Step 7.4: Edit `deploy/docker-compose.yml` — add healthcheck to hermes service**

Use Edit to insert a `healthcheck:` block under the hermes service definition. Use a minimal probe — `pgrep -f hermes-cli` works if the CLI process is named that; otherwise a curl to localhost gateway endpoint:
```yaml
  hermes:
    # ... existing config ...
    healthcheck:
      test: ["CMD-SHELL", "pgrep -f hermes-cli || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
```

- [ ] **Step 7.5: Add `logs/` to `.gitignore` (if absent)**

Use Edit:
```
old_string: "<existing last line of .gitignore>"
new_string: "<existing last line>
logs/"
```

- [ ] **Step 7.6: Recreate hermes to apply healthcheck**

Run:
```bash
docker compose -f deploy/docker-compose.yml up -d --force-recreate hermes
sleep 45  # exceed start_period
docker inspect autonomous-agent-hermes-1 --format '{{.State.Health.Status}}'
```
Expected: `healthy` (or `starting` if checked too soon — retry after another 30s).

- [ ] **Step 7.7: Manually run the cron script to verify it succeeds now**

Run:
```bash
./scripts/healthcheck-ping.sh
echo "exit code: $?"
ls -la logs/
cat logs/healthcheck.log 2>/dev/null | tail -5
```
Expected: exit code 0; `logs/healthcheck.log` exists and contains a successful ping line.

- [ ] **Step 7.8: Commit + push + open PR (with `Closes #29` body)**

Run:
```bash
git add scripts/healthcheck-ping.sh deploy/docker-compose.yml .gitignore
git commit -m "$(cat <<'EOF'
fix(healthcheck): correct service name + add compose healthcheck + ensure logs dir

Three coupled defects keeping issue #29 ("AutonomousAgent is DOWN") open:
1. scripts/healthcheck-ping.sh:29 looked for compose service "hermes-agent";
   actual service is "hermes" → grep never matched → script always pinged
   ${URL}/fail.
2. The hermes service in docker-compose.yml had no healthcheck: block, so
   even with the correct service name, .State.Health would be empty.
3. The cron's >> logs/healthcheck.log redirect silently no-op'd because
   logs/ didn't exist at the repo root.

Fixes all three:
- Service-name replacement at the grep site.
- Adds a minimal pgrep-based healthcheck to the hermes service (5s timeout,
  3 retries, 30s start_period).
- Adds an idempotent mkdir -p in the script's prelude; adds logs/ to
  .gitignore.

Manually verified: ./scripts/healthcheck-ping.sh exits 0 with the stack
healthy; logs/healthcheck.log gets a non-empty success line.

Closes #29.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push -u origin fix/healthcheck-cron-dual-fix
gh pr create --base phase/1-completion --head fix/healthcheck-cron-dual-fix \
  --title "fix(healthcheck): correct service name + add compose healthcheck" \
  --body "$(cat <<'EOF'
## Summary
- 3 defects fixed in one PR: script grep target ("hermes-agent" → "hermes"); missing healthcheck block in compose; missing logs/ dir.
- Closes #29 (will close on merge).

## Test plan
- [x] Manual: `./scripts/healthcheck-ping.sh` exits 0 with stack healthy
- [x] `docker inspect autonomous-agent-hermes-1 --format '{{.State.Health.Status}}'` returns "healthy"
- [x] `logs/healthcheck.log` gets a success line
- [ ] CI green (shellcheck must pass on the modified script)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 7.9: After CI green, squash-merge + verify issue #29 closed**

Run:
```bash
gh pr merge --squash --delete-branch
gh issue view 29 --json state
git checkout phase/1-completion && git pull --ff-only
```
Expected: PR merged; issue #29 state is `CLOSED` (auto-closed by `Closes #29` in commit body).

### Task 8: Phase α-0 end-to-end verification

**Files:** none (verification only)

- [ ] **Step 8.1: Restart the full stack to ensure all 4 fixes apply cleanly together**

Run:
```bash
docker compose -f deploy/docker-compose.yml down
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up -d
sleep 30
```

- [ ] **Step 8.2: Smoke test 7/7 PASS**

Run:
```bash
bash scripts/smoke.sh
```
Expected: `✅ All 7 smoke checks passed`.

- [ ] **Step 8.3: Phoenix has fresh traces**

Run:
```bash
curl -fsS http://localhost:6006/v1/projects 2>&1 | python3 -m json.tool
```
Expected: at least one project with `traceCount > 0`.

- [ ] **Step 8.4: Hermes DNS still healthy**

Run:
```bash
docker exec autonomous-agent-hermes-1 nslookup api.telegram.org 2>&1 | head -5
```
Expected: real IP returned.

- [ ] **Step 8.5: Healthcheck cron has produced a ping line in the last 5 min**

Run:
```bash
tail -5 logs/healthcheck.log 2>/dev/null
```
Expected: at least one timestamped success entry. (If empty, wait one cron cycle and re-check.)

---

## Phase α: Tasks 6 + 20b + P1-6 + session briefs

### Task 9: PR α.1 — APPEND `anchors:` and `evaluators:` sections to `config/limits.yaml`

**Files:**
- Modify: `config/limits.yaml` (APPEND-only)
- Verify: `lib/limits_validator.py` accepts the result

- [ ] **Step 9.1: Create branch**

Run:
```bash
git checkout phase/1-completion
git checkout -b feat/limits-anchors-and-evaluators
```

- [ ] **Step 9.2: Inspect current EOF state to know where to append**

Run:
```bash
tail -20 config/limits.yaml
```
Expected: see user's existing keys (`budget.daily_usd_cap: 500`, `agent.dynamic_guardrails: true`, etc.). Note the last line so the APPEND adds a clean newline separator.

- [ ] **Step 9.3: APPEND `anchors:` section (Task 6)**

Use the Write tool to overwrite `config/limits.yaml` with the existing content + appended sections, OR use a shell heredoc append. Recommended: read the file, append two YAML sections, re-write.

The appended content:
```yaml

anchors:
  max_clarification_questions: 6
  lock_confidence_threshold: 0.85
  draft_silence_lock_h: 12
  draft_locked_silence_escalate_h: 24
  spec_storage_dir: /data/specs

evaluators:
  axes: [code-correctness, safety, scope-fit, completeness]
  consensus:
    accept_threshold: 3   # of 4 judges
    reject_threshold: 3
    on_split: escalate_to_5th_judge
    fifth_judge_model: vertex_ai/claude-opus-4-7
  rejection_repeat_threshold: 3
  judge_timeout_s: 120
  parallel_judges_max: 4
  per_axis_model:
    code-correctness: vertex_ai/claude-sonnet-4-6
    safety: vertex_ai/claude-opus-4-7
    scope-fit: vertex_ai/claude-sonnet-4-6
    completeness: vertex_ai/gemini-3.1-pro-preview
```

- [ ] **Step 9.4: Verify schema validates**

Run:
```bash
python lib/limits_validator.py config/limits.yaml
```
Expected: exit 0 with no error. (If validator complains about unknown keys, either the schema is too strict or one of the appended keys is misspelled — diff against the plan source-of-truth and re-add.)

- [ ] **Step 9.5: Verify smoke check 6 still passes**

Run:
```bash
bash -c 'cd . && .venv/bin/python -m lib.limits_validator config/limits.yaml'
```
Expected: exit 0 silently.

- [ ] **Step 9.6: Verify user's existing keys preserved**

Run:
```bash
grep -E "daily_usd_cap: 500|dynamic_guardrails: true|telegram_escalation_timeout_h: 24" config/limits.yaml
```
Expected: all 3 lines present (the APPEND did not clobber them).

- [ ] **Step 9.7: Commit + push + open PR**

Run:
```bash
git add config/limits.yaml
git commit -m "$(cat <<'EOF'
feat(config): append anchors + evaluators sections to limits.yaml

Task 6 (anchors:) and Task 20b (evaluators:) per the Phase 1 plan.
APPEND-only at end of file; preserves user's existing keys
(daily_usd_cap: 500, dynamic_guardrails: true,
 telegram_escalation_timeout_h: 24).

Anchors section: max_clarification_questions=6,
lock_confidence_threshold=0.85, draft silence/escalate windows,
spec storage dir.

Evaluators section: 4 axes (code-correctness, safety, scope-fit,
completeness); 3-of-4 consensus thresholds with 5th-judge tiebreak;
per-axis model mapping with the completeness judge routed to
gemini-3.1-pro-preview (model id verified at the Vertex global
endpoint per the 2026-05-17 handoff).

Verified: python lib/limits_validator.py config/limits.yaml passes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push -u origin feat/limits-anchors-and-evaluators
gh pr create --base phase/1-completion --head feat/limits-anchors-and-evaluators \
  --title "feat(config): append anchors + evaluators sections to limits.yaml" \
  --body "$(cat <<'EOF'
## Summary
- Tasks 6 + 20b: APPEND-only addition of anchors: and evaluators: top-level keys.
- Preserves user's existing budget + agent keys from commit 0b0cb06.
- Unblocks lib/anchors and lib/evaluators runtime config consumption.

## Test plan
- [x] python lib/limits_validator.py config/limits.yaml passes
- [x] grep confirms user's daily_usd_cap/dynamic_guardrails/telegram_escalation_timeout_h preserved
- [ ] CI green (smoke check 6 = limits.yaml validates)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 9.8: After CI green, squash-merge**

Run:
```bash
gh pr merge --squash --delete-branch
git checkout phase/1-completion && git pull --ff-only
```

### Task 10: PR α.2 — P1-6 Durability subsystem (bundled)

**Files:**
- Create: `lib/durability/__init__.py` (scaffolded register())
- Create: `lib/durability/failure_matrix.py` (33 F-codes → trichotomy class)
- Create: `lib/durability/trichotomy.py` (classifier + retry policy)
- Create: `lib/durability/escalation.py` (24h Telegram silence watcher)
- Modify: `docs/architecture/failure-matrix.md` (extend 16 → 33 modes)
- Modify: `deploy/docker-compose.yml` (add `escalation-watcher` sidecar service)
- Modify: `config/limits.yaml` (APPEND `durability:` and `retries:` sections — see Step 10.3)
- Create: `tests/unit/test_failure_matrix.py`
- Create: `tests/unit/test_trichotomy.py`
- Create: `tests/unit/test_durability_plugin.py` (register() contract tests)
- Create: `tests/integration/test_p1_6_failure_matrix.py` (5 representative modes against live stack)

- [ ] **Step 10.1: Create branch**

Run:
```bash
git checkout phase/1-completion
git checkout -b feat/p1-6-durability-subsystem
```

- [ ] **Step 10.2: Write failing test — `tests/unit/test_failure_matrix.py`**

Create the file:
```python
"""Unit tests for the 33-mode failure matrix lookup table."""
from lib.durability.failure_matrix import FAILURE_MATRIX, TrichotomyClass, lookup


def test_all_33_codes_present():
    expected_codes = {f"F{i}" for i in range(1, 34)}
    assert set(FAILURE_MATRIX.keys()) == expected_codes


def test_every_code_maps_to_valid_class():
    valid_classes = {TrichotomyClass.FAIL_LOUD, TrichotomyClass.FAIL_SOFT, TrichotomyClass.SELF_HEAL}
    for code, entry in FAILURE_MATRIX.items():
        assert entry["class"] in valid_classes, f"{code} maps to invalid class {entry['class']}"


def test_no_duplicate_codes():
    codes_in_matrix = list(FAILURE_MATRIX.keys())
    assert len(codes_in_matrix) == len(set(codes_in_matrix))


def test_lookup_returns_entry():
    entry = lookup("F1")
    assert entry["class"] in {TrichotomyClass.FAIL_LOUD, TrichotomyClass.FAIL_SOFT, TrichotomyClass.SELF_HEAL}
    assert "description" in entry


def test_lookup_unknown_code_raises():
    import pytest
    with pytest.raises(KeyError):
        lookup("F999")
```

- [ ] **Step 10.3: APPEND new `durability:` + `retries:` sections to `config/limits.yaml`**

Append:
```yaml

durability:
  checkpoint:
    interval_steps: 5
    retention_count: 50
    keep_every_nth: 100
    autoresume_enabled: true
  escalation:
    watcher_interval_s: 300

retries:
  self_heal:
    max_retries: 3
    backoff_strategy: exponential_with_jitter
    base_delay_ms: 500
    max_delay_ms: 30000
    jitter_range_pct: 25
```

Run `python lib/limits_validator.py config/limits.yaml` — expected pass.

(Note: `durability:` here is the P1-6 portion. P1-3 will extend the `durability.checkpoint:` subkey later. P1-4 will add a separate `memory:` top-level key.)

- [ ] **Step 10.4: Create `lib/durability/__init__.py` skeleton (no register() body yet — TDD)**

```python
"""Durability plugin entry point."""
# Implementation to follow per TDD steps below.
```

Touch the file so imports won't fail in upcoming test runs:
```bash
mkdir -p lib/durability
touch lib/durability/__init__.py
```

- [ ] **Step 10.5: Run failing tests to confirm they fail correctly**

Run:
```bash
.venv/bin/pytest tests/unit/test_failure_matrix.py -v
```
Expected: all 5 tests FAIL with `ModuleNotFoundError: No module named 'lib.durability.failure_matrix'`.

- [ ] **Step 10.6: Write `lib/durability/failure_matrix.py` to pass the tests**

```python
"""33-mode failure matrix mapping F-codes to trichotomy class + handler reference.

Source of truth: docs/architecture/failure-matrix.md (extended in this PR
from the initial 16-mode draft to 33 modes per the AA-Atelier sweep).
"""
from enum import Enum
from typing import Dict, Any


class TrichotomyClass(str, Enum):
    FAIL_LOUD = "fail_loud"
    FAIL_SOFT = "fail_soft"
    SELF_HEAL = "self_heal"


FAILURE_MATRIX: Dict[str, Dict[str, Any]] = {
    # === Self-heal (transient, retry with backoff) ===
    "F1": {"class": TrichotomyClass.SELF_HEAL, "description": "Rate limit (429)", "handler": "retry_with_backoff"},
    "F2": {"class": TrichotomyClass.SELF_HEAL, "description": "Network timeout", "handler": "retry_with_backoff"},
    "F3": {"class": TrichotomyClass.SELF_HEAL, "description": "Transient DNS resolution failure", "handler": "retry_with_backoff"},
    "F4": {"class": TrichotomyClass.SELF_HEAL, "description": "5xx from upstream LLM API", "handler": "retry_with_backoff"},
    "F5": {"class": TrichotomyClass.SELF_HEAL, "description": "Connection reset by peer", "handler": "retry_with_backoff"},
    "F6": {"class": TrichotomyClass.SELF_HEAL, "description": "Temporary tool sandbox crash", "handler": "restart_sandbox_and_retry"},
    "F7": {"class": TrichotomyClass.SELF_HEAL, "description": "Honcho/Chroma temporary unavailable", "handler": "retry_with_backoff"},
    "F8": {"class": TrichotomyClass.SELF_HEAL, "description": "Stale Vertex AI auth token", "handler": "refresh_adc_and_retry"},
    "F9": {"class": TrichotomyClass.SELF_HEAL, "description": "Race on Kanban claim_lock", "handler": "retry_with_backoff"},
    "F10": {"class": TrichotomyClass.SELF_HEAL, "description": "Checkpoint write contention", "handler": "retry_with_backoff"},
    "F11": {"class": TrichotomyClass.SELF_HEAL, "description": "Gemini thinking-tokens silent truncation (max_tokens too low)", "handler": "retry_with_higher_max_tokens"},

    # === Fail-soft (degrade and continue) ===
    "F12": {"class": TrichotomyClass.FAIL_SOFT, "description": "Chroma vector store down — disable semantic memory", "handler": "disable_chroma_for_session"},
    "F13": {"class": TrichotomyClass.FAIL_SOFT, "description": "OTel collector unreachable — log spans locally instead", "handler": "fallback_local_log"},
    "F14": {"class": TrichotomyClass.FAIL_SOFT, "description": "Github MCP server unavailable — skip github-tagged tools", "handler": "skip_tool_class"},
    "F15": {"class": TrichotomyClass.FAIL_SOFT, "description": "Skill extractor temporarily failing — defer extraction", "handler": "defer_extraction"},
    "F16": {"class": TrichotomyClass.FAIL_SOFT, "description": "Single evaluator judge timeout — proceed with N-1 judges", "handler": "drop_judge_continue_consensus"},
    "F17": {"class": TrichotomyClass.FAIL_SOFT, "description": "Phoenix UI down — traces still collected, viewer offline", "handler": "log_and_continue"},
    "F18": {"class": TrichotomyClass.FAIL_SOFT, "description": "Honcho metadata API slow — use cached metadata", "handler": "use_cached"},
    "F19": {"class": TrichotomyClass.FAIL_SOFT, "description": "Per-task token budget exceeded — truncate response", "handler": "truncate_and_warn"},
    "F20": {"class": TrichotomyClass.FAIL_SOFT, "description": "MEMORY/REJECTED.md inject would exceed context budget — skip inject", "handler": "skip_inject"},

    # === Fail-loud (halt + alert via Telegram + snapshot) ===
    "F21": {"class": TrichotomyClass.FAIL_LOUD, "description": "Daily budget cap exceeded", "handler": "halt_alert_snapshot"},
    "F22": {"class": TrichotomyClass.FAIL_LOUD, "description": "Critical secret leak detected by scrubber", "handler": "halt_alert_snapshot"},
    "F23": {"class": TrichotomyClass.FAIL_LOUD, "description": "Sandbox escape attempt detected", "handler": "halt_alert_snapshot"},
    "F24": {"class": TrichotomyClass.FAIL_LOUD, "description": "Multi-judge consensus failure (split vote, no 5th judge available)", "handler": "halt_alert_snapshot"},
    "F25": {"class": TrichotomyClass.FAIL_LOUD, "description": "TaskSpec lock-time clarification loop exceeded max questions", "handler": "halt_alert_request_approval"},
    "F26": {"class": TrichotomyClass.FAIL_LOUD, "description": "3-strike approach rejection (same fingerprint, REJECTED.md trigger)", "handler": "halt_alert_snapshot"},
    "F27": {"class": TrichotomyClass.FAIL_LOUD, "description": "Persistent Vertex AI auth failure after retry+refresh", "handler": "halt_alert_snapshot"},
    "F28": {"class": TrichotomyClass.FAIL_LOUD, "description": "Disk full on checkpoint write", "handler": "halt_alert_snapshot"},
    "F29": {"class": TrichotomyClass.FAIL_LOUD, "description": "Hermes Kanban DB corruption / migration failure", "handler": "halt_alert_snapshot"},
    "F30": {"class": TrichotomyClass.FAIL_LOUD, "description": "Approval-required tool fired without approval (policy violation)", "handler": "halt_alert_snapshot"},
    "F31": {"class": TrichotomyClass.FAIL_LOUD, "description": "Egress allowlist violation attempt", "handler": "halt_alert_snapshot"},
    "F32": {"class": TrichotomyClass.FAIL_LOUD, "description": "24h Telegram silence on blocked card → escalate to triage", "handler": "alert_user_escalate_kanban"},
    "F33": {"class": TrichotomyClass.FAIL_LOUD, "description": "F-code lookup failed (unclassified exception)", "handler": "halt_alert_snapshot"},
}


def lookup(code: str) -> Dict[str, Any]:
    """Look up an F-code; raises KeyError if unknown."""
    return FAILURE_MATRIX[code]
```

Run:
```bash
.venv/bin/pytest tests/unit/test_failure_matrix.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 10.7: Write failing test — `tests/unit/test_trichotomy.py`**

```python
"""Unit tests for the trichotomy classifier + retry policy."""
import time
import pytest
from unittest.mock import MagicMock, patch
from lib.durability import trichotomy
from lib.durability.failure_matrix import TrichotomyClass


class FakeRateLimitError(Exception):
    pass


class FakeTimeoutError(TimeoutError):
    pass


def test_classify_rate_limit_to_F1_self_heal():
    err = FakeRateLimitError("HTTP 429 rate_limit_exceeded")
    code = trichotomy.classify(err)
    assert code == "F1"


def test_classify_timeout_to_F2_self_heal():
    err = FakeTimeoutError("upstream timed out after 60s")
    code = trichotomy.classify(err)
    assert code == "F2"


def test_classify_unknown_exception_to_F33_fail_loud():
    err = RuntimeError("something exotic")
    code = trichotomy.classify(err)
    assert code == "F33"


def test_retry_policy_exponential_backoff_within_tolerance():
    # 3 retries: ~500ms, ~1000ms, ~2000ms (with jitter)
    delays = [trichotomy.backoff_delay(attempt=i) for i in range(1, 4)]
    assert 250 <= delays[0] <= 750
    assert 500 <= delays[1] <= 1500
    assert 1000 <= delays[2] <= 3000


def test_retry_policy_caps_at_max_delay():
    delay = trichotomy.backoff_delay(attempt=20)
    assert delay <= 30000   # max_delay_ms from limits.yaml
```

Run to confirm failure:
```bash
.venv/bin/pytest tests/unit/test_trichotomy.py -v
```
Expected: all 5 tests FAIL (`ModuleNotFoundError` or `AttributeError`).

- [ ] **Step 10.8: Write `lib/durability/trichotomy.py` to pass the tests**

```python
"""Failure classifier + retry policy. Consumes config/limits.yaml retries.self_heal.*."""
import random
import re
from typing import Optional

from lib.durability.failure_matrix import lookup, TrichotomyClass


# Pattern-based classifier — fast string matching against exception message.
# Order matters: more specific patterns first.
_CLASSIFIERS = [
    (re.compile(r"rate.?limit|429|too many requests", re.I), "F1"),
    (re.compile(r"timed? out|timeout|deadline exceeded", re.I), "F2"),
    (re.compile(r"name or service not known|dns|nxdomain", re.I), "F3"),
    (re.compile(r"5\d\d|internal server error|bad gateway", re.I), "F4"),
    (re.compile(r"connection reset", re.I), "F5"),
    (re.compile(r"sandbox.*(crash|exit)", re.I), "F6"),
    (re.compile(r"chroma.*unavailable", re.I), "F7"),
    (re.compile(r"vertex.*(auth|credentials)|invalid token", re.I), "F8"),
    (re.compile(r"claim.?lock|claim contention", re.I), "F9"),
    (re.compile(r"checkpoint.*(contention|locked)", re.I), "F10"),
    (re.compile(r"max_tokens too low|thinking tokens truncated", re.I), "F11"),
    (re.compile(r"chroma.*down", re.I), "F12"),
    (re.compile(r"otel.*unreachable", re.I), "F13"),
    (re.compile(r"github.?mcp.*unavailable", re.I), "F14"),
    (re.compile(r"skill.?extractor.*fail", re.I), "F15"),
    (re.compile(r"judge.*timeout", re.I), "F16"),
    (re.compile(r"daily.*budget.*exceeded", re.I), "F21"),
    (re.compile(r"secret.?leak|REDACTED:critical", re.I), "F22"),
    (re.compile(r"sandbox.*escape", re.I), "F23"),
    (re.compile(r"consensus.*(fail|split)", re.I), "F24"),
    (re.compile(r"clarification.*max.*questions", re.I), "F25"),
    (re.compile(r"3.?strike|consecutive_rejections.*3", re.I), "F26"),
    (re.compile(r"disk full|no space left", re.I), "F28"),
    (re.compile(r"kanban.*(corrupt|migration)", re.I), "F29"),
    (re.compile(r"approval.*required.*without", re.I), "F30"),
    (re.compile(r"egress.*denied|allowlist.*violation", re.I), "F31"),
]


def classify(err: Exception) -> str:
    """Classify an exception to an F-code. Falls through to F33 (fail-loud unknown)."""
    msg = f"{type(err).__name__}: {err}"
    for pat, code in _CLASSIFIERS:
        if pat.search(msg):
            return code
    return "F33"


def trichotomy_class(err: Exception) -> TrichotomyClass:
    """Convenience: classify the error then return its trichotomy class."""
    return lookup(classify(err))["class"]


def backoff_delay(attempt: int, base_ms: int = 500, max_ms: int = 30000, jitter_pct: int = 25) -> int:
    """Exponential backoff with jitter. attempt is 1-indexed."""
    raw = base_ms * (2 ** (attempt - 1))
    raw = min(raw, max_ms)
    jitter = raw * (jitter_pct / 100.0)
    delay = raw + random.uniform(-jitter, jitter)
    return max(0, int(delay))


def before_tool_call(ctx, tool_call):
    """Hook: registered as pre_tool_call. Currently no-op; reserved for future policy."""
    return None


def after_tool_call(ctx, tool_call, result_or_error):
    """Hook: registered as post_tool_call. If result_or_error is an exception, classify and
    dispatch per trichotomy class."""
    if isinstance(result_or_error, Exception):
        code = classify(result_or_error)
        cls = lookup(code)["class"]
        # Emit OTel span for observability (acceptance step 4 visibility).
        try:
            from opentelemetry import trace
            tracer = trace.get_tracer("hermes.durability")
            with tracer.start_as_current_span("durability.classify") as span:
                span.set_attribute("f_code", code)
                span.set_attribute("trichotomy_class", cls.value)
        except ImportError:
            pass  # OTel SDK absent in unit tests; skip silently.
        # NOTE: actual retry/escalation handling is invoked elsewhere; this hook is
        # observation-only. Synchronous retry happens at the tool-call site.
    return None
```

Run:
```bash
.venv/bin/pytest tests/unit/test_trichotomy.py -v
```
Expected: all 5 tests PASS.

- [ ] **Step 10.9: Write `lib/durability/escalation.py` (24h Telegram silence watcher)**

```python
"""24h Telegram silence watcher. Designed to run periodically (cron or docker
sidecar) — scans Hermes Kanban DB for blocked cards with stale last_heartbeat_at
and emits escalation alerts.

Consumes config/limits.yaml agent.telegram_escalation_timeout_h.
"""
import os
import sqlite3
import time
from pathlib import Path
from typing import List, Tuple

# Default Kanban DB path inside the hermes container is /root/.hermes/kanban/kanban.db;
# mounted to a host volume per docker-compose.yml. Override via env for tests.
KANBAN_DB_PATH = os.environ.get("HERMES_KANBAN_DB", "/root/.hermes/kanban/kanban.db")


def find_stale_blocked_cards(threshold_h: int = 24, db_path: str = None) -> List[Tuple[int, str, float]]:
    """Return [(card_id, title, last_heartbeat_age_h), ...] for cards stuck in blocked
    longer than threshold_h hours."""
    db_path = db_path or KANBAN_DB_PATH
    if not Path(db_path).exists():
        return []
    now = time.time()
    threshold_s = threshold_h * 3600
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, last_heartbeat_at FROM tasks "
            "WHERE status = 'blocked' AND (? - last_heartbeat_at) > ?",
            (now, threshold_s),
        ).fetchall()
    finally:
        conn.close()
    return [(r[0], r[1], (now - r[2]) / 3600) for r in rows]


def emit_escalation(card_id: int, title: str, age_h: float) -> None:
    """Send a Telegram alert for an escalated card. Stubbed for now; real impl
    delegates to the Hermes Telegram bridge which P1-5 (session-e) will land."""
    # TODO(P1-5): replace with telegram_bridge.send_alert(...) once available.
    print(f"[ESCALATION F32] card={card_id} title={title!r} blocked_age_h={age_h:.1f}")


def run_once(threshold_h: int = 24, db_path: str = None) -> int:
    """One pass. Returns count of cards escalated."""
    stale = find_stale_blocked_cards(threshold_h=threshold_h, db_path=db_path)
    for card_id, title, age_h in stale:
        emit_escalation(card_id, title, age_h)
    return len(stale)


if __name__ == "__main__":
    n = run_once()
    print(f"escalated {n} card(s)")
```

(No unit test for escalation — it talks to an external SQLite DB; integration test in Step 10.13 covers the live path. The standalone runner makes it easy to validate manually.)

- [ ] **Step 10.10: Write the scaffolded `lib/durability/__init__.py` register()**

Replace the empty `__init__.py` with the full scaffold:

```python
"""Durability plugin: failure-matrix-driven retry policy, checkpoint-resume (P1-3),
and REJECTED-inject (P1-4). P1-6 lands the real hook bodies here; P1-3 and P1-4
fill the on_session_start stubs in subsequent PRs."""
from lib.durability import failure_matrix, trichotomy, escalation

__all__ = ["register", "failure_matrix", "trichotomy", "escalation"]


def register(ctx):
    # P1-6 hooks (real implementations from this PR)
    ctx.register_hook("pre_tool_call",  trichotomy.before_tool_call)
    ctx.register_hook("post_tool_call", trichotomy.after_tool_call)

    # P1-3 + P1-4 hooks (stubs; sessions c + d fill in)
    # ORDER MATTERS: resume must run first so REJECTED-inject can read active TaskSpec
    ctx.register_hook("on_session_start", _p1_3_resume_session)   # session-c fills
    ctx.register_hook("on_session_start", _p1_4_inject_rejected)  # session-d fills


def _p1_3_resume_session(ctx):
    """TODO(P1-3 session-c): on container start, scan /data/checkpoints/ for incomplete
    sessions and rehydrate the latest checkpoint per session. See lib/durability/checkpoint.py
    (will be added by session-c)."""
    return None


def _p1_4_inject_rejected(ctx):
    """TODO(P1-4 session-d): read active TaskSpec.intent_category, load matching unexpired
    REJECTED.md entries, inject as system message: 'Past failed approaches for this kind
    of task — DO NOT repeat:'. See lib/memory/rejected.py (will be added by session-d)."""
    return None
```

- [ ] **Step 10.11: Write test for the register() contract — `tests/unit/test_durability_plugin.py`**

```python
"""Tests the register() contract for the durability plugin.

Mirrors the pattern from tests/unit/test_anchors_plugin.py + test_evaluators_plugin.py.
"""
from unittest.mock import MagicMock
from lib.durability import register


def test_register_wires_pre_tool_call_hook():
    ctx = MagicMock()
    register(ctx)
    hook_names = [call.args[0] for call in ctx.register_hook.call_args_list]
    assert "pre_tool_call" in hook_names


def test_register_wires_post_tool_call_hook():
    ctx = MagicMock()
    register(ctx)
    hook_names = [call.args[0] for call in ctx.register_hook.call_args_list]
    assert "post_tool_call" in hook_names


def test_register_wires_on_session_start_in_correct_order():
    """P1-3's resume hook MUST be registered before P1-4's inject hook so the call-
    sequence ordering inside Hermes' on_session_start dispatch matches design spec L332."""
    ctx = MagicMock()
    register(ctx)
    session_start_calls = [
        call for call in ctx.register_hook.call_args_list
        if call.args[0] == "on_session_start"
    ]
    assert len(session_start_calls) == 2
    callback_names = [call.args[1].__name__ for call in session_start_calls]
    assert callback_names == ["_p1_3_resume_session", "_p1_4_inject_rejected"], \
        "Resume hook MUST register before REJECTED-inject hook per design-alignment spec L332"


def test_stub_callbacks_return_none():
    from lib.durability import _p1_3_resume_session, _p1_4_inject_rejected
    ctx = MagicMock()
    assert _p1_3_resume_session(ctx) is None
    assert _p1_4_inject_rejected(ctx) is None
```

Run:
```bash
.venv/bin/pytest tests/unit/test_durability_plugin.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 10.12: Extend `docs/architecture/failure-matrix.md` from 16 → 33 modes**

Open the file; append F17-F33 as new rows in the same table format used for F1-F16. Each row should match the entries in `lib/durability/failure_matrix.py` (description, trichotomy class). Keep the existing F1-F16 rows untouched.

Verify:
```bash
grep -cE "^\| F[0-9]+ \|" docs/architecture/failure-matrix.md
```
Expected: 33 (one row per F-code).

- [ ] **Step 10.13: Write integration test — `tests/integration/test_p1_6_failure_matrix.py`**

```python
"""Integration test exercising 5 representative failure modes against the live stack.

Requires: live litellm-proxy on localhost:4000 + secrets/litellm-master-key.
Skips cleanly if proxy unreachable.
"""
import os
import pytest
import requests

PROXY_URL = "http://localhost:4000"
KEY_FILE = "secrets/litellm-master-key"


def _proxy_reachable():
    try:
        return requests.get(f"{PROXY_URL}/health/readiness", timeout=2).ok
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _proxy_reachable(), reason="LiteLLM proxy not running")


def _master_key():
    return open(KEY_FILE).read().strip() if os.path.exists(KEY_FILE) else None


def test_F1_rate_limit_classifies_as_self_heal():
    """Synthetic: invoke trichotomy.classify with a rate-limit-shaped exception."""
    from lib.durability.trichotomy import classify
    err = RuntimeError("HTTP 429 rate_limit_exceeded: too many requests")
    assert classify(err) == "F1"


def test_F2_timeout_classifies_as_self_heal():
    from lib.durability.trichotomy import classify
    err = TimeoutError("request timed out after 60s")
    assert classify(err) == "F2"


def test_F22_secret_leak_classifies_as_fail_loud():
    from lib.durability.trichotomy import classify, trichotomy_class
    from lib.durability.failure_matrix import TrichotomyClass
    err = RuntimeError("REDACTED:critical aws_secret_key detected in output")
    assert classify(err) == "F22"
    assert trichotomy_class(err) == TrichotomyClass.FAIL_LOUD


def test_F11_gemini_thinking_truncation_classifies_as_self_heal():
    from lib.durability.trichotomy import classify
    err = RuntimeError("Empty content — max_tokens too low for thinking model")
    assert classify(err) == "F11"


def test_F33_unclassified_exception_falls_through_to_fail_loud():
    from lib.durability.trichotomy import classify, trichotomy_class
    from lib.durability.failure_matrix import TrichotomyClass
    err = ValueError("a totally novel error nobody planned for")
    assert classify(err) == "F33"
    assert trichotomy_class(err) == TrichotomyClass.FAIL_LOUD
```

Run:
```bash
.venv/bin/pytest tests/integration/test_p1_6_failure_matrix.py -v
```
Expected: 5/5 PASS if proxy reachable, else 5/5 SKIP cleanly.

- [ ] **Step 10.14a: Create `scripts/escalation_loop.py` (the sidecar entrypoint)**

```python
#!/usr/bin/env python3
"""Periodic 24h Telegram silence watcher; runs in the escalation-watcher sidecar.

Reads thresholds from /config/limits.yaml on each iteration so hot-reloading
limits.yaml works without a sidecar restart.
"""
import sys
import time

from yaml import safe_load

from lib.durability.escalation import run_once


CFG_PATH = "/config/limits.yaml"


def main() -> int:
    while True:
        with open(CFG_PATH) as f:
            cfg = safe_load(f)
        thr = cfg["agent"]["telegram_escalation_timeout_h"]
        interval = cfg["durability"]["escalation"]["watcher_interval_s"]
        n = run_once(threshold_h=thr)
        print(f"escalated {n}", flush=True)
        time.sleep(interval)
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
```

Make it executable:
```bash
chmod +x scripts/escalation_loop.py
```

- [ ] **Step 10.14b: Add `escalation-watcher` sidecar service to `deploy/docker-compose.yml`**

Insert under the `services:` block:
```yaml
  escalation-watcher:
    image: autonomousagent/hermes:0.1.0
    container_name: autonomous-agent-escalation-watcher-1
    command: ["python", "/app/scripts/escalation_loop.py"]
    working_dir: /app
    volumes:
      - ./config/limits.yaml:/config/limits.yaml:ro
      - hermes-data:/root/.hermes
      - ./lib:/app/lib:ro
      - ./scripts:/app/scripts:ro
    environment:
      PYTHONPATH: /app
    networks:
      - internal
      - egress     # for Telegram outbound
    depends_on:
      - hermes
    restart: unless-stopped
```

(NOTE: this sidecar reuses the hermes image; it only needs access to `lib/durability/escalation.py` + the Kanban SQLite DB volume + the `scripts/escalation_loop.py` entrypoint. The script keeps reading limits.yaml on each iteration so the watcher interval is hot-reloadable.)

- [ ] **Step 10.15: Run full unit suite to confirm no regressions**

Run:
```bash
.venv/bin/pytest tests/unit/ -q
```
Expected: 94 pre-existing PASS + new tests from this PR all PASS (101+ total).

- [ ] **Step 10.16: Run integration test against the live stack**

Run:
```bash
.venv/bin/pytest tests/integration/test_p1_6_failure_matrix.py -v
```
Expected: 5/5 PASS.

- [ ] **Step 10.17: Optional — cosmetic F60 string cleanup in consensus.py (audit P0-A downgrade)**

Edit `lib/evaluators/consensus.py` lines 90 + 123: replace the hardcoded `"F60"` strings in `rationale=` fields with `lookup_or_default("F24", "consensus_failure")` calls. (`F60` is not in the 33-code matrix; original cosmetic placeholder. Either pick the closest match — F24 "consensus failure" — or skip this step entirely.)

If skipping: skip Step 10.17.

- [ ] **Step 10.18: Commit + push + open PR**

Run:
```bash
git add lib/durability/ scripts/escalation_loop.py \
        tests/unit/test_failure_matrix.py tests/unit/test_trichotomy.py \
        tests/unit/test_durability_plugin.py tests/integration/test_p1_6_failure_matrix.py \
        docs/architecture/failure-matrix.md deploy/docker-compose.yml config/limits.yaml \
        lib/evaluators/consensus.py 2>/dev/null
git commit -m "$(cat <<'EOF'
feat(durability): add P1-6 failure matrix + trichotomy + escalation watcher

Lands the entire P1-6 Durability subsystem in one bundled PR:
- lib/durability/failure_matrix.py: 33-mode F-code lookup table mapping
  each F-code to trichotomy class (FAIL_LOUD / FAIL_SOFT / SELF_HEAL)
  and a handler reference.
- lib/durability/trichotomy.py: pattern-based exception classifier +
  exponential-backoff-with-jitter retry policy. before/after tool-call
  hooks emit OTel spans for observability.
- lib/durability/escalation.py: standalone 24h Telegram silence watcher
  that scans Hermes Kanban for blocked cards with stale last_heartbeat_at.
- lib/durability/__init__.py: scaffolded register() wiring real P1-6
  pre_tool_call + post_tool_call hooks AND two on_session_start STUBS
  for sessions c (P1-3 resume) + d (P1-4 REJECTED-inject) to fill in
  parallel. Hook order is locked here per design-alignment spec L332.
- docs/architecture/failure-matrix.md: extended from 16 → 33 modes.
- config/limits.yaml: APPENDs durability: + retries: sections.
- deploy/docker-compose.yml: adds escalation-watcher sidecar service
  (reuses hermes image; periodic run_once loop).
- tests/unit/test_{failure_matrix,trichotomy,durability_plugin}.py:
  14 new unit tests asserting matrix invariants + classifier accuracy +
  register() hook ordering (the L332 invariant).
- tests/integration/test_p1_6_failure_matrix.py: 5 representative-mode
  end-to-end checks; skip-guards on proxy availability.

P1-3 (session-c) and P1-4 (session-d) will swap the on_session_start
stubs for real implementations in their respective PRs. Both sessions
edit only the body of their stub function — git auto-merges.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push -u origin feat/p1-6-durability-subsystem
gh pr create --base phase/1-completion --head feat/p1-6-durability-subsystem \
  --title "feat(durability): add P1-6 failure matrix + trichotomy + escalation watcher" \
  --body "$(cat <<'EOF'
## Summary
- Lands the full P1-6 subsystem (Tasks 7-12) as one PR.
- 33-mode failure matrix; trichotomy classifier; escalation watcher; scaffolded register() with stubs for P1-3 + P1-4.

## Files added
- `lib/durability/{__init__,failure_matrix,trichotomy,escalation}.py`
- `tests/unit/test_{failure_matrix,trichotomy,durability_plugin}.py`
- `tests/integration/test_p1_6_failure_matrix.py`

## Files modified
- `docs/architecture/failure-matrix.md` (16 → 33 modes)
- `config/limits.yaml` (APPEND durability: + retries:)
- `deploy/docker-compose.yml` (escalation-watcher sidecar)
- (optional) `lib/evaluators/consensus.py` (cosmetic F60 → matrix lookup)

## Test plan
- [x] 14 new unit tests pass
- [x] 5 integration tests pass against live stack
- [x] register() hook order test enforces the L332 invariant
- [x] No regressions in pre-existing 94 unit tests
- [x] python lib/limits_validator.py config/limits.yaml passes
- [ ] CI green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 10.19: After CI green, squash-merge**

Run:
```bash
gh pr merge --squash --delete-branch
git checkout phase/1-completion && git pull --ff-only
```

### Task 11: PR α.3 — Write 3 session briefs + update coordination ledger

**Files:**
- Create: `docs/superpowers/SESSION-BRIEF-c-2026-05-18.md`
- Create: `docs/superpowers/SESSION-BRIEF-d-2026-05-18.md`
- Create: `docs/superpowers/SESSION-BRIEF-e-2026-05-18.md`
- Modify: `docs/superpowers/session-coordination.md` (add "Active sessions (Phase 1 completion)" subsection)

- [ ] **Step 11.1: Create branch**

Run:
```bash
git checkout phase/1-completion
git checkout -b docs/session-briefs-c-d-e
```

- [ ] **Step 11.2: Write `docs/superpowers/SESSION-BRIEF-c-2026-05-18.md`**

Content (verbatim — engineer can copy):
```markdown
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
```

- [ ] **Step 11.3: Write `docs/superpowers/SESSION-BRIEF-d-2026-05-18.md`**

Same template as Step 11.2, adapted for P1-4:

```markdown
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
```

- [ ] **Step 11.4: Write `docs/superpowers/SESSION-BRIEF-e-2026-05-18.md`**

Same template for P1-5:

```markdown
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
```

- [ ] **Step 11.5: Update `docs/superpowers/session-coordination.md` ledger**

After the existing "Retired sessions" subsection in §"The session-coordination ledger", insert:

```markdown
### Active sessions (Phase 1 completion)

| Session | Track | Owner-since | Status | Branch | Notes |
|---|---|---|---|---|---|
| C | P1-3 (checkpointing) | _(set on claim)_ | not-yet-claimed | session-c/p1-3-* | Fills `_p1_3_resume_session` stub in `lib/durability/__init__.py` |
| D | P1-4 (REJECTED.md) | _(set on claim)_ | not-yet-claimed | session-d/p1-4-* | Fills `_p1_4_inject_rejected` stub in `lib/durability/__init__.py` |
| E | P1-5 (Kanban→Telegram) | _(set on claim)_ | not-yet-claimed | session-e/p1-5-* | Replaces `TODO(P1-5)` in `lib/anchors/__init__.py:55` |
```

- [ ] **Step 11.6: Commit + push + open PR**

Run:
```bash
git add docs/superpowers/SESSION-BRIEF-c-2026-05-18.md \
        docs/superpowers/SESSION-BRIEF-d-2026-05-18.md \
        docs/superpowers/SESSION-BRIEF-e-2026-05-18.md \
        docs/superpowers/session-coordination.md
git commit -m "$(cat <<'EOF'
docs(superpowers): add session briefs c/d/e for Phase 1 parallel execution

Three self-contained briefs that a fresh Claude Code session can read and
immediately claim a Phase 1 completion track:
- SESSION-BRIEF-c: P1-3 Per-step checkpointing + resume (Tasks 22-28)
- SESSION-BRIEF-d: P1-4 REJECTED.md institutional memory (Tasks 29-33)
- SESSION-BRIEF-e: P1-5 Kanban → Telegram bridge (Tasks 34-38)

Each brief contains: the track's plan-section pointer, branch naming,
files-owned vs files-touched-shared (esp. the lib/durability/__init__.py
stub each fills), the do-not-touch list, Hermes upstream symbols cited
at the pinned ddb8d8f (verified by audit Pass 2), and the test scaffolds
each session should green.

Also updates session-coordination.md with an "Active sessions (Phase 1
completion)" subsection listing the 3 tracks as not-yet-claimed.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push -u origin docs/session-briefs-c-d-e
gh pr create --base phase/1-completion --head docs/session-briefs-c-d-e \
  --title "docs(superpowers): add session briefs c/d/e for Phase 1 parallel execution" \
  --body "$(cat <<'EOF'
## Summary
- Three per-session briefs for sessions c (P1-3), d (P1-4), e (P1-5).
- Coordination ledger updated with the 3 tracks as not-yet-claimed.

## Test plan
- [ ] CI green (docs-only)
- [ ] Each brief is self-contained — a fresh session reading just the brief should know exactly what to do

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 11.7: After CI green, squash-merge**

Run:
```bash
gh pr merge --squash --delete-branch
git checkout phase/1-completion && git pull --ff-only
```

### Task 12: Phase α end-to-end verification

**Files:** none (verification only)

- [ ] **Step 12.1: Verify all expected α PRs merged**

Run:
```bash
git log --oneline phase/1-completion ^main | head -20
```
Expected: 6 commits visible (smoke-doc + 4 α-0 + Tasks 6/20b + P1-6 + session briefs).

- [ ] **Step 12.2: Run full unit suite**

Run:
```bash
.venv/bin/pytest tests/unit/ -q
```
Expected: 100+ tests PASS (was 94 before P1-6's 14 new tests).

- [ ] **Step 12.3: Run smoke**

Run:
```bash
bash scripts/smoke.sh
```
Expected: all 7 PASS.

- [ ] **Step 12.4: Run P1-6 integration test**

Run:
```bash
.venv/bin/pytest tests/integration/test_p1_6_failure_matrix.py -v
```
Expected: 5/5 PASS.

- [ ] **Step 12.5: Notify user that Phase α is complete and Phase β can launch**

Output to user: "Phase α merged into phase/1-completion. Sessions c, d, e can now be launched against the briefs in `docs/superpowers/SESSION-BRIEF-{c,d,e}-2026-05-18.md`. This session will resume at Phase γ-prep when all 3 session PRs have merged."

---

## (Phase β happens elsewhere — sessions c/d/e per their briefs. Out of scope for this plan.)

When Phase β is complete, resume here:

---

## Phase γ-prep: Acceptance preflight

### Task 13: Run γ-prep preflight checklist

**Files:**
- Possibly modify: `tests/integration/test_{budget_cap,secret_leak,skill_creation}.py` (add `pytest.mark.skip` decorators with documented reasons)

- [ ] **Step 13.1: Confirm Phase β PRs are merged into phase/1-completion**

Run:
```bash
git checkout phase/1-completion && git pull --ff-only
git log --oneline | head -20
gh pr list --state merged --base phase/1-completion --limit 20
```
Expected: see PRs from session-c (P1-3), session-d (P1-4), session-e (P1-5) all merged. If any missing, STOP and wait.

- [ ] **Step 13.2: Run full unit test suite**

Run:
```bash
.venv/bin/pytest tests/unit/ -q
```
Expected: all PASS (count will exceed 100 after P1-3/4/5 add their tests).

- [ ] **Step 13.3: Run smoke test**

Run:
```bash
bash scripts/smoke.sh
```
Expected: 7/7 PASS.

- [ ] **Step 13.4: Integration test triage matrix (P0-G)**

Apply per-test disposition:

```bash
# Tests that should now PASS (3 + the 1 that already passed):
.venv/bin/pytest tests/integration/test_p1_2_judge_panel.py -v          # PASS (pre-existing)
.venv/bin/pytest tests/integration/test_p1_6_failure_matrix.py -v       # PASS (Phase α P1-6)
.venv/bin/pytest tests/integration/test_full_turn.py -v                 # PASS (Phase β P1-5)
.venv/bin/pytest tests/integration/test_chroma_outage.py -v             # PASS (Phase α P1-6 + Phase β P1-3)
.venv/bin/pytest tests/integration/test_sandbox_isolation.py -v         # PASS (no code change; equivalent to smoke check 5)
```

Expected: 5/5 PASS.

- [ ] **Step 13.5: Mark the 3 deferred integration tests with documented skip reasons**

For each of `test_budget_cap.py`, `test_secret_leak.py`, `test_skill_creation.py`, add at the top:

```python
import pytest
pytestmark = pytest.mark.skip(reason="P2 — <specific reason>; see docs/superpowers/specs/2026-05-18-phase1-completion-coordination-design.md §6.1 triage matrix")
```

Reasons:
- `test_budget_cap.py`: "P2 — requires /v1/admin/limits endpoint not implemented in P1"
- `test_secret_leak.py`: "P2 — requires live `lib/scrubber.py` wiring + _test_inject_response hook; see audit B5"
- `test_skill_creation.py`: "P2 — requires /v1/nudges/skill_extractor/run endpoint; manual skill creation still exercised by acceptance step 2"

Verify:
```bash
.venv/bin/pytest tests/integration/ -v --collect-only 2>&1 | grep -E "SKIP|PASS" | head -20
```
Expected: 3 tests show as SKIP with the documented reason; rest collect normally.

- [ ] **Step 13.6: Verify OTel/Phoenix end-to-end**

Run:
```bash
curl -fsS http://localhost:6006/v1/projects | python3 -m json.tool
MASTER_KEY=$(cat secrets/litellm-master-key)
curl -fsS -X POST http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer ${MASTER_KEY}" -H "Content-Type: application/json" \
  -d '{"model":"vertex_ai/claude-sonnet-4-6","messages":[{"role":"user","content":"trace test"}],"max_tokens":10}'
sleep 3
curl -fsS http://localhost:6006/v1/projects | python3 -m json.tool
```
Expected: `traceCount` increases after the chat completion.

- [ ] **Step 13.7: Verify Telegram bot still healthy**

Run:
```bash
docker exec autonomous-agent-hermes-1 nslookup api.telegram.org 2>&1 | head -3
docker logs autonomous-agent-hermes-1 --since 5m 2>&1 | grep -iE "telegram|polling" | head -5
```
Expected: DNS resolves; no recent telegram errors; polling activity visible.

- [ ] **Step 13.8: Verify LiteLLM spend tracking endpoint**

Run:
```bash
docker exec autonomous-agent-litellm-proxy-1 \
  curl -fsS -H "Authorization: Bearer $(cat /run/secrets/litellm_master_key)" \
  http://localhost:4000/spend/calculate | python3 -m json.tool | head -20
```
Expected: JSON response with at least `total_spend` key.

- [ ] **Step 13.9: Verify skills dir exists on hermes container**

Run:
```bash
docker exec autonomous-agent-hermes-1 ls -la /app/skills 2>&1 | head -5
```
Expected: directory exists (may be empty until acceptance step 2 exercises skill creation).

- [ ] **Step 13.10: If any of 13.2-13.9 failed, fix in `chore/p1-prep-fixes` PR**

If all 13.2-13.9 PASS, skip this step. If any failed, open a small PR against `phase/1-completion` with the specific fixes; merge before proceeding.

- [ ] **Step 13.11: Commit any test-skip changes from Step 13.5**

If you added skip decorators in Step 13.5, commit them:
```bash
git checkout phase/1-completion
git checkout -b chore/integration-test-triage-skips
git add tests/integration/test_budget_cap.py tests/integration/test_secret_leak.py tests/integration/test_skill_creation.py
git commit -m "$(cat <<'EOF'
chore(tests): mark P2-deferred integration tests with documented skip reasons

Per Phase 1 completion design §6.1 triage matrix:
- test_budget_cap: needs /v1/admin/limits endpoint (P2 scope)
- test_secret_leak: needs live lib/scrubber.py wiring + _test_inject_response hook (P2 scope; audit finding B5)
- test_skill_creation: needs /v1/nudges/skill_extractor/run manual-nudge endpoint (P2 scope; autonomous extraction still exercised by acceptance step 2)

Skipped (not removed) so they remain visible scaffolds for Phase 2.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
git push -u origin chore/integration-test-triage-skips
gh pr create --base phase/1-completion --head chore/integration-test-triage-skips \
  --title "chore(tests): mark P2-deferred integration tests with documented skip reasons" \
  --body "Per design spec §6.1 triage. CI green required."
```
After CI green, squash-merge.

### Task 14: Write the "ready-for-acceptance" report

**Files:**
- Create: `docs/runbooks/phase1-acceptance-prep-2026-05-NN.md` (where NN = current day)

- [ ] **Step 14.1: Create branch + the report**

Run:
```bash
TODAY=$(date +%Y-%m-%d)
git checkout phase/1-completion && git pull --ff-only
git checkout -b docs/phase1-acceptance-prep
```

Write to `docs/runbooks/phase1-acceptance-prep-${TODAY}.md`:

```markdown
---
title: "Phase 1 acceptance preflight report — ${TODAY}"
status: GO
prepared_by: Claude Opus 4.7
integration_branch_head: <SHA from `git rev-parse phase/1-completion`>
---

# Phase 1 Acceptance Preflight Report

This report certifies the stack is ready for the human-driven acceptance walk-through documented in `docs/runbooks/phase1-acceptance.md`.

## Preflight checks (per design spec §6.1)

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Smoke 7/7 | ✅ PASS | `bash scripts/smoke.sh` output: all 7 ✓ |
| 2 | Unit tests | ✅ PASS | `.venv/bin/pytest tests/unit/ -q` → N passed in Xs |
| 3 | Integration tests (triaged) | ✅ PASS | 5 PASS, 3 SKIP (P2-deferred per triage matrix) |
| 4 | OTel/Phoenix | ✅ PASS | traceCount increased after live turn |
| 5 | Telegram bot | ✅ PASS | DNS resolves; polling active in logs |
| 6 | LiteLLM spend tracking | ✅ PASS | /spend/calculate returns valid JSON |
| 7 | Skills dir | ✅ PASS | /app/skills exists, writable |

## Footnotes (known caveats for acceptance reporter)

- **Acceptance step 5 (secret-leak file check) is a false-positive PASS.** Per audit finding B5, `lib/scrubber.py` is not wired into the live pipeline; no code writes to `/data/secret-leak-attempts.log`. The file will be absent, satisfying the runbook check by accident. Phase 2 hardening will land the live scrubber wiring.
- **3 integration tests skipped with documented reasons** (`test_budget_cap`, `test_secret_leak`, `test_skill_creation`) — see triage matrix in design spec §6.1.

## What to do next

You (the human) have ~30 min uninterrupted with your phone + a browser. Walk
the 7 acceptance steps in `docs/runbooks/phase1-acceptance.md`. On all-pass:

1. Open promotion PR: `phase/1-completion` → `main`
2. Tag `phase1-accepted` on the resulting main HEAD
3. Cut release per `docs/release-process.md`

If anything fails, capture details in this report and open a `chore/p1-acceptance-fixes` PR against `phase/1-completion`.

## Verdict: **GO**
```

Replace placeholders (`${TODAY}`, `<SHA>`, `N`, `Xs`) with real values from your verification output.

- [ ] **Step 14.2: Commit + push + open PR**

Run:
```bash
git add "docs/runbooks/phase1-acceptance-prep-${TODAY}.md"
git commit -m "docs(runbooks): add acceptance preflight report (GO)" \
           -m "All 7 preflight checks PASS; stack ready for human-driven acceptance walk-through."
git push -u origin docs/phase1-acceptance-prep
gh pr create --base phase/1-completion --head docs/phase1-acceptance-prep \
  --title "docs(runbooks): add Phase 1 acceptance preflight report (GO)" \
  --body "All checks GO. User can now walk docs/runbooks/phase1-acceptance.md."
```

- [ ] **Step 14.3: After CI green, squash-merge**

Run:
```bash
gh pr merge --squash --delete-branch
git checkout phase/1-completion && git pull --ff-only
```

---

## Phase γ-acceptance: Human walk-through

### Task 15: Hand off to user for manual acceptance

**Files:** none (no code; user-driven)

- [ ] **Step 15.1: Notify the user**

Output to user, verbatim:

> "Phase γ-prep complete and committed. Preflight report at `docs/runbooks/phase1-acceptance-prep-${TODAY}.md` says GO.
>
> Please walk `docs/runbooks/phase1-acceptance.md` when you have ~30 minutes uninterrupted with your phone + a browser open to http://localhost:6006.
>
> When you've completed all 7 acceptance steps (or hit a failure), let me know the result. On all-pass I'll open the promotion PR + cut the tag."

- [ ] **Step 15.2: WAIT for user to report results**

Do not proceed. User runs the 7-step manual walk-through asynchronously.

- [ ] **Step 15.3: If user reports failures, open a fixup PR**

For any failed acceptance step, identify root cause (likely a regression in one of the subsystem PRs or a missed cross-cutting concern). Open `chore/p1-acceptance-fixes` PR against `phase/1-completion` with the specific fix. After merge, ask user to re-run the failed step.

If user reports all 7 PASS, proceed to Task 16.

---

## Phase γ-acceptance: Promotion to main + tag

### Task 16: Promotion PR — `phase/1-completion` → `main`

**Files:** none (PR + tag operations)

- [ ] **Step 16.1: Verify everything is on phase/1-completion**

Run:
```bash
git checkout phase/1-completion && git pull --ff-only
git log --oneline phase/1-completion ^origin/main | head -20
```
Expected: see the full list of merged α-0 + α + β + γ-prep commits.

- [ ] **Step 16.2: Open the promotion PR (squash merge, per branch protection)**

Run:
```bash
gh pr create --base main --head phase/1-completion \
  --title "chore(phase1): accept Phase 1 — close out subsystems P1-3/4/5/6 + acceptance" \
  --body "$(cat <<'EOF'
## Summary

Promotes the Phase 1 completion integration branch to main. Contains everything from the 2026-05-18 design spec:

- **Phase α-0**: 4 live-stack defect fixes (OTel double /v1/traces URL, Phoenix host port publishing, hermes egress network for Telegram DNS, healthcheck cron dual-fix — closed #29).
- **Phase α**: limits.yaml anchors + evaluators APPEND (Tasks 6+20b); P1-6 Durability subsystem (33-mode failure matrix, trichotomy classifier, 24h escalation watcher sidecar); 3 session briefs for parallel execution; smoke-doc count fix.
- **Phase β** (per session briefs):
  - session-c: P1-3 per-step checkpointing + resume
  - session-d: P1-4 REJECTED.md institutional memory
  - session-e: P1-5 Kanban → Telegram bridge
- **Phase γ-prep**: integration-test triage matrix applied (3 P2-deferred); acceptance preflight report GO.
- **Phase γ-acceptance**: human-driven walk-through PASSED all 7 steps per `docs/runbooks/phase1-acceptance.md`.

## Tagging

On merge, tag `phase1-accepted` will be cut against this commit. Release notes workflow will auto-fire.

## Test plan
- [x] All unit + integration tests green (excl. 3 documented P2-deferred skips)
- [x] Smoke 7/7 PASS
- [x] Acceptance runbook PASSED all 7 steps (human-verified)
- [ ] CI green on this PR

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 16.3: After CI green, squash-merge** (branch protection requires squash, not merge commit)

Run:
```bash
gh pr merge --squash
git checkout main && git pull --ff-only origin main
```

- [ ] **Step 16.4: Tag `phase1-accepted` + push the tag**

Run:
```bash
git tag -a phase1-accepted -m "Phase 1 accepted per docs/runbooks/phase1-acceptance.md walk-through ${TODAY}."
git push origin phase1-accepted
```
Expected: tag visible on origin; release-notes workflow triggered (check with `gh run list --limit 5`).

- [ ] **Step 16.5: Cleanup**

Run:
```bash
# Delete the integration branch on origin (kept local for archive)
git push origin --delete phase/1-completion
# (Optional) delete the spec/plan branches if not already deleted by squash-merge
gh pr list --state merged --limit 30 --json headRefName | python3 -c "import json,sys; [print(r['headRefName']) for r in json.load(sys.stdin)]"
```

- [ ] **Step 16.6: Update the session-coordination ledger — move Phase 1 tracks to retired**

Edit `docs/superpowers/session-coordination.md`: cut the "Active sessions (Phase 1 completion)" subsection content and paste into the "Retired sessions" subsection. Add a closing note: "Phase 1 accepted ${TODAY}; all tracks closed."

Open a small docs PR for this (or fold into Task 16's promotion PR body if doing in same flight).

---

## Plan complete. Acceptance signal

Phase 1 is **DONE** when:

- [ ] `git log --oneline origin/main | head -1` shows the squashed Phase 1 promotion commit
- [ ] `git tag -l phase1-accepted` returns the tag
- [ ] Issue #29 is `CLOSED`
- [ ] Release notes have auto-fired (`gh run list --limit 5` shows the release workflow as completed/success)

At that point this plan is concluded. Phase 2 (GCP cloud-prod migration) is a separate spec + plan; see `docs/superpowers/specs/` for kickoff.
