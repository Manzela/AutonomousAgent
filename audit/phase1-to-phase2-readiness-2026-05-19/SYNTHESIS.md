# Phase 1 → Phase 2 Readiness — Forensic Audit Synthesis

**Date**: 2026-05-19
**Branch audited**: `main` @ `85512a3` (tag `phase1-accepted` from yesterday)
**Audit method**: 5 parallel Opus subagents + controller verification of the most consequential claims.
**Discipline**: per `verification-before-completion` — every claim below has `file:line` evidence; the one I judged most consequential (hook signature mismatch) was independently verified by direct grep + container log inspection.

---

## TL;DR — Phase 1 was tagged prematurely

The `phase1-accepted` tag certifies a state where most of the Phase 1 enhancement layer is **dead-on-arrival in production**. Plugin discovery succeeds, register() runs, but the hook bodies use POSITIONAL signatures `(ctx, tool_call)` while Hermes calls them as `invoke_hook(**kwargs)` — every invocation TypeErrors and gets swallowed at WARN level.

**Verified in live container logs (just now):**
```
WARNING Hook 'pre_tool_call' callback before_tool_call raised:
  before_tool_call() got an unexpected keyword argument 'tool_name'
```

**What this means for Phase 1 subsystems:**

| Subsystem | Plugin loads? | Hooks fire successfully? | Real production effect |
|---|---|---|---|
| P1-1 anchors | ✅ | partial | 5 slash commands return literal `"TODO(...)"` strings (`lib/anchors/__init__.py:38-80`) |
| P1-2 evaluators | ✅ | NO | Judge dispatch never runs against real tool calls |
| P1-3 checkpointing | ✅ | NO | `Checkpoint.maybe_write` has ZERO live callers; restart = full state loss |
| P1-4 REJECTED.md | ✅ | NO | inject hook TypeErrors; institutional memory never injects |
| P1-5 kanban→telegram | ✅ | NO | Card creation + notifications inert (hook bodies are stubs anyway) |
| P1-6 durability | ✅ | NO | trichotomy classifier, backoff_delay, escalation watcher all unreachable |
| observability (PR #52) | ✅ | YES | Only plugin using kwargs-correct signatures — emits `turn.start` / `tool.dispatch` / `model.call` |

**The `phase1-accepted` tag should be qualified or revoked.** Acceptance was based on "plugins load + tests pass" which is necessary but not sufficient. The tests test functions in isolation; the discovery proves register() runs; but the runtime hook bodies fail silently.

---

## Forensic findings by domain

### 🔴 SECURITY — 3 CRITICAL (`audit/.../security-audit.md`)

1. **`lib/scrubber.py` is dead code at runtime.** Mounted into container, tested in isolation, but no production code path imports `Scrubber`. The documented `SECURITY.md` "Tier 4: scrubbed at egress" boundary does NOT exist. *(Confirms audit B5 from prior sweep.)*
2. **4 leaked credentials un-rotated 4 days past warning.** `SESSION-COMPLETE-2026-05-15-P1-KICKOFF.md:316-326` flagged: Telegram bot token, Chroma Cloud key, Healthchecks URL, GitHub PAT with `repo, workflow, read:org` scope (full private-repo read/write across org). `git log -- secrets/*.sops` shows all four still at the original Phase/1 squash-merge SHA `0f74412 (2026-05-15)`. No rotation.
3. **Container runs as root + floating image tags.** `deploy/Dockerfile.hermes` has no `USER` directive; 3 images use `:latest` tags; no `@sha256` digest pins; no Trivy/Snyk/Grype in CI.

Solid areas: sops/age encryption, exact-pinned Python deps in submodule, network isolation logic, Telegram allowlist, OTel doesn't log PII.

### 🔴 RELIABILITY — 6 BLOCKING (`audit/.../observability-reliability-audit.md`)

1. **P1-3/P1-4/P1-6 hooks dead-on-arrival** (verified above).
2. **`Checkpoint.maybe_write` zero live callers** — well-tested in isolation, never instantiated in production. Restart = full state loss.
3. **Telegram escalation is `print()`/`logger.info()` stubs** (`lib/durability/escalation.py:38`, `lib/kanban/telegram_bridge.py:128`).
4. **`escalation-watcher` sidecar isn't running** despite being defined in compose.
5. **DR is paper-only** — `snapshot.sh`/`panic.sh`/`recovery.md` reference removed services (`chroma`, `honcho-db`, `hermes-gateway`). No snapshots exist on disk. No RTO/RPO defined.
6. **OTel→Phoenix exporter drops spans intermittently** (connection resets on collector→Phoenix).

Other: zero metrics emitted; zero error tracking (Sentry/Rollbar); zero circuit breakers; Hermes ignores its own `logs.format: json` config; daily 4am session reset hostile to 48h jobs; `budget.daily_usd_cap` + `agent.max_concurrent_tasks` unenforced.

**Reliability grade: D**

### 🟠 CI/CD MATURITY — Grade C− (`audit/.../cicd-audit.md`)

What works (real strengths):
- 11 required status checks; ~10% failure rate over last 50 runs (mostly self-inflicted PR-title rejections)
- ~20s median CI runtime; clean concurrency on heavy workflows
- Read-default workflow `permissions:`; no `pull_request_target` (no priv-escalation surface)
- No hardcoded custom secrets in workflows (only `GITHUB_TOKEN`)
- Pre-commit is a strict superset of CI's blocking checks
- SemVer + Keep-a-Changelog discipline is real

**Critical enterprise gaps:**
1. **GitHub Advanced Security entirely OFF** — Code Scanning, Secret Scanning service, Vulnerability Alerts, Dependabot security fixes all return 404 / `enabled:false` via `gh api`.
2. **No SAST.** CodeQL was deliberately removed (README documents this); never reintroduced. No `ruff` security rules selected.
3. **No SCA / dependency CVE scan.** Dependabot bumps versions but never scans for known vulns.
4. **No container image scanning.** hadolint is `continue-on-error: true` — required-check theatre.
5. **No SBOM, no signing (cosign/Sigstore), no SLSA provenance** on releases. Release ships only Markdown notes.
6. **Branch protection holes**: `required_approving_review_count: 0`; `enforce_admins: false`; `required_signatures: false`; `required_linear_history: false`.
7. **No third-party action SHA pinning** — all `uses:` are floating tags (`@v6`, `@v7`).
8. **Tests**: no coverage gate, no parallelism, no flaky-retry, no integration tests in CI, single Python version only.
9. **CI observability**: no runtime trend, no cost tracking, no failure alerts.

### 🟠 CODE QUALITY — Grade B− (`audit/.../quality-audit.md`)

- Coverage 75% on `lib/`; integration tier mostly skipped (6 of 14 tests skipped per documented P2 deferral)
- Test quality solid (no vacuous asserts; ~21% are wiring-smoke that wouldn't catch real bugs)
- **5 user-visible TODO returns in `lib/anchors/__init__.py:38-80`** — actual slash commands return literal `"TODO(...)"` strings to the user
- **4 lib/ modules tested but never imported by production**: `scrubber`, `toolset_router`, `healthcheck`, `limits_validator`
- 10 active TODOs across the codebase; 5 are user-facing (the anchors stubs)
- Subprocess discipline excellent (zero shell, zero `os.system`, zero `shell=True`)
- Error handling discipline good (16 broad excepts, all annotated + logged)

### 🟠 DOCS + ARCHITECTURE — Grade D+ (`audit/.../docs-architecture-audit.md`)

- **README claims 12 services**; actual `deploy/docker-compose.yml` ships **7** (litellm-proxy, otel-collector, phoenix, shell-sandbox, github-mcp, hermes, escalation-watcher). Chroma is cloud-only; Honcho is disabled; Modal/Daytona never existed in this stack.
- **The `hermes-agent → hermes` rename didn't propagate** to `phase1-acceptance.md`, `recovery.md`, `snapshot.sh`, `panic.sh`, `teardown.sh` → **entire DR path is broken** (snapshot execs non-existent `chroma` and `honcho-db`; restore references non-existent volumes).
- `docs/architecture/failure-matrix.md` claims 33 modes with 16 named handlers; `grep` for handler definitions returns **empty** for 32 of 33 (only F32 escalation sidecar has any real handler — and the sidecar isn't actually running).
- **Hermes submodule 718 commits behind upstream** including at least one security-relevant fix (atomic-writes/TOCTOU at upstream `62573f44c`) + gateway data-loss fixes. No Hermes upgrade ADR.
- **No Phase 2 spec or plan exists.** The pre-existing `audit/audit-plan.md` from 2026-05-15 has a P2 section but it's stale.
- 10 missing ADRs for major decisions made post-Phase-1 (plugin loading via `~/.hermes/plugins/`, OTel approach, daily session reset, observability service-name override, etc.)

---

## Phase 1.1 issue list (the real one — supersedes #53/#54/#55)

The 3 issues opened yesterday (Honcho, OpenInference attrs, LiteLLM spend DB) are valid but TINY relative to what this audit found. The real Phase 1.1 work:

### 🔴 P0 — must land before Phase 2 has any meaning

1. **Fix hook signature mismatch** in `lib/durability/__init__.py` + `lib/durability/trichotomy.py` (positional → kwargs to match Hermes' `invoke_hook(**kwargs)` contract). 1-2 PRs, ~2-3h. Without this, all of P1-3/P1-4/P1-6 are inert.
2. **Wire `Checkpoint.maybe_write` into the live flow** — find where Hermes' agent loop runs, add a `post_tool_call` hook that calls it. ~3-4h.
3. **Fix P1-1 anchors slash commands** — `/lock`, `/skip`, `/cancel`, `/confirm`, `/new` currently return literal `"TODO(...)"` strings. Implement against the real TaskSpec store from PR #43 etc. ~6-8h.
4. **Fix P1-5 kanban hook bodies** — currently TODO stubs. Implement card-creation-on-message + status-change notifications. ~4-6h.
5. **Wire `lib/scrubber.py` as LiteLLM callback** — currently dead code. Add to `deploy/litellm/config.yaml callbacks:` + verify writes happen to `/data/secret-leak-attempts.log`. ~2h.
6. **Rotate 4 leaked credentials**: Telegram token, Chroma Cloud key, Healthchecks URL, GitHub PAT. ~30 min + downstream re-configs.
7. **Add `USER` directive to `deploy/Dockerfile.hermes`** (non-root). ~30 min.
8. **Pin all base images by digest + remove `:latest` tags**. ~1h.
9. **Update DR scripts** (`snapshot.sh`, `panic.sh`, `teardown.sh`, `recovery.md`) for the `hermes-agent → hermes` rename + removal of `chroma`/`honcho-db`. ~2h.

### 🟠 P1 — should land before Phase 2 acceptance

10. **Add CodeQL back to CI** (was removed; track that decision in an ADR; re-enable for security). ~30 min + first-scan triage.
11. **Add container image scanning** (Trivy or Grype). ~1h.
12. **Add SBOM generation** (syft → upload artifact on release). ~1h.
13. **Add `pip-audit` or `safety`** for SCA. ~30 min.
14. **Pin all third-party GitHub Actions by SHA** (not floating tags). ~1h.
15. **Set `required_approving_review_count: 1`** + `enforce_admins: true` on branch protection. 5 min via API.
16. **Bump Hermes submodule** to a recent commit, write the upgrade ADR. ~4-6h (test surface is wide).
17. **OpenInference span attrs** (issue #53). ~2h.
18. **Honcho memory wiring** (issue #54). ~6-10h.
19. **LiteLLM spend DB attachment** (issue #55). ~2h.
20. **Failure matrix handlers** — implement at least 5-10 of the 33 modes (currently only F32 has anything). ~6-8h.

### 🟡 P2 — Phase 2 prep

21. **Write Phase 2 spec + plan** (not just stale audit-plan.md text). ~4-6h.
22. **Fix README + runbook drift** (12 services → 7; service-name rename propagation). ~2h.
23. **10 missing ADRs** for decisions made post-Phase-1. ~30 min each.
24. **Add metrics emission** (not just traces). ~4h.
25. **Add error tracking** (Sentry or equivalent). ~2-3h.
26. **Document/disable daily 4am session reset** (hostile to long-running jobs Phase 1 was designed for). ~1h.

---

## Recommendation

**Do not start Phase 2 against this baseline.** The `phase1-accepted` tag certifies a system whose enhancement layer doesn't actually run. Phase 2 (cloud-prod migration) on top of inert P1 hooks would deploy a system that looks like it has retry/checkpoint/REJECTED.md but doesn't.

Two clean paths:

**Path α: Phase 1.0.1 hotfix** (recommended)
- Land items 1-9 (P0) as one bundled PR or 3-4 small PRs.
- Re-verify with the runbook + check `docker logs hermes` shows ZERO `Hook ... raised:` warnings post-deploy.
- Cut a new tag `phase1.0.1-accepted`.
- THEN start Phase 2 spec.
- Effort: ~24-32h.

**Path β: Phase 1.1 batch**
- Land items 1-20 (P0 + P1) as a coherent Phase 1.1 work cycle.
- Cut `phase1.1-accepted`.
- Effort: ~50-70h.

Either way, the current `phase1-accepted` tag should be acknowledged as a "plugins-load milestone" not a "production-ready milestone." Consider adding an annotation to the tag note explaining this.

---

## Companion deliverables in this audit dir

- `security-audit.md` — full security findings + secret rotation checklist
- `quality-audit.md` — coverage report, TODO inventory, dead code list
- `cicd-audit.md` — required-check inventory, 12-item enterprise-gap remediation list
- `observability-reliability-audit.md` — span coverage, 6 blocking reliability findings
- `docs-architecture-audit.md` — 30-row drift table, 10 missing ADRs, submodule risk
- `SYNTHESIS.md` — this document
