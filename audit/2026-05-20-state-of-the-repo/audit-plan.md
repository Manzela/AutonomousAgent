# Audit plan — State of the Repo (2026-05-20)

> **Source.** Pass-1 draft against `findings.md` in this directory. References to `F-2026-05-20-N` are the new findings in §5 of that file. References to `R*` are the risk register in §6. References to `#N` are GitHub issues (verified open at 2026-05-20).
>
> **Discipline.** This plan is the deliverable. No fixes are run until the user approves, picks a subset, or amends. Items are listed in execution order within each tier so a partial green-light can flow top-down without re-sequencing.

---

## P0 — Operational restoration (blocks the agent from doing anything)

### P0-A — Diagnose-then-restart the hermes stack (closes #94) — REVISED in pass 2

- **What.** **Diagnose first, restart only after the silent-crash root cause is identified.** Pass-2 finding: hermes exited with code 137 (SIGKILL) after the last log line `[plugins] INFO Plugin discovery complete: 27 found, 24 enabled` — the gateway event loop never started; there is no traceback. A naive `docker compose up -d` will reproduce the crash.
- **Why.** F-2026-05-20-1 / R24. The autonomous agent itself is offline. No spans, no progress, no F32 escalation channel, no daily snapshot, no budget watchdog. Everything else in this plan is secondary while the deployment is dark. The silent-exit pattern between plugin discovery and gateway listen-loop is the suspect surface.
- **Where.**
  1. **Capture forensics** (logs may be lost on next compose lifecycle):
     ```bash
     docker logs autonomous-agent-hermes-1 --tail=500 > /tmp/hermes-down-stdout.log 2>&1
     docker inspect autonomous-agent-hermes-1 > /tmp/hermes-down-inspect.json
     docker compose -f deploy/docker-compose.yml ps -a > /tmp/compose-state.txt
     ```
  2. **Suspect 1 — Container hardening (PR #98).** `cap_drop: [ALL]`, `no-new-privileges`, `read_only` + `tmpfs` may block a path the gateway init writes to. Temporarily relax in a `docker-compose.override.yml` (local-only, untracked) and retry to isolate.
  3. **Suspect 2 — Submodule bump (#92/#105).** Compare against last-known-good submodule (`ddb8d8fa8`) — checkout the submodule at the older SHA, rebuild image, retry. If that brings hermes up, the regression is in `5e743559..5e743559e` upstream.
  4. **Suspect 3 — Plugin (#91 disk-cleanup).** Briefly disable the disk-cleanup plugin in `config/hermes/cli-config.yaml` and retry. If that brings hermes up, the regression is in the plugin allowlist wire-up.
  5. **Restart** (only after a hypothesis is confirmed): `docker compose -f deploy/docker-compose.yml up -d hermes escalation-watcher phoenix` — let `restart: unless-stopped` (already set, `deploy/docker-compose.yml:3-4`) hold it up. escalation-watcher and phoenix will follow once hermes reaches `service_started`.
  6. **Verify:** 3 healthchecks.io pings consecutive green, Phoenix UI at `:6006` loads, `docker logs autonomous-agent-hermes-1 --tail=50` shows main loop tick past plugin discovery.
- **Effort.** S–M — diagnosis 30–60 min (depends on how many suspects need elimination); restart + verify 5 min after that.
- **Acceptance.** Issue #94 closed with root cause + recovery action + the suspect that was confirmed. The 3 containers up for ≥15 min with no restart-thrash. Add a regression test if the cause is a config-time bug.
- **Risk if delayed.** Every hour without hermes is an hour without F33-escalation safety net. If a Telegram outage coincides, the GitHub-issue fallback (#83) also relies on hermes being up to author the issue. **Worse risk if rushed:** repeated `up -d` without diagnosis will crash-loop and may exceed daemon restart cap, leaving the stack in a harder-to-recover state.

### P0-B — Enable repo-level Code Scanning (clears CodeQL red on main) — REVISED in pass 2

- **What.** Enable code scanning via the scriptable API endpoint. Pass-2 finding (F-2026-05-20-8): `gh api /repos/Manzela/AutonomousAgent/code-scanning/default-setup` is accessible and returns `{"state":"not-configured","languages":["actions","python"]...}` — the toggle is PATCH-able from CLI.
- **Why.** F-2026-05-20-2 / R25. The CodeQL workflow ships clean from PR #85, scans all 85 Python files, and fails only on the SARIF-upload 403 because the toggle is off. The workflow has been red on every push for ~30 commits, training operators to ignore CI failures — bad signal hygiene.
- **Where.**
  ```bash
  gh api -X PATCH /repos/Manzela/AutonomousAgent/code-scanning/default-setup \
    -f state=configured \
    -F 'languages[]=actions' -F 'languages[]=python' \
    -f query_suite=default
  # Then verify:
  gh api /repos/Manzela/AutonomousAgent/code-scanning/default-setup | jq .state
  ```
  Capture the PATCH response in `audit/2026-05-20-state-of-the-repo/code-scanning-enable-evidence.json` for audit trail. UI fallback if PATCH fails: `Settings → Security → Code scanning → Default setup → Enable`.
- **Effort.** XS — 30 seconds.
- **Acceptance.** Latest push on main shows `CodeQL — success`. `gh api /repos/Manzela/AutonomousAgent/code-scanning/alerts` returns a list (possibly empty), not 403.
- **Risk if delayed.** Continued CI noise; real future vulnerability findings will surface as the same shade of red and may be missed.

### P0-C — Apply two single-call branch-protection flips (closes #102, #103)

- **What.** Two API calls against `/repos/Manzela/AutonomousAgent/branches/main/protection`:
  1. `enforce_admins: true` (#102) — admins (only operator currently) must obey the same required-status-checks + review rules.
  2. `required_approving_review_count: 1` (#103) — at least one approval required before merge.
- **Why.** F-2026-05-20-3. PR #85 documented these as required for SLSA Source L3 / NIST SSDF PS.2; the *codebase* is ready (CODEOWNERS in place, auto-review running, dismiss-stale-reviews on). Only the GitHub-side flips are missing.
- **Where.**
  ```bash
  gh api -X PUT /repos/Manzela/AutonomousAgent/branches/main/protection \
    --input <full-current-protection.json-with-two-fields-edited>
  ```
  Cannot do partial PUT on this endpoint — must `gh api /repos/Manzela/AutonomousAgent/branches/main/protection > current.json`, edit `enforce_admins` and `required_pull_request_reviews.required_approving_review_count`, then PUT back. Capture before/after diff in `audit/2026-05-20-state-of-the-repo/protection-flip-evidence.json` for audit trail.
- **Effort.** S — 10–15 min once #94 is resolved (don't flip while the agent is down or the next recovery commit may need a PR + self-review dance).
- **Acceptance.** `gh api /repos/Manzela/AutonomousAgent/branches/main/protection` shows both fields true/≥1. Both issues closed with API-response snippet pasted in.
- **Caveats.**
  - **Solo-operator constraint:** `required_approving_review_count: 1` with `dismiss_stale_reviews: true` means *every PR needs an approver other than the author*. As a single-maintainer repo this requires either (a) granting a Copilot/bot review-from-account permission, or (b) accepting that emergency hotfixes will need temporary admin bypass — which `enforce_admins: true` blocks. **Recommend doing P0-B and #102 in step 1, then deciding on #103 separately once a co-reviewer mechanism is in place.** Note this in #103 before flipping.
  - Don't apply these *during* P0-A recovery; the recovery push itself may need to land first.

---

## P1 — Operational debt + small technical gaps (do this week)

### P1-A — Provision GCS bucket + service account for snapshot uploads (closes #101)

- **What.** Stand up the GCS bucket the PR #86 executor expects (default name from `config/limits.yaml`), create a service account with `roles/storage.objectCreator` scoped to that bucket, mount the JSON key into the hermes container, and verify a snapshot lands.
- **Why.** R2 / #101. The snapshot executor (PR #86) and the FinOps spend-log appender (PR #108) both target a GCS bucket; without it they silently no-op or fail-soft. Snapshot DR is the headline mitigation for R2 (crash-after-partial-state-mutation). Today the daily tar exists locally inside the container only — lost on rebuild.
- **Where.**
  - `config/limits.yaml` — confirm bucket name + path prefix conventions
  - `deploy/docker-compose.yml` — mount the SA key under `/run/secrets/gcs-sa.json`
  - `secrets/` — store the SOPS-encrypted SA JSON next to `honcho.env.sops`
  - Manual: `gcloud storage buckets create gs://<name> --location=us-central1 --uniform-bucket-level-access`, `gcloud iam service-accounts create autonomous-agent-snapshot`, grant role.
- **Effort.** M — 30–45 min including the gcloud handshake; the operator step is required (no service-account auto-provisioning from CI).
- **Acceptance.** Issue #101 closed. A snapshot tar appears at `gs://<bucket>/snapshots/YYYY-MM-DD/` within 24h of the next 04:00 UTC cron, and `weekly-cost-summary.yml` Sunday issue shows a non-empty `spend.csv` count.
- **Depends on.** P0-A (hermes must be back up to schedule the cron).

### P1-B — Extend snapshot tar to include Honcho + Phoenix state (closes #110)

- **What.** Per the acceptance criteria already drafted in #110: add a Honcho session-export step + Phoenix sqlite bundle step to the snapshot executor, both fail-open with a runbook entry, unit-tested.
- **Why.** F-2026-05-20-5 / R26. PR #108 explicitly scoped Honcho + Phoenix out as a follow-up. Without them, snapshot-restore would recover code state but lose conversation memory and observability history — degrading the autonomous agent's ability to reason about its own past.
- **Where.**
  - Locate the snapshot executor (search: `snapshot_executor`, `make_snapshot`, the file PR #86 / #108 most recently touched under `src/` or `hermes_plugins/`)
  - Add 2 new steps; each step writes into a `tmp/` subpath then the existing tar bundler picks them up
  - Add unit tests under `tests/` exercising both happy + fail-open paths
  - Update `docs/runbooks/snapshot-restore.md` (or create) with the 2 new restore steps
- **Effort.** M — 1–2 hours of implementation, half an hour of runbook.
- **Acceptance.** Issue #110 closed. Two new green CI checks. Next 04:00 UTC snapshot tar contains `honcho/` + `phoenix.sqlite` paths.
- **Depends on.** P1-A (bucket must exist for the new artifacts to land somewhere).

### P1-C — Prune 17 stale branches — OBSOLETE in pass 2

Pass-2 verification (Explore subagent) found **0** of the 17 branches in `wave-3-branch-ledger-verification.md` actually exist — neither local nor remote. An unrecorded cleanup pass between 10:05Z (verification report) and 18:00Z (this audit) already retired them. Only `main` + `origin/main` remain.

**No action required.** Drop the `phase_1_trap_warning.md` index entry from `MEMORY.md` (covered by P2-B).

### P1-D — Investigate hermes-1 down root cause + add restart-policy safeguard

- **What.** Once P0-A captures logs, classify the failure (crash / OOM / manual stop / config error / dependency outage). If it's anything other than "user pressed stop," add a guard:
  - OOM → add `mem_limit:` + log-on-OOM annotation in compose
  - Crash → wire to F32 path so a future occurrence opens a GitHub issue automatically (the watcher does this when running, but if the *watcher* dies with hermes, who watches the watcher? — recommend a thin host-level cron that pings `docker compose ps` and opens an issue on shrinkage)
  - Manual stop → no code change; document the operational habit
- **Why.** R24 will recur otherwise. Today the only signal is healthchecks.io → GitHub issue, and that pipeline takes ≥10 minutes to fire and assumes someone watches the inbox.
- **Where.** `deploy/docker-compose.yml` (resource limits if OOM), a new `scripts/host-watchdog.sh` + crontab entry if implementing a true outside-the-stack watcher.
- **Effort.** S to M — depends on root cause (S if "manual stop", M if a host watchdog is warranted).
- **Acceptance.** Root cause documented in #94's closing comment. If a guard is added, a smoke test shows it fires on intentional `docker kill autonomous-agent-hermes-1`.

---

## P2 — Coordination + housekeeping (no urgency)

### P2-A — GPG key inventory + registration (unblocks P2-5, closes #104)

- **What.** Inventory all human contributors with merge rights; collect GPG public keys; register at GitHub user level; commit a `CONTRIBUTORS.md` block documenting the keys; *then* flip `required_signatures: true` via the branch-protection PUT used in P0-C.
- **Why.** P2-5 / R17–R23. The last leg of supply-chain hardening. Today the operator-side flip would lock everyone out of merging on the next non-signed commit. Coordination first, code flip second.
- **Where.** GitHub UI (per contributor: `Settings → SSH and GPG keys`). For automation accounts (if any auto-merge their own PRs), use machine-account GPG keys stored encrypted in `secrets/`.
- **Effort.** L (calendar days for human coordination) → then XS (one API call).
- **Acceptance.** #104 closed with the contributor list. `required_signatures.enabled: true` in protection JSON. Next merged commit shows `gpg signature verified` in GitHub UI.
- **Caveat.** Wait until P1-A, P1-B are merged so the flip doesn't gate the immediate operational work.

### P2-B — Memory index consolidation

- **What.** After this audit lands, drop the now-superseded `project_state_2026-05-17.md` and `project_state_2026-05-19.md` index entries from `MEMORY.md`; keep `project_state_2026-05-20.md` (or write a fresh consolidated 2026-05-21 entry after operational P0/P1 closes); delete `phase_1_trap_warning.md` once P1-C deletes `origin/phase/1`.
- **Why.** F-2026-05-20-6. `MEMORY.md` is loaded into every session and lines past 200 truncate — keeping superseded entries wastes the window. Auto-memory hygiene rule.
- **Where.** `memory/MEMORY.md` (index only — don't delete the underlying .md files until certain nothing references them).
- **Effort.** XS — 5 min.

### P2-C — Address still-open original risks (R11, R12, R13)

- **What.** Three risks from the 2026-05-19 register that were not in scope for any P0/P1/P2 wave:
  - R11 — OTel collector OOM (config currently unbounded). Add a `memory_limiter` processor + per-batch byte cap to `otel-collector-config.yaml`.
  - R12 — Honcho rate-limit / no Fail-Soft path. Add a try/except around Honcho calls with a same-process LRU fallback for the conversation window.
  - R13 — LiteLLM proxy `restart: unless-stopped`. Verify in `deploy/docker-compose.yml`; add if missing.
- **Why.** These are open per `findings.md` §6; none are blockers but each is a known sharp edge. R13 is trivial; R11 and R12 are ~1 hour each.
- **Where.**
  - R11: `deploy/otel-collector-config.yaml` (processors section)
  - R12: source file for the Honcho client (search: `from honcho`, likely under `src/hermes_plugins/memory/` or similar)
  - R13: `deploy/docker-compose.yml` — `litellm-proxy:` service block
- **Effort.** R13 = XS. R11 = S (config + restart). R12 = M (code + test).
- **Acceptance.** Three small PRs; #110 / followup issues referenced in body.

### P2-D — Adopt the `superpowers:writing-plans` doc format for future planning

- **What.** If the user keeps using `/audit` for state-of-repo passes, codify a template that copies the §-structure of this plan (P0/P1/P2 tiers, what/why/where/effort/acceptance, depends-on links) into `~/.claude/skills/audit/templates/audit-plan-template.md`.
- **Why.** The 2026-05-19 plan and this one diverged in formatting; a template makes diff comparison across audits trivial.
- **Effort.** XS — 10 min, but only do it if the user signals they want a recurring audit cadence.

### P2-E — Bump hermes submodule 5e743559e → 42c428841 (added in pass 2)

- **What.** Bump `hermes-agent` submodule pointer from `5e743559e0157df42e0f640cd06d736e898370d0` to `42c428841` (current upstream `~/Professional Profile/Hermes/` HEAD). Two commits ahead, both provider-compatibility fixes.
- **Why.** F-2026-05-20-7. The upstream commits (`258965663`, `42c428841`) fix `tool_name` stripping for strict OpenAI-compatible providers (Moonshot/Kimi) — useful as the OpenRouter fallback (#109) exercises more providers. Pass-2 confirmed zero changes to plugin contracts, F-code handlers, or Layer 4 orchestration guarantees. Low risk.
- **Where.**
  ```bash
  git -C hermes-agent fetch && git -C hermes-agent checkout 42c428841
  git add hermes-agent
  git commit -m "chore(deps): bump hermes-agent submodule 5e743559e->42c428841

  - 258965663: fix(chat_completions): strip tool_name from messages for strict providers
  - 42c428841: fix(chat_completions): broaden tool_name strip docstring + AUTHOR_MAP
  "
  ```
  Mirror the format of PR #92's commit message (chore/hermes-submodule-bump pattern). Update ADR via PR #95's ADR-enrich template if the workflow expects it.
- **Effort.** XS — 5 min once the test suite passes on the bumped image.
- **Acceptance.** Submodule SHA matches upstream main. CI green. Optional: include this bump in the same PR as P0-A's fix if the silent-crash root cause is in the submodule layer (kills two birds).
- **Caveat.** Defer until **after** P0-A is resolved — if the silent crash is in the submodule, you don't want to layer another bump on top.

---

## Execution sequencing (one-page summary)

```
┌─ P0-A  restore hermes  (S)  ────────► unblocks daily ops + P1-A acceptance
├─ P0-B  enable Code Scanning  (XS) ──► clears CI red
└─ P0-C  enforce_admins flip  (S)  ───► closes #102

then

┌─ P1-A  GCS bucket  (M)  ────────────► closes #101, unblocks DR
├─ P1-B  honcho+phoenix snapshot (M) ─► closes #110, depends on P1-A
├─ P1-C  prune 17 branches  (S)  ────► closes hygiene gap
└─ P1-D  hermes RCA + guard  (S/M) ──► closes #94 with prevention

then

┌─ P2-A  GPG coordination  (L→XS) ───► closes #104, then P2-5
├─ P2-B  MEMORY.md trim  (XS)  ──────► hygiene
├─ P2-C  R11/R12/R13  (XS+S+M)  ────► retires last legacy risks
└─ P2-D  audit template  (XS)  ──────► tooling polish
```

Total wall-clock if executed serially: ~5–7 working hours of code/ops + ~3 calendar days of GPG coordination.

If the user is solo-operator, recommend:
1. **Tonight:** P0-A + P0-B (the agent is down; toggle is 1 click).
2. **Tomorrow AM:** P0-C (#102 only, defer #103), P1-C (with approval), P1-D RCA writeup.
3. **This week:** P1-A → P1-B, P2-B.
4. **When time permits:** P2-A (start the GPG email thread first), then P2-C.

---

## Open questions for the user before execution

1. **#103 (`required_approving_review_count: 1`).** Solo-maintainer repo — confirm preferred path: (a) flip and rely on Copilot-as-reviewer, (b) flip and require self-bypass via temporary admin disable on each merge, (c) defer until a second human reviewer is onboarded.
2. **P1-A bucket naming + region.** GCS project to bill against? Bucket name convention (`<repo>-snapshots-prod`?). Default region for low-latency from the hermes host?
3. **P1-D — host watchdog.** Is a host-level cron acceptable, or should the next-level fallback be e.g. a UptimeRobot webhook → SMS? (The hermes-in-hermes watcher is provably insufficient when hermes itself dies.)
4. **P2-A scope.** Just the operator's key, or build the muscle for future contributors now?

These are deferrable — defaults (gcloud, GCS-US, host cron, just-operator) work for everything. The questions surface only because the cost of guessing is non-trivial.

---

## Changes from pass 1

Pass-2 enrichment dispatched 3 parallel Explore subagents (hermes upstream, hermes-1 root cause, branch+API verification). Material updates:

1. **P0-A escalated from "restart" to "diagnose-first."** Hermes exited code 137 silently after plugin discovery — `docker compose up -d` will reproduce the crash. Added 3 suspect-elimination steps (container hardening PR #98, submodule bump #92/#105, disk-cleanup plugin #91) before any restart attempt. Effort revised S → S–M.

2. **P0-B made scriptable.** Code-scanning endpoint `/repos/.../code-scanning/default-setup` is PATCH-able from `gh` CLI — revised from "UI click" to a one-line `gh api -X PATCH` command, with the PATCH response captured as audit evidence.

3. **P1-C marked OBSOLETE.** Pass-2 found 0 of the 17 stale branches actually exist; an unrecorded cleanup between 10:05Z and 18:00Z already retired them. Only `main` + `origin/main` remain. The `[[phase_1_trap_warning]]` memory is implicitly retired (rolled into P2-B housekeeping).

4. **New P2-E added: submodule bump opportunity.** Upstream `~/Professional Profile/Hermes/` HEAD is 2 commits ahead of the pinned `5e743559e` — both LLM-transport fixes for strict providers, zero contract drift. Low-priority follow-up. Caveat: defer until P0-A is resolved so the bump doesn't tangle with the silent-crash diagnosis.

5. **Findings.md updated**: F-2026-05-20-1 augmented with root-cause forensics; F-2026-05-20-4 marked RESOLVED; F-2026-05-20-7 and F-2026-05-20-8 added; R15 promoted from Partial → RESOLVED in the risk register.

No new P0 work was uncovered. No previously-shipped work was found regressed. The single load-bearing operational issue remains the silent hermes-1 crash.
