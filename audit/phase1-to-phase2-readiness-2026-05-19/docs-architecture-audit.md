# Documentation Drift + Architecture Audit — Phase 1 → Phase 2 Readiness

**Target:** AutonomousAgent
**HEAD audited:** `85512a3` (main, tagged `phase1-accepted`)
**Hermes submodule pin:** `ddb8d8fa842283ef651a6e4514f8f561f736c72e` (2026-05-14)
**Auditor:** Claude Opus 4.7, forensic pass
**Date:** 2026-05-19

> Methodology: every "drift" finding below cites `file:line` against the actual artifact on disk at the audited HEAD. Cross-checks ran against `lib/`, `deploy/`, `config/`, `scripts/`, `tests/`, `hermes-agent/` and the live `git log` / `git ls-remote`. The audit was run side-by-side with the previously merged final-sweep findings (`audit/phase1-completion-final-sweep-2026-05-18/findings.md`) and supersedes them where contradicted by the actual current state.

---

## TL;DR

**Phase 1 acceptance was earned on a documentation false-positive.** The acceptance runbook passes because every gate is satisfied by **upstream Hermes behaviour or by infrastructure that does not require our `lib/*` code to execute correctly**. Our plugins now load (verified by docker-compose mounts at `deploy/docker-compose.yml:265-285` and the `plugins.enabled` list at `config/hermes/cli-config.yaml:108-115`), but most of their hook bodies are either `TODO` stubs (P1-1, P1-5) or surface-only adapters (P1-6: `lib/durability/trichotomy.py:68-87` is a 2-line no-op `before_tool_call` plus an OTel-span emitter — none of the 16 handler symbols named in the 33-mode failure matrix exist in code).

The README still claims a **12-service stack**; the actual compose file ships **7** (`grep -E "^  [a-z][a-z0-9_-]+:" deploy/docker-compose.yml` returns: litellm-proxy, otel-collector, phoenix, shell-sandbox, github-mcp, hermes, escalation-watcher). Three of the historical services (chroma, honcho-db, honcho) were deleted with comments left in the compose file but never propagated to README, runbook, or `scripts/snapshot.sh` — which still exec's `chroma` and `honcho-db` and is therefore **broken**, and so is `scripts/teardown.sh --remove-volumes` (it calls snapshot first), and so is the entire `docs/runbooks/recovery.md` "Restoring from a snapshot" procedure (it references `autonomous-agent_chroma-data` and `autonomous-agent_honcho-db-data` volumes that do not exist).

Hermes upstream is **718 commits ahead** of our pin (`git -C hermes-agent rev-list --count ddb8d8f..origin/HEAD`); the diff window includes at least four `fix(gateway|telegram|cli)` commits that touch the exact subsystems we depend on, plus a fix that adds atomic writes and TOCTOU guards. We have no Hermes upgrade ADR, no plugin-API compatibility matrix, and no scheduled bump cadence.

**Overall grade: D+ for Phase 1 → Phase 2 readiness on documentation/architecture axes.** The phase has shipped working software but the documentation describes a system that does not exist (12 services, working failure handlers, working scrubber, working snapshot/restore). A Phase 2 build that takes README/runbook/spec at face value will fail loud within hours.

---

## 1. Doc-drift table (claim vs reality)

Cell format: **CLAIM** — what the doc says · **REALITY** — what the code/config/file actually does · **EVIDENCE** — file:line.

| # | Doc | Claim | Reality | Evidence |
|---|---|---|---|---|
| D1 | `README.md:55` | "twelve services" docker-compose stack | **7 services** | `deploy/docker-compose.yml` services block lists `litellm-proxy, otel-collector, phoenix, shell-sandbox, github-mcp, hermes, escalation-watcher` |
| D2 | `README.md:55` | "agent core (`hermes-agent`) talks to a LiteLLM proxy" | Service is named **`hermes`** (singular), not `hermes-agent` | `deploy/docker-compose.yml:198` `hermes:` (also confirms doc-drift pattern from phase1-acceptance runbook §1) |
| D3 | `README.md:55` | "State lives in SQLite + Chroma + Honcho (with Postgres)" | Chroma is **cloud-only** (api.trychroma.com); Honcho **disabled** (no public Docker image) | `deploy/docker-compose.yml:11-13` ("chroma-data: removed", "honcho-db-data: removed") and `:25-37`, `:42-45` removal comments |
| D4 | `README.md:55` | "Modal/Daytona cloud sandbox" tier | No Modal/Daytona images, env vars, or code anywhere | `grep -ri "modal\|daytona" config/ deploy/ lib/` → only ADR-0003 text, no implementation |
| D5 | `README.md:30-37` Phase table | "1: 🚧 in progress" | Phase 1 is tagged `phase1-accepted` on `main` | `git tag --list phase*` |
| D6 | `README.md:81` | `trajectories/` Phase 3 placeholder | Directory contains only an empty `.gitkeep` (verified `ls trajectories/`) | `ls trajectories/` |
| D7 | `docs/runbooks/phase1-acceptance.md:96` | Read `/data/secret-leak-attempts.log` | File is never written; `lib/scrubber.py` is **not wired** into any live code path (only `tests/unit/test_scrubber.py` exercises it) | `grep -rn "scrubber\|Scrubber" lib/ deploy/ scripts/` returns only the file itself, its tests, and the description-string in `failure_matrix.py`. Acceptance step 5 passes vacuously. |
| D8 | `docs/runbooks/phase1-acceptance.md:31` | `docker compose ... exec ... hermes-agent ls /app/skills` | Service is named `hermes`, not `hermes-agent`; command fails | `deploy/docker-compose.yml:198` |
| D9 | `docs/runbooks/phase1-acceptance.md:39` | `docker compose ... restart hermes-agent` | Same service-name drift; runbook step is uncopyable | as D8 |
| D10 | `docs/runbooks/phase1-acceptance.md:103-107` | `litellm-proxy curl ... /spend/calculate` — Expected: JSON with non-zero `total_spend` | `/spend/calculate` is a **POST**; GET returns 405. `/spend/logs` returns 500 (no DB). Pre-flight footnote acknowledges this but the runbook body still says "GET-style" curl | runbook is unmodified; the preflight (`docs/runbooks/phase1-acceptance-prep-2026-05-18.md:23`) documents the caveat but the canonical runbook never got the update |
| D11 | `docs/runbooks/recovery.md:14` | Restore: `docker volume rm autonomous-agent_hermes-data autonomous-agent_chroma-data autonomous-agent_honcho-db-data` | `autonomous-agent_chroma-data` and `autonomous-agent_honcho-db-data` **do not exist** (services removed) | `deploy/docker-compose.yml:9-13` volumes block — only `hermes-data, workspace, phoenix-data`. Restore procedure fails at line 1. |
| D12 | `docs/runbooks/recovery.md:18` | `$COMPOSE start honcho-db` | No `honcho-db` service exists | `deploy/docker-compose.yml` services list |
| D13 | `docs/runbooks/recovery.md:1` "After a panic": `docker compose ... logs hermes-agent --tail=200` | Service is named `hermes`; command fails | service-name drift, D8 pattern |
| D14 | `docs/runbooks/recovery.md:3` | `docker compose unpause hermes-agent hermes-gateway` | Neither service exists by those names; `hermes-gateway` was collapsed into `hermes` ("collapse to one service running `hermes gateway run`" — comment at `deploy/docker-compose.yml:194-197`) | as above |
| D15 | `scripts/snapshot.sh:14-17` | `$COMPOSE exec -T hermes-agent tar ...` and `$COMPOSE exec -T chroma tar ...` and `$COMPOSE exec -T honcho-db pg_dump ...` | All three exec'd services either don't exist (`chroma`, `honcho-db`) or are mis-named (`hermes-agent` → `hermes`). **Snapshot is broken end-to-end.** | `scripts/snapshot.sh:14-20` cross-referenced against `deploy/docker-compose.yml` services |
| D16 | `scripts/panic.sh:9` | `$COMPOSE pause hermes-agent hermes-gateway` | Both names wrong (see D14). Panic is broken. | `scripts/panic.sh:9` |
| D17 | `scripts/teardown.sh:11` | `"$ROOT/scripts/snapshot.sh"` invoked before teardown | Inherits D15 brokenness; teardown will spew exec errors before bringing down stack | `scripts/teardown.sh:11` |
| D18 | `README.md:71` | `lib/` is "our Python helpers (validators, scrubber, router, healthcheck)" | Massively understates current reality: 6 plugin packages (`anchors`, `durability`, `evaluators`, `kanban`, `memory`, `observability`) plus 4 root modules. ~3,200 LOC. | `wc -l lib/*.py lib/*/*.py` → 3,187 total |
| D19 | `README.md:111-126` | "11 required checks" | True today (verified `.github/workflows/`), but README claims CodeQL is "removed" (line 136); never re-added even though Phase 1 has shipped Python on main. CodeQL planned re-introduction is overdue. | `.github/workflows/` lists ci.yml, pr-validation.yml, release.yml, secret-scan.yml — no CodeQL |
| D20 | `docs/conventions/logging.md:84-103` | "Hermes-Specific Event Catalog": `TASK_CREATED, CHECKPOINT_WRITTEN, EVALUATOR_VOTE_RECORDED, CONSENSUS_REACHED, RETRY_TRIGGERED, ESCALATION_TRIGGERED, MEMORY_REJECTED_APPENDED, …` | **None** of these event names appear in our code. `grep -n "TASK_CREATED\|CHECKPOINT_WRITTEN\|EVALUATOR_VOTE\|CONSENSUS_REACHED\|RETRY_TRIGGERED\|ESCALATION_TRIGGERED\|MEMORY_REJECTED_APPENDED" lib/ -r` → no matches. The catalog is aspirational. | `grep` above |
| D21 | `docs/architecture/failure-matrix.md` (whole doc) | 33 F-codes, each with a named `handler` (e.g. `halt_alert_snapshot`, `retry_with_backoff`, `disable_chroma_for_session`) | **Zero handlers exist as functions**. `grep -rE "def halt_alert_snapshot\|def retry_with_backoff\|def restart_sandbox\|def refresh_adc\|def disable_chroma\|def fallback_local_log\|def skip_tool_class\|def defer_extraction\|def drop_judge\|def log_and_continue\|def use_cached\|def truncate_and_warn\|def skip_inject\|def halt_alert_request_approval\|def alert_user_escalate_kanban\|def retry_with_higher_max_tokens" lib/` → empty. All 16 handler symbols are **string-only labels**. | `lib/durability/failure_matrix.py:17-186` defines the table; `lib/durability/trichotomy.py:68-87` is the entire dispatcher and it only emits an OTel span; nothing else calls any handler. |
| D22 | `docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md` decisions table | Decision 9: "SQLite (sessions) + Chroma (vectors) + nightly GCS backup"; Decision 10: "Honcho deployment: Self-hosted via Docker Compose"; Decision 11 lists "GitHub MCP, Playwright MCP, Context7 MCP" | Chroma is cloud, Honcho disabled, Playwright deferred (comment at `deploy/docker-compose.yml:188-194`); only GitHub MCP runs. No `docs/decisions/` entry supersedes any of these. | spec lines 24-37; compose comments cited |
| D23 | `CHANGELOG.md:13-37` `[Unreleased]` | Lists open PRs #10-#15 as "in flight" with statuses "needs rebase + retitle" | These PRs landed weeks ago (squashed into commits like `1dca48f`, `8127e5b`, then folded into `4ee6991`); the table is now historical fiction. The `[Unreleased]` Added section also still references "SDLC + parallel-session documentation" as if it hasn't shipped, but PR #18 (`5ee4dcc docs: comprehensive SDLC + parallel-session documentation pass`) merged. | `git log --oneline -- CHANGELOG.md` shows no edit between merge of #18 and now |
| D24 | `CHANGELOG.md:111` `[Unreleased]` link | `compare/0f74412...HEAD` | No `[phase1-accepted]` section exists even though the tag was cut. The Keep-a-Changelog cadence the project's own docs (`docs/conventions/pull-requests.md:170`) require is broken. | `git tag --list` shows `phase1-accepted`; CHANGELOG has no matching entry |
| D25 | `docs/runbooks/telegram-bot-setup.md:32` | Asks user to set `notify_channels.telegram_chat_id` in `config/limits.yaml` | Such a key path does not exist in `config/limits.yaml` (the file has `notify_channels:` near line 130+ but the substructure differs). Runbook claims it does. | `grep -n "telegram_chat_id\|notify_channels" config/limits.yaml` |
| D26 | `docs/runbooks/README.md:7-12` | Lists 4 runbooks | Directory has 5 markdown files (the new `phase1-acceptance-prep-2026-05-18.md` is not indexed) and adds `healthcheck-cron-setup.md` which IS indexed | `find docs/runbooks -name "*.md"` — index missed one |
| D27 | `docs/decisions/README.md` index | 7 ADRs through 0007 | True. But major decisions made since (Chroma Cloud migration, Honcho deferral, single-service `hermes` collapse, plugin loading via `~/.hermes/plugins/<name>/`, OTel SDK wiring approach, the entire 33-mode failure matrix design, the multi-judge per-axis model routing) have **no ADR**. See ADR-gap list §2. | physical count of `docs/decisions/*.md` |
| D28 | `docs/architecture/README.md:14` | "12-service docker-compose stack: spec §2" | Spec §2 itself enumerates 12 services. Reality is 7. Both docs drift the same way. | `docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md:30-40` |
| D29 | `docs/superpowers/HANDOFF-2026-05-17.md:18` | "Unit tests on main: 94/94 PASS" | Current `tests/unit/` has 26 test files (`ls tests/unit/`). Test count claim is stale (preflight at 2026-05-18 reports 162 passed). | physical count |
| D30 | `docs/superpowers/specs/SESSION-COMPLETE-2026-05-15-P1-KICKOFF.md:14-19` | "→ 6 containers up: hermes, github-mcp, litellm-proxy, shell-sandbox, phoenix, otel-collector" | Today there are 7 (escalation-watcher was added in P1-6 and never updated in the kickoff brief). And the smoke check counts 5 containers, not 6 (excludes escalation-watcher per `scripts/smoke.sh:40`). | three-way drift across compose, smoke, and brief |

**Drift severity rollup**: D7, D11–D17, D21 are **operationally fatal** (broken commands or never-executed code that is documented as load-bearing). D1–D4, D20, D22, D28 are **integrity issues** (the project's mental model in published docs no longer matches running reality). D23–D29 are **hygiene/lag** (CHANGELOG, index, brief metadata not maintained).

---

## 2. ADR gap list

ADRs 0001-0007 are all "Accepted" and remain substantively correct (we still use Hermes, LiteLLM, sops/age, the tiered sandbox concept, the iterative phase model, and worktrees). But the project has made the following architecturally load-bearing decisions **with no ADR**:

| # | Missing ADR | Why it matters | First evidence in code |
|---|---|---|---|
| G1 | **Plugin loading via `~/.hermes/plugins/<name>/` bind-mount** instead of a Python package | Defines our entire contract with Hermes. Six plugins depend on it. If Hermes changes plugin discovery (it's currently at `hermes-agent/hermes_cli/plugins.py:797-905`) we break silently. | `deploy/docker-compose.yml:265-285` mounts; `config/hermes/cli-config.yaml:108-115` enable-list |
| G2 | **OTel SDK init at plugin register time** (Hermes ships uninitialized `ProxyTracerProvider`) | Determines that every other plugin must depend on `observability` being loaded first, but there is no enforced ordering documented. | `lib/observability/__init__.py:48-55`, `lib/observability/otel_setup.py:1-20` |
| G3 | **Chroma Cloud over self-hosted Chroma** | Reverses ADR 0003-implied "self-hosted" stack; introduces cloud dependency in a "local" phase; affects security model (egress allowlist) and snapshot/restore | `deploy/docker-compose.yml:25-37` |
| G4 | **Honcho deferral** | Removes a major architecture pillar listed in spec decision #10 | `deploy/docker-compose.yml:17-23, 42-45` |
| G5 | **Single `hermes` service (gateway+agent collapsed)** vs spec's two-service design | Affects every runbook, snapshot, restart procedure; the "two-service" assumption pervades the architecture spec | `deploy/docker-compose.yml:194-197` comment block |
| G6 | **33-mode failure matrix as the durability contract** | Major new contract surface (P1-6) — but with zero handler implementations and no decision-record describing the migration plan from "string labels" to "wired handlers" | `lib/durability/failure_matrix.py:17-186` + `docs/architecture/failure-matrix.md` |
| G7 | **Per-axis judge model routing** (Sonnet for code+scope, Opus for safety, Gemini for completeness) | Material cost + correctness decision; pinned in a Python module constant with no ADR | `lib/evaluators/orchestrator_hook.py:21-27` |
| G8 | **Gemini 3.1 Pro Preview model id quirks** (`-preview` suffix, `global` endpoint only, thinking model) | Operational constraint that surfaces in 3 places in code/config and has burned debugging time per `docs/superpowers/HANDOFF-2026-05-17.md:34-43`; needs a permanent decision record, not just a session-log mention | `deploy/litellm/config.yaml`, `lib/evaluators/orchestrator_hook.py:25` |
| G9 | **Squash-only merge policy + 11 required checks** (vs ADR 0007's `--no-ff` phase-merge story) | Tension between two documented policies — branching ADR says `--no-ff`, pull-requests convention says squash. No ADR resolves. | `docs/conventions/branching.md:46-56` vs `docs/conventions/pull-requests.md:147-156` |
| G10 | **Daily 9am session-reset for Claude Code** referenced in user-global instructions | Project-side behavior implications not documented anywhere in `docs/` | inferred from operating model; absent in repo |

Status of existing ADRs against current code:

- **ADR 0001** (Use Hermes as base): Accepted, **still correct**, but no migration plan ADR for upstream upgrades despite us being 718 commits behind.
- **ADR 0002** (Vertex AI via LiteLLM): Accepted, **correct**, model id list now broader (Opus, Sonnet, Gemini) — could update with a "models served" appendix.
- **ADR 0003** (Tiered sandboxing): Accepted; **2 of 5 tiers do not exist** in code today (`browser_sandbox` not built; `cloud_sandbox` Modal/Daytona not configured). Status should be "Partially Implemented" until the missing tiers ship.
- **ADR 0004** (sops/age): Accepted, **correct**.
- **ADR 0005** (soft loop now / hard loop Phase 4): Accepted, **correct** — no Phase 4 work has started so nothing to validate.
- **ADR 0006** (iterative phase gates): Accepted; **the phase 1 gate passed on a false positive** (D7, D21) but the meta-decision (gate-driven progress) remains sound. A "lessons learned from Phase 1 acceptance" ADR is overdue.
- **ADR 0007** (worktree-per-phase): Accepted but **rendered moot in practice**: Phase 1 work happened mostly via `session-<letter>/<path>` branches (see `docs/superpowers/session-coordination.md`), not via `.worktrees/phase1/`. The `.worktrees/` directory contains a stub. ADR should be superseded or re-scoped.

**Verdict on ADR coverage: C-.** Existing ADRs are honest but stale on edges. The substantial architectural pivots made post-2026-05-14 carry no decision record at all.

---

## 3. Runbook accuracy catalog

### `docs/runbooks/phase1-acceptance.md`

Drift items (some already cited above):
- Service name `hermes-agent` should be `hermes`: lines 31, 39, 95, 104 (4 places).
- Step 5 (secret-leak file) trivially passes because scrubber never writes the file (D7) — runbook should be either removed or rewritten to assert the scrubber even runs.
- Step 6 (LiteLLM spend) — `/spend/calculate` is POST not GET, and `/spend/logs` 500s (preflight footnote acknowledges; runbook never updated).
- Pass-criteria daily-cap text says "well under $100 cap" but `config/limits.yaml:2` `daily_usd_cap: 500`.

### `docs/runbooks/recovery.md`

Catastrophically out of date:
- All `chroma` and `honcho-db` references must go (D11, D12).
- All `hermes-agent` and `hermes-gateway` references must go (D13, D14).
- "Restoring from a snapshot" block uses `snapshots/$TS/honcho.dump`, `chroma-data.tar.gz` — neither is produced because snapshot itself is broken (D15).
- Net: an operator following this runbook today cannot recover.

### `docs/runbooks/telegram-bot-setup.md`

- D25: `notify_channels.telegram_chat_id` config path is wrong.
- Otherwise factually correct (BotFather flow, sops encryption, curl check).

### `docs/runbooks/healthcheck-cron-setup.md`

- Accurate after the 2026-05-18 PATH fix. The script (`scripts/healthcheck-ping.sh:11`) sets `PATH=/usr/local/bin:/usr/bin:/bin:$PATH` and the runbook correctly leaves the crontab line unchanged. **Verified OK.**

### `docs/runbooks/README.md` (index)

- Lists 4 runbooks; physical count is 5 (D26 — the prep report `phase1-acceptance-prep-2026-05-18.md` is unindexed; this is intentional since it's a one-shot report, but flagging for clarity).

---

## 4. Conventions docs vs actual code

| Convention doc | Drift |
|---|---|
| `docs/conventions/commit-messages.md` | Conformant. Scopes used in recent commits (`observability`, `evaluators`, `anchors`, `kanban`) all match the listed set. Note: `evaluators` and `anchors` scopes are listed in `pull-requests.md:47` but missing from `commit-messages.md:33` — minor index drift. |
| `docs/conventions/branching.md` | Drift: ADR 0007 and this doc both prescribe `.worktrees/phaseN/` but actual work has been done via `session-<letter>/<path>` branches (D-G9). The doc never mentions the session-branch pattern; that fact lives only in `docs/conventions/pull-requests.md:34-36`. |
| `docs/conventions/logging.md` | **Major drift (D20)** — entire "Hermes-Specific Event Catalog" (lines 84-103) is aspirational; no event name from this catalog is emitted by any `lib/*` module. |
| `docs/conventions/code-style.md` | Conformant in spirit. TODO-with-date convention (line 33) is observed in `lib/anchors/__init__.py:18` (`TODO(P1-1 task 6)`) and 9 other places, but the format is task-tag not date — minor variation. Acceptable. |
| `docs/conventions/pull-requests.md` | The most accurate convention doc; reflects current `pr-validation.yml` regex and squash policy. **OK**. |
| `docs/conventions/new-repo-template.md` | Doc not exercised yet (no new repo created). Cannot audit conformance; documentation appears internally consistent. |

---

## 5. Onboarding-friction findings

Time-to-first-commit estimate for a new dev who clones the repo and follows README's quickstart:

1. Install host prereqs (`docker`, `uv`, `jq`, `gcloud`, `sops`, `age`, `git`): **30 min** if not already installed.
2. `./scripts/verify-prereqs.sh`: passes if all the above install; the `gcloud auth application-default login` flow can be 5 minutes.
3. Telegram bot setup (`docs/runbooks/telegram-bot-setup.md`): **~10 min**, mostly waiting for `@BotFather`. The doc step "set `notify_channels.telegram_chat_id` in `config/limits.yaml`" is wrong path (D25) — newcomer will be confused.
4. Decrypt secrets (`scripts/decrypt-secrets.sh`): requires the *project's* age key, which a new dev **cannot have**. There is no documented procedure for a fresh dev to generate their own secrets bundle. The bootstrap.sh assumes the encrypted secrets already exist and decrypt. **This is a blocker for any non-owner onboarding.**
5. `./scripts/bootstrap.sh`: untested by me but inherits #4's blocker.
6. `./scripts/smoke.sh`: requires Telegram bot to be live (item 3), LiteLLM credentials (item 4 blocked), Chroma Cloud credentials (item 4 blocked).
7. First test run: assuming credentials existed, `pytest tests/unit/ -q` should pass in <1s.

**Net**: a single-developer-friendly setup, fundamentally **not multi-contributor friendly** without an onboarding guide on:
- How to provision personal credentials for Telegram / Chroma Cloud / LiteLLM
- How to swap the project age recipient for a personal one for dev
- How to bring up the stack without the encrypted secrets bundle

`CONTRIBUTING.md` is good on workflow but assumes you already have access. The README "Development" section (lines 84-91) is a one-line pointer; it does not call out the credential-provisioning gap.

**Onboarding grade: D for any non-owner; B+ for the owner.**

---

## 6. API surface docs

- **Slash commands**: defined inline in `lib/anchors/__init__.py:88-101` (`/lock, /skip, /cancel, /confirm`), `lib/memory/__init__.py:24-67` (`/forget, /rejections`), and one `/cancel <id>` dispatch from anchors → kanban (anchors/__init__.py:55-65). **No central reference doc** lists all slash commands; users learn them by reading source.
- **CLI subcommands**: `hermes new <intent>` is registered at `lib/anchors/__init__.py:102-108`. Not documented in README or any runbook.
- **HTTP endpoints**: None — we are a Telegram-only entrypoint per the architecture spec (decision #4).
- **Plugin contract**: documented in `hermes-agent/AGENTS.md:465-525` (upstream) and re-implemented in our 6 `plugin.yaml` manifests. There is **no internal doc** in our repo describing which hooks we use, what kwargs Hermes passes, or which hooks are blocking vs observational. Each plugin re-derives this from upstream source.

**API surface grade: D.** All public surfaces exist; none are documented user-facing.

---

## 7. Phase 2 readiness check

`docs/superpowers/plans/` directory has:
- `2026-05-14-phase1-local-deployment.md` (Phase 1 plan; complete)
- `2026-05-15-phase1-10x-implementation.md` (10× expansion; complete)
- `2026-05-18-phase1-completion-implementation.md` (completion sweep; complete)
- **No Phase 2 plan file.**

`audit/audit-plan.md` lines 159-242 (P2 section) describes a 1-week GCP migration plan, but:
- It pre-dates the actual Phase 1 acceptance (and its discovered gaps).
- It assumes the 12-service architecture that does not exist.
- It does not address the documentation/architecture drift catalogued here (which must be fixed before Phase 2 starts; otherwise Phase 2 will inherit and amplify the drift).

`audit/findings.md` (the corresponding findings doc) ends without a "Phase 2 entrance criteria" checklist.

**Phase 2 readiness checklist** (proposed, to be added to repo as `docs/superpowers/specs/2026-05-19-phase2-readiness-gate.md`):

- [ ] All D1-D17 drift items resolved (snapshot/recovery/runbook work end-to-end against the real services)
- [ ] All 16 failure-matrix handlers either implemented or removed-and-replaced with a simpler honest contract
- [ ] Scrubber wired into the live model-output and tool-result path (currently dead code)
- [ ] CHANGELOG `[phase1-accepted]` block cut from `[Unreleased]`
- [ ] Hermes upstream bump (or explicit pin-staleness ADR)
- [ ] Plugin-loading mechanism documented as ADR
- [ ] OTel-init-ordering documented and tested
- [ ] Per-axis judge routing decisions captured as ADR
- [ ] Onboarding "fresh-dev secrets bundle" runbook authored
- [ ] Architecture diagram(s) added (none today — see §13)
- [ ] Phase 2 design ADR + plan authored, peer-reviewed

**Phase 2 readiness grade: F.** No Phase 2 spec exists; the only Phase-2 reference is an outdated planning section in `audit/audit-plan.md`.

---

## 8. Inline code comments

| Module | Module docstring? | Public function docstrings? | TODO debt |
|---|---|---|---|
| `lib/anchors/__init__.py` | Single-line only (line 1) | Yes, terse | **8 active TODOs**, all marked `TODO(P1-1 task 6)` — slash handlers + CLI handler return TODO strings |
| `lib/durability/__init__.py` | Multi-line (lines 1-3) | Yes, including the order-matters comment at lines 13-15 | 0 TODOs in `__init__.py`; one in `escalation.py:40` |
| `lib/durability/failure_matrix.py` | Yes (lines 1-5) | Minimal (lookup has 1-line) | none |
| `lib/durability/trichotomy.py` | Single-line | Yes | none |
| `lib/durability/checkpoint.py` | Excellent (lines 1-23) | Yes | none |
| `lib/durability/resume.py` | Comprehensive | Yes | none |
| `lib/evaluators/__init__.py` | Excellent multi-paragraph (lines 1-13) | Yes including signature kwargs | 0 |
| `lib/evaluators/judge.py` | Yes (lines 1-10) | Yes | 0 |
| `lib/evaluators/consensus.py` | Yes | Yes | 1 (local import for rejected memory) |
| `lib/evaluators/orchestrator_hook.py` | Yes | Yes | 0 |
| `lib/kanban/__init__.py` | Excellent (lines 1-22) | Yes but bodies are TODOs | **2 TODOs** marking unimplemented hook bodies |
| `lib/kanban/telegram_bridge.py` | Yes | Yes | 0 |
| `lib/kanban/notification_policy.py` | Yes | Yes | 0 |
| `lib/memory/__init__.py` | Yes (lines 1-12) | Yes | 0 |
| `lib/memory/rejected.py` | Yes | Yes | 0 (largest module: 336 LOC) |
| `lib/memory/intent_classifier.py` | Yes | Yes | 0 |
| `lib/observability/__init__.py` | Excellent (lines 1-24) | Yes including kwarg discussion | 0 |
| `lib/observability/otel_setup.py` | Excellent (lines 1-20) | Yes | 0 |
| `lib/scrubber.py` | Yes including public-API hint (lines 1-12) | Yes | dead code (D7) |
| `lib/limits_validator.py` | not inspected in this audit | – | – |
| `lib/toolset_router.py` | not inspected in this audit | – | – |
| `lib/healthcheck.py` | not inspected in this audit | – | – |

**Comment grade: B+.** Doc-strings are generally good; the bigger problem is that **TODOs in `lib/anchors/` and `lib/kanban/` indicate the plugin hook bodies are unfinished**, but the modules' Hermes contracts are wired. So we have working register() with stub handlers — see the architectural-coupling implication in §10.

---

## 9. Plugin contract stability

**Surface we depend on** (from `hermes-agent/hermes_cli/plugins.py` at pin `ddb8d8f`):

- `register(ctx)` entry-point per plugin (6 plugins implement this).
- `ctx.register_hook(hook_name, callback)` with `hook_name` ∈ `VALID_HOOKS = {pre_tool_call, post_tool_call, transform_terminal_output, transform_tool_result, transform_llm_output, pre_llm_call, post_llm_call, pre_api_request, post_api_request, on_session_start, on_session_end, on_session_finalize, on_session_reset, subagent_stop, pre_gateway_dispatch, …}` (line 128).
- `ctx.register_command(name, handler, description)` for slash commands (line 401).
- `ctx.register_cli_command(name, help, setup_fn, handler_fn, description)` for CLI subcommands (line 376).

Hooks we register across our plugins:
- `on_session_start` (anchors, durability×2, observability) — Hermes invokes each in turn
- `pre_tool_call` (anchors, durability, kanban, observability)
- `post_tool_call` (durability, evaluators, kanban, observability)
- `pre_llm_call` (evaluators, observability)
- `post_llm_call` (observability)
- `on_session_end` (evaluators)

**Migration risk**: any of the following upstream changes would silently break us:
- Rename of `VALID_HOOKS` entries (e.g. `on_session_start` → `on_session_begin`).
- Change of `ctx.register_hook(name, callback)` signature.
- Change of plugin discovery path (currently `~/.hermes/plugins/<name>/`, see `cli-config.yaml:104-115` comment).
- Change of `register_command` / `register_cli_command` signature.
- Removal of `pre_tool_call`'s veto-return-dict semantics (we use it nowhere today — all our `_on_pre_tool_call` bodies return `None` — but the docstrings in `lib/kanban/__init__.py:62` reserve the right).

**Submodule pin**: `ddb8d8fa842283ef651a6e4514f8f561f736c72e` committed **2026-05-14**. Upstream `HEAD` is `a0bd11d0227239674fe378ff8817f8f6129ef5a7`. Delta: **718 commits**. Notable upstream commits in the delta (selected from `git -C hermes-agent log --oneline ddb8d8f..origin/HEAD`):

- `a0bd11d02 fix(tests): catch up 25 stale tests after recent merges (#28626)`
- `62573f44c fix: guard yaml.safe_load, flock unlock, TOCTOU races, and atomic writes` ← **security-relevant**
- `b8a9cbd18 fix: tolerate unreadable gateway JSONL transcripts` ← **gateway resilience**
- `e2a1a2bf1 fix(gateway): pre-mark sessions as resume_pending before drain to prevent data loss (#27856)` ← **session/resume bug we'd inherit**
- `4d44304e8 Revert "fix(telegram): enforce TELEGRAM_ALLOWED_USERS allowlist on inbound messages"` ← **security regression-revert; check if our `secrets/telegram.env` `TELEGRAM_ALLOWED_USERS` setting still has any enforcement**
- `425aba766 fix(cli): ignore stale HERMES_TUI_RESUME env`

There is **no Hermes-upgrade ADR**, no CI check that pins our test suite against an upstream-bump diff, and no documented bump cadence. Migration risk: **HIGH**.

**Plugin contract grade: C** for the structure of our integration, **F** for the maintenance discipline around upstream tracking.

---

## 10. Coupling map (lib/* dep graph)

`grep -rEn "from lib\." lib/` results, summarized:

```
                       ┌────────────────────────────────────────────┐
                       │ lib.observability                          │
                       │   • __init__.py                            │
                       │   • otel_setup.py                          │
                       │ (no in-tree fan-out;                       │
                       │  installs global TracerProvider for all)   │
                       └─────────────┬──────────────────────────────┘
                                     │ side-effect import
                                     ▼
   ┌──────────────────┐     ┌───────────────────┐     ┌──────────────────┐
   │ lib.anchors      │     │ lib.durability    │     │ lib.evaluators   │
   │                  │     │   __init__       ──┼────▶│ orchestrator_hook│
   │  __init__ ──────┐│     │   failure_matrix  │     │   judge          │
   │  task_spec     ◀┘│     │   trichotomy ─────┤     │   consensus  ───┐│
   │  spec_store ◀───┘│     │   checkpoint ──┐  │     │                 ││
   │  intent_clf      │     │   resume ◀─────┘  │     │ (consensus      ││
   │  clarification   │     │   escalation      │     │  reaches into   ││
   │  loop            │     │                   │     │  lib.memory)    ││
   └────────┬─────────┘     └─────────┬─────────┘     └──────┬──────────┘│
            │ local import           │ local import          │           │
            │ (slash /cancel <id>)    │  (REJECTED-inject)   │           │
            ▼                         ▼                      ▼           │
   ┌──────────────────┐     ┌───────────────────┐                        │
   │ lib.kanban       │◀────│ lib.memory        │◀───────────────────────┘
   │  __init__        │     │   __init__        │
   │  telegram_bridge │     │   rejected        │
   │  notification…   │     │   intent_clf ─────┼─────▶ (reads
   │                  │     │                   │       lib.anchors.
   │                  │     │                   │       intent_classifier)
   └──────────────────┘     └───────────────────┘
```

Concrete edges (from `lib/*` and `scripts/escalation_loop.py`):

- `lib.anchors.__init__` → `lib.kanban.telegram_bridge` (local import, only on `/cancel <id>` dispatch)
- `lib.anchors.spec_store` → `lib.anchors.task_spec`
- `lib.durability.__init__` → `lib.durability.{failure_matrix,trichotomy,escalation,checkpoint,resume}`
- `lib.durability.__init__` → `lib.memory.{intent_classifier, rejected}` (local import inside `_p1_4_inject_rejected`)
- `lib.durability.resume` → `lib.durability.checkpoint`
- `lib.durability.trichotomy` → `lib.durability.failure_matrix`
- `lib.evaluators.__init__` → `lib.evaluators.orchestrator_hook`
- `lib.evaluators.consensus` → `lib.evaluators.judge`
- `lib.evaluators.consensus` → `lib.memory.rejected` (local import)
- `lib.kanban.__init__` → `lib.kanban.{telegram_bridge, notification_policy}`
- `lib.kanban.telegram_bridge` → `lib.kanban.notification_policy`
- `lib.memory.__init__` → `lib.memory.rejected`
- `lib.memory.intent_classifier` → `lib.anchors.intent_classifier`
- `lib.observability.__init__` → `lib.observability.otel_setup`
- `scripts/escalation_loop.py` → `lib.durability.escalation`

**Cycle check**: no module-level cycles. `lib.memory.intent_classifier` imports from `lib.anchors.intent_classifier` (one direction); `lib.anchors.__init__` does a *local* import of `lib.kanban.telegram_bridge` inside a slash-handler function — safe, but creates an implicit run-order edge.

**God-modules vs anemic-modules**:
- **Heaviest**: `lib/memory/rejected.py` (336 LOC, 1 module — fine, but largest single file).
- **Anemic**: `lib/anchors/__init__.py` is wired (`register(ctx)` works, hooks fire) but **6 of 8 handlers are TODO-string returns** — public surface without implementation.
- **Surface-only**: `lib/durability/trichotomy.py:68-87` (`before_tool_call`, `after_tool_call`) — registered as hooks, but `before_tool_call` is `return None` and `after_tool_call` only emits an OTel span; neither calls any of the 16 named handlers in the matrix.
- **Pure data**: `lib/durability/failure_matrix.py` is a dict literal + lookup; this is fine and exactly what it claims to be.

**Coupling grade: B.** Architecture is clean, dependencies flow one direction, no cycles. The issue is depth, not breadth: too many modules are wired-but-stubbed.

---

## 11. Upstream submodule risk

(See §9 for the substantive analysis.)

| Metric | Value |
|---|---|
| Hermes pin | `ddb8d8f` (2026-05-14) |
| Upstream HEAD | `a0bd11d` (current) |
| Commits behind | **718** |
| Calendar staleness | ~5 days but **very active upstream** (~150 commits/day) |
| Known security-relevant fixes in delta | At least 1 (`62573f44c` — atomic writes, TOCTOU, flock) |
| Known gateway/session-data fixes in delta | At least 2 (`e2a1a2bf1`, `b8a9cbd18`) |
| Test-suite gating against bump | None |
| Upgrade procedure | None documented |

**Submodule risk grade: D.** Acceptable for a 5-day pin window, but only because *we did not validate against an upgrade.* No mitigation plan exists.

---

## 12. Failure-matrix coverage

The 33-mode matrix in `docs/architecture/failure-matrix.md` and `lib/durability/failure_matrix.py` is **string-only**:

- `lib/durability/failure_matrix.py:17-186` defines a Python dict mapping `F1..F33` → `{class, description, handler}` where `handler` is a *string label*, not a callable.
- `lib/durability/trichotomy.py:40-46` `classify(err)` does work — it regex-matches exception text to an F-code. (Good. Unit-tested at `tests/unit/test_trichotomy.py`.)
- `lib/durability/trichotomy.py:68-87` `before_tool_call` is `return None`; `after_tool_call` only emits an OTel span. **No code calls any handler.**
- `grep -rE "def halt_alert_snapshot|def retry_with_backoff|def restart_sandbox|…"` across `lib/` returns **empty**.

What does fire:
- The F32 (24h-Telegram-silence) path is wired via `scripts/escalation_loop.py` → `lib.durability.escalation.run_once`. **One** of the 33 modes has a real action.
- Unit tests in `tests/unit/test_failure_matrix.py` assert: 33 codes present (line 6), every code maps to a valid class (line 11), no duplicates (line 19), lookup works (line 25), unknown raises (line 38). **None** of these test that a handler executes.
- Integration test `tests/integration/test_p1_6_failure_matrix.py` is a 5-mode regex check — also no handler invocation.

**Net**: of 33 documented modes, **1 has a real handler** (F32 escalation sidecar), the others are aspirational. Documentation asserts a "Fail-Loud / Fail-Soft / Self-Heal trichotomy" "enforced" by the system; the system does not enforce it.

**Failure-matrix grade: F.** Doc claims a contract the code does not honor.

---

## 13. Architecture diagrams

- `find . -type f -name "*.mmd" -o -name "*.drawio" -o -name "*.puml"` (excluding upstream): **empty**.
- `grep -rln "```mermaid"` in `docs/`: 1 match (`docs/superpowers/specs/SESSION-COMPLETE-2026-05-15-P1-KICKOFF.md`).
- Existing prose architecture is in `docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md` (an excellent 12-section design doc — but it describes the **planned** 12-service stack, not the realised 7-service one).

**Onboarding via prose alone**: an architect new to the system will spend their first day reading a spec that does not match the running stack. Cross-reference the doc-drift §1 to estimate the wasted effort.

**Recommendation**: a `docs/architecture/system-diagram.md` with a Mermaid `graph LR` of the actual 7 services + the `lib/*` plugin layer + the data flow (Telegram → hermes → litellm → Vertex AI → back; OTel → collector → Phoenix; Chroma Cloud egress; GitHub MCP sidecar). One source-truth diagram would absorb the bulk of the drift currently spread across spec + README + runbook.

**Diagram grade: F.**

---

## 14. Service boundaries

The 7 services in `deploy/docker-compose.yml`:

| Service | Image | Responsibility (per compose comments + code) | Boundary clarity |
|---|---|---|---|
| `litellm-proxy` | `ghcr.io/berriai/litellm:v1.84.0` | OpenAI-format → Vertex AI translation, budget caps, retries, OTel cost export | **Clear.** Per ADR 0002. |
| `otel-collector` | `otel/opentelemetry-collector-contrib:latest` | Receive OTLP from agent + LiteLLM, route to Phoenix | **Clear.** |
| `phoenix` | `arizephoenix/phoenix:latest` | Local trace UI + OTLP receiver | **Clear.** |
| `shell-sandbox` | locally-built `autonomousagent/shell-sandbox:0.1.0` | Sleeping `network_mode: none` container for `docker exec`-style tool dispatch | **Underused.** Not wired into a `tool_dispatch` path I can find. May be present but unconnected. |
| `github-mcp` | `ghcr.io/github/github-mcp-server:latest` | HTTP MCP endpoint with `--toolsets all` | **Clear.** |
| `hermes` | locally-built `autonomousagent/hermes:0.1.0` | Agent loop + Telegram gateway (collapsed per `:194-197` comment) | **God-service.** Telegram polling, agent loop, plugin host, OTel emitter, Chroma Cloud client, Honcho client (disabled), GitHub MCP client, LiteLLM client. Boundary is "everything that isn't otel/phoenix/shell-sandbox". |
| `escalation-watcher` | reuses `autonomousagent/hermes:0.1.0` | Periodic Kanban scan + Telegram alert for stuck cards (F32 path) | **Clear.** Sidecar pattern. |

Service-to-service contracts:
- `hermes` → `litellm-proxy`: HTTP `/v1/chat/completions` per OpenAI schema. **Documented in ADR 0002.** Authenticated via master-key bearer. OK.
- `hermes` → `github-mcp`: HTTP MCP (not stdio per compose comment). **Undocumented contract.** Token via `GITHUB_PERSONAL_ACCESS_TOKEN_FILE`.
- `hermes` → `otel-collector`: OTLP HTTP `:4318/v1/traces`. **Documented in `lib/observability/otel_setup.py:73-83`.** OK.
- `litellm-proxy` → `otel-collector`: OTLP HTTP `:4318`. **Documented in compose.**
- `otel-collector` → `phoenix`: OTLP HTTP `:6006/v1/traces` per the recent OTel collector config fix. **Documented in `deploy/otel/collector.dev.yaml`** (not opened in this audit but referenced in PR #35).
- `escalation-watcher` → Telegram API: outbound HTTPS, via `lib/kanban/telegram_bridge.send_alert`. But `lib/durability/escalation.py:40` has a `TODO(P1-5)` that says "replace with `telegram_bridge.send_alert`" — so the watcher currently `print()`s instead of sending. **Broken contract.**
- `shell-sandbox` ↔ `hermes`: not wired; no `docker exec` calls into shell-sandbox found in code.

**Service-boundary grade: C.** `hermes` is a god-service by accident of the gateway/agent collapse (the collapse decision is defensible but should be ADR'd, see G5). `shell-sandbox` and the watcher have semi-broken integration.

---

## 15. Data lifecycle

| Store | Lives in | Retention | Backup? | Restore? |
|---|---|---|---|---|
| **Hermes Kanban / sessions** | named volume `hermes-data` → container `/data` and `/root/.hermes` | `durability.checkpoint.retention_count: 50` + `keep_every_nth: 100` per `config/limits.yaml` | `scripts/snapshot.sh:14` (**BROKEN** — exec's `hermes-agent` not `hermes`) | `docs/runbooks/recovery.md:14-23` (**BROKEN** — references non-existent volumes) |
| **Chroma vectors** | external (Chroma Cloud) | N/A — cloud-managed | Per Chroma Cloud SLA (unverified) | Per Chroma Cloud SLA (no local restore procedure) |
| **Honcho** | disabled | N/A | N/A | N/A |
| **Phoenix traces** | named volume `phoenix-data` | none documented; Phoenix default | not snapshotted | not in recovery runbook |
| **Local logs** | `logs/` (gitignored), `logs/healthcheck.log` rotated by JSON-file driver | `local_logs_dev.rotate_size_mb: 100, keep_files: 5` per `config/limits.yaml` | none | none |
| **Per-step checkpoints** | `hermes-data:/data/checkpoints/{session}/step-N.json` written by `lib.durability.checkpoint.Checkpoint.maybe_write` | `retention_count: 50` + `keep_every_nth: 100` | **None wired** — `grep "Checkpoint(" lib/` returns only the class definition and tests. The hook in `lib/durability/__init__.py:42` calls `resume.rehydrate_latest_for_session` but no code path **writes** new checkpoints in the agent loop. Will-recover-from-nothing. | resume code exists, but with no writes it has nothing to load |

**Data-lifecycle grade: D.** Checkpoint writer is dead code; snapshot/restore is broken; only the Hermes-built-in Kanban + memory store is actually durable (and only because Hermes upstream writes it).

---

## 16. CHANGELOG honesty

Reading `CHANGELOG.md` against the actual git log:

- `[Unreleased]` section (lines 13-37):
  - "Added — SDLC + parallel-session documentation" bullets correctly correspond to PR #18 (`5ee4dcc`), but PR #18 has *merged*. The bullets should be in a new dated section, not "[Unreleased]".
  - "In flight (open PRs against main)" table lists PRs #10-#15. All are merged or superseded. **Historical fiction.**
  - "Fixed" bullet about CI baseline failures is real (commit `9901462` and surrounding fixes).
- `[0.0.1-phase1.merge] — 2026-05-15` section is comprehensive and accurate **for commit `0f74412`**, but everything merged since (`b7738f9`, `1dca48f`, `8127e5b`, `4ee6991`, `85512a3`, and the dozen smaller PRs) is unrepresented.
- No `[phase1-accepted]` section exists despite the tag being cut.

**CHANGELOG grade: D.** The project's own pull-requests convention (`docs/conventions/pull-requests.md:170`) requires CHANGELOG entries per PR. That contract is **broken** for at least 20 PRs since 2026-05-15.

---

## Grades per category

| Category | Grade | Rationale |
|---|---|---|
| 1. README accuracy | **D** | 12-service / Chroma-self-hosted / Honcho-self-hosted / Modal-Daytona claims are all false |
| 2. ADRs | **C-** | Existing 7 are honest; 10 major decisions made post-2026-05-14 have no ADR |
| 3. Runbooks | **D-** | Acceptance + recovery + snapshot use service names that don't exist; healthcheck-cron OK; telegram-bot has one wrong config path |
| 4. Conventions | **B-** | Logging catalog aspirational; commit/branch/code-style are conformant; PR doc is the most accurate doc in the repo |
| 5. Onboarding | **D** | Non-owner cannot bring up the stack (no docs for personal-credential bootstrap) |
| 6. API surface | **D** | All public surfaces exist (slash commands, CLI subcommand, plugin contract); none are documented user-facing |
| 7. Phase 2 readiness | **F** | No Phase 2 plan or spec exists; the audit-plan P2 section is stale |
| 8. Inline code comments | **B+** | Most modules have good docstrings; TODOs reveal unfinished hook bodies |
| 9. Plugin contract stability | **C** | Structure is sound; upstream-tracking discipline is absent |
| 10. Coupling map | **B** | Clean DAG, no cycles, sane fan-out |
| 11. Submodule risk | **D** | 718 commits behind upstream with security-relevant fixes; no upgrade plan |
| 12. Failure-matrix coverage | **F** | 32 of 33 modes have no handler |
| 13. Architecture diagrams | **F** | None exist |
| 14. Service boundaries | **C** | `hermes` is a god-service; `shell-sandbox` and watcher partly disconnected |
| 15. Data lifecycle | **D** | Checkpoint writer is dead code; snapshot/recovery broken |
| 16. CHANGELOG honesty | **D** | Convention violated for ~20 PRs; "in-flight" table is fictional |

**Composite grade: D+.**

Phase 2 lives or dies on documentation accuracy. The documentation today **cannot** safely ground a Phase 2 build. Before any Phase 2 plan is drafted, the following P0 doc-debt items must be retired:

1. **Fix `scripts/snapshot.sh`, `scripts/panic.sh`, `scripts/teardown.sh`** to use real service names and not exec `chroma`/`honcho-db` (D14-D17). These three scripts are the **disaster-recovery path**; they currently don't work.
2. **Rewrite `docs/runbooks/recovery.md`** against the 7-service reality (D11-D14).
3. **Patch `docs/runbooks/phase1-acceptance.md`** service names so the runbook is re-usable for any future acceptance regression (D8-D10).
4. **Decide: kill or wire the 33-mode handlers.** Either implement the named handlers or shrink the matrix to what the code actually does. As-is, `docs/architecture/failure-matrix.md` makes a contract the code does not honor (D21).
5. **Decide: kill or wire the scrubber.** Either remove `lib/scrubber.py` + step 5 of the acceptance runbook, or wire the scrubber into the model-output / tool-result path before any Phase 2 work hardens the architecture against an absent component (D7).
6. **Cut a `[phase1-accepted]` CHANGELOG section** with the actual commit set; clear the "in-flight" historical-fiction table (D23-D24).
7. **Open ADRs** for the 10 missing decisions in §2 (G1-G10).
8. **Add one architecture diagram** showing the real 7-service stack (`docs/architecture/system-diagram.md`, mermaid).
9. **Author a Phase 2 spec + plan** under `docs/superpowers/{specs,plans}/`.
10. **Hermes upstream bump audit + ADR.** 718 commits behind is a Phase 2 entrance liability.

If items 1-3 do not happen, Phase 2 will start by re-discovering Phase 1's broken DR path under stress. If items 4-5 do not happen, Phase 2 will inherit two dead subsystems (failure-matrix handlers, scrubber) and amplify the lie. If items 6-10 do not happen, Phase 2 has no foundation to plan against.

---

## References to enrich next pass

- `audit/audit-plan.md` P2 section (lines 159-242) — needs rewrite against current 7-service reality
- `audit/findings.md` — does not include a Phase 2 entrance gate
- `audit/phase1-completion-final-sweep-2026-05-18/findings.md` — most findings still relevant; reconciled in this audit where state has changed
- Hermes upstream `hermes-agent/hermes_cli/plugins.py` lines 376-905 — definitive source of the plugin contract
- Hermes upstream commit `62573f44c` (atomic writes, TOCTOU) — likely Phase 2 must absorb before extended unattended runs
