# Security + Supply-Chain Audit — Phase 1 → Phase 2 Readiness

**Audit date:** 2026-05-19
**Target SHA:** `85512a3` (tag `phase1-accepted`, 2026-05-18)
**Auditor:** automated forensic sweep
**Scope:** secrets, containers, network, supply chain, scrubbing, RBAC, rotation, logging

---

## Executive summary

Three CRITICAL findings would block enterprise deployment as-is. The most important: **the secret scrubber (`lib/scrubber.py`) is dead code at runtime** — it is mounted into the container but never imported by anything that runs in production. The second: **four credentials known to be leaked into a prior chat transcript on 2026-05-15 have not been rotated** (git log shows `secrets/*.sops` files last touched in the Phase/1 squash merge `0f74412`, contradicting the rotation guidance in `docs/superpowers/specs/SESSION-COMPLETE-2026-05-15-P1-KICKOFF.md:316-326`). The third: **the Hermes container runs as root** with no `USER` directive and several base images use floating `:latest` tags. The remaining controls (sops/age encryption at rest, exact-pin Python deps in the submodule, `internal: true` network isolation, Telegram allowlist enforcement, OTel privacy hygiene, branch protection wired to 11 required checks) are genuinely solid and should not be reworked.

---

## CRITICAL findings

### C1. Secret scrubber is dead code at runtime
**Evidence:** `lib/scrubber.py:1-65` defines the class. The only importers in the repo are `tests/unit/test_scrubber.py:9` (`from lib.scrubber import Scrubber`) and `lib/scrubber.py:7` (docstring example). `grep -rIn "Scrubber\|scrubber" config/hermes/ deploy/ hermes-agent/` returns ZERO production import sites. `lib/durability/failure_matrix.py:128` only references the *concept* (string description) of a scrubber hit, not a call. The patterns file (`config/scrubber-patterns.yaml`) and module are both mounted into the container (`deploy/docker-compose.yml:256, 266`), but `config/hermes/cli-config.yaml:108-115` does not enable a `scrubber` plugin, and Hermes upstream has no scrubber concept of its own.
**Severity:** CRITICAL. SECURITY.md §"Defense-in-depth boundaries" lists "**Tier 4**: Secret exposure (sops-encrypted at rest, **scrubbed at egress**)" as a documented boundary; the egress half does not exist.
**Fix:** wire `Scrubber` into the OTel `pre_llm_call`/`post_llm_call` and `pre_tool_call`/`post_tool_call` hooks in `lib/observability/__init__.py`, or expose it as a true plugin (`lib/scrubber/__init__.py` with `register(ctx)`). Add to `plugins.enabled` in `config/hermes/cli-config.yaml`.
**Effort:** 1–2 days (plugin scaffold + hook wiring + an integration test that asserts a planted credential is `[REDACTED]` in persisted logs).

### C2. Four leaked credentials still un-rotated, 4 days past warning
**Evidence:** `docs/superpowers/specs/SESSION-COMPLETE-2026-05-15-P1-KICKOFF.md:316-326` explicitly enumerates four tokens pasted into chat: Telegram bot token, Chroma Cloud API key, Healthchecks.io ping URL, GitHub PAT. `git log --format='%H %ai' -- secrets/telegram.env.sops secrets/github-pat.sops secrets/chroma-cloud.env.sops secrets/healthchecks-url.sops` all return a single commit: `0f74412 2026-05-15 18:30:21 +0300 Phase/1 (#6)` — the same commit that introduced the warning. No subsequent re-encryption commits exist. Today is 2026-05-19; the leaked tokens have been live for 4 days.
**Severity:** CRITICAL. GitHub PAT scopes per `deploy/docker-compose.yml:161` are `repo, workflow, read:org, security_events` — read/write to all private repos.
**Fix:** rotate all four tokens via the procedure documented in the spec (BotFather `/revoke`, Chroma Cloud dashboard, Healthchecks.io, GitHub `/settings/tokens`). After each, `sops -e` the new value into `secrets/*.sops` and commit with a `chore(secrets): rotate <name>` message that lets git log prove rotation.
**Effort:** 30 minutes total; blocker for any external review or Phase 2 promotion.

### C3. Hermes container runs as root + floating image tags
**Evidence:** `deploy/Dockerfile.hermes:1-42` contains no `USER` directive — the container will inherit root from `python:3.11-slim`. By contrast `deploy/sandboxes/Dockerfile.shell-sandbox:20` correctly sets `USER sandbox`. Image tags: `deploy/docker-compose.yml:102` (`otel/opentelemetry-collector-contrib:latest`), `:123` (`arizephoenix/phoenix:latest`), `:163` (`ghcr.io/github/github-mcp-server:latest`). LiteLLM is correctly pinned at `:67` (`ghcr.io/berriai/litellm:v1.84.0`). Zero images use digest pins (`@sha256:...`).
**Severity:** CRITICAL for hardened deployment. Root + writable `/data, /root/.hermes` + secrets mounts at `/run/secrets/litellm_master_key` means a Hermes-process RCE is a direct path to host volume tampering. Floating `:latest` tags also nullify the supply-chain protection that exact-pinning the Python deps provides.
**Fix:** add `RUN useradd -m -u 1000 -s /bin/bash hermes && chown -R hermes:hermes /app /data` and `USER hermes` to `Dockerfile.hermes`. Pin every image to a concrete version, then to `@sha256:` digests after one stable cycle. Dependabot already covers Docker (`.github/dependabot.yml:54-62`) so updates remain mechanical.
**Effort:** 1 day (rebuild + smoke test the volume-permission interaction with `hermes-data`).

---

## HIGH findings

### H1. Branch protection requires zero approving reviews
**Evidence:** `gh api repos/Manzela/AutonomousAgent/branches/main/protection` returns `"required_approving_review_count":0` while `dismiss_stale_reviews:true` and `require_code_owner_reviews:true` are set. With zero required reviews, CODEOWNERS enforcement is moot. `enforce_admins:false` also lets the owner bypass.
**Severity:** HIGH. Single-developer project today, but trivially exploitable if a Hermes-driven PR ever runs with the leaked PAT.
**Fix:** set `required_approving_review_count: 1` (self-merge still possible after a wait; for a single-dev project use the GitHub "linear history + require PR" combination), or accept the risk and document it in SECURITY.md as a "Known security non-goal." Enable `enforce_admins:true` once a co-maintainer joins.
**Effort:** 5 minutes (one `gh api` PATCH).

### H2. "Egress allowlist" is documented but not enforced
**Evidence:** `docs/decisions/0003-tiered-sandboxing-strategy.md:19` claims `external_https` has "egress allowlist enforcement" and `docs/architecture/failure-matrix.md:62` defines `F31 Egress allowlist violation attempt`. The actual enforcement in `deploy/docker-compose.yml:13-20` is a normal `egress: driver: bridge` (not `internal: true`); there are no iptables rules, no NetworkPolicy, no Docker iptables egress filter, no `lib/` code that intercepts httpx calls. The "allowlist" is purely advisory: `config/limits.yaml:24 modal_network_allowlist` is a list that is read by `lib/limits_validator.py` only as a schema field, never consumed at request time.
**Severity:** HIGH. Any tool the agent runs in `external_https` tier can hit arbitrary internet hosts. A prompt-injection that asks the agent to POST to attacker.com would succeed.
**Fix:** for Phase 2, either (a) deploy behind a forward proxy (squid/envoy) with deny-by-default + per-host allow, or (b) implement an httpx event hook that consults `config/limits.yaml:modal_network_allowlist` (rename to a generic `egress_allowlist`) and raises on miss. Until then, downgrade ADR-0003's wording from "enforcement" to "convention."
**Effort:** 3–5 days for proper proxy; 1 day for the httpx hook prototype.

### H3. No image scanning anywhere in CI or dev
**Evidence:** `grep -rIn "trivy\|grype\|snyk\|sbom" .github/ scripts/ deploy/` returns nothing. `.github/workflows/ci.yml:1-311` runs ruff/shellcheck/yamllint/hadolint/pytest/limits-validator/compose-render — no image vulnerability scan.
**Severity:** HIGH. We pull `python:3.11-slim`, `debian:bookworm-slim`, plus three `:latest` tags; CVEs in any could land silently.
**Fix:** add a `scan-images` job to `.github/workflows/ci.yml` using `aquasecurity/trivy-action` against the built `autonomousagent/hermes:0.1.0` and `autonomousagent/shell-sandbox:0.1.0` images. Fail on `HIGH,CRITICAL` once baseline is clean.
**Effort:** 4 hours.

### H4. No documented rotation cadence; no audit trail
**Evidence:** `secrets/README.md:1-25` covers add/edit/encrypt operations but says nothing about rotation cadence. `docs/decisions/0004-sops-age-secret-management.md:12` claims "clean rotation story" but no procedure exists in `docs/runbooks/` (verified: `find docs/runbooks -name "*.md"` returns six files, none named `rotation`). `docs/superpowers/specs/2026-05-14-hermes-agent-architecture-design.md:347` says "rotated quarterly" for LiteLLM master key — aspirational, no automation.
**Severity:** HIGH (compliance). Most enterprise security policies require 90-day rotation for bearer tokens; we have no mechanism to demonstrate compliance.
**Fix:** add `docs/runbooks/secret-rotation.md` with a per-secret table (Telegram, Chroma, Healthchecks, GitHub PAT, LiteLLM master key, age key) listing channel, owner, cadence, last-rotated date. Add a `git log --format='%ai %s' -- secrets/<name>.sops | head -1` audit script. Consider monthly Dependabot-style reminders via a scheduled workflow.
**Effort:** 1 day for the runbook + audit script.

### H5. GitHub PAT scopes are over-broad
**Evidence:** `deploy/docker-compose.yml:161` documents `repo, workflow, read:org, security_events`. `repo` alone grants full read/write to all private repos the user can see. The GitHub MCP is invoked with `--toolsets all` (`:160`) which includes `actions, code_security, copilot, dependabot, discussions, gists, git, issues, labels, notifications, orgs, projects, pull_requests, repos, secret_protection, security_advisories, stargazers, users`.
**Severity:** HIGH. Blast radius of a PAT compromise = the entire `Manzela` org footprint.
**Fix:** switch to a fine-grained PAT scoped to `AutonomousAgent` only with the minimum repository permissions, OR move to a GitHub App with per-repo install + short-lived tokens. Until then, narrow the MCP toolset from `all` to the actually-used subset (likely `repos, pull_requests, issues, actions`).
**Effort:** 4 hours for fine-grained PAT; 1–2 days for GitHub App migration.

---

## MEDIUM findings

### M1. `.secrets.baseline` is 4 days stale across active development
**Evidence:** `.secrets.baseline:8261-8264` shows `"generated_at": "2026-05-15T15:46:47Z"`. Many commits since (`85512a3`, `4ee6991`, etc.) added Python code under `lib/` without regenerating. The pre-commit hook (`detect-secrets`) runs on changed files only, so the baseline drifting silently is a real risk.
**Severity:** MEDIUM. The hook should still catch new findings; the baseline drift just means re-audit is harder.
**Fix:** in CI, after `detect-secrets scan --baseline`, add a step that diffs the regenerated baseline against the committed one and fails on net-new whitelisted findings.
**Effort:** 2 hours.

### M2. `high_entropy_hex` pattern would false-positive on git SHAs
**Evidence:** `config/scrubber-patterns.yaml:42-45` defines `\b[a-f0-9]{40,}\b` at `severity: info`. Every git SHA in a log line is 40 hex chars; every `uv.lock` hash is 64 hex chars. With the scrubber currently dead this doesn't matter at runtime, but when C1 is fixed the scrubber will redact every SHA in every commit message printed by the agent.
**Severity:** MEDIUM. Will degrade observability when the scrubber goes live.
**Fix:** tighten the regex to exclude obvious git contexts (`(?<!commit |sha:|@)\b...`) or raise the floor to 64 chars and add a negative-lookbehind for `sha256:` / `commit `.
**Effort:** 2 hours.

### M3. detect-secrets job uses `|| true` — never fails
**Evidence:** `.github/workflows/secret-scan.yml:120` runs `detect-secrets audit --report .secrets.baseline || true`. The `|| true` swallows any non-zero exit. The job is advisory-only.
**Severity:** MEDIUM. Gitleaks does fail on findings, so we're not blind, but detect-secrets adds defense-in-depth that's currently not enforced.
**Fix:** remove `|| true` once the baseline drift in M1 is resolved.
**Effort:** 1 minute (after M1).

### M4. Hermes upstream submodule pinned to `ddb8d8f`, no security update process
**Evidence:** `.gitmodules:1-3` + `git submodule status` → `ddb8d8fa842283ef651a6e4514f8f561f736c72e hermes-agent (v2026.5.7-647-gddb8d8fa8)`. ADR mentions watching for upstream updates but no automation: Dependabot doesn't handle git submodules.
**Severity:** MEDIUM. Hermes is a 87-file Python codebase with its own dep tree; a CVE in `openai==2.24.0` (pinned `hermes-agent/pyproject.toml:33`) won't reach us via our Dependabot.
**Fix:** add a weekly scheduled workflow that runs `git -C hermes-agent log --oneline ddb8d8f..origin/main` and opens an issue if non-empty.
**Effort:** 4 hours.

### M5. Phoenix UI bound only to localhost — but no auth at all
**Evidence:** `deploy/docker-compose.yml:125-127` binds Phoenix UI to `127.0.0.1:6006`. That mitigates remote exposure but anyone on the host (any user account, any local process) can read trace data including session IDs, model names, tool names — operationally useful intel for an attacker. SECURITY.md §"Known security non-goals" doesn't list "host-local users."
**Severity:** MEDIUM for single-user Mac; MEDIUM-HIGH for Phase 2 GCP VM where shared SSH access becomes plausible.
**Fix:** in Phase 2, put Phoenix behind an OAuth2 proxy or restrict via the VM firewall. For now, document the host-trust assumption in SECURITY.md.
**Effort:** 2 hours (docs) / 1 day (proxy in Phase 2).

---

## LOW findings

### L1. SARIF upload `continue-on-error: true`
**Evidence:** `.github/workflows/secret-scan.yml:77, 86`. Without GitHub Advanced Security, uploads silently no-op. Acceptable for a private repo today; revisit if Advanced Security is enabled.
**Severity:** LOW. Documented; intentional.

### L2. `pyproject.toml` uses `>=` ranges for the wrapper
**Evidence:** `pyproject.toml:6-10` — `pyyaml>=6.0, jsonschema>=4.20, httpx>=0.27, pydantic>=2.6`. Submodule is correctly exact-pinned (`hermes-agent/pyproject.toml:33-58` exhaustively `==X.Y.Z`). Wrapper has only 4 direct deps and no `uv.lock` of its own (only `hermes-agent/uv.lock` exists at the repo root mention; the project-root `uv.lock` is 102K lines, which is the submodule's resolution).
**Severity:** LOW. Surface is small; transitive risk is bounded.
**Fix:** convert to `==` pins to match the submodule's exact-pin policy.
**Effort:** 1 hour.

### L3. Phoenix attached to `egress` network only for port-publishing reasons
**Evidence:** `deploy/docker-compose.yml:130-131` comment. Phoenix doesn't actually need outbound — it's a side-effect of `internal: true` blocking published ports. Minor surface increase.
**Severity:** LOW. Document or split into a published-only network.

### L4. Dependabot alerts API returns 403
**Evidence:** `gh api repos/Manzela/AutonomousAgent/dependabot/alerts` → `"Dependabot alerts are disabled for this repository"`. Private repos require GitHub Advanced Security or org-level free coverage.
**Severity:** LOW. Dependabot version-update PRs still flow; alerts (CVE-based) do not. 8 of 9 Dependabot PRs to date are merged; the 9th `phase1-accepted` tag was hit before any new ones queued.

---

## CLEAN AREAS — verified solid

1. **sops/age encryption at rest is correct.** All 7 `*.sops` files in `secrets/` contain `ENC[AES256_GCM,...]` markers (`grep -l "ENC\["` returns 7/7). Plaintext counterparts (`secrets/chroma-token`, `litellm-master-key`, etc.) have permissions `-rw-------` (mode 600). No plaintext secret has ever been committed (`git log --all --diff-filter=A --name-only` for `.env|.key|.pem|credentials|password.txt|token.txt` returns empty for non-sops paths).
2. **`secrets/.gitignore` is deny-by-default with explicit allow.** `secrets/.gitignore:5` uses `*` followed by `!*.sops`, `!README.md`, `!.gitignore`, `!*.template.txt`. Belt-and-suspenders with root `.gitignore:1-7`.
3. **Submodule Python deps are exact-pinned with a written rationale.** `hermes-agent/pyproject.toml:16-29` documents the Mini Shai-Hulud worm justification for `==X.Y.Z`. All 19 core deps are exact.
4. **Shell sandbox is genuinely sandboxed.** `deploy/docker-compose.yml:139-152`: `network_mode: none`, `cap_drop: [ALL]`, `read_only: true`, `mem_limit: 1g`, `pids_limit: 200`. `Dockerfile.shell-sandbox:20` uses `USER sandbox` (UID 1000).
5. **Network isolation is correctly modeled.** `internal: true` on the `internal` bridge (`docker-compose.yml:14-16`) blocks all egress; only services on the `egress` bridge can reach the internet. Ports publish to `127.0.0.1` only, never `0.0.0.0` (`grep "0.0.0.0"` returns empty).
6. **Telegram allowlist is actually enforced** in the Hermes submodule at `hermes-agent/gateway/platforms/telegram.py:500-504` (reads `TELEGRAM_ALLOWED_USERS` CSV, denies if user_id not in set, accepts `*` wildcard explicitly).
7. **OTel spans never carry message content.** `lib/observability/__init__.py:96-263` reviewed: emits `session.id`, `tool.name`, `model`, `response.length` — never `user_message`, `assistant_response`, `args`, or `conversation_history` (all received as kwargs and discarded).
8. **CODEOWNERS exists and is reasonable.** `.github/CODEOWNERS:8-18` covers security-critical paths even though the global `* @Manzela` already does. Good hygiene.
9. **Branch protection has 11 required checks** (gitleaks, detect-secrets, lint matrix, unit tests, validate-config, validate-compose, conventional-commit, branch-name). `allow_force_pushes:false, allow_deletions:false`.
10. **Pre-commit hooks** (`.pre-commit-config.yaml:1-30`) include trailing-whitespace, end-of-file-fixer, check-added-large-files, detect-private-key, detect-aws-credentials, detect-secrets baseline, ruff. The exclude rules for `tests/unit/test_scrubber.py` are appropriate (fixture file).

---

## Phase-1 → Phase-2 gate recommendation

**Do not promote to Phase 2 until C1, C2, and C3 are resolved.** All three are 1–2 days of work and have no architectural risk. H1 and H2 should be in-flight before Phase 2 deployment to a shared VM; the rest are tracked debt for a Phase 1.x or Phase 2.0 release.

**Estimated total remediation:** 5–8 engineer-days for CRITICAL+HIGH, plus 2–3 days for MEDIUM. The clean areas should not be reworked.
