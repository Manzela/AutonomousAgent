# Audit Plan — Resume + Orchestration (2026-05-19)

> **Companion to** `findings.md` in this directory. Industry standards explicitly cited
> per user request: SLSA v1.0, NIST SSDF (SP 800-218), OWASP ASVS v4.0.3, OWASP CICD-SEC
> Top-10 (2023), CIS Docker Benchmark v1.6, OpenInference semantic conventions, OTel SDK
> resource-detection. Pass-1 (codebase-only); pass-2 reference enrichment to follow.

---

## TL;DR — Recommendation (user-confirmed: Path B + pass-2-now + add R1/R2 P0)

**Path B (Fix-the-foundation-then-Phase-2)**, with pass-2 enrichment completed and a new **P0-6** added per user direction to cover R1/R2 with a snapshot integrity CI test.

1. **P0 today (≤6 hours):** Ship 3 uncommitted WIP files + add snapshot integrity CI test + correct README service count + implement 3 baseline failure-matrix handlers + wire MCP error classification (pass-2 found these errors firing live but uncaptured).
2. **P1 this week:** Query `spend_logs` table directly for budget tracking (pass-2 found `/spend/total` returns 404 — schema-direct is the only path), grow trichotomy regex from live MCP error data, implement GCS snapshot executor (pass-2: cron is config-only with no executor), supply-chain baseline (CodeQL + Trivy + SBOM + cosign + Action SHA pinning — hermes-agent sibling at 100% SHA-pinned is the reference).
3. **P2 next:** Phase-2 spec authored *on top of* a hardened foundation, plus Hermes submodule bump (pass-2: 757 commits / 5 days behind, hook contract stable).

**Why not Path A (Phase 2 spec first)?** Authoring a Phase-2 spec on top of unimplemented handler stubs (R4 — pass-2 confirmed: not in upstream either, must be local) means the spec inherits the same gap. Foundation first costs ~5–7 days; Phase 2 spec then has accurate failure modes to design around.

**Why not Path C (everything in parallel)?** Possible (the orchestration surface in `findings.md` §4 supports it) but parallel work on a foundation that *itself* has known gaps amplifies blast radius of any single mistake. Sequence the foundation; parallelize once the seams are tight.

---

## P0 — Today (≤4 hours of human-supervised work)

### P0-1. Ship `config/hermes/cli-config.yaml` session-reset override
**What:** `git add config/hermes/cli-config.yaml` → commit `feat(config): opt out of 04:00 session reset for long-running tasks` → PR.
**Why:** Closes the §5.2 ghost-reset finding from the prior audit (`audit/handoff-doc-2026-05-19-review/findings.md`). Today, every container restart silently reintroduces a 04:00 context drop.
**Where:** `config/hermes/cli-config.yaml:121-123` (already written).
**Effort:** 15 min including PR + CI.
**Acceptance:** `grep -A2 "session_reset" config/hermes/cli-config.yaml` returns `mode: none`; no CI red.

### P0-2. Ship `lib/kanban/telegram_bridge.py` rewrite + 3 tests
**What:** Same-PR or follow-up: `feat(kanban): heartbeat-aware update_card_status + Telegram dedup`.
**Why:** Closes R10 (false F32 escalations from same-status writes). Without this, the 24h watchdog cries wolf on every long-running card.
**Where:** `lib/kanban/telegram_bridge.py` (+139/-16), `tests/unit/test_kanban_telegram_bridge.py` (+75).
**Effort:** 30 min (code is written; needs commit + PR + CI green).
**Acceptance:** All 3 new tests green in CI; existing 11 required status checks still pass.

### P0-3. Ship `audit/handoff-doc-2026-05-19-review/` as a `chore(audit): ...` PR
**What:** `git add audit/handoff-doc-2026-05-19-review/{audit-plan,findings}.md` → PR.
**Why:** Audit trail is a SLSA Source L2 requirement (`Source.Available`). Audit deliverables that live only locally violate that.
**Where:** `audit/handoff-doc-2026-05-19-review/` (already exists, untracked).
**Effort:** 10 min.
**Acceptance:** Files appear in `git log -- audit/handoff-doc-2026-05-19-review/`.

### P0-4. Resolve `uv.lock` drift
**What:** `git diff uv.lock` → decide commit-or-revert.
**Why:** Unreviewed lockfile drift breaks SCA reproducibility (NIST SSDF PW.4, "implement repeatable builds").
**Effort:** 10 min.

### P0-5. Implement the 3 baseline failure-matrix handlers (R4)
**What:** Create `lib/durability/handlers.py` with:
- `retry_with_backoff(attempt, base_delay_ms, max_delay_ms, jitter_range_pct) -> None` — applies the formula already documented in `docs/architecture/failure-matrix.md:78-84`.
- `halt_alert_snapshot(f_code, context) -> None` — writes checkpoint, sends Telegram via `lib/kanban/telegram_bridge.py`'s alert path, transitions task to `BLOCKED`.
- `fallback_local_log(f_code, context) -> None` — degrades to local JSONL when remote target (e.g. otel-collector) is unreachable.
Wire `lib/durability/failure_matrix.py` to dispatch the `"handler"` string to these functions.
**Why:** Today the matrix is documentation, not behavior. R4 in `findings.md` §5.1. Closing this also closes R3 (F33 routing has somewhere to actually go).
**Pass-2 confirmation:** Searched `hermes-agent/` submodule for these functions — they do **NOT exist upstream either**. Must be implemented locally; no copy-paste source exists.
**Where:** `lib/durability/handlers.py` (new), `lib/durability/failure_matrix.py` (dispatch wiring).
**Effort:** 3 hours including unit tests.
**Acceptance:** Add unit test `test_all_33_handlers_dispatchable` that loops through `failure_matrix.TABLE` and asserts the handler name resolves to a callable.

### P0-6. Snapshot integrity CI test (R1 + R2 mitigation — user-directed)
**What:** Add a single integration test `tests/integration/test_snapshot_integrity.py` that:
1. Starts a synthetic Hermes session that creates a Kanban card, writes a Honcho memory, and a Chroma vector.
2. Triggers `Checkpoint.maybe_write()` (the post_tool_call hook from PR #58).
3. Simulates crash → restart via reading the checkpoint back.
4. Asserts Kanban card state, Honcho session ID, and Chroma collection state all match expected post-restore values.
**Why:** R1 (context truncation) and R2 (partial-write crash) are mitigated structurally by the Layer-4 primitives, but there is no CI test asserting that the primitives actually work end-to-end. A single test pins the contract.
**Where:** `tests/integration/test_snapshot_integrity.py` (new). Wire into CI `.github/workflows/ci.yml` under "Unit Tests" (or split out as "Integration Tests" if isolation needed).
**Effort:** 3–4 hours (test fixtures for sqlite Kanban + mocked Honcho/Chroma clients).
**Acceptance:** Test runs green; deliberately corrupting the checkpoint file makes it fail loudly with a clear assertion message.
**Standard:** ISO/IEC 25010 (reliability — recoverability subcharacteristic).

### P0-7. MCP error classification wiring (NEW from pass-2)
**What:** Pass-2 found live MCP failures (`github-mcp` returning 401 Unauthorized; `context7` "Session terminated") that are logged as WARNINGs but **never flow through `lib/durability/trichotomy.classify()`**. Wire the MCP tool error handler in `tools/mcp_tool.py` to call `trichotomy.classify(err)` and dispatch the returned F-code via P0-5's new handler layer.
**Why:** Today, failure modes happen and the trichotomy machinery never sees them. The 33-mode matrix is useless without inputs. This is a higher-priority finding than I had in pass-1.
**Where:** `tools/mcp_tool.py` (the connection-retry path, lines verified by pass-2). May also need to extend `trichotomy._CLASSIFIERS` with F14-matching regexes for "github.?mcp.*unauthorized" if F14 doesn't already match the 401 case.
**Effort:** 1 hour wiring + 30 min new regex patterns + 30 min tests.
**Acceptance:** Re-run the live container; `docker compose logs hermes | grep "F1[24]"` returns matches.

### P0-8. README service count correction
**What:** `README.md:55` claims "twelve services". Actual = **9 defined / 7 long-running + 1 init sidecar**. Pass-2 confirmed PR #67 did not patch this string. Replace with accurate per-service table.
**Why:** Documentation lying about infrastructure is a NIST SSDF PB.2.3 traceability gap and confuses future audits.
**Where:** `README.md:55` + add a "Service inventory" subsection.
**Effort:** 20 min.

> **Note on P0 sizing.** P0-1 through P0-4 are <1 hour total (mostly ship-the-WIP). P0-5 through P0-7 are the "critical foundation" items — ~5 hours combined. P0-8 is 20 min cosmetic. Realistic P0 day = 6 hours of focused work. If shorter, ship P0-1/2/3/4/8 standalone today and treat P0-5/6/7 as P0.5 (this week).

---

## P1 — This week

### P1-1. Wire F21 (daily budget cap) — query spend_logs table directly
**What:** Add `lib/durability/budget_watchdog.py` polling **Postgres `spend_logs` table directly** every 5 min via psycopg/asyncpg (NOT `/spend/total` — pass-2 found that endpoint returns 404; the LiteLLM extension that exposes it is not loaded). Trigger F21 at 100%, alert at `limits.yaml: budget.alert_at_pct` (75%).
**Why:** R5. Cap is configured but enforcement is open-loop. Pass-2 confirmed spend IS being tracked (Phoenix shows $0.0345 across 108 spans), and PR #71 attached the Postgres backend, but no exposed REST endpoint to poll.
**Standard:** None specifically — internal financial control.
**Where:** `lib/durability/budget_watchdog.py` (new). Connection string from `secrets/litellm-db.env.sops`.
**Effort:** ~4 hours (was 3 — direct-DB approach needs schema discovery first).
**Acceptance:** Synthetic test sets `daily_usd_cap: 0.01`, runs one LLM call, verifies F21 fires and task is BLOCKED. Also: `docker exec autonomous-agent-litellm-proxy-1 psql -U litellm -d litellm -c "\\dt"` confirms `spend_logs` table exists with expected schema.

### P1-2. Expand trichotomy regex from real MCP error data (pass-2 source)
**What:** Pass-2 found three live error strings already in `hermes` container logs that the trichotomy doesn't classify:
- `unhandled errors in a TaskGroup (1 sub-exception)` → should map to F4 or F14
- `Client error '401 Unauthorized' for url 'http://github-mcp:8003'` → should map to F14 (currently doesn't because the regex requires "unavailable" not "unauthorized")
- `Session terminated` (context7 MCP) → should map to F14
Add these patterns to `lib/durability/trichotomy.py:22+` `_CLASSIFIERS` table.
**Why:** R3 — keep F33 rate low; F33 → 24h human wait is expensive. **Pass-2 changed the data source: Phoenix had zero error spans (the agent gracefully recovers most MCP failures without raising), but raw container logs are full of them.**
**Effort:** 1 hour (was 1 day — pass-2 already extracted the patterns).
**Acceptance:** Add `test_mcp_errors_classify_to_f14` unit test using the three observed strings.

### P1-1b. Implement GCS snapshot executor (NEW from pass-2 — was assumed-present)
**What:** Pass-2 found `/app/lib/snapshots/` does not exist in the hermes container; no Python file references `gcs_snapshot`. The `snapshots.gcs_snapshot_cron: "0 4 * * *"` config in `config/limits.yaml` is **config-only with no backing executor.** Build:
- `lib/snapshots/gcs_snapshot.py` — `snapshot(cron_run_id)` function that tars `/home/hermes/.hermes/` and uploads via `google-cloud-storage` SDK to the GCS bucket configured in `secrets/gcs-snapshot.env.sops` (create if absent).
- Cron registration in `hermes_cli` or APScheduler entry that fires at `0 4 * * *`.
**Why:** This is the **disaster-recovery foundation** for R2 (partial-write crash) and is currently a complete no-op. P0-6 (snapshot integrity CI test) is meaningless if no snapshots are actually taken.
**Standard:** ISO/IEC 22301 (business continuity); NIST 800-34 (contingency planning).
**Effort:** ~6 hours including GCS bucket provisioning + SA key creation + cron wiring + smoke test.
**Acceptance:** `gsutil ls gs://<bucket>/hermes-snapshots/$(date +%Y-%m-%d)/` lists the day's snapshot tarball; container logs at 04:00 UTC show "snapshot uploaded".

> **Sequence note.** P1-1b is paired with P0-6; without P1-1b, P0-6 only tests *whether* a restore-from-checkpoint works on a hand-crafted fixture, not whether the system actually produces those checkpoints daily.

### P1-3. Supply-chain hardening — SLSA L2 + NIST SSDF PW.7
**One bundled PR** `chore(security): supply-chain baseline`. **Reference implementation: pass-2 confirmed `hermes-agent/` sibling repo is 100% SHA-pinned across 11 workflows — use as the copy-paste source.**
- **CodeQL** (NIST SSDF PW.7 — "perform a code review of source code"): `.github/workflows/codeql.yml` running on every PR, languages: `python,javascript` (only those present). **Pass-2 confirmed: not currently enabled (HTTP 403 from GH API).**
- **Trivy image scan** (CIS Docker 4.x / SLSA Build L2): `.github/workflows/trivy.yml` scanning the built `hermes-agent` image on `release` events, exiting non-zero on `CRITICAL` or `HIGH`.
- **SBOM** (NIST 800-218 PW.4 / SLSA Provenance L2): `anchore/sbom-action@v0` on tag, attaching `.spdx.json` to the GitHub release.
- **cosign sign + attest** (SLSA Provenance L3): `sigstore/cosign-installer@v3` + `cosign sign` + `cosign attest --predicate <sbom>` on tag. **Pass-2 confirmed: OIDC default policy is enabled — keyless cosign ready, no secret-key management needed.**
- **SCA — Dependabot grouped PRs as blocking** (NIST SSDF PW.4): pass-2 found Dependabot alerts return 403 (disabled or insufficient scope). Enable in repo settings first, then wire security-update PRs as required status checks via branch protection.
- **Pin all 3rd-party Actions to commit SHAs** (OWASP CICD-SEC-04 / SLSA Source L3): pass-2 confirmed **0/8 actions are SHA-pinned in AutonomousAgent** (8 tag-pinned). Reference: `hermes-agent/.github/workflows/osv-scanner.yml` shows the pattern `actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5  # v4`.
- **Branch protection tightening** (SLSA Source L3): pass-2 found `enforce_admins: false` (admins can bypass), `required_approving_review_count: 0` (relies on CODEOWNERS only). Flip `enforce_admins: true`; consider `required_approving_review_count: 1` (or document why CODEOWNERS-only is acceptable).

**Effort:** 1 day for the workflow files; the cosign keyless flow needs an OIDC trust policy on the GitHub side (already enabled per pass-2). None of this requires Hermes code changes.

**Acceptance:** Each new workflow returns green on a no-op test PR; a release-tag dry-run produces signed image + attested SBOM; `gh api /repos/Manzela/AutonomousAgent/branches/main/protection` shows `enforce_admins: true`.

### P1-4. F32 fallback channel
**What:** Second alert path when Telegram itself is down (R8). Cheapest: open a GitHub issue via `gh api` from the watcher sidecar; label `incident/auto`. Costs nothing, durable.
**Where:** `lib/escalation/watcher.py` (or wherever the sidecar lives).
**Effort:** 2 hours.

### P1-5. Phoenix span coverage spot-test
**What:** Add a single integration test that runs one short Hermes turn and asserts both an LLM span AND a TOOL span are emitted with the OpenInference attribute set populated.
**Why:** Locks in the #70 deliverable so regressions are caught at PR time.
**Standard:** OpenInference semantic-conventions.
**Effort:** 1 hour.

### P1-6. Prune locked sub-agent worktrees
**What:** `git worktree list` shows 3 locked `.claude/worktrees/agent-*` dirs with `0` file-status. Unlock + remove (or extend the auto-cleanup heuristic in the harness to handle them).
**Why:** R16 — disk leak, also confusing in future audits.
**Effort:** 30 min.

### P1-7. Refresh memory snapshot
**What:** Update `~/.claude/projects/.../memory/project_state_2026-05-17.md` → `project_state_2026-05-19.md` reflecting the actual HEAD `c180892`, Phase 1.1 closure, etc.
**Why:** Stale memory mis-routes future sessions (drift documented in `findings.md` §2.1).
**Effort:** 15 min.

---

## P2 — Next iteration / parallelizable

### P2-1. Phase-2 spec authored on the hardened foundation
**What:** Once P0 + P1 land, draft `docs/spec/phase2.md` with:
- The orchestration architecture from `findings.md` §4 as the system-of-record.
- Failure modes referenced by F-code (now meaningful, post-P0-5).
- ADR appendix: 5 layers, what each layer optimizes for, when to cross layer boundaries.
**Effort:** 2 days drafting + 1 day review.
**Standard:** ISO/IEC/IEEE 42010 (architecture description).

### P2-2. Branch hygiene script
41 local-only branches, all squash-shipped. One-shot script: `git branch --merged origin/main | xargs git branch -D` (manually inspected list, no automation).
**Effort:** 30 min.

### P2-3. README correction (cosmetic but persistent)
`README.md:55` says "twelve services". Reality is 8 defined / 7 running. Fix the count + add a per-service table.
**Effort:** 20 min.

### P2-4. Handoff §7.3 correction
Same as carry-over from prior audit's DEFECT-3.
**Effort:** 30 min.

### P2-5. Branch protection: require signed commits
Once contributors are aligned on signing, flip the toggle.
**Standard:** NIST SSDF PS.2.
**Effort:** 5 min for the toggle; days of human coordination first.

### P2-6. Hermes submodule bump + ADR (NEW from pass-2)
**What:** Pass-2 found `hermes-agent/` pinned at `ddb8d8fa8` (2026-05-14), **757 commits / 5 days behind** upstream HEAD. Includes one release tag `v2026.5.16`. Hook contract is **STABLE** in the delta (no breaking changes — verified). One backward-compatible feature added: tool-override flag in `ctx.register_tool()` (commit `016c772e7`).
**Why:** Routine maintenance; not urgent. But will be P1 if it stays untouched for 30+ days.
**Standard:** NIST SSDF PW.4 ("Keep only needed software" — current).
**Where:** `git submodule update --remote hermes-agent` → review delta → commit + ADR.
**Effort:** 4 hours including ADR + regression test pass.
**Acceptance:** ADR in `docs/architecture/decisions/0001-hermes-submodule-bump-2026-05-19.md` documents what changed; CI green.

### P2-7. Consider upstream `disk-cleanup` plugin (NEW from pass-2)
**What:** Pass-2 found upstream ships a `disk-cleanup` v2.0.0 plugin (hooks: `post_tool_call`, `on_session_end`) that auto-cleans ephemeral test/temp files. Useful for long-running autonomous sessions.
**Why:** Low-effort, high-value for Layer-4 (Hermes runtime) hygiene.
**Where:** Add `disk-cleanup` to `config/hermes/cli-config.yaml:113` plugin allowlist; verify no permission issues.
**Effort:** 30 min.

### P2-8. Restrict `allowed_actions` to GitHub + verified publishers
**What:** Pass-2 found `allowed_actions: "all"` — any GitHub Action from any namespace can run. Tighten to `"github,verified"`.
**Why:** OWASP CICD-SEC-05 ("Restrict action usage").
**Effort:** 5 min; requires verifying no current workflow uses an unverified action.

---

## §1 — Orchestration architecture (for path-forward §3)

The capability surface in `findings.md` §4 maps to **5 layers**. Optimization criteria per layer:

| Layer | What | Optimize for | Cross-layer call when |
|---|---|---|---|
| 0 | Single Claude Code reasoning loop | Depth of thought, tight feedback | Always the default |
| 1 | Parallel/background within one session (Agent, Bash bg, Cron, Worktree) | Wall-clock parallelism, context isolation | Independent work fits in <30 min; need parallel subagents |
| 2 | Multiple Claude Code terminals (cross-terminal) | Independent user-facing workstreams | Two humans, OR one human + one fully-autonomous Hermes loop |
| 3 | Verification skills | Honesty (avoid "I think it works") | Always — these are policies not features |
| 4 | Hermes runtime | Multi-day autonomous goals | The work is too long-running for a single Claude Code session; need durable Kanban + checkpoints |
| 5 | External services (Vertex, Honcho, Chroma, Phoenix, GCS, Telegram, SOPS) | Durability, scale, out-of-band human signal | The work needs to persist beyond a container restart OR cross a session boundary |

**Practical rule.** *Stay in Layer 0 by default. Promote to Layer 1 when the same Claude Code session has 2+ truly-independent threads. Promote to Layer 4 (Hermes) when the goal is multi-day, not multi-hour.* The temptation is to over-promote; resist it. Each layer up adds coordination cost.

**The "super-orchestrator" pattern.** Claude Code (this session) is the *design and implementation* environment for Hermes. Hermes is the *execution* environment for long-running autonomous goals. The handoff happens via:
- Code (PR-shipped) → Hermes runtime.
- Kanban cards → durable task state.
- Checkpoint files → durable progress.
- Phoenix spans → durable observability.
- Honcho memory → durable cross-session recall.
- Telegram → durable out-of-band human signal.

This is *exactly* the right architecture. The work in P0–P1 is closing the seams, not rebuilding.

---

## §2 — What can go wrong (risk register from `findings.md` §5, prioritized for mitigation)

23-item register lives in `findings.md` §5. Top-tier mitigations now embedded in this plan:

- **R4 (handlers not implemented)** → P0-5.
- **R3 (F33 catch-all)** → P1-2.
- **R5 (budget cap not enforced)** → P1-1.
- **R8 (Telegram self-loop)** → P1-4.
- **R9 (ghost reset)** → P0-1.
- **R10 (false F32)** → P0-2.
- **R14 (uv.lock drift)** → P0-4.
- **R16 (worktree leak)** → P1-6.
- **R17–R23 (SDLC supply-chain)** → P1-3 (bundled).

R1 (context truncation) and R2 (partial-write crash) are mitigated structurally by Layer 4 (Kanban + checkpoint), not by a single PR — they remain ongoing operational discipline items. P2-1 (Phase-2 spec) is where they get formal review.

---

## §3 — Three candidate paths

### Path A — "Phase 2 spec first"
Draft `docs/spec/phase2.md` immediately, including failure-handling architecture; *then* implement.
**Pros.** Aligns the team on direction before code. Lower wasted-work risk if Phase 2 changes scope.
**Cons.** Spec authored on top of unimplemented handler stubs (R4) inherits the same gap. The 5-day spec drafting blocks all foundation work. A spec that doesn't reflect what the code actually does is the worst kind.

### Path B — "Foundation, then Phase 2" ⬅ recommended
Land P0 + the critical pieces of P1 (P1-1, P1-2, P1-3 bundle, P1-5) in ~5–7 days. *Then* draft Phase 2 (P2-1) on top of a foundation where every F-code has a real handler, every budget cap is enforced, and the supply chain is signed.
**Pros.** Phase 2 spec has accurate failure modes to design around. SLSA L2 baseline by end of week. The 3 uncommitted WIP files ship immediately (low risk, high value).
**Cons.** Spec is delayed ~1 week. Acceptable given that the previous spec (`docs/spec/phase1`) was tagged then required ten hotfix PRs to actually work — that pattern reflects spec-without-verification, which we should not repeat.

### Path C — "Everything in parallel"
Dispatch Hermes itself (Layer 4) on the P1-3 supply-chain bundle in one terminal, draft Phase 2 spec in another, while this session lands P0 + P1-1 + P1-2.
**Pros.** Wall-clock fastest. Demonstrates the orchestration architecture in action.
**Cons.** Foundation gaps amplify blast radius of parallel mistakes. The Layer-4 runtime *itself* has R4 unfixed — running a critical PR series through it is gambling. Once P0-5 ships, Path C becomes safe.

### Path D — "User-driven prioritization"
Present this plan, let user pick a subset (e.g. "just P0 today, P1 later").
**This is the actual ending state** per the audit-skill approval gate (§4 below). Path B is the recommendation; user holds the final call.

---

## §4 — Approval gate (per `audit` skill)

This plan is pass-1 (codebase-only). Pass-2 enrichment with sibling-repo and live-data references is queued. Per the skill workflow, we **stop here and wait for user direction**.

**User decisions captured (pre-pass-2):**
1. ✅ **Path B** — foundation-first.
2. ✅ **Pass-2 enrichment ran in parallel** (4 Explore subagents, 30 min wall-clock).
3. ✅ **R1/R2 promoted to P0** as new item P0-6 (snapshot integrity CI test).

---

## §5 — Changes from pass 1

Pass-2 enrichment ran 4 parallel `Explore` agents against: (a) the upstream `hermes-agent/` sibling repo, (b) live container runtime (Phoenix API, LiteLLM proxy, container logs, OTel collector config), (c) GitHub security APIs (Dependabot, code-scanning, branch protection, Actions permissions, OIDC), and (d) cross-check of prior audit deliverables vs current code reality. Key corrections to the pass-1 plan:

### New items added
- **P0-6** — Snapshot integrity CI test (per user direction; mitigates R1+R2).
- **P0-7** — MCP error classification wiring. Pass-2 found `github-mcp 401 Unauthorized` and `context7 Session terminated` errors firing in live logs but **never flowing through `trichotomy.classify()`**. The 33-mode matrix gets no input today. This is higher-priority than I had in pass-1.
- **P0-8** — README service count fix (still says "twelve", reality is 9 defined / 7 long-running). Pass-2 confirmed PR #67 did not patch the line.
- **P1-1b** — Implement GCS snapshot executor. Pass-2 found `/app/lib/snapshots/` does not exist in the hermes container and no Python code references `gcs_snapshot`. The 04:00 UTC cron is **config-only with no executor.** This pairs with P0-6 — snapshot integrity tests are meaningless without snapshots actually being produced.
- **P2-6** — Hermes submodule bump (757 commits / 5 days behind upstream; hook contract stable; no breaking changes in the delta).
- **P2-7** — Upstream `disk-cleanup` plugin available; useful for long-running session hygiene.
- **P2-8** — Tighten `allowed_actions` from "all" to "github,verified" (OWASP CICD-SEC-05).

### Items corrected in place
- **P1-1** (budget cap) — pass-2 found `/spend/total` endpoint **returns 404**. The endpoint doesn't exist (LiteLLM extension not loaded). Approach changed: query `spend_logs` Postgres table directly via psycopg/asyncpg. Effort revised 3hr → 4hr.
- **P1-2** (trichotomy regex) — pass-2 found Phoenix has zero error spans (the agent recovers most MCP failures gracefully without raising), but container logs are full of unclassified errors. Effort revised 1 day → 1 hour; data source changed Phoenix → logs.
- **P1-3** (supply chain) — pass-2 confirmed: 0/8 Actions SHA-pinned, CodeQL disabled, Trivy/SBOM/cosign absent, OIDC ready for keyless cosign, hermes-agent sibling 100% SHA-pinned (perfect copy-paste reference), `enforce_admins: false`, `required_approving_review_count: 0`. Bundled PR scope expanded slightly.
- **P0-5** (failure-matrix handlers) — pass-2 confirmed: the 3 handlers do **NOT exist upstream** either. No copy-paste source; must implement locally. Sizing unchanged.

### Items confirmed
- **R4** — 0 of 16 named handlers implemented (matched against the full distinct handler-name list, not just the 3 baseline).
- **R3** — `origin/phase/1` branch trap: 77 commits not on main. Confirmed legacy, do not re-merge.
- **Failure-matrix doc-vs-code parity** — exactly 33 rows in doc; CI guard works.
- **Honcho reachability** — `https://api.honcho.dev/health` returns 200 from inside hermes container. Network egress confirmed.
- **OTel collector** — `deploy/otel/collector.dev.yaml`, batch=512, no disk-backed queue (R11 valid).
- **Issue #50 (closed)** and stash audit dir (rescued via PR #65) — both pass-1 carryovers now confirmed resolved.

### Open items NOT changed by pass-2
- R1, R2 — now covered by P0-6 (per user direction).
- R5 (24h Telegram silence) — already P1-4.
- R6 (max_turns enforcement) — needs single-pass code review of every `Agent` dispatch path; deferred to P2-1 (Phase 2 spec).
- R7 (approval middleware) — same; deferred to P2-1.

### Net result
**14 P0/P1/P2 items** in the updated plan (was 11 in pass-1). Three additions are direct consequences of live-runtime discovery (P0-7 MCP wiring, P0-8 README count, P1-1b GCS executor) that no amount of code-reading could have surfaced. Pass-2 was worth the 30-minute wall-clock cost.

No fixes shipped. Awaiting final user approval before implementation.
