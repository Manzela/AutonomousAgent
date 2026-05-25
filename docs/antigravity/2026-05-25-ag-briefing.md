# Google Antigravity Agent Briefing — AutonomousAgent SDLC Wave 1+2
**Date:** 2026-05-25
**For:** Google Antigravity IDE (Gemini 3.1 Pro Preview)
**Authority:** Parallel session — you have exclusive write ownership of `deploy/scripts/`, `deploy/docker-compose.canary.yml`, and `audit/2026-05-21-a2a-spike-plan/`. Do NOT write to `lib/a2a/` (Claude Code subagents own that territory).

---

## 1. Project Overview

**AutonomousAgent** is a multi-agent AI system running on a GCE VM in GCP project `autonomous-agent-2026` (recently migrated from the old project `i-for-ai` — all references to `i-for-ai` in active code and docs are bugs). The system runs ~10 Docker containers via Docker Compose:

```
litellm-db        PostgreSQL for LiteLLM spend tracking
litellm-proxy     LiteLLM v1.84.0 — proxies Claude Opus 4.7 via Vertex AI
otel-collector    OpenTelemetry collector → Cloud Trace + Cloud Logging
phoenix           Arize Phoenix observability UI
shell-sandbox     Isolated shell execution sandbox
github-mcp        GitHub MCP server (HTTP mode)
hermes            Main AI agent service (Python FastAPI + plugin system)
escalation-watcher 24h Telegram silence watcher
snapshot-watchdog  GCS snapshot watchdog
budget-watchdog    F21 daily budget watchdog
```

**VM path:** `/opt/hermes/bootstrap/` — this is the working directory for Docker Compose. The compose files reference `./lib`, `./config`, `./scripts`, `./otel`, `./litellm` as bind-mount sources relative to this directory.

**Hermes Docker image:** `us-central1-docker.pkg.dev/autonomous-agent-2026/autonomousagent-images/hermes:latest`

**Bootstrap lifecycle:** On VM first boot, `scripts/vm-bootstrap/install.sh` runs via GCE startup-script-url. It fetches `gs://autonomous-agent-2026-snapshots/bootstrap/hermes-bootstrap.tar.gz`, extracts to `/opt/hermes/bootstrap/`, installs systemd units, and starts services.

---

## 2. Current State — What Has Been Done

| Area | State |
|------|-------|
| GCP project migration i-for-ai → autonomous-agent-2026 | COMPLETE (all PRs merged) |
| Phase 0a cutover | COMPLETE (10 containers running, PASS=8 FAIL=0 DEFER=5) |
| A2A Days 1-3 | COMPLETE (PR #120 merged, 22/22 tests pass) |
| Terraform variable defaults | FIXED this session (model-armor + postgres now default to `autonomous-agent-2026`) |
| Bootstrap tarball | STALE — missing 9 config files that were manually SCP'd in the last session |
| Spike plan docs | STALE — 3 occurrences of `i-for-ai` remain in audit docs |
| A2A Days 4-10 | IN PROGRESS (Claude subagents own lib/a2a/) |

---

## 3. Your Role in the Parallel SDLC

You run **concurrently** with:
- **Gemini CLI agents** (G1–G3): handling GCP API calls (terraform apply, SA provisioning, monitoring setup)
- **Claude subagents** (SA1–SA5): implementing Python code in `lib/a2a/`

**Your directory boundary:**
```
YOURS (exclusive write access):
  deploy/scripts/                     (create rebuild-bootstrap-tarball.sh - note: dir will be implicitly created)
  deploy/docker-compose.canary.yml    (create canary peer — Wave 2 only)
  audit/2026-05-21-a2a-spike-plan/    (fix stale i-for-ai refs)

NOT YOURS (read-only at most):
  lib/a2a/                            → Claude Code subagents
  terraform/                          → Gemini CLI only
  GCP APIs (terraform state, IAM)     → Gemini CLI only
```

---

## 4. Task 1 — Wave 1 (Start Immediately)

### 4.1 Research Phase — Read These Before Writing Anything

1. **`scripts/vm-bootstrap/install.sh`** — understand how the tarball is fetched, extracted, and consumed by the VM. Pay special attention to the working directory and the symlink at `/opt/hermes/secrets`.

2. **`deploy/docker-compose.gcp.override.yml`** — understand every volume mount path. These are all `./`-relative to `/opt/hermes/bootstrap/`. Each path referenced here must exist in the tarball.

3. **`deploy/docker-compose.yml`** — see all service definitions to understand what mount paths the base compose file uses.

4. **`config/` directory listing** — run `find config/ -type f | sort` to see all config files now committed in the repo (these were manually SCP'd in the previous session and must now be in the tarball).

5. **`audit/2026-05-21-a2a-spike-plan/spike-plan.md` lines 82–90 and 160–170** — see the stale `i-for-ai` references you need to fix.

6. **`audit/2026-05-21-a2a-spike-plan/integration-points.md` line ~196** — see the second stale reference.

### 4.2 Guiding Questions to Resolve Before Writing Code

Ask yourself these and verify against the files above before writing the script:

1. **What does `./lib` need to contain?** Check which services in `docker-compose.gcp.override.yml` bind-mount `./lib:/app/lib:ro`. The answer determines whether the full `lib/` directory must be in the tarball.

2. **Does `./scripts` need to be in the tarball?** Check `escalation-watcher`, `snapshot-watchdog`, `budget-watchdog` volume mounts in the override compose. Exclude `scripts/vm-bootstrap/` from `./scripts` in the tarball (those live at the top level of staging as `load-secrets.sh`, `hermes-watchdog.sh`, `systemd/`).

3. **Does `./otel/collector.prod.yaml` exist in the tarball?** Check `otel-collector` in the override compose. Source is `deploy/otel/collector.prod.yaml` in the repo.

4. **Does `./litellm/config.yaml` exist in the tarball?** Check `litellm-proxy` in the override compose. Source is `deploy/litellm/config.yaml`.

5. **Does the tarball need `docker-compose.yml` AND `docker-compose.gcp.override.yml`?** The VM's systemd unit runs: `docker compose -f /opt/hermes/bootstrap/docker-compose.yml -f /opt/hermes/bootstrap/docker-compose.gcp.override.yml up -d`. Both files must be at the root of the tarball.

6. **What is the correct symlink path for secrets?** The install.sh creates `ln -sfn /run/hermes/env /opt/hermes/secrets`. Docker Compose's `env_file` uses paths like `../secrets/*.env` relative to the working directory `/opt/hermes/bootstrap/` — that resolves to `/opt/hermes/secrets/*.env` = `/run/hermes/env/*.env`. Do NOT include `secrets/` in the tarball (SOPS-encrypted secrets are loaded by the `hermes-secrets.service` at runtime, not by the tarball).

7. **Should `docs/conventions/new-repo-template.md` be included?** This file was manually SCP'd to the VM. Check whether any service bind-mounts it. If not, include it under `docs/conventions/` in the tarball anyway — hermes uses it as context.

8. **Should `.git/`, `__pycache__/`, `*.pyc` be excluded?** Yes. Always. Add explicit excludes to the tar command.

9. **Should the script be idempotent?** Yes — if re-run, it should rebuild and re-upload without leaving stale state.

### 4.3 Task 1a — Fix Stale `i-for-ai` References in Spike Plan Docs

**Files to edit:**

`audit/2026-05-21-a2a-spike-plan/spike-plan.md`:
- Line 85: `agent-canary-spike@i-for-ai.iam.gserviceaccount.com` → `agent-canary-spike@autonomous-agent-2026.iam.gserviceaccount.com`
- Line 162: `GCP project \`i-for-ai\` has Cloud Run + IAM Credentials API + Cloud Trace + Cloud Logging APIs enabled...` → replace `i-for-ai` with `autonomous-agent-2026`
- Line 165: `Spike owner has Cloud Trace + Cloud Logging viewer roles on \`i-for-ai\`.` → replace `i-for-ai` with `autonomous-agent-2026`

`audit/2026-05-21-a2a-spike-plan/integration-points.md`:
- Line ~196: `agent-runtime@i-for-ai.iam.gserviceaccount.com` → `agent-runtime@autonomous-agent-2026.iam.gserviceaccount.com`

**Additional files to edit (missed in original runbook):**
- `audit/2026-05-20-model-armor-j1-runbook/terraform/model_armor.tf` (Line 8)
- `audit/2026-05-20-model-armor-j1-runbook/validate.sh` (Lines 2, 5)
- `audit/2026-05-21-phase2-postgres/terraform/secret_manager_db.tf` (Lines 11, 12)
- `terraform/phase-0a-gcp/postgres/main.tf` (Line 195)

**Verification command (must return 0 results):**
```bash
grep -rn "i-for-ai" audit/2026-05-21-a2a-spike-plan/
```

### 4.4 Task 1b — Create `deploy/scripts/rebuild-bootstrap-tarball.sh`

Create the script below, make it executable (`chmod +x`), then execute it from the repo root.

**Script structure:**

```bash
#!/usr/bin/env bash
# deploy/scripts/rebuild-bootstrap-tarball.sh
# Rebuilds the Hermes VM bootstrap tarball from current repo HEAD and uploads
# to gs://autonomous-agent-2026-snapshots/bootstrap/.
#
# Run from repo root: ./deploy/scripts/rebuild-bootstrap-tarball.sh
# Requires: gsutil authenticated, SOPS-free (secrets are runtime-loaded, not in tarball)
# Idempotent: safe to re-run.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STAGING_DIR="$(mktemp -d /tmp/hermes-bootstrap-staging.XXXXXX)"
TARBALL_PATH="$(mktemp /tmp/hermes-bootstrap.XXXXXX.tar.gz)"
GCS_BUCKET="gs://autonomous-agent-2026-snapshots/bootstrap"

trap 'rm -rf "${STAGING_DIR}" "${TARBALL_PATH}"' EXIT

echo "=== rebuilding bootstrap tarball from ${REPO_ROOT} ==="
echo "staging: ${STAGING_DIR}"

# --- 1. Copy runtime directories (bind-mounted by docker-compose.gcp.override.yml) ---
# lib/: bind-mounted as ./lib:/app/lib:ro in hermes, watcher services, litellm-proxy
cp -r "${REPO_ROOT}/lib"        "${STAGING_DIR}/lib"

# config/: bind-mounted as ./config/... in hermes + litellm-proxy + otel-collector
cp -r "${REPO_ROOT}/config"     "${STAGING_DIR}/config"

# scripts/: bind-mounted as ./scripts:/app/scripts:ro in watcher services
# Exclude vm-bootstrap/ — those live at staging root, not in ./scripts
cp -r "${REPO_ROOT}/scripts"    "${STAGING_DIR}/scripts"
rm -rf "${STAGING_DIR}/scripts/vm-bootstrap"

# docs/: hermes uses docs/conventions/ as context (new-repo-template.md)
mkdir -p "${STAGING_DIR}/docs/conventions"
cp "${REPO_ROOT}/docs/conventions/new-repo-template.md" \
   "${STAGING_DIR}/docs/conventions/new-repo-template.md"

# --- 2. Copy deploy artefacts ---
# Compose files must be at staging root (systemd unit runs:
#   docker compose -f .../docker-compose.yml -f .../docker-compose.gcp.override.yml)
cp "${REPO_ROOT}/deploy/docker-compose.yml"             "${STAGING_DIR}/docker-compose.yml"
cp "${REPO_ROOT}/deploy/docker-compose.gcp.override.yml" "${STAGING_DIR}/docker-compose.gcp.override.yml"

# otel/: bind-mounted by otel-collector as ./otel/collector.prod.yaml
mkdir -p "${STAGING_DIR}/otel"
cp "${REPO_ROOT}/deploy/otel/collector.prod.yaml"       "${STAGING_DIR}/otel/collector.prod.yaml"

# litellm/: bind-mounted by litellm-proxy as ./litellm/config.yaml
mkdir -p "${STAGING_DIR}/litellm"
cp "${REPO_ROOT}/deploy/litellm/config.yaml"            "${STAGING_DIR}/litellm/config.yaml"

# --- 3. Copy vm-bootstrap files (installed as system files by install.sh) ---
mkdir -p "${STAGING_DIR}/systemd"
cp "${REPO_ROOT}/scripts/vm-bootstrap/systemd"/*.service "${STAGING_DIR}/systemd/"
cp "${REPO_ROOT}/scripts/vm-bootstrap/load-secrets.sh"   "${STAGING_DIR}/load-secrets.sh"
cp "${REPO_ROOT}/scripts/vm-bootstrap/hermes-watchdog.sh" "${STAGING_DIR}/hermes-watchdog.sh"
cp "${REPO_ROOT}/scripts/vm-bootstrap/expected-containers.txt" "${STAGING_DIR}/expected-containers.txt"
chmod +x "${STAGING_DIR}/load-secrets.sh" "${STAGING_DIR}/hermes-watchdog.sh"

# --- 4. Remove artefacts that must NOT be in the tarball ---
# .git/ leaks history; __pycache__/*.pyc are build artefacts; secrets/ is runtime-only
find "${STAGING_DIR}" -name ".git" -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGING_DIR}" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${STAGING_DIR}" -name "*.pyc" -delete 2>/dev/null || true
find "${STAGING_DIR}" -name ".DS_Store" -delete 2>/dev/null || true
find "${STAGING_DIR}" -name ".venv" -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGING_DIR}/lib" -name "tests" -prune -exec rm -rf {} + 2>/dev/null || true
# Secrets are SOPS-encrypted and loaded at runtime by hermes-secrets.service
# via GCP Secret Manager. Do NOT include secrets/ in the tarball.
rm -rf "${STAGING_DIR}/secrets" 2>/dev/null || true

# --- 5. Build tarball ---
echo "building tarball..."
tar -czf "${TARBALL_PATH}" -C "${STAGING_DIR}" .
ENTRY_COUNT=$(tar -tzf "${TARBALL_PATH}" | wc -l)
TARBALL_SIZE=$(du -h "${TARBALL_PATH}" | cut -f1)
echo "tarball: ${ENTRY_COUNT} entries, ${TARBALL_SIZE}"

# Smoke-check: key files must be present
for f in \
  "./config/hermes/AGENTS.md" \
  "./config/hermes/cli-config.yaml" \
  "./config/toolsets.yaml" \
  "./lib/a2a/server.py" \
  "./docker-compose.gcp.override.yml" \
  "./systemd/docker-compose-hermes.service" \
  "./otel/collector.prod.yaml" \
  "./litellm/config.yaml"; do
  if ! tar -tzf "${TARBALL_PATH}" | grep -q "^${f}$"; then
    echo "ERROR: missing from tarball: ${f}"
    exit 1
  fi
done
echo "smoke check: all required files present"

# --- 6. Upload to GCS ---
echo "uploading tarball..."
gsutil cp "${TARBALL_PATH}" "${GCS_BUCKET}/hermes-bootstrap.tar.gz"
echo "uploading install.sh..."
gsutil cp "${REPO_ROOT}/scripts/vm-bootstrap/install.sh" "${GCS_BUCKET}/install.sh"

# --- 7. Verify upload ---
echo "verifying GCS upload..."
gsutil ls -l "${GCS_BUCKET}/hermes-bootstrap.tar.gz"
gsutil ls -l "${GCS_BUCKET}/install.sh"

echo "=== bootstrap tarball rebuild complete ==="
echo "  tarball: ${ENTRY_COUNT} entries, ${TARBALL_SIZE}"
echo "  GCS:     ${GCS_BUCKET}/hermes-bootstrap.tar.gz"
```

**After executing the script, verify:**
```bash
# 1. GCS upload succeeded
gsutil ls -l gs://autonomous-agent-2026-snapshots/bootstrap/

# 2. config/hermes/AGENTS.md is in the tarball
gsutil cat gs://autonomous-agent-2026-snapshots/bootstrap/hermes-bootstrap.tar.gz \
  | tar -tzf - | grep "AGENTS.md"
```

Both must succeed before raising the PR.

### 4.5 Branch, Commit, and PR

```bash
# Branch
git checkout -b fix/bootstrap-tarball-rebuild

# Stage only the files you changed
git add deploy/scripts/rebuild-bootstrap-tarball.sh \
        audit/2026-05-21-a2a-spike-plan/spike-plan.md \
        audit/2026-05-21-a2a-spike-plan/integration-points.md

# Commit (conventional format — lowercase subject after colon+space)
git commit -m "fix(infra): rebuild bootstrap tarball + fix stale i-for-ai spike-plan refs"

# Push and open PR
git push -u origin fix/bootstrap-tarball-rebuild
gh pr create \
  --title "fix(infra): rebuild bootstrap tarball + fix stale i-for-ai spike-plan refs" \
  --body "$(cat <<'EOF'
## Summary
- Creates \`deploy/scripts/rebuild-bootstrap-tarball.sh\` — idempotent script that
  packs all VM bind-mount directories from repo HEAD and uploads to GCS
- Executes the script: uploads \`hermes-bootstrap.tar.gz\` + \`install.sh\` to
  \`gs://autonomous-agent-2026-snapshots/bootstrap/\`
- Fixes 3 stale \`i-for-ai\` references in \`audit/2026-05-21-a2a-spike-plan/spike-plan.md\`
- Fixes 1 stale reference in \`audit/2026-05-21-a2a-spike-plan/integration-points.md\`

## Verification
- \`grep -rn "i-for-ai" audit/2026-05-21-a2a-spike-plan/\` → 0 results
- \`gsutil ls gs://autonomous-agent-2026-snapshots/bootstrap/hermes-bootstrap.tar.gz\` → success
- Tarball contains \`./config/hermes/AGENTS.md\`

## Collision analysis
Changes are limited to \`deploy/scripts/\` and \`audit/\` — no overlap with
Claude subagents (lib/a2a/) or Gemini CLI (terraform/, GCP APIs).

🤖 Generated with Google Antigravity (Gemini 3.1 Pro Preview)
EOF
)"
```

---

## 5. Task 2 — Wave 2 (Start After Task 1 PR Merged)

**Wait for signal:** Do not start Task 2 until the Wave 1 Claude PRs (`feat/a2a-day5-auth`, `feat/a2a-day7-bridge`, `feat/a2a-day6-otel`) have merged into main. You can check with `gh pr list --state merged --limit 10`.

### 5.1 Research Phase

1. **`deploy/docker-compose.yml`** — read the `hermes` service definition fully: ports, volumes, environment, networks. The canary compose must mirror this service with surgical differences.

2. **`deploy/docker-compose.gcp.override.yml`** — understand the `*ar-hermes` anchor (`us-central1-docker.pkg.dev/autonomous-agent-2026/autonomousagent-images/hermes:latest`).

3. **`audit/2026-05-21-a2a-spike-plan/spike-plan.md §Day 9`** — read the Day 9 description for the canary peer requirements. Key quote: *"spin up a second compose stack (`deploy/docker-compose.canary.yml`) with a stub Hermes shaped exactly like ours but with a hard-coded 'echo + delay' behavior"*. If the second Hermes container is too heavy, a minimal Python FastAPI app implementing only the needed methods is acceptable (time-box to 2h).

4. **`lib/a2a/server.py`** — read the server to understand which JSON-RPC methods the canary needs to serve (at minimum: `message/send`, `message/stream`, `tasks/get`, `tasks/subscribe`, `tasks/cancel`).

### 5.2 Guiding Questions

1. **What port does the canary expose?** Port 9002 (9001 is used by the main hermes service). Map it as `9002:9001` so the canary container internally listens on 9001 but is reachable on the host at 9002.

2. **Does the canary need the full Hermes image?** The spike plan says "stub Hermes shaped exactly like ours." Use `hermes:latest` with an env var `HERMES_A2A_CANARY_MODE=true` to trigger echo+delay behavior. If this env var isn't implemented by the time you create the compose file, document it as a TODO and use a placeholder stub container (`python:3.12-slim` with a minimal FastAPI entrypoint).

3. **Does the canary share volumes with the main stack?** No. The canary is a fully isolated peer. It should have its own config volume pointing to a `config/canary/` directory (create it with minimal placeholder files) or no config volume at all for a stub.

4. **Which network does the canary join?** The `deploy_internal` network (created by `deploy/docker-compose.yml`) so the main hermes can reach the canary at `http://agent-canary:9001/`.

5. **Does the canary need the GCP logging driver?** For local dev testing, no. Add a comment noting that on the VM you'd add `logging: *gcplogs`.

### 5.3 Create `deploy/docker-compose.canary.yml`

```yaml
# deploy/docker-compose.canary.yml
# A2A Day 9 — stub canary peer for end-to-end A2A testing.
#
# Usage (local):
#   docker compose -f deploy/docker-compose.yml up -d
#   docker compose -f deploy/docker-compose.canary.yml up -d
#
# The canary joins the deploy_internal network created by docker-compose.yml,
# making it reachable from the main hermes container at http://agent-canary:9001/.
#
# Host port 9002 maps to container port 9001 to avoid collision with the
# main hermes service on 9001.

x-ar-hermes: &ar-hermes us-central1-docker.pkg.dev/autonomous-agent-2026/autonomousagent-images/hermes:latest

services:
  agent-canary:
    image: *ar-hermes
    container_name: agent-canary
    ports:
      - "9002:9001"
    environment:
      HERMES_A2A_CANARY_MODE: "true"
      HERMES_A2A_PORT: "9001"
      HERMES_LOG_LEVEL: "DEBUG"
    networks:
      - internal
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9001/health')"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

networks:
  internal:
    external: true
    name: deploy_internal
```

**Acceptance test:**
```bash
docker compose -f deploy/docker-compose.canary.yml config
# → must produce valid compose config with no errors

docker compose -f deploy/docker-compose.canary.yml ps --format json 2>/dev/null | head -5
# → service agent-canary should appear
```

### 5.4 Branch, Commit, and PR

```bash
git checkout -b feat/canary-peer-compose
git add deploy/docker-compose.canary.yml
git commit -m "feat(deploy): canary peer compose stack — A2A Day 9 stub"
git push -u origin feat/canary-peer-compose
gh pr create \
  --title "feat(deploy): canary peer compose stack — A2A Day 9 stub" \
  --body "$(cat <<'EOF'
## Summary
- Creates \`deploy/docker-compose.canary.yml\` — isolated canary peer for A2A Day 9 e2e testing
- Canary joins \`deploy_internal\` network; reachable at \`http://agent-canary:9001/\` from main hermes
- Exposed on host port 9002 (9001 reserved for main hermes)
- \`HERMES_A2A_CANARY_MODE=true\` signals echo+delay behavior (Day 10 implementation)

## Verification
- \`docker compose -f deploy/docker-compose.canary.yml config\` → valid, no errors
- Service name \`agent-canary\`, port 9002:9001

## Collision analysis
New file in \`deploy/\` — no overlap with Claude subagents (lib/a2a/) or Gemini CLI (terraform/).
Used by SA5 (Claude Wave 3) as the live peer for e2e A2A testing.

🤖 Generated with Google Antigravity (Gemini 3.1 Pro Preview)
EOF
)"
```

---

## 6. Security and Operational Constraints

**These are non-negotiable — violating them will cause CI or security failures:**

| Constraint | Detail |
|-----------|--------|
| Never commit plaintext secrets | `detect-secrets` + `gitleaks` run in CI. Any string matching a secret pattern fails CI immediately. |
| `secrets/` directory is SOPS-encrypted | Never copy, read, or include any file from `secrets/` in the tarball or in code. |
| Never use `git add -A` or `git add .` | Stage specific files by name only. |
| Never use `--no-verify` on commits | Pre-commit hooks run secret detection. If a hook fails, fix the underlying issue. |
| Never force-push | Not to any branch, ever. |
| Branch name format | `feat/<desc>` or `fix/<desc>` — NO dots in `<desc>`, no capital letters in type. |
| PR title format | `type(scope): lowercase subject` — the letter after `: ` must be lowercase. |
| GCP project | Always `autonomous-agent-2026`. Never create anything in `i-for-ai` for AutonomousAgent. |
| Do not delete `i-for-ai` project | The project itself is shared infrastructure and must remain. Only AutonomousAgent resources moved. |

---

## 7. Repository Conventions

```
Squash-only merges to main
GitHub operations: gh CLI (authenticated as Manzela)
Python package management: uv (not pip directly)
  Install A2A deps: uv sync --extra a2a --extra dev
  Run tests: uv run pytest lib/a2a/tests/ -v
  Run linter: uv run ruff check lib/a2a/
  Run formatter: uv run ruff format lib/a2a/
Terraform: >= 1.7.0, providers google ~> 5.30
GCS paths: gs://autonomous-agent-2026-snapshots/bootstrap/
AR image registry: us-central1-docker.pkg.dev/autonomous-agent-2026/autonomousagent-images/
```

---

## 8. Expected Outcomes and Acceptance Criteria

### Task 1 (Wave 1) — Done When:

```bash
# All of these must pass:

# 1. No stale i-for-ai refs in spike plan docs
grep -rn "i-for-ai" audit/2026-05-21-a2a-spike-plan/
# → (empty output, exit code 1 is expected from grep — that means 0 matches found)

# 2. Tarball on GCS
gsutil ls -l gs://autonomous-agent-2026-snapshots/bootstrap/hermes-bootstrap.tar.gz
# → shows file with size > 50KB (previous was ~94KB)

# 3. install.sh on GCS
gsutil ls -l gs://autonomous-agent-2026-snapshots/bootstrap/install.sh

# 4. Key file in tarball
gsutil cat gs://autonomous-agent-2026-snapshots/bootstrap/hermes-bootstrap.tar.gz \
  | tar -tzf - | grep "config/hermes/AGENTS.md"
# → ./config/hermes/AGENTS.md

# 5. Script exists and is executable
test -x deploy/scripts/rebuild-bootstrap-tarball.sh && echo "OK"

# 6. PR is open and CI is green
gh pr checks --watch
```

### Task 2 (Wave 2) — Done When:

```bash
# 1. Compose file validates
docker compose -f deploy/docker-compose.canary.yml config > /dev/null && echo "valid"

# 2. Service is named correctly
docker compose -f deploy/docker-compose.canary.yml config | grep "agent-canary"

# 3. Port mapping is correct
docker compose -f deploy/docker-compose.canary.yml config | grep "9002"

# 4. PR is open and CI is green
gh pr checks --watch
```

---

## 9. What NOT To Do

| What | Why |
|------|-----|
| Do NOT touch `lib/a2a/` | Claude subagents SA1–SA5 own this directory. Writing there will cause merge conflicts and likely corrupt their work. |
| Do NOT touch `terraform/` | Gemini CLI agents G1–G3 own this. Your changes to Terraform HCL will conflict with their `terraform apply` and potentially corrupt GCP state. |
| Do NOT run `terraform apply` | Only Gemini CLI does this. You don't have the right state lock. |
| Do NOT touch `deploy/docker-compose.yml` | This is the production compose file, not yours to change. The canary peer goes in a NEW file. |
| Do NOT touch `secrets/` | SOPS-encrypted, do not read or copy. |
| Do NOT include `config/hermes/MEMORY.md` sensitive content in PR description | This file is included in the tarball but its contents are Hermes agent memory — don't quote it publicly. |

---

## 10. Context Files to Read (In Order)

For maximum effectiveness, read these files from the repo before starting any work:

1. `scripts/vm-bootstrap/install.sh` — how the tarball is consumed
2. `deploy/docker-compose.gcp.override.yml` — what paths must be in the tarball
3. `deploy/docker-compose.yml` — full service list and base config
4. `config/` — run `find config/ -type f | sort` to see all 10 config files
5. `audit/2026-05-21-a2a-spike-plan/spike-plan.md` — full spike plan context
6. `audit/2026-05-21-a2a-spike-plan/auth-design.md` — auth design for Day 5 (context only)
7. `docs/superpowers/specs/2026-05-25-parallel-sdlc-delegation-design.md` — the master design doc for this SDLC wave
