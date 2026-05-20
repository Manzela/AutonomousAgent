# Phase 0a — GCP Always-Online Migration (Lift-and-Shift)

**Date:** 2026-05-20
**Status:** Approved (design); pending implementation plan
**Brainstorm session:** 2026-05-20 (continuation from `/audit` of 2026-05-20-state-of-the-repo)
**Successor brainstorms:**
- Phase 0b — Vertex AI Training + Agent Engine substrate (next)
- Phase 0c — Multi-zone HA + Slack/PagerDuty alerting (deferred)
- Phase 3 — A100 + Qwen self-host + LiteLLM class routing
- Phase 4 — Atropos trajectories + RL training pipelines

---

## 1. Goal

Move the 8-service AutonomousAgent docker-compose stack off the developer laptop and onto a Google Cloud Platform VM such that:

- **Hermes never silently dies.** The container is currently exiting 137 after plugin discovery and disappearing from `docker ps` with no host-level restart (audit finding F-2026-05-20-1). The migrated stack must surface that failure and recover automatically.
- **Uptime target: ~99%** (3.65 days/yr error budget). This justifies a single VM with auto-restart, host-level watchdog, daily snapshots, and email alerting — not multi-zone HA, not GKE, not Cloud Run.
- **Functional parity.** Same docker-compose, same images, same volumes, same ports. The migration is a relocation, not a refactor.
- **Substrate for later phases.** Phase 0a must leave Workload Identity Federation, Artifact Registry, Secret Manager, and a known-good VM image baseline in place so Phase 0b (Vertex AI Training), Phase 3 (A100 self-host), and Phase 4 (RL training) can land without re-doing IAM and CI plumbing.

## 2. Non-goals

Explicitly out of scope for this phase:

- ❌ A100 GPU, vLLM, Qwen self-host, LiteLLM class-based routing → **Phase 3** (separate brainstorm)
- ❌ Vertex AI Training infrastructure, RL pipelines, Atropos → **Phase 0b** / **Phase 4**
- ❌ Agent Engine / Gemini Enterprise Agent Platform integration → **Phase 0b**
- ❌ Multi-zone or multi-region HA → **Phase 0c** (single VM is consistent with a 99% SLA budget)
- ❌ GKE Autopilot / Cloud Run refactor → deferred (lift-and-shift first, refactor when there's a reason)
- ❌ Honcho / Chroma Cloud wiring → application-layer concern, Phase 1.1 work
- ❌ Architecture Components 1–6 (Phase-Aware MoE Router, RL Generator Agent, Hierarchical Memory, Intrinsic Reward, Free-Agent, Consensus/Episodic split) → Phases 1–5. Phase 0a is *substrate*, not application logic.

## 3. Pre-flight blocker — must land before migration

**Audit task P0-A.** Hermes exits 137 silently after plugin discovery on the developer laptop. If we migrate that bug, we migrate the crash. The migration is gated on a root-cause investigation that ships a fix to `main` first.

Suspects, in priority order:

1. **PR #98 read-only-fs + tmpfs hardening** may be starving the plugin loader. Mitigation: expand `/tmp` tmpfs sizing or carve a dedicated rw bind mount for plugin scratch space.
2. **Hermes submodule is 2 commits behind upstream** (`5e743559e → 42c428841`). Audit finding F-2026-05-20-7 / task P2-E. May contain a regression fix. Bump and re-test.
3. **Disk-cleanup plugin** may be tripping its own resource guard during startup discovery. Mitigation: temporarily disable in `config/plugins.yaml`, confirm exit-137 stops, then fix the guard.

Acceptance for unblocking Phase 0a:
- Hermes survives 24 hours of idle uptime locally without exit 137
- `docker ps` shows hermes continuously present
- Phoenix UI confirms tracing stream is uninterrupted

## 4. Architecture

```
GCP Project: rx-research-autonomousagent (new)
Region:      us-central1
Zone:        us-central1-a

┌──────────────────────────────────────────────────────────────────┐
│ Single GCE VM — e2-standard-4 (4 vCPU, 16 GB RAM)                │
│ OS: Debian 12                                                    │
│ Boot disk: 50 GB pd-balanced                                     │
│ Data disk: 100 GB pd-balanced → mounted at /opt/hermes/data      │
│   └─ backs the docker named volume "hermes-data"                 │
│      (shared by hermes, escalation-watcher,                      │
│       snapshot-watchdog, budget-watchdog)                        │
│                                                                  │
│ docker-compose stack (deploy/docker-compose.yml, unchanged):     │
│   litellm-db, litellm-proxy, otel-collector, phoenix,            │
│   shell-sandbox, github-mcp, volume-init,                        │
│   hermes, escalation-watcher,                                    │
│   snapshot-watchdog, budget-watchdog                             │
│                                                                  │
│ + NEW: hermes-watchdog.service (systemd, host-level)             │
│   Polls `docker compose ps` every 30s.                           │
│   If any expected container is missing or exited,                │
│   runs `docker compose up -d` and emits a structured log line.   │
└──────────────────────────────────────────────────────────────────┘
   │              │                  │                  │
   │              │                  │                  │
 Cloud Logging  Cloud Monitoring   GCS bucket        Secret Manager
 (stdout/stderr (uptime check +    (weekly snap     (replaces SOPS
  via gcplogs    custom metrics)    from PR #108)    at runtime)
  log driver)
                                    + daily PD snapshot (7-day retention)

 Artifact Registry (us-central1):  autonomousagent/hermes:<git-sha>
 VPC (hermes-vpc):                  /24 in us-central1, no public IP on VM
 IAP TCP forwarding:                SSH access + Phoenix UI tunneling
```

## 5. Compute and persistence

| Concern | Choice | Rationale |
|---|---|---|
| Machine type | `e2-standard-4` (4 vCPU, 16 GB) | Re-audit confirms current stack uses no GPU. LiteLLM proxies to managed Vertex AI / Anthropic. CPU + memory headroom for 11 containers comfortably fits in 16 GB. |
| OS | Debian 12 | Cleaner docker-compose + systemd integration than COS for this use case (COS optimizes for short-lived Kubernetes nodes, not long-running compose stacks with a host-level watchdog). |
| Boot disk | 50 GB pd-balanced | OS + docker daemon + container images |
| Data disk | 100 GB pd-balanced, mounted at `/opt/hermes/data` | Separated so we can resize/snapshot state independently of the OS. Backs the named volume `hermes-data`. |
| Snapshots | Daily PD snapshot schedule, 7-day retention | Complements the existing weekly GCS snapshot job from PR #108. |
| Auto-restart | Three-layer | (1) VM-level `automatic_restart: true`, `on_host_maintenance: MIGRATE`. (2) Docker-level `restart: unless-stopped` on every service (already set in compose). (3) Host-level `hermes-watchdog.service` (new) — restarts compose if any container is missing or exited. Directly addresses F-2026-05-20-1. |

## 6. Networking, auth, CI/CD

### Networking
- Dedicated VPC `hermes-vpc`, single `/24` subnet in us-central1
- **No public IP on the VM.** SSH only via `gcloud compute ssh --tunnel-through-iap`
- Firewall: deny-all ingress except IAP CIDR (`35.235.240.0/20`) on tcp/22. Egress allow-all (required for Vertex AI, Anthropic, GitHub, OpenRouter, npm/pip)
- Phoenix UI stays bound to localhost inside the VM; accessed via `gcloud compute start-iap-tunnel <vm> 6006 --local-host-port=localhost:6006`. No external exposure.

### Service accounts
| SA | Purpose | Roles |
|---|---|---|
| `hermes-runtime@<project>.iam.gserviceaccount.com` | VM runtime identity | `secretmanager.secretAccessor`, `logging.logWriter`, `monitoring.metricWriter`, `artifactregistry.reader`, `storage.objectCreator` (snapshot bucket) |
| `gha-deployer@<project>.iam.gserviceaccount.com` | GitHub Actions CI/CD | `compute.instanceAdmin.v1`, `artifactregistry.writer`, `iam.serviceAccountUser` |

### CI/CD path
- **Workload Identity Federation** between GitHub Actions and `gha-deployer@<project>.iam`. No long-lived JSON keys.
- On merge to `main`:
  1. CI builds `autonomousagent/hermes:<git-sha>` from `deploy/Dockerfile.hermes`
  2. CI pushes to Artifact Registry repo `hermes` in us-central1
  3. CI SSHs into the VM via IAP, runs `docker compose pull && docker compose up -d`
  4. CI runs a post-deploy smoke check (`litellm-proxy /health` returns 200 within 90s)
- On smoke-check failure: CI rolls back to the prior image tag and alerts.

## 7. Secrets

- **Source of truth at runtime:** Secret Manager.
- **Source of truth in repo:** SOPS files (`secrets/*.env.sops`) remain for local dev workflow and as the canonical encrypted backup.
- **Migration script** (`scripts/migrate-secrets-to-secret-manager.sh`, new):
  - For each `secrets/*.env.sops`, decrypt → parse key/value pairs → write each value to Secret Manager as `hermes-<env-filename>-<key>` (e.g., `hermes-honcho-HONCHO_API_KEY`)
  - Idempotent: skips secrets whose latest version already matches
- **VM secret-loading:** a systemd one-shot unit (`hermes-secrets.service`, runs `Before=docker-compose-hermes.service`) executes `/usr/local/bin/load-secrets.sh` on every boot. The script uses the VM's runtime service account to pull each `hermes-*` secret from Secret Manager and write ephemeral env files at `/run/hermes/env/*.env` (tmpfs, wiped on reboot). The docker-compose unit then references them via `--env-file`. Hard ordering: secret-loading → compose-up → watchdog start.
- **Honcho key handling:** stays at `secrets/honcho.env.sops` per memory entry. The migration tool decrypts it once and lands `HONCHO_API_KEY` in Secret Manager. **No re-asking the user.**

## 8. Observability

- **Logging:** Docker `gcplogs` log driver streams every container's stdout/stderr to Cloud Logging. Estimated ~10 GB/mo ingest, well inside the 50 GB free tier.
- **Monitoring:**
  - Uptime check: `litellm-proxy /health` every 60s, alert on 3 consecutive failures
  - VM metrics auto-collected: CPU, memory, disk, network
  - Custom metric (new): `hermes_containers_expected_vs_running` emitted by the watchdog service every 30s
- **Tracing UI:** Phoenix unchanged at `:6006` inside the VM, accessed via IAP tunnel
- **Alert policies (email-only for Phase 0a):**
  - VM down >5 min
  - Any expected container missing >2 min
  - Disk >85% full
  - Daily PD snapshot job failed
  - Slack and PagerDuty deferred to Phase 0c

## 9. Disaster recovery

| Layer | Mechanism | RPO | RTO |
|---|---|---|---|
| Application state (hermes-data volume) | Daily PD snapshot, 7-day retention | 24 h | ~10 min (snapshot restore + remount) |
| Cross-region durability | Weekly GCS snapshot (existing PR #108) → multi-region bucket | 7 days | ~30 min |
| Container images | Artifact Registry, tagged by git SHA | n/a | ~5 min (pull) |
| Secrets | Secret Manager + SOPS in repo as backup | n/a | ~5 min |
| Full VM rebuild | Terraform-defined, ~15 min apply | n/a | ~30 min |

**Combined target: RTO ~30 min, RPO ~24 h.** Both fit inside the 99% SLA budget (3.65 days/yr).

## 10. Cost

Monthly estimate (us-central1 list pricing, no sustained-use discount):

| Item | Monthly |
|---|---|
| `e2-standard-4` always-on (730 hr) | ~$98 |
| 150 GB pd-balanced (50 boot + 100 data) | ~$15 |
| 7 daily snapshots | ~$3 |
| GCS snapshot bucket (~5 GB, multi-region) | ~$0.10 |
| Cloud Logging (~10 GB ingest) | ~$5 |
| Cloud Monitoring + alerting | $0 (free tier) |
| Secret Manager (~20 secrets) | ~$0.12 |
| Artifact Registry (~10 GB) | ~$1 |
| Network egress (~20 GB) | ~$2.40 |
| **Phase 0a total** | **~$125/mo** |

For comparison: Phase 3 will add **+$800–2,520/mo** for the A100 (depending on pause/resume vs always-on). Phase 0a is a tenth of that.

## 11. Acceptance criteria

Phase 0a is done when all of:

1. ✅ Pre-flight blocker (Section 3) is closed: hermes survives 24h idle locally without exit 137
2. ✅ GCE VM provisioned, docker-compose stack running, all 10 long-running containers continuously present in `docker compose ps` for 72 consecutive hours (`volume-init` is a one-shot init container and is exempt; the watchdog tracks long-running services only)
3. ✅ `litellm-proxy /health` returns 200 for 99%+ of uptime check polls over a 7-day window
4. ✅ `hermes-watchdog.service` log shows zero "restart triggered" events under steady state, AND demonstrates a successful auto-recovery when a container is killed manually (chaos test)
5. ✅ Daily PD snapshot succeeds for 7 consecutive days
6. ✅ Test recovery: provision a new VM from latest PD snapshot, verify state continuity (hermes resumes from last checkpoint, no data loss in `hermes-data`)
7. ✅ CI workflow: merge to `main` triggers build → push → deploy → smoke check, end-to-end, in <10 min
8. ✅ Workload Identity Federation works; no JSON key files anywhere in the repo or in GitHub Actions secrets
9. ✅ All secrets accessible from Secret Manager; SOPS files retained in repo for dev workflow
10. ✅ Cost actuals within ±20% of the $125/mo estimate after one full billing cycle

## 12. Open questions / risks

| ID | Item | Resolution path |
|---|---|---|
| OQ-1 | GCP project name `rx-research-autonomousagent` — does the user want to use an existing project (e.g., `i-for-ai` from CLAUDE.md) or create a new one? | Confirm during implementation plan. Default to new for blast-radius isolation. |
| OQ-2 | Workload Identity pool naming convention. | Standard `github-actions` pool, provider `manzela-autonomousagent`. Confirm in plan. |
| OQ-3 | Should the daily PD snapshot also push to a separate cross-region GCS bucket, or is the existing weekly GCS snapshot (PR #108) enough? | Default: yes, daily PD only stays in-region; weekly GCS is the cross-region durability layer. |
| R-1 | The pre-flight blocker (Section 3) could surface a deeper bug requiring more than a tmpfs tweak or submodule bump. | If RCA takes >3 days, escalate: either downgrade PR #98 hardening or add a feature flag to bypass it for the migration window. |
| R-2 | `docker compose pull` over IAP may be slow if image is large. | Mitigation: enable Artifact Registry image streaming, or pre-pull on a schedule. Confirm during implementation. |
| R-3 | Honcho API rate limits when the stack is always-on (vs intermittent laptop use). | Out of scope for Phase 0a infra, but flag for Phase 1.1 owner. |
| R-4 | Vertex AI quota in the new project may not match the existing project. | Quota request to be filed during plan-writing, not during execution. |

## 13. Implementation hand-off

After this spec is approved by the user, the next step is the `superpowers:writing-plans` skill, which will produce a step-by-step implementation plan covering:

- Terraform module structure (`terraform/phase-0a-gcp/`)
- The order of operations (project setup → IAM → networking → VM → secrets → CI → cutover)
- The pre-flight blocker work (audit task P0-A — diagnose-then-fix hermes locally)
- Smoke tests, chaos tests, and the acceptance-criteria gate
- Rollback plan (keep laptop docker-compose running in parallel during cutover week)

## 14. References

- Audit: `audit/2026-05-20-state-of-the-repo/audit-plan.md` (P0-A hermes RCA, P2-E submodule bump)
- Audit: `audit/2026-05-20-state-of-the-repo/findings.md` (F-2026-05-20-1 silent hermes death; F-2026-05-20-7 submodule lag)
- Stack: `deploy/docker-compose.yml` (8-service stack, all hardened per PR #58 CIS baseline)
- Forward-looking quota: `config/limits.yaml` (A100 declarations are Phase 3, unused in Phase 0a)
- Phase 3 source-of-truth: older `audit/audit-plan.md` (a2-highgpu-1g, vllm/qwen-coder-32b, LiteLLM class routing)
- Existing GCS snapshot job: PR #108 (weekly cost-summary + spend-log snapshot)
- OpenRouter R3 fallback: PR #109 (single-provider risk mitigation, complementary)
- Memory: `phase_1_trap_warning` (do NOT re-merge `origin/phase/1`)
- Memory: `honcho_api_key_location` (secrets/honcho.env.sops, never re-ask)
