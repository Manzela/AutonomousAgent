# Claude + Gemini GCP Orchestration Design

**Date:** 2026-05-20
**Status:** Approved (design); pending implementation plan
**Brainstorm session:** 2026-05-20 (continuation from Phase 0a GCP migration planning)
**Scope:** Defines the Claude Code ↔ Gemini CLI collaboration protocol for completing Phase 0a (Tasks 16–38) and serving as the orchestration substrate for future phases.

---

## 1. Architecture & roles

Three-tier model: Claude orchestrates, Gemini executes GCP-native operations, GCP holds state.

```
┌──────────────────────────────────────────────────────────────────┐
│                       CLAUDE (orchestrator)                       │
│  - Reads repo/spec, authors HCL + bash + systemd units            │
│  - Constructs Gemini prompts with explicit "prefer MCP" directive │
│  - Reviews plan.json (terraform show -json tfplan output)         │
│  - Decides apply / re-plan / escalate                             │
│  - Maintains audit trail in audit/gemini-<task-id>-<step>.json    │
└────────────────────────┬─────────────────────────────────────────┘
                         │ scoped prompt + --output-format json
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                       GEMINI (executor)                           │
│  - Runs: terraform init/plan/apply, gcloud, MCP tool calls        │
│  - Returns: JSON envelope via --output-format json | jq pipeline  │
│  - Holds: GCP-native MCP (cloudrun, bigquery, spanner, ...)       │
│  - Constraint: --yolo + maxActionsPerTask=100 (default cap)       │
└────────────────────────┬─────────────────────────────────────────┘
                         │ touches
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                       GCP (i-for-ai)                              │
│  - GCS state bucket (locked) + Secret Manager + Artifact Registry │
│  - VM + disks + WIF (locked to repo + ref) + alert policies       │
└──────────────────────────────────────────────────────────────────┘
```

**Authentication:** Gemini CLI uses `selectedType: "vertex-ai"` routing through project `i-for-ai` (confirmed in `~/.gemini/settings.json`). Not subject to the 2026-06-18 Google AI Pro/Ultra sunset.

**Three locked decisions:**
1. **Hybrid by task type** — GCP API ops → Gemini end-to-end; Terraform → Claude authors HCL, Gemini applies; Local files → Claude only
2. **Plan-then-apply gate** — Gemini runs plan, Claude reviews `show -json` output, Claude authorizes separate apply call with bound plan file
3. **Independent same-shape fan-out, cap 5** — gated on OAuth parallel probe test passing

**Ownership table:**

| Category | Author | Executor | Pattern |
|----------|--------|----------|---------|
| Local files (HCL, bash, systemd, yml) | Claude | Claude | Direct Edit/Write |
| `terraform fmt`, `validate`, `version` | Claude | Claude | Local read-only |
| `terraform init`, `plan`, `apply` | Claude (HCL) | Gemini | Plan-then-apply gate |
| `gcloud` state-mutating ops | Claude (prompt) | Gemini | Scoped prompt per op |
| `gcloud` read-only (list/describe) | Either | Either/Gemini | Direct MCP or fan-out |
| Secret migrate: SOPS decrypt | Claude | Claude | Local `sops -d` |
| Secret migrate: SM write | Claude (prompt) | Gemini | Stdin-pipe, `--data-file=-` |
| `gh` CLI (PRs, issues) | Claude | Claude | Not delegated |
| **Destructive primitives** (rm, drop, delete, --force, kill, disable) | **Never autonomous** | **Never autonomous** | Plan-bound apply OR explicit user confirmation |
| Executor prompt construction | Claude | — | Allowlist + blocklist in every prompt preamble |

**Destructive primitive rule** (Anthropic production incident basis): any prompt containing `rm -rf`, `--force`, `--auto-approve`, `delete`, `destroy`, `drop`, `kill`, `disable` must be Claude-reviewed and user-confirmed before Gemini receives it. Exception: `terraform apply tfplan` where `tfplan` is the Claude-reviewed bound plan file.

**Open pre-execution items:**
1. Run 5-concurrent OAuth parallel probe test before first fan-out batch
2. WIF `attribute_condition` patch is required before VM apply (see Section 7 — security gaps)

---

## 2. Components

### 2.1 Task categorization rules

| Signal | Owner | Reason |
|--------|-------|--------|
| Write local file (HCL, bash, systemd, yml) | Claude only | File system is Claude's environment |
| `terraform fmt`, `validate`, `version` | Claude | No GCP state touched |
| `terraform init`, `plan`, `apply` | Gemini | GCP credential isolation |
| `gcloud` state-mutating (create/delete/update/patch) | Gemini | Credential isolation |
| `gcloud` read-only (list/describe/inspect) | Claude MCP direct OR Gemini fan-out | MCP direct for single calls; fan-out for audits |
| Secret migrate: SOPS decrypt | Claude (local sops binary) | Keeps plaintext only in memory |
| Secret migrate: SM write | Gemini (`--data-file=-` stdin) | Never via positional arg; never logged |
| `gh` CLI (PRs, issues, releases) | Claude | Not GCP; no delegation needed |
| Any prompt with destructive primitives | Never autonomous | Must be Claude-reviewed + user-confirmed |

### 2.2 Plan-apply gate — canonical 4-step sequence

For every state-modifying Terraform operation:

**Prompt preamble (embedded in every Gemini prompt):**
```
[PREAMBLE: prefer MCP tools over gcloud CLI.
 FORBIDDEN in this invocation: rm -rf, --force, --auto-approve (unless this is the authorized apply),
 delete, destroy, drop, kill, disable.
 Output format: JSON only.]
```

**Step 1 — Plan (Gemini):**
```bash
GEMINI_CLI_TRUST_WORKSPACE=true GOOGLE_CLOUD_PROJECT=i-for-ai GOOGLE_CLOUD_LOCATION=global \
  gemini --yolo --output-format json \
  -p "[PREAMBLE]
      TASK: cd terraform/phase-0a-gcp && \
      terraform plan -out=tfplan -lock-timeout=300s 2>&1 && \
      terraform show -json tfplan" \
  | jq -r '.response' > /tmp/audit/gemini-task-N-plan.json
```

**Step 2 — Review (Claude):**
- Read plan.json from step 1
- Check `resource_changes[].change.actions` — any `delete` or `replace` → surface to user
- Go / re-plan / L3-escalate decision by Claude

**Step 3 — Apply (Gemini, plan-bound):**
```bash
gemini --yolo --output-format json \
  -p "[PREAMBLE — exception: terraform apply tfplan is authorized]
      TASK: cd terraform/phase-0a-gcp && terraform apply tfplan 2>&1; echo EXIT_CODE:$?
      CRITICAL: apply the exact saved plan file. Never run terraform apply without tfplan argument." \
  | jq -r '.response' > /tmp/audit/gemini-task-N-apply.json
```

**Step 4 — Verify (Gemini):**
```bash
gemini --yolo --output-format json \
  -p "[PREAMBLE]
      TASK: cd terraform/phase-0a-gcp && \
      terraform plan -detailed-exitcode -lock-timeout=60s 2>&1; echo EXIT_CODE:$?
      Expected: exit 0 = no changes. Exit 2 = drift = escalate." \
  | jq -r '.response' > /tmp/audit/gemini-task-N-verify.json
```

GCS state lock: acquired in step 1, held through step 3, released after step 4. Non-zero exit auto-releases the lock. Stale lock recovery: `terraform force-unlock <lock-id>` — always user-confirmed, never automated.

### 2.3 Structured output envelope

Claude wraps and persists every Gemini invocation result:

```json
{
  "task_id": "phase-0a-task-16-vm-plan",
  "step": "plan | apply | verify | gcp-api",
  "status": "success | retry | escalate",
  "output": "<parsed Gemini --output-format json response>",
  "audit_trail": ["terraform.plan", "terraform.show_json", "duration_ms: 4231"],
  "sources": ["terraform/phase-0a-gcp/compute.tf:L49"],
  "timestamp": "2026-05-20T14:00:00Z",
  "agent": "gemini-executor",
  "retry_count": 0
}
```

Persisted to: `audit/gemini-<task-id>-<step>.json`. This is the paper trail for L3 escalations and post-mortems.

### 2.4 Fan-out protocol

For read-only same-shape batch operations (inventory, multi-region audit, smoke checks):

```bash
# Eligibility checklist enforced by Claude before spawning:
# [ ] OAuth probe test has passed (run once before first fan-out)
# [ ] All N prompts are read-only (list/describe/inspect only)
# [ ] No two prompts target the same GCS state path
# [ ] N <= 5 concurrent
# [ ] All prompts include PREAMBLE + FORBIDDEN block

PIDS=()
for i in 1 2 3 4 5; do
  GEMINI_CLI_TRUST_WORKSPACE=true GOOGLE_CLOUD_PROJECT=i-for-ai GOOGLE_CLOUD_LOCATION=global \
    gemini --yolo --output-format json \
    -p "[PREAMBLE] TASK: $(cat /tmp/prompts/task-${i}.txt)" \
    > "/tmp/results/task-${i}.json" 2>&1 &
  PIDS+=($!)
done
wait "${PIDS[@]}"
# Claude reads each result; if any status=escalate, pause all and escalate
```

---

## 3. Data flow

### 3.1 Terraform gate flow

```
Claude                                    Gemini                       GCP (i-for-ai)
  |                                          |                              |
  |-- reads spec/plan → authors HCL         |                              |
  |-- writes terraform/phase-0a-gcp/*.tf    |                              |
  |                                          |                              |
  |-- constructs scoped plan prompt -------->|                              |
  |                                          |-- terraform plan -out=tfplan |
  |                                          |   write lock acquired ------->|
  |                                          |-- terraform show -json tfplan |
  |<-- plan.json in structured envelope -----|                              |
  |                                          |                              |
  |-- reads resource_changes[].actions       |                              |
  |   any delete/replace? → surface to user  |                              |
  |   go/no-go decision                      |                              |
  |                                          |                              |
  |-- constructs scoped apply prompt ------->|                              |
  |                                          |-- terraform apply tfplan ---->|
  |                                          |   (plan-bound, no re-plan)   |
  |                                          |   write lock released ------->|
  |<-- apply stdout in envelope -------------|                              |
  |                                          |                              |
  |-- constructs verify prompt ------------->|                              |
  |                                          |-- terraform plan -detailed-exitcode
  |<-- exit_code: 0 (clean) or 2 (drift) ---|                              |
  |                                          |                              |
  |-- writes audit/gemini-<task>-{plan,apply,verify}.json                  |
```

State that stays in Claude: structured envelopes, audit JSONs, plan.json review decision.
State that stays in Gemini: `tfplan` binary, GCP credentials, active state lock.
State that touches GCP: resource mutations, `.tflock` object, `.tfstate` object.

### 3.2 Secret migration flow (Tasks 24-25)

```
Claude (local sops)                      Gemini                   Secret Manager
  |                                          |                           |
  |-- sops -d secrets/<name>.env.sops        |                           |
  |   plaintext only in shell var            |                           |
  |                                          |                           |
  |-- constructs stdin-pipe prompt --------->|                           |
  |                                          |-- echo -n "$VAL" | gcloud |
  |                                          |   secrets versions add    |
  |                                          |   autonomousagent-<name>  |
  |                                          |   --data-file=- ---------->|
  |<-- version_id confirmed in envelope -----|                           |
  |                                          |                           |
  |-- hash check: sha256(sops) vs SM version |                           |
  |   match = idempotent skip                |                           |
  |-- writes audit/gemini-secret-<name>.json |                           |
  |-- repeat for each of 5 required env files|                           |
```

Plaintext never written to disk, never in positional args (`echo -n` prevents trailing newline in `--data-file=-`). SOPS is source of truth; SM is the runtime copy.

---

## 4. Phase 0a playbook

Per-task labels for all remaining tasks. **Owner** = author + executor. **Gate** = whether plan-then-apply gate applies. **Fan-out** = eligible for parallel execution.

| Task | Description | Author | Executor | Gate | Fan-out |
|------|-------------|--------|----------|------|---------|
| **16** | GCE VM resource (`compute.tf` addition) | Claude (HCL) | Gemini (plan) — **apply HOLD** | Plan-only; apply requires explicit user "apply now" | No |
| **17** | `install.sh` master bootstrap | Claude | Claude | Local file only | No |
| **18** | `load-secrets.sh` (SM → /run/hermes/env/) | Claude | Claude | Local file only | No |
| **19** | `hermes-secrets.service` systemd unit | Claude | Claude | Local file only | No |
| **20** | `docker-compose-hermes.service` systemd unit | Claude | Claude | Local file only | No |
| **21** | `hermes-watchdog.sh` + `expected-containers.txt` | Claude | Claude | Local file only | No |
| **22** | `hermes-watchdog.service` systemd unit | Claude | Claude | Local file only | No |
| **23** | `docker-compose.gcp.override.yml` (gcplogs driver + bind mount) | Claude | Claude | Local file only | No |
| **WIF patch** | Add `repository_owner` + `ref` to `attribute_condition` in `wif.tf` | Claude (HCL) | Gemini (apply) | Full gate — REQUIRED before Task 29 | No |
| **SM patch** | Add `hermes-provider` to `sops_env_files` in `secret_manager.tf` | Claude (HCL) | Gemini (apply) | Full gate — REQUIRED before Tasks 24-25 | No |
| **24–25** | SOPS → Secret Manager migration (5 required env files: chroma-cloud, hermes-provider, honcho, litellm-db, telegram) | Claude (sops decrypt) | Gemini (SM write) | Stdin-pipe per secret; idempotent hash check | Sequential |
| **26** | Upload bootstrap scripts to GCS; inline startup-script metadata on VM | Claude (Terraform HCL) | Gemini (apply) | Full gate | No |
| **27** | Cloud Logging sink + log-based alert policies | Claude (Terraform HCL) | Gemini (apply) | Full gate | No |
| **28** | Budget alert (`google_billing_budget`) | Claude (Terraform HCL) | Gemini (apply) | Full gate | No |
| **29** | VM create: apply Task 16 plan | — | Gemini (apply only — plan already saved) | Apply = gate step 3 (plan already reviewed) | No |
| **30** | docker-compose up on VM (SSH → `systemctl start`) | Claude (constructs command) | Gemini (`gcloud compute ssh`) | GCP API op via MCP | No |
| **31** | Smoke tests: `/health` endpoints, container count | Claude (orchestrates) | Gemini (MCP checks) | Read-only audit | Yes (parallel) |
| **32** | Chaos test: kill container + watchdog recovery | Claude (orchestrates) | Gemini (triggers, monitors) | Staged; user confirms each destructive step | No |
| **33** | Cutover: Telegram webhook swap laptop → GCP | Claude (orchestrates) | Gemini (gcloud updates) | Full gate; user confirms webhook swap | No |
| **34** | 72h stability watch + alert policy verification | Claude (orchestrates) | Gemini (metric reads) | Read-only | Yes (parallel metric checks) |
| **35–38** | Rollback plan (documented) + post-cutover cleanup | Claude authors runbook | Gemini (if triggered) | Full gate; rollback is destructive | No |

**Apply-HOLD tasks** (require explicit user "apply now"): Task 16/29 (VM create), Task 33 (webhook cutover), Tasks 35-38 (rollback).

---

## 5. Error handling + escalation ladder

| Tier | Trigger | Action | Who |
|------|---------|--------|-----|
| **L0 Retry** | Transient: network timeout, 429 rate-limit, `lock already held` | Re-dispatch same prompt; add `-lock-timeout=300s`; max 2 retries; increment `retry_count` | Claude (automatic) |
| **L1 Reflexion** | Malformed JSON, wrong tool selected, missing expected fields | Claude prepends previous output to new prompt: "Your previous response had problem [X]. Retry and fix." Max 1 reflexion pass per step | Claude (automatic) |
| **L2 Fallback** | Gemini stuck after L1 (`status=escalate` from executor) or MCP tool unavailable | Claude takes over: uses native `cloudrun`/`bigquery` MCP directly or runs `gcloud` via Bash; writes to same audit envelope | Claude (automatic) |
| **L3 Human** | State-mutating op fails after L2; plan contains `delete`/`replace` on named resources; GCS state serial mismatch; cost anomaly; any rollback trigger | Surface full audit trail to user with recommended next action; do not auto-proceed | User decides |

**Specific triggers:**

| Error | Tier | Notes |
|-------|------|-------|
| `terraform plan` exits non-zero (provider error, missing var) | L0 then L1 | Check if auth expired; retry with explicit init |
| `terraform apply tfplan`: "state serial mismatch" | L3 | Plan is stale; re-plan from scratch, user-confirmed apply |
| `terraform apply tfplan`: resource API error (403, quota) | L1 then L3 | Reflexion rarely helps for quota; escalate fast |
| `terraform plan -detailed-exitcode` returns 2 (drift post-apply) | L3 | Never auto-remediate drift |
| Gemini envelope `status: escalate` | L2 | Claude takes over that specific step |
| SOPS decrypt fails | L3 | Key management issue; never auto-retry |
| SM write: permission denied | L1 (check IAM) then L3 | |
| OAuth token expired mid-fan-out | L0 (wait 2min, retry single) then L3 | Flag for probe test re-run |
| `delete`/`replace` in plan `resource_changes` | L3 | Always; even if expected, user confirms |

**Invariants — never auto-resolved:**
- State serial mismatch → always re-plan + user-confirmed apply
- Disk/VM delete → user confirmation required
- Fan-out with any `status=escalate` → pause all remaining jobs, escalate all

---

## 6. Testing + acceptance

### 6.1 Pre-flight checks (before any apply)

| Check | Who | Pass criteria |
|-------|-----|---------------|
| WIF probe: `gcloud auth print-identity-token` | Gemini | Exit 0, token returned |
| OAuth parallel probe: 5 concurrent Gemini invocations | Claude orchestrates | All 5 complete without `401`/`token_expired` in stderr |
| Terraform validate | Claude | Exit 0 |
| Terraform plan dry-run (no `-out`) | Gemini | Exit 0, `0 to destroy` |
| WIF attribute_condition covers `repository_owner` + `repository` + `ref` | Claude (reads wif.tf after patch) | All three conditions present |

### 6.2 Smoke tests (Task 31 — fan-out eligible)

```bash
# All read-only; parallel fan-out:
- [ ] gcloud compute instances describe autonomousagent-vm → STATUS=RUNNING
- [ ] SSH: sudo systemctl is-active docker-compose-hermes → active
- [ ] SSH: docker compose ps --format json | jq '[.[] | select(.State=="running")] | length' → 10
- [ ] SSH: curl -s http://localhost:8080/health → 200
- [ ] SSH: curl -s http://localhost:4000/health → 200 (litellm-proxy)
- [ ] gcloud compute disks describe autonomousagent-vm-data → READY
- [ ] gcloud secrets versions access latest --secret=autonomousagent-telegram → non-empty
```

### 6.3 Chaos test (Task 32 — sequential, user-confirmed per destructive step)

```bash
1. Take manual snapshot: gcloud compute disks snapshot autonomousagent-vm-data
   → user confirms snapshot visible before proceeding

2. SSH: sudo systemctl stop docker-compose-hermes
   → watchdog fires within 60s: systemctl is-active docker-compose-hermes = active
   → verify: docker compose ps → all 10 containers RUNNING

3. SSH: sudo kill -9 $(docker ps -q | head -1)
   → watchdog fires within 60s, re-raises dead container

4. SSH: sudo reboot
   → wait 3min, SSH back; verify all 10 containers running,
     /opt/hermes/data mount present, secrets loaded
```

### 6.4 Acceptance criteria (verbatim from spec Section 11)

| AC | Criterion | Verification method |
|----|-----------|---------------------|
| AC-1 | Pre-flight blocker closed: hermes survives 24h idle locally without exit 137 | `docker ps` shows hermes present for 24h; Phoenix tracing uninterrupted |
| AC-2 | 10 long-running containers present for 72 consecutive hours | `docker compose ps` every 30min; `volume-init` exempt (one-shot) |
| AC-3 | `litellm-proxy /health` returns 200 for 99%+ over 7-day window | Cloud Monitoring uptime check |
| AC-4 | Watchdog shows zero restarts under steady state AND auto-recovery on kill | `hermes-watchdog.log` + chaos test steps 2-3 |
| AC-5 | Daily PD snapshot for 7 consecutive days | `gcloud compute snapshots list --filter=labels.disk=autonomousagent-data` → 7 entries |
| AC-6 | Snapshot recovery test: provision new VM from latest PD snapshot, verify state continuity | Timed drill; hermes resumes from checkpoint, no data loss in hermes-data |
| AC-7 | CI end-to-end (merge → build → push → deploy → smoke) <10min | GitHub Actions run log, wall-clock |
| AC-8 | WIF works; zero JSON key files in repo or GitHub Actions secrets | `gcloud iam service-accounts keys list` → 0 user-managed keys |
| AC-9 | All secrets from Secret Manager; SOPS files retained in repo | `/run/hermes/env/*.env` populated; SOPS files NOT present on VM |
| AC-10 | Cost within ±20% of $125/mo after one billing cycle | Billing budget alert not triggered at month end |

---

## 7. Trade-offs + non-goals

### Trade-offs accepted

| Decision | Cost | Benefit |
|----------|------|---------|
| Two-agent system | More moving parts; OAuth probe required; envelope overhead | GCP-native MCP access; credential isolation; parallel fan-out |
| Plan-then-apply gate adds ~60s latency per Terraform task | Slower than `-auto-approve` | Eliminates "deleted prod DB in 9 seconds" failure class (Anthropic production incident) |
| Fan-out capped at 5, same-shape only | Limits parallelism for complex mixed operations | GCS lock serializes state-modifying ops anyway; keeps audit trail manageable |
| `--output-format json | jq` pipeline | Slightly more complex invocation | Reliable structured output; parseable without brittle string scraping |
| Gemini CLI over direct API | CLI startup overhead per invocation | Antigravity skills, 1M-token context, MCP server access bundled |

### Non-goals

- Not multi-cloud (GCP-only)
- Not multi-developer (no Atlantis-style PR comment workflow)
- Not Terraform Cloud / Spacelift (unnecessary overhead for one developer on one project)
- Not Sentinel / OPA policy-as-code (`resource_changes` review in Claude is the policy gate)
- Not Kubernetes (docker-compose on single VM; GKE is a future phase)
- Not zero-touch CI (human approval required for apply, webhook swap, rollback — always)

### Security gaps — required fixes before execution

| Gap | Location | Fix |
|-----|----------|-----|
| WIF `attribute_condition` covers only `attribute.repository`; missing `repository_owner` and `ref` | `wif.tf:45` | Add all three conditions (see Section 4 WIF patch row) |
| `hermes-provider.env` not in `secret_manager.tf` `sops_env_files`; SM resource not created | `secret_manager.tf:26` | Add `"hermes-provider"` to the list before Tasks 24-25 run |

### Known operational risks

| Risk | Mitigation |
|------|------------|
| OAuth token expiry mid-operation (~60min) | Pre-flight probe test; L0 retry with 2-min wait; L3 escalation on second failure |
| GCS state lock stale (process killed mid-apply) | `terraform force-unlock <lock-id>` — documented recovery, always user-confirmed |
| Bootstrap script in GCS has drift from code | Lock bucket ACL to runtime SA read-only; audit access logs; move to inline metadata post-stabilization |
| Gemini CLI internal context budget (50k reverse budget) | Cap task prompt size; prefer structured concise prompts over large context dumps |

---

## 8. References

- Spec: `docs/superpowers/specs/2026-05-20-phase-0a-gcp-always-online-design.md` (Phase 0a acceptance criteria — Section 11)
- Plan: `docs/superpowers/plans/2026-05-20-phase-0a-gcp-migration.md` (Tasks 16-38 detail)
- Skill: `~/.claude/skills/gemini-gcp/SKILL.md` (invocation patterns, auth state, MCP servers)
- Memory: `gemini_antigravity_setup.md` (auth confirmed as `vertex-ai`, not `oauth-personal`)
- Terraform: `terraform/phase-0a-gcp/wif.tf` (WIF gap — attribute_condition single-condition)
- Terraform: `terraform/phase-0a-gcp/secret_manager.tf` (SM gap — hermes-provider missing)
- Research: HashiCorp plan-apply gate patterns (plan binary + `show -json` + bound apply)
- Research: Anthropic multi-agent system (4-tier escalation, structured output contract)
- Research: Gemini CLI headless behavior (`--output-format json`, maxActionsPerTask=100)
- Research: GCP IaC best practices 2026 (WIF conditions, secret stdin, verify-after-apply)
