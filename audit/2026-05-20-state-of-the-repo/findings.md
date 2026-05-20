# Findings — State of the Repo (2026-05-20)

> **Scope.** Where the repo + deployment sit at end-of-day 2026-05-20. Where the Wave-3 (2026-05-19 resume + orchestration) plan landed, what remains, and what new findings have surfaced since. Codebase-only pass 1. Pass 2 (sibling-repo + live reference enrichment) follows.

---

## §1 — TL;DR

- **Code work:** all 8 P0 + all 7 P1 items from the 2026-05-19 plan are SHIPPED on `main`. 7 of 8 P2 items are SHIPPED; 1 (P2-5 signed commits) is operator-blocked on issue #104.
- **CI:** 11/11 required status checks green on every merged PR; latest run on `main` (`6fffe21`) is fully green except `CodeQL`, which fails because **repo-level Code Scanning is not enabled** (HTTP 403 from `/code-scanning/alerts`). This is a one-click repo-settings toggle, not a code defect.
- **Live deployment:** **the agent itself is offline.** `autonomous-agent-hermes-1`, `autonomous-agent-escalation-watcher-1`, and `autonomous-agent-phoenix-1` are missing from `docker ps`; only 5 of the 8 compose services are running. **Issue #94 ("AutonomousAgent is DOWN") is a true positive.**
- **Branch protection:** 3 of the 4 hardening flips that PR #85 set up runbooks for are still operator-pending (`enforce_admins`, `required_approving_review_count`, `required_signatures`), tracked as #102/#103/#104.
- **Branch hygiene gap:** 17 stale work-branches (11 local + 6 remote, including `origin/phase/1`) survived the Wave-3 deletion pass; documented in `audit/2026-05-19-resume-orchestration/wave-3-branch-ledger-verification.md`.
- **Open follow-on issues:** #94 (DOWN), #101–#104 (4 ops follow-ups), #110 (extended snapshot scope). No open PRs.

The audit work itself converged. The remaining surface is **operational** (toggle code scanning, restart the hermes stack, finish the 4 branch-protection / GCS-provisioning ops items), plus one small **technical** follow-up (#110 — extend the snapshot tar to include Honcho + Phoenix state).

---

## §2 — Authoritative state, verified 2026-05-20

### 2.1 Git

| Field | Value |
|---|---|
| `main` HEAD | `6fffe21` — `docs(audit): wave-3 branch-ledger verification report (#111)` |
| Local-only commits | 0 (clean working tree, in sync with `origin/main`) |
| Local branches | 1 (`main`) — wave-3 implementers' worktree branches all merged/cleaned |
| `git worktree list` | 1 entry — only the primary working tree on `main` (P1-6 from prior plan: nothing to prune) |
| Open PRs | 0 |
| Open issues | 6 (see §3) |

### 2.2 CI (latest `main` push, 2026-05-20T10:52Z)

All required and most non-required checks green:

```
Lint Python                            success
Lint Shell                             success
Lint YAML                              success
Lint Dockerfiles                       success
Unit Tests                             success
Validate config/limits.yaml            success
Validate docker-compose                success
Conventional Commit title              success
Branch name follows convention         success
gitleaks                               success
detect-secrets                         success
Enforce Action SHA-pinning             success    (PR #107)
Phoenix span coverage                  success    (PR #82)
Snapshot integrity                     success    (PR #79)
SOUL.md integrity                      success    (PR #99)
Plugin loader smoke (docker compose)   success    (PR #100)
Secret Scan                            success
PR Validation                          success
Auto-review                            success
CodeQL                                 **failure** — see §2.3
```

### 2.3 CodeQL failure — root cause confirmed

From `gh run view 26157649362 --log-failed`:

> `##[warning] This run of the CodeQL Action does not have permission to access the CodeQL Action API endpoints… Code scanning is not enabled for this repository. Please enable code scanning in the repository settings.`

Cross-verified: `gh api /repos/Manzela/AutonomousAgent/code-scanning/alerts` → `403 — Code scanning is not enabled for this repository`.

The workflow `codeql.yml` runs to completion and analyzes all 85 Python files cleanly; it fails only on the SARIF upload step because the repo-level toggle is off. **One-click fix in repo Settings → Security → Code scanning → Default setup.** No code change needed.

### 2.4 Live deployment — `docker ps`, 2026-05-20

| Container | Status | Expected? |
|---|---|---|
| `autonomous-agent-litellm-proxy-1` | Up 2 hours (healthy) | ✅ |
| `autonomous-agent-litellm-db-1` | Up 2 hours (healthy) | ✅ |
| `autonomous-agent-github-mcp-1` | Up 2 hours | ✅ |
| `autonomous-agent-otel-collector-1` | Up 2 hours | ✅ |
| `autonomous-agent-shell-sandbox-1` | Up 2 hours | ✅ |
| `autonomous-agent-hermes-1` | **MISSING** | ❌ |
| `autonomous-agent-escalation-watcher-1` | **MISSING** | ❌ |
| `autonomous-agent-phoenix-1` | **MISSING** | ❌ |

Plus 4 unrelated stray containers (`interesting_proskuriakova`, `youthful_rubin`, `friendly_wilbur`, `wonderful_chatelet`) — cosmetic, not part of this compose stack.

**Issue #94 is a true positive.** The healthchecks.io probe (cron-pinged from the hermes loop or the watcher sidecar) reports DOWN because the agent loop itself stopped pinging. Root cause is `hermes-1` being absent — investigation needed (crash? OOM? manual stop?). Until restarted:

- No autonomous task progression
- No F32 escalation if Telegram silences (watcher is also down)
- No new spans in Phoenix (also down)
- Snapshots from the 04:00 UTC cron will not have fired (the executor lives in the hermes container)
- Budget watchdog (P1-1) and OpenRouter fallback (PR #109) are dormant

This is the single highest-priority finding in the audit and supersedes everything else in the queue.

### 2.5 Branch protection — current API state

`gh api /repos/Manzela/AutonomousAgent/branches/main/protection`:

| Setting | State | Target | Issue |
|---|---|---|---|
| Required status checks | 11 contexts | (current) | — |
| `dismiss_stale_reviews` | true | true | — |
| `require_code_owner_reviews` | true | true | — |
| `required_approving_review_count` | 0 | ≥1 | #103 |
| `enforce_admins` | **false** | true | #102 |
| `required_signatures.enabled` | **false** | true | P2-5 / #104 |
| `required_conversation_resolution` | true | true | — |
| `allow_force_pushes` | false | false | — |
| `allow_deletions` | false | false | — |

PR #85 shipped the supply-chain *baseline* (CodeQL workflow, Trivy, SBOM, cosign, SHA-pinning enforcement); the *branch-protection flips* it documented are still operator-pending across #102/#103/#104.

### 2.6 Actions permission — already tightened

`gh api /repos/Manzela/AutonomousAgent/actions/permissions`:

```json
{ "enabled": true, "allowed_actions": "selected", "sha_pinning_required": false }
```

**P2-8 API flip is APPLIED** (the docs in PR #89 + commit `f08fa6e` were prep; the actual flip happened — `allowed_actions: selected` is live). Verified-Creator publishers (`anchore`, `aquasecurity`, `astral-sh`, `sigstore`) plus the 3 patterns from `f08fa6e` are accepted; everything else is now denied at the API gateway. `sha_pinning_required: false` is acceptable because PR #107 enforces SHA-pinning at CI time instead (`scripts/check-sha-pinning.sh`, runs every PR + push).

### 2.7 Workflow inventory

10 workflows under `.github/workflows/`:

```
auto-review.yml            PR #106 — automated code review
ci.yml                     core lint/test pipeline + Snapshot integrity, Phoenix span coverage, Plugin loader smoke
codeql.yml                 PR #85 — gated on repo-level toggle (currently failing)
nightly-eval.yml           PR #106 — nightly evaluator smoke
pr-validation.yml          conventional commit + branch name + secret scan
release.yml                tag-driven release flow
sbom-cosign.yml            PR #85 — SBOM + cosign signing on tag
secret-scan.yml            PR #97 origin — gitleaks + detect-secrets
trivy.yml                  PR #85 — Trivy image scan on release
weekly-cost-summary.yml    PR #108 — Sun 09:07 UTC, posts cost summary to GitHub issue
```

---

## §3 — Open issues

All 6 open issues (none have PRs in flight):

| # | Title | Type | Blocks | Note |
|---|---|---|---|---|
| 94 | AutonomousAgent is DOWN | incident | live operation | True positive — `hermes-1` absent from `docker ps` |
| 101 | ops: provision GCS bucket + SA for snapshot uploads | operational | P1-1b executor cannot upload | Needs `gcloud` access; one-shot operator task |
| 102 | ops: enable branch-protection `enforce_admins` on main | operational | SLSA Source L3 | One API call |
| 103 | ops: raise `required_approving_review_count` to 1 on main | operational | SLSA Source L3 | One API call; coordinate with CODEOWNERS practice |
| 104 | ops: inventory + register contributor GPG keys (unblocks P2-5) | operational | P2-5 (signed commits) | Days of human coordination first |
| 110 | Extended GCS snapshot: Honcho session export + Phoenix sqlite bundle | enhancement | completionist DR | Follow-on from PR #108's FinOps slice |

Recently CLOSED (since 2026-05-19):
- #50 (AutonomousAgent is DOWN — prior incident; closed 2026-05-19, recurrence is #94)
- #53/#54/#55 (Phase 1.1 trio — closed by #69/#70/#71 — pre-audit baseline)

---

## §4 — Wave-3 plan vs reality (closure ledger)

Reconciling `audit/2026-05-19-resume-orchestration/audit-plan.md` against shipped commits:

### P0 — 8 / 8 SHIPPED

| ID | Title | PR | Commit |
|---|---|---|---|
| P0-1 | session-reset override (`cli-config.yaml`) | #72 | `6b6b58a` |
| P0-2 | kanban heartbeat-aware `update_card_status` | #73 | `36d00c3` |
| P0-3 | handoff-doc forensic review audit dir | #74 | `18ca884` |
| P0-4 | commit `uv.lock` | #75 | `5246f33` |
| P0-5 | baseline failure-matrix handlers | #77 | `bf30ac3` |
| P0-6 | snapshot integrity CI test | #79 | `5dfab55` |
| P0-7 | MCP error classify + dispatch | #78 | `b04edfe` |
| P0-8 | README service count + inventory | #76 | `76006cc` (extended by #87 wave-2 sidecars) |

### P1 — 7 / 7 SHIPPED

| ID | Title | PR | Commit |
|---|---|---|---|
| P1-1 | F21 budget watchdog (psycopg `LiteLLM_SpendLogs`) | #84 | `34174a5` |
| P1-1b | GCS snapshot executor (feature-flagged) | #86 | `8b49016` |
| P1-2 | trichotomy regex from real MCP errors | #81 | `d9a5c7b` |
| P1-3 | supply-chain baseline (CodeQL/Trivy/SBOM/cosign) | #85 | `8452360` |
| P1-4 | F32 fallback: GitHub issue when Telegram down | #83 | `9103f39` |
| P1-5 | Phoenix span coverage integration test | #82 | `814bd06` |
| P1-6 | prune locked worktrees | (none needed) | `git worktree list` shows 1 entry — clean |
| P1-7 | memory snapshot refresh | (memory) | `project_state_2026-05-19.md` + `…2026-05-20.md` written |

### P2 — 7 / 8 SHIPPED (1 operator-deferred)

| ID | Title | PR | Status |
|---|---|---|---|
| P2-1 | Phase-2 spec on hardened foundation | #90 | SHIPPED; back-refs wired by #93 |
| P2-2 | branch hygiene script + ledger | (76ba414) | SHIPPED (40/40 ledger entries verified by #111); **17 stale survivors uncovered** — see §6 |
| P2-3 | README service count | #76 (+#87) | SHIPPED |
| P2-4 | Handoff §7.3 correction | #88 | SHIPPED |
| P2-5 | signed-commits branch protection | — | **DEFERRED** — blocked on #104 GPG key registration |
| P2-6 | Hermes submodule bump + ADR | #92 + #95 + #105 | SHIPPED; submodule at `5e743559e` |
| P2-7 | `disk-cleanup` plugin allowlist | #91 / `596e860` | SHIPPED |
| P2-8 | restrict `allowed_actions` to github+verified | docs #89 + flip via gh API | **DOCS shipped; API flip APPLIED** (verified — `allowed_actions: selected`) |

### Post-Wave-3 follow-ups also landed

These were not in the original plan but shipped as natural consequences of Wave 3:

| Commit | Title | Closes |
|---|---|---|
| `0de843a` (#93) | Phase-2 system-of-record back-refs | P2 #22 |
| `4234a31` (#95) | ADR enrich (rl-extras + env diff) | P2-6 follow-up |
| `57e526b` (#98) | hermes container hardening (cap_drop, no-new-privileges, read_only + tmpfs) | P2 #20 (Docker CIS 5.x) |
| `0c7dbdb` (#96) | expose reasoning via display + `llm.reasoning` span attribute | P2 #33 |
| `3889b4f` (#97) | scrub Telegram + GitHub alert payloads | P2 #34 |
| `ef3bed7` (#105) | correct hermes-agent commit-count delta 757→790 | audit Task #27 |
| `f712ce0` (#107) | enforce Action SHA-pinning in CI | audit Task 24 |
| `2cc1ffe` (#100) | docker-compose smoke test for hermes plugin loader | (new) |
| `ba7b567` (#99) | SOUL.md sha256 pin + CI verification | (new) |
| `9575da1` (#109) | OpenRouter fallback for R3 single-provider risk | (new) |
| `4a2ae23` (#106) | MCP inventory + nightly evaluator smoke + auto-review workflow | (new) |
| `d0bbbba` (#108) | weekly LiteLLM cost-summary + spend-log GCS snapshot (FinOps) | Hermes audit #18 |

That's 12 additional bonus commits since the original plan, broadening security, observability, and resilience surface beyond what was tracked.

---

## §5 — New findings discovered in pass 1 (since 2026-05-19)

### F-2026-05-20-1 — Hermes container is offline (CRITICAL) — pass-2 root cause

`autonomous-agent-hermes-1` exit code **137** (SIGKILL); `…-escalation-watcher-1` and `…-phoenix-1` were never created (escalation-watcher depends on `hermes: service_started` which hermes never reached). Issue #94 fired at 09:20 UTC after 212 healthchecks.io pings (last = failure).

**Pass-2 root cause finding (Explore subagent, 2026-05-20).** Last log line before exit:
```
[plugins] INFO Plugin discovery complete: 27 found, 24 enabled
```
Process exited **silently** after plugin discovery succeeded — no traceback, no SIGTERM, no OOM notice. The hermes gateway event loop never started. `restart: unless-stopped` is set on all 3 services (YAML anchor `deploy/docker-compose.yml:3-4`) but the container is still down 2h later, meaning either a hard manual stop or it crash-looped past the daemon's implicit cap.

**This means `docker compose up -d` will reproduce the crash.** A diagnose-first pass is required — likely culprit is an unhandled exception in `hermes gateway run` initialization between plugin-discovery completion and gateway listen-loop, possibly triggered by the recent container hardening (PR #98: `cap_drop`, `no-new-privileges`, `read_only` + `tmpfs`) or by the submodule bump (#92 → #105). Re-investigate startup with debug logging first.

Until restored:
- No new spans, no autonomous progress, no F32 escalation channel, no daily snapshot
- The deployment is effectively a dev-time stack only (LiteLLM + MCP + sandbox + collector)

### F-2026-05-20-2 — Code Scanning toggle off → CodeQL CI red on main

PR #85 shipped a perfectly functional `codeql.yml`; the repo-level toggle was never flipped, so SARIF upload 403s and the workflow concludes red. This has been failing on every push since #85 merged. No code defect.

### F-2026-05-20-3 — Branch-protection hardening from PR #85 only partially applied

Of the 4 flips PR #85 documented as required for SLSA Source L3 / NIST SSDF PS.2:

- `allowed_actions: selected` → **APPLIED** ✅
- `enforce_admins: true` → **NOT APPLIED** (#102)
- `required_approving_review_count: ≥1` → **NOT APPLIED** (#103)
- `required_signatures: true` → **NOT APPLIED** (P2-5 / #104)

The codebase is ready; the GitHub-side flip is not.

### F-2026-05-20-4 — 17 work-branches surviving the Wave-3 cleanup — RESOLVED in pass 2

Pass-1 cited `wave-3-branch-ledger-verification.md` (generated 2026-05-20T10:05Z) which listed 11 local + 6 remote stale branches as surviving cleanup. **Pass-2 verification (Explore subagent, ~14:30 local) found 0 of those 17 branches exist** — neither locally nor on `origin`. Repo now shows only `main` + `origin/main`.

Interpretation: between the verification report's generation (10:05Z) and the audit (14:00 local ≈ 18:00Z), an unrecorded cleanup pass deleted all 17. The ledger gap from the prior audit is closed; the `[[phase_1_trap_warning]]` memory is retired (no `origin/phase/1` to mistakenly re-merge).

**No action required.** P1-C in `audit-plan.md` is marked OBSOLETE.

### F-2026-05-20-5 — #110 widens the snapshot contract beyond PR #108

PR #108 shipped the FinOps slice (spend-log CSV in the daily tar) but explicitly scoped Honcho sessions and Phoenix sqlite OUT. Issue #110 lays out clean acceptance criteria for both (fail-open, feature-flagged, runbook entry, unit test). This is the only enhancement-class open issue.

### F-2026-05-20-6 — Memory cleanup nicety

`MEMORY.md` index lists both `project_state_2026-05-19.md` (now superseded for cli-config/supply-chain/hermes-submodule by 2026-05-20) and `project_state_2026-05-20.md`. The 2026-05-17 file is also still indexed but only authoritative for a narrowing subset. After this audit, a single `project_state_2026-05-20.md` (or a fresh 2026-05-21 snapshot) should consolidate, and the older two should be deleted from the index.

Also retire `phase_1_trap_warning.md` (the offending `origin/phase/1` branch is now gone — see F-2026-05-20-4).

### F-2026-05-20-7 — Hermes submodule 2 commits behind upstream (new in pass 2)

Pass-2 Explore subagent against `~/Professional Profile/Hermes/`: pinned `5e743559e` is 2 commits behind upstream `main` HEAD `42c428841` (2026-05-19T23:09):

- `258965663` — fix(chat_completions): strip tool_name from messages for strict providers
- `42c428841` — fix(chat_completions): broaden tool_name strip docstring + AUTHOR_MAP

Both are provider-compatibility fixes for strict OpenAI-compatible providers (Moonshot/Kimi). Zero changes to plugin contracts, F-code handlers, or Layer 4 runtime guarantees. **Safe to bump but not urgent** — added as P2-E in the plan. Useful to do during the same PR cycle as the P0-A diagnose-fix to confirm the silent-crash root cause is unrelated to plugin/orchestration drift.

### F-2026-05-20-8 — Code-scanning toggle is scriptable (revises F-2026-05-20-2)

Pass-2 finding: `gh api /repos/Manzela/AutonomousAgent/code-scanning/default-setup` returns `{"state":"not-configured","languages":["actions","python"],"query_suite":"default"...}`. The endpoint exists and accepts PATCH. P0-B can therefore be done via a single command (`gh api -X PATCH .../default-setup -f state=configured`) rather than UI clicking, making it scriptable and audit-traceable.

---

## §6 — Risk register (delta from 2026-05-19)

Most pre-audit risks were retired by the work in §4. Status of the original §5 risks:

| # | Risk | Status |
|---|---|---|
| R1 | Context window truncation | Structurally mitigated (Layer 4) — Phase-2 spec covers |
| R2 | Crash after partial state mutation | RESOLVED — snapshot integrity test + GCS executor + 04:00 cron (subject to hermes-1 being up) |
| R3 | F33 catch-all → 24h wait | MITIGATED — trichotomy regex expanded (PR #81) + OpenRouter fallback (PR #109) |
| R4 | Handlers named but not implemented | RESOLVED — PR #77 (handlers) + PR #78 (dispatch wiring) |
| R5 | Budget cap silently exceeded | RESOLVED — PR #84 watchdog, polls `LiteLLM_SpendLogs` |
| R6 | Sub-agent loop runs forever | Deferred to Phase-2 implementation (PR #90 spec) |
| R7 | Approval middleware | Deferred to Phase-2 implementation |
| R8 | Telegram self-loop (F32 on F32) | RESOLVED — PR #83 GitHub issue fallback |
| R9 | 04:00 ghost reset | RESOLVED — PR #72 |
| R10 | False F32 from heartbeat | RESOLVED — PR #73 |
| R11 | OTel collector OOM | Open — collector config still unbounded |
| R12 | Honcho rate-limit | Open — no Fail-Soft path |
| R13 | LiteLLM proxy restart policy | Open — verify `restart: unless-stopped` |
| R14 | uv.lock drift | RESOLVED — PR #75 |
| R15 | Stale local branches | RESOLVED — pass-2 verification (F-2026-05-20-4) found 0 stale branches exist; the 17 in the prior report were already cleaned |
| R16 | Worktree leak | RESOLVED — 0 worktrees currently |
| R17–R23 | SDLC supply-chain | Code-side RESOLVED (PR #85 + #107); operator-side OPEN (#102/#103/#104, code-scanning toggle) |

New risks added at end of day 2026-05-20:

| # | Risk | Source | Severity |
|---|---|---|---|
| R24 | Hermes deployment offline since at least 09:20 UTC; no detection apart from healthchecks.io / issue #94 | F-2026-05-20-1 | **HIGH** — blocks all autonomous work |
| R25 | CodeQL noise on every push masks real future failures | F-2026-05-20-2 | LOW — but bad signal-to-noise |
| R26 | Snapshot DR is incomplete without Honcho + Phoenix | #110 | MED — only matters if a snapshot is ever restored |

---

## §7 — References to enrich in pass 2

- `~/Professional Profile/Hermes/` upstream — confirm `5e743559e` is still current and that no new hook-contract breakage has landed since the 2026-05-20 bump
- `~/Professional Profile/Hermes/docs/` — cross-check plugin allowlist and Phase-2 spec assumptions
- `docker compose ps -a` + `docker compose logs --tail=200 hermes` (live) — root-cause for F-2026-05-20-1
- GitHub Settings → Security UI screenshots (operator action) — confirm code-scanning + branch-protection toggles
- Phoenix UI at `localhost:6006` (if Phoenix is back up) — verify spans resumed post-restart
- `gh api /repos/Manzela/AutonomousAgent/code-scanning/default-setup` — to script the toggle if API path exists
- The 17 surviving branches — `git log --oneline <branch> ^main` for each to confirm they're truly ancestors before `-D` deletion

---

## §8 — Open assumptions (pass-1)

1. The hermes stack was healthy at some point yesterday (per memory `project_state_2026-05-20.md`) — assuming the down is recent (09:20 UTC fire), not a never-started state.
2. GCS bucket from #101 has not been provisioned — assuming the snapshot executor (PR #86) has therefore never successfully uploaded, even on days hermes was up. Pass-2 should `gsutil ls` if access exists.
3. The 17 stale branches are all ancestors of squash-merged commits (per the ledger verification report's recommendation language). Confirm before deletion.
4. `allowed_actions: selected` flip happened *recently* (post-Wave-3) — verifier evidence in `f08fa6e` is from 2026-05-20; assuming the flip was applied same-day.
5. `weekly-cost-summary.yml` cron (Sun 09:07 UTC) has not yet fired since #108 merged ~09:45 UTC today — first cost-summary issue will appear next Sunday.
