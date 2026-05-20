# Audit plan — State of the Repo (2026-05-20, end-of-day v2)

> **Source.** Pass-1 draft against `findings.md` in this directory. References to `F-2026-05-20-V2-N` are findings in §3 of that file. References to `R*` are the risk register in §5. References to `#N` are GitHub issues (verified open 2026-05-20T18:18 local). References to `Task N` are the Phase 0a plan tasks in `docs/superpowers/plans/2026-05-20-phase-0a-gcp-migration.md`.
>
> **Discipline.** This plan is the deliverable. No fixes are run until the user approves, picks a subset, or amends. Items are listed in execution order within each tier so a partial green-light can flow top-down without re-sequencing.
>
> **Relationship to morning audit-plan** (`audit/2026-05-20-state-of-the-repo/audit-plan.md`). That plan is still valid for items it covers (P0-B CodeQL toggle, P0-C/P1 branch-protection flips, P1-C stale branches, P2-A GPG inventory, etc.). The v2 plan focuses on what has changed in the ~7 hours since (Phase 0a IaC landed) and the new operational state. Carry-overs are tracked here under §P3 but the morning plan's prescriptions remain authoritative for those items.

---

## P0 — Unblock PR #112 and close out the morning's incident (do today)

### P0-A — Patch `.gitleaks.toml` to allowlist all audit-tree docs + logs (REVISED in pass 2)

- **What.** Append a 1-line allowlist entry to `.gitleaks.toml` that excludes the entire audit tree (`.md` + `.log`) from the gitleaks scan.
- **Why.** F-2026-05-20-V2-2 + **F-2026-05-20-V2-7** / R-PR112. Pass-1 prescribed `audit/.*/p0a-rca/.*\.log$` to cover the 2 morning RCA log false-positives. Pass-2 re-scan after writing v2 audit files revealed **15 leaks** total — 5 of which are in `audit/2026-05-20-state-of-the-repo-v2/{findings,audit-plan}.md` (the v2 audit files quote the `key=observability/langfuse` string for explanation). Path-based allowlist must broaden to cover the entire audit tree. Audit-tree content is by definition documentary/forensic, never executable, and intentionally quotes patterns from the codebase for forensic clarity. The secrets/*.env hits (7) are gitignored on the runner so don't reach CI.
- **Where.**
  - `.gitleaks.toml` — append under existing `[allowlist].paths`:
    ```toml
    '''audit/.*\.(md|log)$''',
    ```
  - Verify locally before push:
    ```bash
    gitleaks detect --source . --no-git --redact --report-format sarif --report-path /tmp/gl.sarif --config .gitleaks.toml
    jq '[.runs[0].results[] | select(.locations[0].physicalLocation.artifactLocation.uri | startswith("audit/"))] | length' /tmp/gl.sarif
    # expect 0 — every audit/* match excluded
    ```
- **Effort.** XS — 2 minutes including local re-scan.
- **Acceptance.** PR #112 CI re-runs green on `gitleaks` (in CI mode, only audit/* paths exist on the runner so leak_count → 0). PR moves to MERGEABLE.
- **Risk if delayed.** Tasks 16–38 cannot be sub-PR-merged incrementally. PR #112 will accumulate v2 audit commits and keep regressing red.
- **Depends on.** Nothing.

### P0-B — Close out issue #94 with the NON-REPRODUCTION evidence + reframe as soak-test tracker

- **What.** Two-step issue update: (1) post a comment on #94 with the RCA non-reproduction summary + current `docker ps` snapshot showing 10/10 services up + hermes uptime 6h+; (2) decide between close-as-not-planned vs keep-open-as-soak-tracker.
- **Why.** F-2026-05-20-V2-5 / R24. The headline P0 from the morning audit is operationally cleared (stack back up, no restarts), but the underlying root cause is unknown (F-2026-05-20-V2-1). Leaving #94 silently open while the stack is healthy creates two bad signals: (a) the issue tracker drifts from reality, (b) Phase 0a Task 5 (24h idle soak — the only remaining bar) lacks a public tracker.
- **Where.**
  ```bash
  gh issue comment 94 --body "$(cat <<'EOF'
  Status update 2026-05-20T18:18 local: stack is up.

  - RCA: NON-REPRODUCTION (see `audit/2026-05-20-state-of-the-repo/p0a-rca/REPRODUCTION-SUMMARY.md`). The exit-137 signature did not reappear on a clean restart at commit `feat/phase-0a-gcp-migration@07d2d63`.
  - Live state: `docker ps` → 10/10 long-running containers up; `autonomous-agent-hermes-1` healthy, uptime 6h 15m, `RestartCount=0`, `OOMKilled=false`.
  - Underlying cause: still unknown. Plan Tasks 2 (submodule), 3 (PR #98 tmpfs), 4 (disk-cleanup plugin) were not run because there was no failure to bisect.

  Keeping this issue open as the tracker for Phase 0a plan Task 5 (24-hour idle soak). Will close once `hermes survives 24h idle locally` passes — projected ~2026-05-21T12:03 local if uptime holds.
  EOF
  )"
  ```
  Do **not** close yet; convert to soak-tracker per Recommendation in F-2026-05-20-V2-5.
- **Effort.** XS — 3 minutes.
- **Acceptance.** Issue #94 has a status comment + an open checkbox for Task 5. Operator and any future auditor sees current state without re-reading audit folder.
- **Depends on.** Nothing.

### P0-C — Document `secrets/hermes-provider.env` as a DERIVED secret (REVISED in pass 2)

- **What.** Add a "Derived secrets" section to `secrets/README.md` explaining that `hermes-provider.env` is generated by `scripts/decrypt-secrets.sh` from `litellm-master-key` and never needs its own `.sops` source.
- **Why.** Pass-1 prescribed a grep-then-escalate flow under the assumption hermes-provider was a stand-alone secret with no source. Pass-2 Explore against `scripts/decrypt-secrets.sh:65-73` resolved this: the file is derived at bootstrap from `litellm-master-key` (the OPENAI-compatible token the hermes container uses to call litellm-proxy). No `.sops` source is needed, no SM migration line item is needed. The only remaining gap is documentation so a future operator (or auditor) doesn't trip on the same finding.
- **Where.** `secrets/README.md` — append a new "Derived secrets" heading:
  ```markdown
  ## Derived secrets

  These files are NOT committed in any form — they are generated at bootstrap by `scripts/decrypt-secrets.sh` from other (committed) `.sops` sources.

  | Derived file          | Source secret        | Generated by                                                          |
  | --------------------- | -------------------- | --------------------------------------------------------------------- |
  | `hermes-provider.env` | `litellm-master-key` | `scripts/decrypt-secrets.sh:65-73` (writes `OPENAI_API_KEY=<master>`) |
  ```
- **Effort.** XS — 2 minutes.
- **Acceptance.** `secrets/README.md` has a "Derived secrets" section listing `hermes-provider.env`. Task 24 author (P2-A) skips this file. Future audits resolve this inline.
- **Depends on.** Nothing.

### P0-D — Reconcile Terraform state with already-deployed GCP resources (NEW in pass 2)

- **What.** Run `terraform init` against the backend bucket `gs://i-for-ai-autonomousagent-tfstate/`, then `terraform plan` against `terraform/phase-0a-gcp/`. Compare with the 11 already-deployed resources Gemini enumerated against live `i-for-ai`. Per-resource decision: import into state, treat as out-of-band, or delete and let Terraform recreate.
- **Why.** F-2026-05-20-V2-6 / R-IaC-drift. Pass-2 GCP pre-flight via Gemini revealed the IaC for Phase 0a has been **partially applied off-branch** to live `i-for-ai`: VPC + 4 SM env-file secrets (`autonomousagent-{chroma-cloud,honcho,litellm-db,telegram}`) + AR repo `autonomousagent-images` + GCS buckets `i-for-ai-autonomousagent-{snapshots,tfstate}` + runtime + CI SAs (`autonomousagent-{vm-runtime,github-ci}`) + snapshot policy `autonomousagent-data-daily-snapshot` all exist. Only the VM (Task 16) is missing. PR #112 is still DRAFT and unmerged. **Code-state divergence is real.** Without reconciliation, the first `terraform apply` will either error ("resource already exists") or duplicate. The cost meter is already running for the deployed resources (negligible until VM lands, but real).
- **Where.**
  ```bash
  cd terraform/phase-0a-gcp/
  terraform init -backend-config="bucket=i-for-ai-autonomousagent-tfstate"
  terraform plan -out=/tmp/p0a-reconcile.tfplan 2>&1 | tee /tmp/p0a-reconcile.log
  # Expect: "<resource> already exists" warnings, or "1 to create, 0 to change, 0 to destroy" if state is in sync
  ```
  - **If `terraform.tfstate` already exists in the bucket:** plan should be near-zero diff. Diff what is unexpected — it indicates manual edits in the GCP console.
  - **If `terraform.tfstate` is empty:** `terraform import` each of the 11 resources (script the imports — `for_each` resources need explicit `<addr>[<key>]` indexing).
  - **Reframe PR #112 description:** instead of "applies IaC for the first time", reframe as "syncs Terraform code with deployed state + adds VM (Task 16)" — sets correct expectation for reviewer.
- **Effort.** M — 45 min for init+plan+diff; up to 90 min if 11 imports are needed. Worth front-loading because P1-B (Task 16 VM) cannot proceed without trusted state.
- **Acceptance.** `terraform plan` shows exactly 1 new resource to create (`google_compute_instance.autonomousagent_vm` from P1-B once authored), zero unexpected diffs. State stored in `gs://i-for-ai-autonomousagent-tfstate/`. PR #112 description updated.
- **Risk if delayed.** P1-B (Task 16 VM) cannot be applied without trusted state — `apply` would either duplicate or error. Cost remains low while only existing resources run, but blocks all of Phase D execution.
- **Depends on.** Nothing. Runs in parallel with P0-A/B/C; gates P1-B.

---

## P1 — Phase 0a Phase D foundation (compute + secrets) — start after P0 lands

> **Phase D scope.** Tasks 16–23 build the runtime substrate: the GCE VM, the install/bootstrap scripts, the three systemd units (`hermes-secrets`, `docker-compose-hermes`, `hermes-watchdog`), and the docker-compose GCP override. After P1 lands, the VM is provision-ready but secrets have not yet migrated to Secret Manager (that is Phase E / P2 below).

### P1-A — Fix Task 14 Secret Manager coverage gap (REFINED in pass 2 — 2 production-critical singletons + host-side healthchecks-url)

- **What.** Extend `terraform/phase-0a-gcp/secret_manager.tf` so Secret Manager covers the 2 SOPS-encrypted singletons that the running stack actually depends on (plus `healthchecks-url` for the host-side watchdog timer), **not** the 5 prescribed in pass 1. Done as a Terraform edit before Task 24 (`migrate-secrets-to-secret-manager.sh`) is written, so the migration script has a complete `secret_id` list.
- **Why.** F-2026-05-20-V2-3 (refined). Pass-1 prescribed 5 singletons by inventorying every `.sops` file under `secrets/`. Pass-2 Explore agent A inspected each consumer in `deploy/docker-compose.yml` + `scripts/decrypt-secrets.sh` and classified them:
  - `chroma-token` — **DEAD CODE** (Chroma Cloud migration deprecated this consumer). Strike from inventory.
  - `github-pat` — **PRODUCTION-CRITICAL** (loaded by `github-mcp` service via `env_file`). MUST migrate.
  - `healthchecks-url` — **HOST-SIDE ONLY** (loaded by the laptop's cron, not by any container). MUST migrate but via a different path (see below).
  - `honcho-db-password` — **DISABLED** (Honcho service commented out in `docker-compose.yml`). Defer until Honcho re-enabled.
  - `litellm-master-key` — **PRODUCTION-CRITICAL** (consumed by `litellm-proxy` + derives `hermes-provider.env` per P0-C). MUST migrate.
- **Where.**
  - `terraform/phase-0a-gcp/secret_manager.tf` — add a second list and a second resource block:
    ```hcl
    locals {
      sops_env_files = [
        "chroma-cloud",
        "honcho",
        "litellm-db",
        "telegram",
      ]
      sops_singletons = [
        "github-pat",         # consumed by github-mcp service
        "litellm-master-key", # consumed by litellm-proxy + derives hermes-provider.env
        "healthchecks-url",   # consumed host-side by hermes-watchdog timer (NOT a container env)
      ]
      # Deferred (Honcho disabled): "honcho-db-password"
      # Dead code (Chroma Cloud migration): "chroma-token"
    }

    resource "google_secret_manager_secret" "singleton" {
      for_each = toset(local.sops_singletons)
      secret_id = "autonomousagent-${each.key}"
      replication { auto {} }
      labels = local.common_labels
    }
    ```
  - `outputs.tf` — add `singleton_secret_ids = { for k, s in google_secret_manager_secret.singleton : k => s.id }`.
  - **Host-side consumption note for `healthchecks-url`:** load-secrets.sh (P1-C) pulls it from SM into `/run/secrets/healthchecks-url.env` like the container env files; hermes-watchdog systemd timer (P1-E) consumes via `EnvironmentFile=/run/secrets/healthchecks-url.env`.
- **Effort.** S — 20 min including `terraform validate` + `terraform plan` against `i-for-ai` (expect 3 new SM secrets in the plan because the 4 env-file secrets already exist per F-V2-6 — see P0-D reconciliation).
- **Acceptance.** `terraform plan` shows 3 new singleton SM secret resources (`autonomousagent-github-pat`, `autonomousagent-litellm-master-key`, `autonomousagent-healthchecks-url`). `outputs.tf` exposes both maps. Plan output attached to commit body for diff review.
- **Risk if delayed.** Lower than pass-1 estimate (3 secrets not 5; `chroma-token` and `honcho-db-password` removed entirely). Still cascades into Task 24 if the migration script is authored against the 4-entry list.
- **Depends on.** P0-D (state must be reconciled so plan diff is meaningful).

### P1-B — Task 16: GCE VM `autonomousagent-vm` (Terraform)

- **What.** Author `google_compute_instance.autonomousagent_vm` in `terraform/phase-0a-gcp/compute.tf`, attaching the boot disk + data disk (already authored in `ce7d875`), the runtime SA (already authored in `iam.tf`), and a startup-script that delegates to `install.sh` (Task 17, P1-C).
- **Why.** First infrastructure resource that materially commits us to GCP cost. Without the VM, none of P1-C through P1-G (install, secrets-loader, systemd units, watchdog, compose override) have anything to run on.
- **Where.** Plan section `docs/superpowers/plans/2026-05-20-phase-0a-gcp-migration.md:1155-1267` (Task 16) has the full spec including machine type (`e2-standard-4`), startup-script metadata reference, and the IAP SSH attachment.
- **Effort.** M — 45–60 min (Task 16 is large because it wires together 5 already-created resources + adds startup-script metadata; plan ahead for `terraform plan` review).
- **Acceptance.** `terraform plan` shows 1 new `google_compute_instance` + 2 disk attachments + 0 unrelated diffs. No resources applied yet (that is Task 34 in P3).
- **Depends on.** P0-A (PR #112 merged so the commit lands cleanly on `main`); P1-A (so the VM has the full secret list to reference via env-from-metadata later).

### P1-C — Tasks 17 + 18: install.sh master bootstrap + load-secrets.sh

- **What.** Two POSIX shell scripts that run on first boot under the GCE startup-script metadata:
  - `scripts/phase-0a/install.sh` — installs docker, docker-compose, the Google Cloud Ops Agent, the SOPS binary (transitional only), and clones the repo to `/opt/autonomousagent`.
  - `scripts/phase-0a/load-secrets.sh` — fetches each secret from SM (using the VM's runtime SA via metadata server) and writes plaintext env files to `/run/secrets/` (tmpfs) for docker-compose to mount.
- **Why.** Without these the VM boots but never starts the stack. `load-secrets.sh` is the bridge between Secret Manager (P2-A) and docker-compose's `env_file:` directives — it MUST iterate over both `sops_env_files` and `sops_singletons` (per P1-A's split).
- **Where.** Plan sections at lines `1268-1372` (install.sh) and `1373-1426` (load-secrets.sh). Both end with `set -euo pipefail` and write to journald so any failure surfaces in Cloud Logging.
- **Effort.** M — 60 min for install.sh + tests; 45 min for load-secrets.sh + dry-run via `gcloud secrets versions access`.
- **Acceptance.** Two scripts pass `shellcheck`. A local rehearsal (using a test VM or `gcloud compute ssh` to a throwaway VM) shows docker + compose + ops-agent + repo present at `/opt/autonomousagent` after install.sh, and `/run/secrets/*.env` populated after load-secrets.sh.
- **Depends on.** P1-A (load-secrets.sh enumerates both secret lists); P1-B (the VM that will run these scripts must be in Terraform).

### P1-D — Tasks 19 + 20: hermes-secrets.service + docker-compose-hermes.service systemd units

- **What.** Two systemd units that turn the stack into a true managed service:
  - `systemd/hermes-secrets.service` — oneshot that runs `load-secrets.sh` at boot, required-by `docker-compose-hermes.service`.
  - `systemd/docker-compose-hermes.service` — Type=oneshot RemainAfterExit=yes that runs `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.gcp.override.yml up -d` (depends on P1-G), with `ExecStop=docker compose down`.
- **Why.** Without systemd integration, hermes only starts when an operator SSHes in. With these units, the stack starts on every reboot — required for the spec §11 acceptance criterion "survives `sudo reboot` cleanly."
- **Where.** Plan sections at lines `1427-1466` (hermes-secrets) and `1467-1504` (docker-compose-hermes). Both must be `enable`d in install.sh after install.
- **Effort.** S — 30 min for both units + `systemd-analyze verify`.
- **Acceptance.** Both `.service` files validate under `systemd-analyze verify`. Local dry-run: `sudo systemctl enable --now hermes-secrets.service docker-compose-hermes.service` brings the stack up; `sudo systemctl stop docker-compose-hermes.service` brings it down cleanly.
- **Depends on.** P1-C (the scripts the units invoke); P1-G (the compose override the unit references).

### P1-E — Tasks 21 + 22: hermes-watchdog.sh + hermes-watchdog.service + expected-containers.txt

- **What.** A bash-based watchdog that runs every 60s under a systemd `.timer`, reads `config/phase-0a/expected-containers.txt` (the canonical list of 10 long-running services), compares against `docker compose ps --format json`, and restarts any missing containers. Emits a single log-line metric `hermes_watchdog_missing_count=N` (consumed by P2-B's custom metric).
- **Why.** The morning audit's R24 ("hermes dies silently") + F-2026-05-20-V2-1 (RCA non-reproduction) make this the **load-bearing operational guarantee** for Phase 0a. Without it, a future silent exit goes undetected until the healthchecks.io ping window closes (10+ min).
- **Where.** Plan sections at lines `1505-1588` (watchdog script) and `1589-1622` (systemd unit + timer + expected-containers.txt). Watchdog script must shellcheck-clean.
- **Effort.** M — 45 min for the script + 15 min for the systemd unit + timer.
- **Acceptance.** Chaos test (`docker kill autonomous-agent-hermes-1`) followed by ≤90s wait shows the container restarted and `hermes_watchdog_missing_count=0` in logs. Unit test in `tests/` exercising both happy + missing-container paths.
- **Depends on.** P1-D (compose-hermes must be running for watchdog to be meaningful).

### P1-F — Task 23: docker-compose.gcp.override.yml

- **What.** GCP-specific compose override layered on top of `deploy/docker-compose.yml`: mounts `/run/secrets/*.env` from tmpfs (instead of the laptop's bind-mount), routes `/var/lib/autonomousagent/data` to the attached data disk, attaches a `gcplogs` log driver targeting Cloud Logging, and removes any host-network bindings safe only on the laptop.
- **Why.** Single override that flips the stack from laptop-mode to GCP-mode without forking `docker-compose.yml`. Forking the base compose would diverge two long-term targets — the override pattern keeps the laptop dev-loop unchanged.
- **Where.** Plan section at lines `1623-1691`. Pay attention to volume declarations — the data disk must mount at the path docker-compose expects for hermes session state.
- **Effort.** S — 30 min including `docker compose -f base -f override config` validation.
- **Acceptance.** `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.gcp.override.yml config` returns 0 with no warnings. A local rehearsal on the laptop (with the override pointed at `/tmp` instead of `/var/lib/autonomousagent`) brings up all 10 services.
- **Depends on.** P1-A (so secret paths in the override match what load-secrets.sh produces).

### P1-G — Per-task commits + sub-PRs against `feat/phase-0a-gcp-migration`

- **What.** Each of P1-A through P1-F gets its own commit on `feat/phase-0a-gcp-migration` and lands via separate sub-PR if PR #112 is merged before P1 starts, or stacks as additional commits if PR #112 is still open.
- **Why.** Wave-3 audit conclusion: one-task-per-commit + conventional-commit titles is the established workflow contract. The 13 commits already on `feat/phase-0a-gcp-migration` (§2.2 of findings) all follow this pattern.
- **Where.** `git commit -m "feat(phase-0a): ..."` per task. Each commit's message body must reference the plan section by line range so reviewers can verify against spec.
- **Effort.** XS per commit (already part of each task's wrap-up).
- **Acceptance.** Each task's commit passes pre-commit hooks (gitleaks, conventional-commits, branch-name) on its own. PR #112 (or its successors) shows the linear, reviewable history.

---

## P2 — Phase 0a Phase E + Phase F (secrets migration + observability + cutover) — do after P1

### P2-A — Tasks 24, 25, 26: SOPS → Secret Manager migration

- **What.** Three sub-tasks executed in order:
  - **Task 24:** Author `scripts/phase-0a/migrate-secrets-to-secret-manager.sh` — iterates `sops_env_files` + `sops_singletons` (per P1-A), decrypts each `secrets/*.sops` with the operator's age key, and writes the plaintext to the corresponding SM secret via `gcloud secrets versions add`.
  - **Task 25:** Run the script in dry-run mode (`--dry-run` flag echoes `gcloud` calls without executing). Verify each expected secret_id appears.
  - **Task 26:** Run for real; verify each `gcloud secrets versions access latest --secret=autonomousagent-<name>` returns the expected plaintext. Capture the run log under `audit/phase-0a-secrets-migration-evidence.log`.
- **Why.** Secrets cannot live in SOPS forever — that ties operations to the laptop's age key holder. SM via Workload Identity Federation (Task 11, already shipped) is the long-term substrate. Cutover (P2-D) depends on this completing.
- **Where.** Plan sections at lines `1692-1846`. Pay attention to UTF-8 handling for any secrets that contain trailing newlines (sops strips them, gcloud preserves them; the migration script must normalize).
- **Effort.** Task 24 = M (60 min including idempotency test + audit log path). Tasks 25+26 = S each (15 min + 10 min once Task 24 is solid).
- **Acceptance.** `gcloud secrets versions access` returns correct plaintext for all 9 (or 10) secrets. `audit/phase-0a-secrets-migration-evidence.log` exists with timestamps. PR adds Task 26's evidence file (path only, content gitignored).
- **Risk if delayed.** None directly — the laptop can run on SOPS until cutover. But Task 26 must precede Task 38 (cutover), and Task 26 cannot be retried without `gcloud secrets versions destroy` + re-run (versions accumulate).
- **Depends on.** P1-A (full secret list); P1-B (VM provisioned so the SM bindings exist).

### P2-B — Tasks 27, 28, 29: Cloud Monitoring (uptime + custom metric + 4 alert policies)

- **What.** Three sub-tasks that wire the operational signals:
  - **Task 27:** Uptime check against `litellm-proxy /health` exposed via the VM's external IP (or internal HTTPS LB if scope grows).
  - **Task 28:** Log-based custom metric `hermes_watchdog_missing_count` derived from the watchdog log-line (P1-E).
  - **Task 29:** Four alert policies — (1) litellm-proxy down >5 min, (2) `hermes_watchdog_missing_count > 0` for 2 evaluation windows, (3) VM CPU > 90% for 10 min, (4) disk free < 10% on data disk.
- **Why.** Acceptance criterion §11.6 ("operator is paged on hermes outage within 5 minutes") is gated on these three. The watchdog (P1-E) restarts containers locally, but Cloud Monitoring is what pages the human if the watchdog itself stops emitting.
- **Where.** Plan sections at lines `1847-2083` (Task 29 is largest — 4 alert policies authored as Terraform `google_monitoring_alert_policy` resources under `terraform/phase-0a-gcp/monitoring.tf`).
- **Effort.** Task 27 = S (30 min). Task 28 = S (20 min). Task 29 = M (60 min for 4 alert policies + notification channel + Terraform validate).
- **Acceptance.** All 4 alert policies show `state: ENABLED` after `terraform apply`. Chaos test (P3-C / Task 32) trips alert 2 (`hermes_watchdog_missing_count > 0`) and the operator receives the notification via the configured channel.
- **Depends on.** P1-B (VM with public-ish endpoint or LB); P1-E (the log-line metric source).

### P2-C — Tasks 30, 31, 32, 33: GitHub Actions deploy + smoke/chaos/acceptance scripts

- **What.** Four interrelated deliverables:
  - **Task 30:** `.github/workflows/phase-0a-deploy.yml` — triggered on `main` push (or manual `workflow_dispatch`), authenticates via WIF (Task 11), builds + pushes hermes image to Artifact Registry, SSHes via IAP to the VM, runs `docker compose pull && docker compose up -d`.
  - **Task 31:** `scripts/phase-0a/smoke.sh` — post-deploy probes: `curl :4000/health`, `curl :6006`, `docker ps` count == 10, hermes recent log line includes `Plugin discovery complete`.
  - **Task 32:** `scripts/phase-0a/chaos.sh` — `docker kill autonomous-agent-hermes-1` then wait 120s and re-run smoke; pass if smoke passes (watchdog restarted it).
  - **Task 33:** `scripts/phase-0a/acceptance.sh` — wraps all 10 acceptance criteria from spec §11 in a single CI gate; pass = green, fail = red with which criterion failed.
- **Why.** Operationalizes the workflow so deploys are automated and reversible. acceptance.sh is the single command the operator runs to know "Phase 0a is done" — closes spec §11 gate.
- **Where.** Plan sections at lines `2084-2421`. Task 30 must use IAP-tunnel SSH (no public SSH port on the VM, per firewall in `networking.tf`).
- **Effort.** Task 30 = L (90 min — most complex; WIF auth + image build + IAP SSH + compose deploy with secret precedence). Task 31 = S (20 min). Task 32 = S (30 min — assumes P1-E watchdog works). Task 33 = M (45 min — 10 criteria need individual probes).
- **Acceptance.** First end-to-end run of Task 30 against the live VM (after P2-D Task 34) shows green deploy + green smoke. Task 33 shows 10/10 criteria pass.
- **Depends on.** P1-B + P2-A (VM exists + secrets in SM); P2-B (monitoring + watchdog so chaos is meaningful).

### P2-D — Tasks 34, 35, 36, 37, 38: Cutover from laptop to GCP

- **What.** The final phase, executed only after every P0/P1/P2 above has landed and every gate has passed:
  - **Task 34:** `terraform apply` against `i-for-ai`. **Irreversible cost commit (~$125/mo).** Captures `terraform.tfstate` to the GCS backend (Task 6) for shared lock.
  - **Task 35:** `docs/runbooks/phase-0a-cutover.md` — step-by-step the operator follows once on cutover day: stop laptop stack, push final secrets, run deploy workflow, run acceptance.sh, redirect healthchecks.io.
  - **Task 36:** `docs/runbooks/phase-0a-rollback.md` — what to do if cutover fails: rehydrate laptop secrets, `docker compose up -d` on laptop, accept GCP cost incurred so far.
  - **Task 37:** `docs/runbooks/phase-0a-recovery.md` — what to do if the VM is lost: `terraform apply` recreates VM with same disk-snapshot policy (Task 15), `load-secrets.sh` re-runs, watchdog re-enables, ≤30 min RTO.
  - **Task 38:** Execute the cutover per the runbook. Acceptance criterion §11.10 ("operator successfully cutover from laptop to GCP, laptop stack shut down for 24h, no SLO regression").
- **Why.** Without this, Phase 0a is just IaC on disk — the agent is still on the laptop. This phase closes spec §11.10 and retires the laptop as the production host.
- **Where.** Plan sections at lines `2422-2740`. Task 34 is the only Terraform action that touches live GCP and creates spend.
- **Effort.** Task 34 = M (60 min including `terraform plan` review + apply + verify). Tasks 35–37 = M each (45 min each for thorough runbook authoring). Task 38 = L (90 min wall-clock for the cutover itself + 24h soak window).
- **Acceptance.** Spec §11 acceptance gate (`acceptance.sh`) returns 10/10 green from the GCP VM, with the laptop stack confirmed down for 24h. Issue #94 closes with the soak-test result. `terraform.tfstate` in GCS shows the live resource graph.
- **Risk if rushed.** Re-running Task 34 after partial apply leaves dangling resources that cost money; runbooks 36 + 37 must be complete BEFORE Task 38.
- **Depends on.** Everything above. Hard gate.

---

## P3 — Carry-over from morning audit (still applicable, deferred but tracked)

Items that the morning audit-plan covers and that remain valid. The morning plan's prescriptions are authoritative — they are listed here so this v2 plan is self-contained for status review.

### P3-A — Enable repo-level Code Scanning (morning P0-B, F-2026-05-20-2)

- **What.** Single API PATCH against `/repos/Manzela/AutonomousAgent/code-scanning/default-setup` to set `state=configured`.
- **Status.** Unchanged from morning. Still actionable in 30 seconds.
- **Where.** Morning audit-plan §P0-B, lines 34-49. Capture evidence in `audit/2026-05-20-state-of-the-repo/code-scanning-enable-evidence.json`.
- **Effort.** XS.
- **Acceptance.** CodeQL job on next main push returns green.
- **Recommend timing.** After PR #112 lands (so CodeQL doesn't first-run on the large IaC diff).

### P3-B — Branch protection flips (morning P0-C, F-2026-05-20-3, F-2026-05-20-4)

- **What.** Flip `enforce_admins: true` (#102) and decide on `required_approving_review_count: 1` (#103, with the solo-operator caveat documented in morning P0-C).
- **Status.** Unchanged. Solo-operator constraint on #103 stands.
- **Where.** Morning audit-plan §P0-C, lines 50-66.
- **Effort.** S.
- **Recommend timing.** After Task 38 (cutover) is complete and the operational rhythm is stable — flipping mid-phase locks the operator out of emergency hotfixes.

### P3-C — Required signatures + GPG inventory (morning P2-A, F-2026-05-20-5)

- **What.** Inventory contributors with merge rights, collect GPG keys, register, then flip `required_signatures: true`.
- **Status.** Unchanged. Calendar-blocked (human coordination).
- **Where.** Morning audit-plan §P2-A, lines 118-125.
- **Effort.** L → XS.

### P3-D — Stale branch cleanup (morning P1-C, F-2026-05-20-7)

- **What.** Pass-2 of morning audit verified 0 of the 17 stale branches actually exist on disk or remote — an unrecorded cleanup happened. Only `main` + `origin/main` + `feat/phase-0a-gcp-migration` remain (this branch).
- **Status.** Effectively complete. No action.
- **Where.** Morning audit-plan §P1-C, line 97-101.
- **Effort.** None.

### P3-E — Extended snapshot scope (issue #110)

- **What.** Honcho session export + Phoenix sqlite bundle into the daily tar.
- **Status.** Unchanged. Defer to post-Phase-0a (after #94 closes).
- **Where.** Morning audit-plan §P1-B, lines 84-95.
- **Effort.** M.

### P3-F — Reconcile Phase 0a spec §4 + §12 with deployed reality (NEW in pass 2)

- **What.** 5-min doc edit to `docs/superpowers/specs/2026-05-20-phase-0a-gcp-always-online-design.md` resolving OQ-1 and OQ-2 to match what's actually deployed.
- **Why.** F-2026-05-20-V2-8. Spec §4 still cites GCP project `rx-research-autonomousagent`; the IaC + deployed resources use `i-for-ai`. Spec §12 cites WIF pool `manzela-autonomousagent`; the IaC + deployed pool is `autonomousagent-github` (pool) / `autonomousagent-actions` (provider). The spec also defaults OQ-3 (WIF condition specificity) without noting that pass-2 inspection of `terraform/phase-0a-gcp/wif.tf:45` revealed `attribute_condition = "attribute.repository == \"Manzela/AutonomousAgent\""` — which accepts **any branch** of the repo. That is the deliberate intent per Task 30 (deploys from main + PR previews) but should be explicit in §12 so future auditors don't flag it as over-broad.
- **Where.** `docs/superpowers/specs/2026-05-20-phase-0a-gcp-always-online-design.md` — three find-replace edits:
  - §4 / OQ-1: `rx-research-autonomousagent` → `i-for-ai`
  - §12 / OQ-2: `manzela-autonomousagent` → `autonomousagent-github` (pool) and `autonomousagent-actions` (provider)
  - §12 / OQ-3: add 1-line note: "WIF attribute condition is `attribute.repository == \"Manzela/AutonomousAgent\"` — accepts all branches. Branch-level gating happens in the workflow (`if: github.ref == 'refs/heads/main'`)."
- **Effort.** XS — 5 minutes.
- **Acceptance.** Spec matches IaC and deployed state. Future audits don't trip on the doc drift.
- **Depends on.** Nothing. Run anytime.

---

## §Execution order summary

```
P0-A (gitleaks patch — broadened)    ← 2 min, unblocks PR #112
  ↓
P0-B (issue #94 comment)             ← 3 min
  ↓
P0-C (hermes-provider doc note)      ← 2 min (REVISED — no escalation needed per F-V2-4)
  ↓
P0-D (terraform state reconcile)     ← 45-90 min (NEW — gates all P1-B)
  ↓
P1-A (SM coverage: 3 singletons)     ← 20 min (REFINED — 3 not 5)
  ↓
P1-B (Task 16 VM Terraform)          ← 60 min
  ↓
P1-C (Tasks 17+18 scripts)           ← 105 min
  ↓
P1-D (Tasks 19+20 systemd)           ← 30 min
  ↓
P1-E (Tasks 21+22 watchdog)          ← 60 min
  ↓
P1-F (Task 23 GCP override)          ← 30 min
  ↓                                                ↓
P2-A (Tasks 24-26 SM mig.)           ← 85 min     P2-B (Tasks 27-29 monitoring) ← 110 min
                                  ↓
                          P2-C (Tasks 30-33 CI/scripts) ← 185 min
                                  ↓
                          P2-D (Tasks 34-38 cutover)    ← 4-6 hours + 24h soak
```

P3 items run in parallel — no blocking dependencies on P0/P1/P2 work — but recommend:
- Defer P3-B and P3-C until after P2-D so emergency hotfixes during cutover aren't blocked by branch-protection.
- Run P3-F (5-min spec edit) opportunistically — no dependency, can land in the same PR as P0-D's state reconciliation since both fix doc-vs-reality drift.

---

## §What this plan does NOT cover

- **Brainstorms paused mid-session.** The Claude/Gemini GCP orchestration design (Section 1 presented twice, awaiting approval) and the Hermes 10-component architecture research are both still paused. Neither blocks the Phase 0a work — they are forward-looking design exercises that would land in `docs/superpowers/specs/` once approved. Recommend resuming after P2-D so design work doesn't compete with the cutover.
- **Sub-projects not in Phase 0a spec §11.** Anything outside the 10 acceptance criteria (e.g. moving Phoenix to managed Cloud Run, splitting the monolithic compose into per-service Cloud Run revisions, swapping LiteLLM proxy for Vertex AI native) is out of scope. Address in a Phase 0b spec after Phase 0a closes.
- **Hermes RCA bisection.** Plan Tasks 2/3/4 in the Phase 0a plan (hypothesis tests) are not in this v2 audit-plan because there is nothing to bisect (F-2026-05-20-V2-1 NON-REPRODUCTION). If the silent exit recurs, those tasks become P0 immediately — but speculative work on them now would not change the migration outcome.

---

## §Changes from pass 1

Pass 2 dispatched 5 parallel operations: 3 `Explore` subagents (SOPS coverage / OQ + WIF / exit-137 evidence), 1 background Gemini delegation (GCP pre-flight against live `i-for-ai`), 1 inline gitleaks SARIF re-scan. Six material changes to this plan:

1. **P0-A allowlist broadened.** Pass-1 prescribed `audit/.*/p0a-rca/.*\.log$` (2 morning RCA log false-positives). Pass-2 gitleaks re-scan after writing v2 audit files revealed 15 leaks total, 5 of them inside `audit/2026-05-20-state-of-the-repo-v2/{findings,audit-plan}.md` (the v2 docs quote pattern strings for forensic clarity). Allowlist now `audit/.*\.(md|log)$` covering the entire audit tree.

2. **P0-C dissolved as a verification step, replaced with a doc note.** Pass-2 Explore against `scripts/decrypt-secrets.sh:65-73` resolved F-V2-4: `hermes-provider.env` is a derived secret, generated at bootstrap from `litellm-master-key`. No `.sops` source needed, no SM migration line. The remaining work is a 3-line note in `secrets/README.md`.

3. **P0-D added (Terraform state reconciliation).** Pass-2 Gemini pre-flight discovered the IaC for Phase 0a has been **partially applied off-branch** to live `i-for-ai`: VPC + 4 SM env-file secrets + AR repo + 2 GCS buckets + 2 SAs + snapshot policy all exist (only the VM is missing). Code-state divergence is real. Without `terraform import` + reconciliation, the first `terraform apply` will fail or duplicate. This becomes a hard gate before P1-B.

4. **P1-A scope refined from 5 singletons to 2 production-critical + 1 host-side.** Pass-2 Explore agent A inspected each singleton's consumer: `chroma-token` is dead code (Chroma Cloud migration), `honcho-db-password` is disabled (Honcho commented out), `healthchecks-url` is host-side cron (not container). The 2 production-critical singletons are `github-pat` (github-mcp) and `litellm-master-key` (litellm-proxy + derives hermes-provider.env). `healthchecks-url` migrates to SM but is consumed by the hermes-watchdog systemd timer via load-secrets.sh.

5. **P3-F added (spec reconciliation).** Pass-2 Explore agent B exposed OQ-1/OQ-2 doc drift: spec §4 cites `rx-research-autonomousagent` but IaC + deployed state uses `i-for-ai`; spec §12 cites `manzela-autonomousagent` pool but IaC uses `autonomousagent-github` / `autonomousagent-actions`. Also surfaced that `wif.tf:45` has `attribute_condition = "attribute.repository == \"Manzela/AutonomousAgent\""` — accepts all branches, not just `main`. 5-min spec edit.

6. **Risk register updates in `findings.md` §5:** R-IaC-drift (medium, NEW), R-WIF-broad (low, NEW), R-Spec-drift (low, NEW), R-SM-coverage (LOWERED — only 2 singletons actually need migration), R-PR112 widened (covers 15 leaks not 2). R-RCA-blind unchanged (F-V2-9 confirmed exit-137 signature unrecoverable from persisted artifacts on macOS Docker Desktop).

**What pass 2 did NOT change:** the P1-B through P2-D Phase 0a Phase D/E/F task structure is intact. Tasks 16–38 in `docs/superpowers/plans/2026-05-20-phase-0a-gcp-migration.md` remain authoritative for the per-task spec; this plan continues to reference them by line range without re-authoring. The brainstorm pauses (Claude/Gemini orchestration design, Hermes 10-component architecture) remain paused — neither blocks Phase 0a execution, but neither has user-approved design either.
