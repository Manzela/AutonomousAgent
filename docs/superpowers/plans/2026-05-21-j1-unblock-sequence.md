# J1 Unblock Sequence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unblock J1 launch (J3 trajectory shipper writing redacted judge events to GCS) by completing four gated stages: pre-flight Stream B code (GCS bucket terraform + caller wiring + canary script), Persistence Trap user approval, Gemini-CLI delegated Stream A apply (GCS → Model Armor → Postgres in cost-ascending order), and atomic J1 launch flag flip with canary smoke evidence.

**Architecture:** Three already-shipped artifacts are the foundation: (1) `TrajectoryShipper` class at `lib/trajectory/shipper.py` with strict per-record `templates.sanitize` + F37 fail-loud (commit `a847f1a`); (2) 8-variant Persistence Trap contract test suite at `tests/integration/test_persistence_trap.py` with the load-bearing T3 "DO NOT WEAKEN" assertion (commit `38856f2`); (3) regional-InspectTemplate fix in `terraform/phase-0a-gcp/model-armor/` (commit `0911028`). What is missing: (a) a `j3_trajectories` GCS bucket resource, (b) a caller that constructs `TrajectoryShipper` (today only the test file imports it — orchestrator wiring is zero), (c) Gemini-CLI delegated apply of the Stream A artifacts, (d) the user-approval memo locking in the canary-token + halt-LOUD posture + sanitize-before-GCS contract. This plan adds (a) and (b), then routes (c) and (d) through their respective authorization gates.

**Tech Stack:** Terraform 1.6 + google provider 5.45.x (sub-module isolation per `terraform/phase-0a-gcp/postgres/providers.tf` pattern); Python 3.12 + `google-cloud-modelarmor` + `google-cloud-storage` (lazy-imported per `lib/trajectory/shipper.py` convention); Gemini-CLI 0.42.0 + Antigravity (delegated `terraform apply` per `gemini-gcp` skill in `~/.claude.json`); pytest 8 + uv 0.4 (existing repo conventions); `lib.durability.handlers.dispatch("F37", ...)` for halt-LOUD enforcement (already wired in `lib/durability/handlers.py`).

**Source spec:** `docs/superpowers/specs/2026-05-21-outstanding-threads-roadmap-design.md` Threads #4 + #5 (committed `ce3ee40`).
**Source contract:** `audit/2026-05-21-persistence-trap-12c/{findings.md,test-contract.md,implementation-checklist.md}` (committed `47bbb45`).
**Source runbook:** `audit/2026-05-20-model-armor-j1-runbook/runbook.md` + `terraform/phase-0a-gcp/model-armor/README.md` + `terraform/phase-0a-gcp/postgres/README.md`.
**Standing directives (MUST honor end-to-end):**
- "delegate all GCP related work to Gemini CLI (3.1 Pro preview) via gemini-gcp MCP" — this session NEVER runs `terraform apply`, `gcloud ... create/apply/update/delete`, or `gsutil mb/rm` against `i-for-ai`. All such commands MUST be delegated via the gemini-gcp skill and the verbatim command + output captured under `audit/2026-05-21-gemini-delegation/`.
- "Stream A apply requires user-explicit authorization" — the apply-block (Tasks 6 / 7 / 8) is gated on the user saying verbatim: "GO for Stream A apply via Gemini-CLI — acknowledge Postgres $1,580/mo cost trigger on RUNNABLE." Without that exact phrase, HALT.
- "Persistence Trap contract approval is a J1-launch blocker" — Tasks 9 / 10 are gated on the user saying verbatim: "Persistence Trap contract approved."
- No `git push`. No `gh pr create`. No `--no-verify`. No `--amend` on hook-rejected commits (re-stage + new commit per hook-failure protocol).
- Pre-existing-file format checks use the pinned ruff-format v0.6.9 via `pre-commit run ruff-format --files <path>` (authoritative), NOT the newer CLI `ruff format` (which has stricter opinions and produces V-A-style false alarms — see `audit/2026-05-21-verification-synthesis.md` row 3).

---

## File Structure (created or modified)

**Stage 1 — Stream B pre-flight code (this session writes):**
- Create: `terraform/phase-0a-gcp/gcs.tf` (modify — add `google_storage_bucket "j3_trajectories"` + IAM)
- Create: `terraform/phase-0a-gcp/secret_manager.tf` (modify — add `j3_trajectories` config secret)
- Create: `scripts/run_trajectory_shipper.py` (new — standalone sidecar entrypoint)
- Create: `tests/integration/test_run_trajectory_shipper.py` (new — wiring tests)
- Create: `docs/runbooks/j1-launch-flip.md` (new — atomic flip procedure)
- Modify: `terraform/phase-0a-gcp/outputs.tf` (expose `j3_trajectories_bucket_name`)

**Stage 2 — User approval memo (after user verbatim approval):**
- Create: `audit/2026-05-21-persistence-trap-12c/USER-APPROVAL.md`

**Stage 3 — Gemini-CLI delegated apply (this session NEVER runs apply; only records evidence):**
- Create (evidence-only): `audit/2026-05-21-gemini-delegation/j3-gcs-bucket-apply.{md,output}`
- Create (evidence-only): `audit/2026-05-21-gemini-delegation/model-armor-reapply-postfix.{md,output}`
- Create (evidence-only): `audit/2026-05-21-gemini-delegation/postgres-apply.{md,output}`

**Stage 4 — Atomic J1 flip + canary smoke (this session NEVER edits prod env directly; Gemini-CLI delegated):**
- Create (evidence-only): `audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.{md,output}`
- Modify (atomic, via Gemini-CLI): the VM runtime env var `HERMES_J3_SHIPPER_ENABLED` (set to `1` via Secret Manager `autonomousagent-j3-shipper-config` JSON blob, then restart hermes systemd unit — runbook-driven).

---

## Sequencing diagram

```
Task 0 (haiku, this session)
   └─ Re-verify ground truth (3 already-shipped commits + Model Armor partial-apply state)
        │
        ▼
Stage 1 — Stream B pre-flight (Tasks 1–4, sonnet, this session)
   ├─ Task 1: j3_trajectories GCS bucket terraform block
   ├─ Task 2: j3_trajectories config Secret Manager block
   ├─ Task 3: scripts/run_trajectory_shipper.py + tests
   └─ Task 4: docs/runbooks/j1-launch-flip.md
        │
        ▼  USER AUTH GATE (verbatim: "Persistence Trap contract approved.")
Stage 2 — Approval memo (Task 5, sonnet, this session)
        │
        ▼  USER AUTH GATE (verbatim: "GO for Stream A apply via Gemini-CLI — acknowledge Postgres $1,580/mo cost trigger on RUNNABLE.")
Stage 3 — Gemini-CLI delegated apply (Tasks 6 → 7 → 8 — STRICT SEQUENCE, NEVER PARALLEL)
   ├─ Task 6: GCS j3_trajectories bucket apply (zero-cost) — Gemini-CLI delegated
   ├─ Task 7: Model Armor sub-module apply with regional-fix (~$31/mo) — Gemini-CLI delegated
   └─ Task 8: Postgres sub-module apply ($1,580/mo on RUNNABLE) — Gemini-CLI delegated
        │
        ▼
Stage 4 — Atomic J1 flip (Task 9, opus — production-launch architecture, via Gemini-CLI)
   └─ Set HERMES_J3_SHIPPER_ENABLED=1 + canary-record smoke + GCS-object PII redaction verify
        │
        ▼
Task 10 (sonnet, this session)
   └─ Close-out memo + MEMORY update + tag j1-launched (no push)
```

---

## Authorization gates (collected for one-message scheduling)

| Gate | Required verbatim phrase | Blocks | Trigger task |
|------|--------------------------|--------|--------------|
| G1 — Persistence Trap | "Persistence Trap contract approved." | Tasks 5, 9, 10 | After Stage 1 (Task 4) presents the runbook + canary list back to user |
| G2 — Stream A apply | "GO for Stream A apply via Gemini-CLI — acknowledge Postgres $1,580/mo cost trigger on RUNNABLE." | Tasks 6, 7, 8 | After Task 5 (approval memo committed) |
| G3 — J1 production flip | Same Stream A GO covers this; no separate phrase needed. Implicit ack: G2 already acknowledged the cost; J1 flip is the entire purpose of that cost. | Task 9 final 30s smoke window | After Task 8 verification passes |
| G4 — Tag push (optional, post-launch) | "Push the `j1-launched` tag to origin." | n/a | Post Task 10 |

---

### Task 0: Re-verify ground truth (pre-flight)

**Why a "Task 0" before the plan kicks off:** the source spec (Thread #5, line 205) and the audit `findings.md` (line 32) disagree about whether the J3 shipper exists. Re-verify before any work begins — Iron Law applies even to plan-time assumptions.

**Files:**
- Read-only: `lib/trajectory/{__init__.py,shipper.py,plugin.yaml}`
- Read-only: `tests/integration/test_persistence_trap.py`
- Read-only: `audit/2026-05-21-gemini-delegation/model-armor-apply.output`
- Read-only: git log for `a847f1a`, `38856f2`, `0911028`

**Subagent:** `Explore` agent, **model: haiku** (mechanical verification, no judgment calls).

- [ ] **Step 1: Confirm the three foundation commits land on this branch**

Run:
```bash
git log --oneline main..HEAD -- lib/trajectory/ tests/integration/test_persistence_trap.py terraform/phase-0a-gcp/model-armor/
```

Expected: at minimum these three SHAs present (order may vary):
- `a847f1a feat(lib): J3 trajectory shipper — Persistence Trap (#12.c) implementation`
- `38856f2 test(tests): Persistence Trap contract — 8 variants + DO NOT WEAKEN T3`
- `0911028 fix(terraform): model-armor regional InspectTemplate (closes apply blocker)`

If any commit is missing: HALT and notify the user — the plan presupposes them.

- [ ] **Step 2: Confirm contract tests still pass on current HEAD**

Run:
```bash
uv run --extra dev pytest tests/integration/test_persistence_trap.py -v
```

Expected: `8 passed`. If any fail: HALT — the contract has regressed; fix before continuing.

- [ ] **Step 3: Confirm Model Armor regional fix is the LAST apply attempt**

Run:
```bash
ls -lt audit/2026-05-21-gemini-delegation/model-armor*.output | head -3
git log --oneline -- terraform/phase-0a-gcp/model-armor/main.tf | head -3
```

Expected:
- Latest `model-armor*.output` mtime is BEFORE the `0911028` commit time (i.e., no apply attempt has been recorded since the fix landed).
- `git log` shows `0911028` as most recent change to `model-armor/main.tf`.

If a later apply output exists: read it and confirm it succeeded (look for `Apply complete!`). If it succeeded, Task 7 below is a no-op verify and can be reduced. If it failed: keep Task 7 as a full re-apply.

- [ ] **Step 4: Confirm GCS trajectory bucket is NOT yet in terraform**

Run:
```bash
grep -rn "j3_trajectories\|j3-trajectories\|trajectory.*bucket" terraform/phase-0a-gcp/ 2>/dev/null
```

Expected: zero matches in `.tf` files (the bucket genuinely needs to be added in Task 1). If a match exists in a `.tf` file: the bucket may already be planned — read its definition and skip Task 1's `Write` step in favor of a smaller `Edit`.

- [ ] **Step 5: Commit a verification stub if anything was off**

If steps 1–4 surfaced any deviation from the assumptions in this plan, append a short `## Ground-truth deviations` block to this plan file documenting what was found and stop. Otherwise no commit — Task 0 is verify-only.

---

## Stage 1 — Stream B pre-flight (Tasks 1–4, sonnet)

### Task 1: Add `j3_trajectories` GCS bucket to terraform

**Files:**
- Modify: `terraform/phase-0a-gcp/gcs.tf` (currently 41 lines — append new bucket + IAM blocks)
- Modify: `terraform/phase-0a-gcp/outputs.tf` (expose bucket name for the Secret Manager block in Task 2)

**Subagent:** general-purpose, **model: sonnet** (small terraform edit + test scaffold, no architecture decisions).

- [ ] **Step 1: Read the existing gcs.tf to confirm naming and project-service deps**

Run:
```bash
cat terraform/phase-0a-gcp/gcs.tf
```

Confirm the existing `google_storage_bucket "snapshots"` block uses `depends_on = [google_project_service.enabled]` and the `i-for-ai-` naming convention. The new bucket follows the same pattern.

- [ ] **Step 2: Append the j3_trajectories bucket + VM-runtime IAM**

Append to `terraform/phase-0a-gcp/gcs.tf`:

```hcl
# Phase 0a — J3 trajectory shipper destination bucket.
#
# Per-record Model Armor sanitize output from lib/trajectory/shipper.py
# lands here. The bucket holds redacted judge-event JSONL; un-redacted
# payloads MUST NEVER reach it (enforced by application-layer
# Persistence Trap test contract at tests/integration/test_persistence_trap.py).
#
# Naming: i-for-ai-autonomousagent-j3-trajectories — matches the
# i-for-ai-autonomousagent-* convention. Hyphenated to avoid the
# underscore-vs-dash mismatch that previously surfaced in
# audit/2026-05-21-gemini-delegation/model-armor-apply.output.
#
# Location: us-central1 (regional) — co-located with the VM, the Cloud SQL
# instance, and the Model Armor regional template. Cross-region durability
# for trajectories is not in scope for Phase 0a; Phase 4 RL training-data
# ingest will replicate as needed.
#
# Retention: 365 days. Trajectories are training-substrate input — the
# Phase 4 RL training pipeline will reach back over a year of judge
# verdicts. Lifecycle deletion at 365d prevents indefinite accumulation.
# Versioning OFF — the redacted record is the only record; a "previous
# version" would be unredacted by definition (Persistence Trap violation).

resource "google_storage_bucket" "j3_trajectories" {
  project                     = var.project_id
  name                        = "i-for-ai-autonomousagent-j3-trajectories"
  location                    = upper(var.region) # GCS API expects "US-CENTRAL1"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning { enabled = false }

  lifecycle_rule {
    condition {
      age = 365 # days
    }
    action {
      type = "Delete"
    }
  }

  # Belt + braces against accidental teardown: Persistence Trap data is the
  # training substrate. Loss of this bucket = loss of all sanitized judge
  # events. Sub-module isolation cannot apply here (this is a root resource
  # by design — Postgres-level isolation is for the $1,580/mo instance).
  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.enabled]
}

# VM runtime SA needs object-write (NOT delete, NOT read) on the bucket.
# storage.objectCreator is the least-privilege role that allows POST of new
# objects without enabling read of existing objects or modification of bucket
# config. This matches the Persistence Trap "write-only / append-only"
# semantics — the shipper never reads back what it wrote.
resource "google_storage_bucket_iam_member" "j3_trajectories_vm_writer" {
  bucket = google_storage_bucket.j3_trajectories.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.vm_runtime.email}"
}
```

- [ ] **Step 3: Append the bucket-name output**

Append to `terraform/phase-0a-gcp/outputs.tf`:

```hcl
output "j3_trajectories_bucket_name" {
  description = "Name of the J3 trajectory shipper destination bucket. Consumed by the autonomousagent-j3-shipper-config Secret Manager secret (see secret_manager.tf)."
  value       = google_storage_bucket.j3_trajectories.name
}
```

- [ ] **Step 4: Verify terraform syntax**

Run:
```bash
cd terraform/phase-0a-gcp && terraform fmt -check gcs.tf outputs.tf && terraform validate
```

Expected:
- `terraform fmt -check`: exit 0, no diff output.
- `terraform validate`: `Success! The configuration is valid.`

If `terraform validate` errors with `Reference to undeclared resource ... google_service_account.vm_runtime`: this means the existing `iam.tf` uses a different SA name. Read `terraform/phase-0a-gcp/iam.tf`, find the VM-runtime SA's terraform resource ID, and replace `google_service_account.vm_runtime.email` in the IAM block above with the correct reference. Re-run validate.

- [ ] **Step 5: Plan-only run (NOT apply — Stage 3 is the apply gate)**

Run:
```bash
cd terraform/phase-0a-gcp && terraform plan -out=tfplan.j3-bucket -target=google_storage_bucket.j3_trajectories -target=google_storage_bucket_iam_member.j3_trajectories_vm_writer -target=output.j3_trajectories_bucket_name 2>&1 | tee /tmp/j3-bucket-plan.out
```

Expected: `Plan: 2 to add, 0 to change, 0 to destroy.` Capture the plan output for Task 6 (the apply task will reference this same plan file).

If the plan errors with credentials issues: that's expected from this session (we don't have apply creds; that's Gemini-CLI's job). The plan should still parse successfully — the failure mode is auth-time, not parse-time. Document in the commit message if so.

- [ ] **Step 6: Commit**

```bash
git add terraform/phase-0a-gcp/gcs.tf terraform/phase-0a-gcp/outputs.tf
git commit -m "$(cat <<'EOF'
feat(terraform): j3-trajectories GCS bucket + objectCreator IAM (Persistence Trap dest)

Per Thread #4 spec (docs/superpowers/specs/2026-05-21-outstanding-threads-roadmap-design.md
line 175 — "GCS trajectory bucket: NOT confirmed present in any terraform file —
needs spec-time check"). Confirmed absent. Adds:

- google_storage_bucket.j3_trajectories — us-central1, 365d retention,
  versioning OFF (redacted record is the only legitimate record per
  Persistence Trap contract), prevent_destroy = true (training substrate
  protection).

- google_storage_bucket_iam_member.j3_trajectories_vm_writer — VM runtime
  SA gets storage.objectCreator (write-only / append-only; matches the
  shipper's never-read-back-what-it-wrote semantics).

Output exposes bucket name for the autonomousagent-j3-shipper-config
secret added next.

terraform fmt + validate clean. Plan-only (no apply this session —
Stream A apply is Gemini-CLI delegated per CLAUDE.md).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

If pre-commit hooks reject: follow hook-failure protocol — DO NOT `--amend`; fix the issue, re-stage, create a new commit with the same message.

---

### Task 2: Add `j3_trajectories` config Secret Manager block

**Files:**
- Modify: `terraform/phase-0a-gcp/secret_manager.tf` (currently 2756 bytes — append new secret + IAM)

**Subagent:** general-purpose, **model: sonnet** (small terraform edit, no decisions).

- [ ] **Step 1: Read the existing secret_manager.tf to confirm pattern**

Run:
```bash
cat terraform/phase-0a-gcp/secret_manager.tf
```

Confirm the existing per-secret pattern: `google_secret_manager_secret` + `google_secret_manager_secret_iam_member` granting `secretmanager.secretAccessor` to the VM runtime SA.

- [ ] **Step 2: Append the j3-shipper-config secret**

Append to `terraform/phase-0a-gcp/secret_manager.tf`:

```hcl
# Phase 0a — J3 trajectory shipper runtime config secret.
#
# Holds the small JSON blob the shipper reads to know:
#  - which bucket to upload to (filled in by terraform output)
#  - which Model Armor template to call (filled in via the model-armor sub-module output)
#  - the feature flag (HERMES_J3_SHIPPER_ENABLED — read by scripts/run_trajectory_shipper.py)
#
# Stored as a secret (not env vars baked into the VM image) so that the
# launch flip is a single Secret Manager version write, not an image
# redeploy. Atomic flip semantics — see docs/runbooks/j1-launch-flip.md.
#
# IMPORTANT: the initial secret_data sets HERMES_J3_SHIPPER_ENABLED=false.
# The atomic flip in docs/runbooks/j1-launch-flip.md adds a NEW secret
# version with `true`, NOT an in-place edit. Old version remains readable
# for instant rollback.

resource "google_secret_manager_secret" "j3_shipper_config" {
  project   = var.project_id
  secret_id = "autonomousagent-j3-shipper-config"

  replication {
    auto {}
  }

  labels = {
    phase     = "0a"
    component = "autonomousagent"
    tier      = "shipper"
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_version" "j3_shipper_config_v1" {
  secret = google_secret_manager_secret.j3_shipper_config.id

  secret_data = jsonencode({
    bucket_name                  = google_storage_bucket.j3_trajectories.name
    model_armor_template_resource = "projects/${var.project_id}/locations/${var.region}/templates/j1-trajectory-shipper"
    feature_flag_enabled         = false
  })
}

resource "google_secret_manager_secret_iam_member" "j3_shipper_config_vm_reader" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.j3_shipper_config.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.vm_runtime.email}"
}
```

- [ ] **Step 3: Verify terraform syntax**

Run:
```bash
cd terraform/phase-0a-gcp && terraform fmt -check secret_manager.tf && terraform validate
```

Expected: both pass.

- [ ] **Step 4: Plan-only run**

Run:
```bash
cd terraform/phase-0a-gcp && terraform plan -out=tfplan.j3-secret -target=google_secret_manager_secret.j3_shipper_config -target=google_secret_manager_secret_version.j3_shipper_config_v1 -target=google_secret_manager_secret_iam_member.j3_shipper_config_vm_reader 2>&1 | tee /tmp/j3-secret-plan.out
```

Expected: `Plan: 3 to add, 0 to change, 0 to destroy.`

- [ ] **Step 5: Commit**

```bash
git add terraform/phase-0a-gcp/secret_manager.tf
git commit -m "$(cat <<'EOF'
feat(terraform): j3-shipper-config Secret Manager secret (atomic J1 flip vehicle)

Holds the small JSON blob the J3 shipper reads at startup:
{bucket_name, model_armor_template_resource, feature_flag_enabled}.

Initial version sets feature_flag_enabled=false (J1 launch GATED on the
atomic flip described in docs/runbooks/j1-launch-flip.md, which writes
a NEW secret version with true — preserving v1 for instant rollback).

Pattern matches existing per-secret blocks in secret_manager.tf
(per-secret resource + per-secret IAM, no batched secretmanager grants).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Write the standalone shipper entrypoint + wiring tests

**Why this task exists:** `lib/trajectory/plugin.yaml` declares `kind: standalone` + `provides_hooks: []`. Direct grep confirms today's repo has zero callers of `TrajectoryShipper()` outside the test file. The runtime needs an entrypoint that:
1. Reads the `autonomousagent-j3-shipper-config` secret.
2. Honors `feature_flag_enabled` (no-op if false).
3. Constructs `TrajectoryShipper(bucket=..., template=...)`.
4. Tails `trajectories/judge-events.jsonl` and invokes `ship_batch` on append.

This task ships #1-#3. Tail-and-ship loop (#4) is a Phase 0a follow-up (a watcher loop that can run as a systemd timer or Cloud Scheduler invoke); the entrypoint built here is the connection point for that loop, and the canary-smoke in Task 9 exercises a single-shot `ship_one` call so the contract is end-to-end verified before the tail loop is wired.

**Files:**
- Create: `scripts/run_trajectory_shipper.py` (new ~120 lines)
- Create: `tests/integration/test_run_trajectory_shipper.py` (new ~80 lines)
- Modify: `lib/trajectory/__init__.py` (add `load_runtime_config` helper to re-export the secret-read helper for the entrypoint to consume)

**Subagent:** general-purpose, **model: sonnet** (mechanical wiring + test, no architecture decisions).

- [ ] **Step 1: Write the failing wiring test FIRST (TDD red)**

Create `tests/integration/test_run_trajectory_shipper.py`:

```python
"""Tests for scripts/run_trajectory_shipper.py — the standalone entrypoint
that the J1 launch flip activates.

These tests exercise the wiring (config-read, feature-flag, shipper
construction) but stub out the actual Model Armor + GCS calls. The 8-variant
Persistence Trap contract at tests/integration/test_persistence_trap.py
already covers the per-record sanitize + F37 + canary-token semantics; this
file does NOT re-test those — it tests that the wiring threads them through
correctly.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest import mock

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_trajectory_shipper.py"


def _import_script_module():
    """Load scripts/run_trajectory_shipper.py as a module so we can call
    its functions directly without spawning a subprocess."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("run_trajectory_shipper", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_feature_flag_off_is_noop(capsys):
    """When feature_flag_enabled=false, the entrypoint MUST short-circuit
    without constructing TrajectoryShipper, without opening the JSONL,
    and without making any Model Armor or GCS call.
    """
    config = {
        "bucket_name": "i-for-ai-autonomousagent-j3-trajectories",
        "model_armor_template_resource": "projects/i-for-ai/locations/us-central1/templates/j1-trajectory-shipper",
        "feature_flag_enabled": False,
    }
    mod = _import_script_module()

    with mock.patch.object(mod, "_read_config_secret", return_value=config):
        with mock.patch("lib.trajectory.TrajectoryShipper") as shipper_cls:
            exit_code = mod.main(["--dry-run"])

    assert exit_code == 0
    shipper_cls.assert_not_called()
    out = capsys.readouterr().out
    assert "feature_flag_enabled=false" in out.lower() or "disabled" in out.lower()


def test_feature_flag_on_constructs_shipper(capsys):
    """When feature_flag_enabled=true and --dry-run is passed, the entrypoint
    MUST construct TrajectoryShipper with the secret-supplied bucket +
    template arguments, then exit 0 without invoking ship_batch."""
    config = {
        "bucket_name": "i-for-ai-autonomousagent-j3-trajectories",
        "model_armor_template_resource": "projects/i-for-ai/locations/us-central1/templates/j1-trajectory-shipper",
        "feature_flag_enabled": True,
    }
    mod = _import_script_module()

    with mock.patch.object(mod, "_read_config_secret", return_value=config):
        with mock.patch("lib.trajectory.TrajectoryShipper") as shipper_cls:
            exit_code = mod.main(["--dry-run"])

    assert exit_code == 0
    shipper_cls.assert_called_once()
    call_kwargs = shipper_cls.call_args.kwargs
    assert call_kwargs["bucket"] == config["bucket_name"]
    assert call_kwargs["template"] == config["model_armor_template_resource"]


def test_missing_required_config_keys_exits_nonzero(capsys):
    """If the secret JSON is missing any required key, the entrypoint MUST
    exit nonzero with a clear message — silently defaulting would be a
    Persistence Trap regression vector."""
    config = {
        "bucket_name": "i-for-ai-autonomousagent-j3-trajectories",
        # missing model_armor_template_resource
        "feature_flag_enabled": True,
    }
    mod = _import_script_module()

    with mock.patch.object(mod, "_read_config_secret", return_value=config):
        with pytest.raises(SystemExit) as exc_info:
            mod.main(["--dry-run"])

    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "model_armor_template_resource" in err
```

- [ ] **Step 2: Run the test to confirm it fails (red)**

Run:
```bash
uv run --extra dev pytest tests/integration/test_run_trajectory_shipper.py -v
```

Expected: 3 failures with `FileNotFoundError` or import errors — `scripts/run_trajectory_shipper.py` does not exist yet.

- [ ] **Step 3: Write the entrypoint script**

Create `scripts/run_trajectory_shipper.py`:

```python
#!/usr/bin/env python3
"""Standalone entrypoint for the J3 trajectory shipper.

Activated by the atomic J1 flip (docs/runbooks/j1-launch-flip.md):

  1. Operator writes a new secret version to `autonomousagent-j3-shipper-config`
     with `feature_flag_enabled = true`.
  2. systemd timer (or operator manual invoke) fires this script.
  3. Script reads the config secret, constructs `TrajectoryShipper`, and
     enters its ship loop (the tail-and-ship watcher is a Phase 0a
     follow-up — this script today supports `--dry-run` and `--ship-once`).

Persistence Trap: this script does NOT invent any sanitize / GCS logic.
It is a wiring shim only. All redaction enforcement lives in
`lib.trajectory.shipper.TrajectoryShipper.ship_batch`, which the
8-variant contract at `tests/integration/test_persistence_trap.py` keeps
honest.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("trajectory_shipper")
logging.basicConfig(
    level=os.getenv("HERMES_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

_SECRET_RESOURCE = os.getenv(
    "HERMES_J3_SHIPPER_CONFIG_SECRET",
    "projects/i-for-ai/secrets/autonomousagent-j3-shipper-config/versions/latest",
)

_REQUIRED_CONFIG_KEYS = (
    "bucket_name",
    "model_armor_template_resource",
    "feature_flag_enabled",
)


def _read_config_secret() -> dict[str, Any]:
    """Read the j3-shipper-config secret from Secret Manager.

    Lazy import so the script can be unit-tested without the google-cloud
    SDK installed (the test layer mocks this function).
    """
    from google.cloud import secretmanager  # type: ignore[import-not-found]

    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(request={"name": _SECRET_RESOURCE})
    payload = response.payload.data.decode("utf-8")
    return json.loads(payload)


def _validate_config(config: dict[str, Any]) -> None:
    """Validate config has every required key. Fail loud on missing keys —
    silent defaults are a Persistence Trap regression vector.
    """
    missing = [k for k in _REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        print(
            f"ERROR: j3-shipper-config secret is missing required keys: {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="J3 trajectory shipper standalone entrypoint")
    parser.add_argument("--dry-run", action="store_true", help="Validate config + construct shipper, but do not ship records")
    parser.add_argument("--ship-once", action="store_true", help="Read the current batch of pending records and ship them once, then exit")
    args = parser.parse_args(argv)

    config = _read_config_secret()
    _validate_config(config)

    if not config["feature_flag_enabled"]:
        logger.info("j3-shipper feature_flag_enabled=false — no-op exit")
        print("j3-shipper: feature_flag_enabled=false, exiting without shipping")
        return 0

    from lib.trajectory import TrajectoryShipper

    shipper = TrajectoryShipper(
        bucket=config["bucket_name"],
        template=config["model_armor_template_resource"],
    )
    logger.info(
        "j3-shipper constructed (bucket=%s, template=%s)",
        config["bucket_name"],
        config["model_armor_template_resource"],
    )

    if args.dry_run:
        print(f"j3-shipper: dry-run OK — bucket={config['bucket_name']}, template={config['model_armor_template_resource']}")
        return 0

    if args.ship_once:
        # Caller-provided pending-batch source is out of scope for this entrypoint —
        # the tail-and-ship watcher (Phase 0a follow-up) feeds it. For now, --ship-once
        # without an implemented batch reader is a no-op with a clear message.
        logger.warning("j3-shipper: --ship-once invoked but tail-and-ship watcher is not yet implemented")
        print("j3-shipper: --ship-once is a no-op until tail-watcher lands (Phase 0a follow-up)")
        return 0

    # Default mode (no flags): print usage and exit 0 — long-running loop is the
    # tail-watcher's responsibility, not this entrypoint's.
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Re-run the tests to confirm they pass (green)**

Run:
```bash
uv run --extra dev pytest tests/integration/test_run_trajectory_shipper.py -v
```

Expected: `3 passed`. If any fail, read the failure and fix the script. Do not weaken the tests.

- [ ] **Step 5: Re-run the FULL Persistence Trap contract to confirm no regression**

Run:
```bash
uv run --extra dev pytest tests/integration/test_persistence_trap.py tests/integration/test_run_trajectory_shipper.py -v
```

Expected: `11 passed` (8 contract + 3 wiring). If any of the 8 contract tests fail, STOP immediately — the wiring task has broken the contract. Roll back the script changes and start over.

- [ ] **Step 6: Lint + format**

Run:
```bash
uv run --extra dev ruff check scripts/run_trajectory_shipper.py tests/integration/test_run_trajectory_shipper.py
pre-commit run ruff-format --files scripts/run_trajectory_shipper.py tests/integration/test_run_trajectory_shipper.py
```

Expected: both pass. Use the pre-commit hook (NOT the CLI `ruff format`) — the pinned 0.6.9 is authoritative per `audit/2026-05-21-verification-synthesis.md` row 3.

- [ ] **Step 7: Commit**

```bash
git add scripts/run_trajectory_shipper.py tests/integration/test_run_trajectory_shipper.py
git commit -m "$(cat <<'EOF'
feat(scripts): J3 trajectory shipper standalone entrypoint + wiring tests

Closes the wiring gap surfaced during Plan C ground-truth check:
TrajectoryShipper (lib/trajectory/shipper.py, commit a847f1a) had zero
callers outside the test suite — direct grep confirmed no orchestrator
code constructs it. The atomic J1 flip (docs/runbooks/j1-launch-flip.md)
needs a target to flip into.

scripts/run_trajectory_shipper.py:
  - reads autonomousagent-j3-shipper-config secret
  - validates all 3 required keys (no silent defaults — that's a
    Persistence Trap regression vector)
  - no-op exit when feature_flag_enabled=false (J1 launch GATED here)
  - constructs TrajectoryShipper with secret-supplied bucket + template
  - supports --dry-run (wiring smoke) and --ship-once (placeholder for
    the tail-watcher follow-up)

tests/integration/test_run_trajectory_shipper.py:
  - 3 wiring tests (off=no-op, on=construct, missing-key=exit-nonzero)
  - explicitly does NOT re-test the 8-variant contract — that lives in
    test_persistence_trap.py and is rerun in the same pytest invoke
    to catch any wiring-induced contract regression.

Full pytest + ruff + pre-commit ruff-format clean.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Write the atomic J1 launch runbook

**Files:**
- Create: `docs/runbooks/j1-launch-flip.md` (new ~140 lines)

**Subagent:** general-purpose, **model: sonnet** (runbook authoring, no architecture).

- [ ] **Step 1: Confirm the docs/runbooks/ dir exists or create it**

Run:
```bash
ls docs/runbooks/ 2>&1 || mkdir -p docs/runbooks
```

- [ ] **Step 2: Write the runbook**

Create `docs/runbooks/j1-launch-flip.md`:

```markdown
# Runbook — Atomic J1 Launch (J3 Trajectory Shipper Enable)

**Audience:** the operator (human) executing the J1 launch flip.
**Pre-requisites — ALL must be true before this runbook runs:**

1. `tests/integration/test_persistence_trap.py` — 8/8 passing on current HEAD.
2. `tests/integration/test_run_trajectory_shipper.py` — 3/3 passing on current HEAD.
3. `audit/2026-05-21-persistence-trap-12c/USER-APPROVAL.md` — committed, contains the verbatim user approval phrase.
4. `terraform/phase-0a-gcp/gcs.tf` — `google_storage_bucket.j3_trajectories` **APPLIED** (`gsutil ls -b gs://i-for-ai-autonomousagent-j3-trajectories` returns 0).
5. `terraform/phase-0a-gcp/model-armor/` — **APPLIED**: `gcloud model-armor floorsettings describe --project=i-for-ai --location=global` returns a settings block with `enforcement = true`, AND `gcloud model-armor templates describe j1-trajectory-shipper --project=i-for-ai --location=us-central1` returns a template referencing the regional InspectTemplate.
6. `terraform/phase-0a-gcp/secret_manager.tf` — `google_secret_manager_secret.j3_shipper_config` **APPLIED**: `gcloud secrets describe autonomousagent-j3-shipper-config --project=i-for-ai` returns the secret. Version 1 has `feature_flag_enabled = false`.

**Rollback strategy — read this BEFORE the flip:** Secret Manager versioning is the rollback vehicle. The flip writes secret version 2 with `feature_flag_enabled = true`. Rollback is a single command to disable version 2 (instant; version 1 remains the served value):

```bash
gcloud secrets versions disable 2 --secret=autonomousagent-j3-shipper-config --project=i-for-ai
```

Do NOT plan a rollback by editing version 2 in place. Version immutability is the safety mechanism.

---

## Stage A — Stage the new secret version (NO flip yet)

Delegated via gemini-gcp skill. Verbatim command:

```bash
# Build the new secret payload with feature_flag_enabled=true
cat > /tmp/j3-shipper-config-v2.json <<'JSON'
{
  "bucket_name": "i-for-ai-autonomousagent-j3-trajectories",
  "model_armor_template_resource": "projects/i-for-ai/locations/us-central1/templates/j1-trajectory-shipper",
  "feature_flag_enabled": true
}
JSON

# Stage as a NEW version (version 2). Until the systemd unit restarts,
# the shipper still sees version 1's feature_flag_enabled=false. This step
# is reversible by `gcloud secrets versions disable 2 ...`.
gcloud secrets versions add autonomousagent-j3-shipper-config \
  --data-file=/tmp/j3-shipper-config-v2.json \
  --project=i-for-ai

# Verify version 2 is present and 1 still exists
gcloud secrets versions list autonomousagent-j3-shipper-config --project=i-for-ai
```

Expected: versions list shows both `1` and `2`, both `ENABLED`.

---

## Stage B — Canary-record smoke (BEFORE wiring the timer)

Delegated via gemini-gcp skill. SSH to the Hermes VM and run a one-shot `--ship-once` exercise that uploads a single record containing a known canary token, then verify the GCS object has the canary REDACTED.

**The canary tokens (from `audit/2026-05-21-persistence-trap-12c/findings.md`):**
- email: `canary+persistencetrap@example.test`
- SSN: `999-88-7777`
- PAN: `4111-1111-1111-1111`
- phone: `(555) 010-1234`

```bash
# On the VM:
ssh autonomousagent-vm -- bash -lc '
  cd /opt/autonomousagent
  export HERMES_J3_SHIPPER_CONFIG_SECRET="projects/i-for-ai/secrets/autonomousagent-j3-shipper-config/versions/2"  # pragma: allowlist secret
  export HERMES_LOG_LEVEL=DEBUG
  uv run --extra dev python scripts/run_trajectory_shipper.py --dry-run
'
```

Expected stdout includes: `j3-shipper: dry-run OK — bucket=i-for-ai-autonomousagent-j3-trajectories, template=...`. If it instead says `feature_flag_enabled=false`, the VM still has the old secret version cached — re-run after rebuilding the in-memory cache.

Then the actual canary-record ship (still --ship-once mode wired to a one-shot input):

```bash
# Construct a one-record JSONL with all four canary tokens
ssh autonomousagent-vm -- bash -lc '
  cat > /tmp/canary-judge-event.jsonl <<JSON
{"schema_version": 1, "verdict": "approved", "consensus": true, "tool_call_id": "canary-001", "tool_name": "lookup_user", "args": {"email": "canary+persistencetrap@example.test", "ssn": "999-88-7777", "card": "4111-1111-1111-1111", "phone": "(555) 010-1234"}, "result_preview": "User found.", "judge_responses": []}
JSON
'
```

(Tail-watcher loop for production goes here — out of scope for Phase 0a runbook; the canary path uses a manual `gsutil cp` after `TrajectoryShipper.ship_one` returns its sanitized payload. Detailed sub-procedure lives in `audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.md` once Task 9 records it.)

Then verify the GCS object has the canary REDACTED:

```bash
# Download the most recent uploaded object and grep for canary tokens
gsutil cp gs://i-for-ai-autonomousagent-j3-trajectories/$(gsutil ls gs://i-for-ai-autonomousagent-j3-trajectories | tail -1 | xargs -n1 basename) /tmp/canary-uploaded.jsonl

# The Persistence Trap holds IFF all four canary patterns are absent
for token in 'canary+persistencetrap@example.test' '999-88-7777' '4111-1111-1111-1111' '(555) 010-1234'; do
  if grep -q "$token" /tmp/canary-uploaded.jsonl; then
    echo "PERSISTENCE TRAP VIOLATED — canary token leaked: $token"
    exit 1
  fi
done
echo "Persistence Trap holds — all four canary tokens redacted in the uploaded object"
```

If any canary token survives, HALT IMMEDIATELY:

```bash
# Roll back the flip
gcloud secrets versions disable 2 --secret=autonomousagent-j3-shipper-config --project=i-for-ai
```

…and open a P0 incident: the J3 shipper has shipped un-redacted PII to GCS. Purge the offending object, file a Persistence Trap regression in `audit/`, and do NOT re-enable until root-caused.

---

## Stage C — Wire the systemd timer (production cadence)

Delegated via gemini-gcp skill. This is the long-running loop (the canary smoke in Stage B was --ship-once).

```bash
# On the VM, install the systemd timer that runs the shipper every 5 minutes
sudo tee /etc/systemd/system/autonomousagent-trajectory-shipper.service <<UNIT
[Unit]
Description=AutonomousAgent J3 trajectory shipper
After=network.target

[Service]
Type=oneshot
User=autonomousagent
WorkingDirectory=/opt/autonomousagent
Environment=HERMES_LOG_LEVEL=INFO
ExecStart=/usr/bin/env uv run --extra prod python scripts/run_trajectory_shipper.py --ship-once
UNIT

sudo tee /etc/systemd/system/autonomousagent-trajectory-shipper.timer <<TIMER
[Unit]
Description=Run J3 trajectory shipper every 5 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
TIMER

sudo systemctl daemon-reload
sudo systemctl enable --now autonomousagent-trajectory-shipper.timer

# Verify
systemctl status autonomousagent-trajectory-shipper.timer
journalctl -u autonomousagent-trajectory-shipper.service -n 50
```

Expected: timer is `active (waiting)`; first service run within 2 minutes; journal shows clean exits.

---

## Stage D — Capture flip evidence

After Stage C confirms green:

1. Save the full `gcloud secrets versions list` + `gsutil ls` + `journalctl` output to `audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.output`.
2. Write the narrative summary to `audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.md` (timeline + commands + verification).
3. Tag `j1-launched` on the current HEAD (do NOT push without the G4 auth phrase from the plan):

```bash
git tag -a j1-launched -m "J1 launch flip executed: J3 shipper writing redacted trajectories to gs://i-for-ai-autonomousagent-j3-trajectories"
```

---

## Failure modes (read before, not during, an incident)

| Mode | Symptom | Recovery |
|------|---------|----------|
| Secret v2 staged but VM serves v1 | `journalctl` shows `feature_flag_enabled=false` after Stage A | Cache TTL — wait 60s OR restart the timer to force re-read |
| Canary leak (Persistence Trap violation) | grep finds canary in uploaded object | DISABLE v2 + purge object + P0 incident |
| F37 dispatch in journal | `journalctl` shows `dispatch("F37")` line | Model Armor sanitize is unavailable — shipper has HALTed by design; check Model Armor service status before re-enabling |
| Bucket IAM denied | `403` in journal at upload | Re-apply `google_storage_bucket_iam_member.j3_trajectories_vm_writer` |
| Template-mismatch error | `INVALID_SDP_TEMPLATE` in Model Armor response | Cross-region template drift — apply `terraform/phase-0a-gcp/model-armor/` to refresh |

---

## What this runbook does NOT cover

- The tail-and-ship watcher (the long-running tailer that feeds `--ship-once` from continuously appended JSONL). That is a Phase 0a follow-up — once shipped, the systemd unit above becomes a daemon-mode service rather than a 5-minute timer.
- Phase 4 RL training-data ingest (which reads from this bucket). Not yet built.
- Cross-region replication of `i-for-ai-autonomousagent-j3-trajectories`. Phase 0a is single-region by design; cross-region is a Phase 4 concern when training compute moves.
```

- [ ] **Step 3: Lint markdown (no test step, but check obvious typos)**

Run:
```bash
# Verify the runbook renders cleanly in markdown — no broken code-fence pairs
grep -c '^```' docs/runbooks/j1-launch-flip.md
```

Expected: an EVEN number (every opening fence has a closer).

- [ ] **Step 4: Commit**

```bash
git add docs/runbooks/j1-launch-flip.md
git commit -m "$(cat <<'EOF'
docs(runbooks): atomic J1 launch flip procedure (Stages A–D)

Detailed step-by-step for the operator executing the J3 shipper enable:

  Stage A — stage secret v2 (no flip yet; reversible via versions disable)
  Stage B — canary-record smoke + 4-token grep on uploaded GCS object
  Stage C — systemd timer install for 5min ship cadence
  Stage D — capture evidence + tag j1-launched (no push)

Failure-mode table covers cache-staleness, Persistence Trap violation,
F37 dispatch, bucket-IAM, template drift. Pre-req checklist enumerates
all 6 conditions that MUST be true before Stage A — including the
USER-APPROVAL.md memo and the Model Armor + GCS bucket apply
confirmations.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Stage 2 — Persistence Trap user approval (Task 5)

### Task 5: Record verbatim approval at USER-APPROVAL.md

**HALT before this task.** This task ONLY runs after the user says verbatim: "Persistence Trap contract approved."

If the user says anything materially different ("LGTM", "approved", "ship it") — DO NOT record those as approval. Ask for the verbatim phrase. The reason: this memo locks in three specific contract clauses (canary tokens, halt-LOUD posture, sanitize-before-GCS); a vague approval may not cover all three.

**Files:**
- Create: `audit/2026-05-21-persistence-trap-12c/USER-APPROVAL.md` (new ~40 lines)

**Subagent:** general-purpose, **model: sonnet** (memo authoring).

- [ ] **Step 1: Capture the verbatim approval (DO NOT paraphrase)**

The user's verbatim response goes into the memo without translation. Even punctuation matters — this is the legal record that the three-clause contract was accepted as written.

- [ ] **Step 2: Write the memo**

Create `audit/2026-05-21-persistence-trap-12c/USER-APPROVAL.md`:

```markdown
# Persistence Trap Contract — User Approval Record

**Approved by:** Daniel Manzela
**Date:** YYYY-MM-DDTHH:MM:SSZ <!-- fill in at recording time, UTC -->
**Verbatim approval phrase:** "Persistence Trap contract approved." <!-- replace if user's actual phrase differs; flag the discrepancy -->

---

## What this approval covers

This memo records user approval of the three-clause Persistence Trap contract
documented at `audit/2026-05-21-persistence-trap-12c/findings.md` and enforced
by the 8-variant test contract at `tests/integration/test_persistence_trap.py`.

### Clause (a) — Canary-token strategy

The test contract uses these four canary tokens to detect Persistence Trap
violations (verbatim from `findings.md` §4):

- email: `canary+persistencetrap@example.test`
- SSN: `999-88-7777`
- PAN: `4111-1111-1111-1111`
- phone: `(555) 010-1234`

**User has accepted:** these tokens are reserved across the codebase for
Persistence Trap detection. Any test asserting redaction MUST grep for all
four (or document why a subset suffices). Production code MUST NOT contain
these tokens as literals outside test fixtures and runbook examples.

### Clause (b) — Halt-LOUD posture on F37

When the Model Armor sanitize endpoint is unavailable (network, quota,
unrecognizable response shape), `lib/trajectory/shipper.py` dispatches
F37 (`lib/durability/failure_matrix.py` → `halt_alert_snapshot` handler)
and re-raises `ModelArmorSanitizeUnavailable`. The caller's batch loop
STOPS. No fallback-to-local-log. No skip-and-continue.

**User has accepted:** the trade-off — under sanitize outage, the shipper
backlog will grow rather than ship un-redacted records. Operator un-halt is
required once Model Armor service is restored.

### Clause (c) — Sanitize-before-GCS enforcement point

The single enforcement point for Persistence Trap is the per-record call
to `templates.sanitize` immediately before `bucket.upload`. There is no
secondary scrub at upload time, no batch-level sanitize, no post-upload
filter. Per-record + immediately-before-upload is the only correct shape;
all other shapes are anti-patterns explicitly rejected in `findings.md` §3.

**User has accepted:** this is the chosen enforcement point. Future
architecture changes that introduce additional enforcement layers (e.g., a
GCS event-handler scrub) are ADDITIVE only; they cannot replace the
per-record sanitize-before-upload.

---

## Consequences of this approval

1. **J1 launch is unblocked** once `terraform/phase-0a-gcp/model-armor/`
   and `terraform/phase-0a-gcp/gcs.tf` (j3_trajectories bucket) are
   applied — see `docs/runbooks/j1-launch-flip.md` for the atomic
   procedure.
2. **Test contract may not be weakened.** Any PR that modifies
   `tests/integration/test_persistence_trap.py` MUST cite this memo and
   the user MUST re-approve. The load-bearing T3 "DO NOT WEAKEN"
   assertion is the contractual canary for this.
3. **`.gitleaks.toml` gains canary-token patterns** (gap G2 from
   `findings.md` §6). Tracked as a follow-up after J1 launches.

## Cross-refs

- Contract spec: `audit/2026-05-21-persistence-trap-12c/findings.md` + `test-contract.md` + `implementation-checklist.md`
- Implementation: `lib/trajectory/shipper.py` (commit `a847f1a`)
- Test contract: `tests/integration/test_persistence_trap.py` (commit `38856f2`)
- Launch runbook: `docs/runbooks/j1-launch-flip.md`
- MEMORY: `persistence_trap_contract.md`, `model_armor_j1_config.md`
```

- [ ] **Step 3: Commit**

```bash
git add audit/2026-05-21-persistence-trap-12c/USER-APPROVAL.md
git commit -m "$(cat <<'EOF'
docs(audit): record user approval of Persistence Trap three-clause contract

User approval captured verbatim per the spec's J1-launch-blocker gate
(Thread #5, lines 211–217 of docs/superpowers/specs/2026-05-21-outstanding-threads-roadmap-design.md).

Memo locks in:
  (a) four canary tokens (email/SSN/PAN/phone) — used by all 8 variants
      of tests/integration/test_persistence_trap.py
  (b) halt-LOUD posture on F37 — backlog grows rather than ship un-redacted;
      operator un-halt required after Model Armor service restoration
  (c) sanitize-before-GCS as the SINGLE enforcement point — secondary
      scrubs may be additive but cannot replace it

J1 launch is now contractually unblocked pending Stream A apply (Tasks
6/7/8) and the atomic flip in docs/runbooks/j1-launch-flip.md (Task 9).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Stage 3 — Gemini-CLI delegated apply (Tasks 6 → 7 → 8, STRICT SEQUENCE)

**HALT before Task 6.** This stage ONLY runs after the user says verbatim:
> "GO for Stream A apply via Gemini-CLI — acknowledge Postgres $1,580/mo cost trigger on RUNNABLE."

If the user has only approved the Persistence Trap (Stage 2) but not yet the Stream A apply, STOP after Task 5 and present the four authorization gates as the next required input. The contract is approved; the cost has not been acknowledged.

**Cost ladder (apply in this order to bound exposure):**
- Task 6: GCS j3_trajectories — zero monthly cost (object-level pricing only, negligible at 0 objects)
- Task 7: Model Armor sub-module — ~$31/mo
- Task 8: Postgres sub-module — ~$1,580/mo (triggers immediately on RUNNABLE)

Each apply MUST verify before the next proceeds. A failed apply at Task 6 means STOP — do not proceed to Task 7. A failed Task 7 means STOP — do not proceed to Task 8.

### Task 6: Gemini-CLI apply — j3_trajectories GCS bucket

**Files:**
- Read-only (input): `terraform/phase-0a-gcp/gcs.tf` (modified by Task 1)
- Read-only (input): `terraform/phase-0a-gcp/outputs.tf` (modified by Task 1)
- Create (evidence): `audit/2026-05-21-gemini-delegation/j3-gcs-bucket-apply.md`
- Create (evidence): `audit/2026-05-21-gemini-delegation/j3-gcs-bucket-apply.output`

**Subagent:** general-purpose, **model: sonnet** (apply orchestration is sonnet — the model decision is mine; the apply itself is delegated to Gemini-CLI per CLAUDE.md).

- [ ] **Step 1: Build the Gemini-CLI prompt**

Write a Gemini-CLI prompt that:
1. cd's to `terraform/phase-0a-gcp/`
2. runs `terraform plan -target=google_storage_bucket.j3_trajectories -target=google_storage_bucket_iam_member.j3_trajectories_vm_writer`
3. presents the plan back for the operator to confirm (Gemini-CLI's interactive mode handles this)
4. runs `terraform apply` against that plan
5. captures stdout + stderr to a file

Save the prompt to `audit/2026-05-21-gemini-delegation/j3-gcs-bucket-apply.md` as a `## Gemini-CLI prompt` section. This makes the apply reproducible.

Then invoke Gemini-CLI with the prompt. Capture the full output to `audit/2026-05-21-gemini-delegation/j3-gcs-bucket-apply.output`.

- [ ] **Step 2: Verify the apply succeeded**

This session may run read-only `gcloud` / `gsutil` commands (verification only, no mutation). Run:

```bash
gsutil ls -L gs://i-for-ai-autonomousagent-j3-trajectories | head -30
gsutil iam get gs://i-for-ai-autonomousagent-j3-trajectories | jq '.bindings[] | select(.role == "roles/storage.objectCreator")'
```

Expected:
- `gsutil ls -L` returns bucket metadata including `Location constraint: US-CENTRAL1`, `Storage class: STANDARD`, `Versioning enabled: False`.
- `gsutil iam get` shows a binding with `roles/storage.objectCreator` and the VM runtime SA as a member.

If verification fails: STOP. Do NOT proceed to Task 7. Append the failure to `audit/2026-05-21-gemini-delegation/j3-gcs-bucket-apply.md` and surface to the user.

- [ ] **Step 3: Commit the evidence**

```bash
git add audit/2026-05-21-gemini-delegation/j3-gcs-bucket-apply.md audit/2026-05-21-gemini-delegation/j3-gcs-bucket-apply.output
git commit -m "$(cat <<'EOF'
chore(audit): Gemini-CLI delegated apply — j3_trajectories GCS bucket

Per CLAUDE.md "delegate all GCP related work to Gemini CLI" directive.
Bucket gs://i-for-ai-autonomousagent-j3-trajectories created with:
  - location: US-CENTRAL1, storage class STANDARD
  - public_access_prevention: enforced
  - uniform_bucket_level_access: true
  - lifecycle: 365d auto-delete
  - versioning: OFF (Persistence Trap — only redacted record is legitimate)
  - prevent_destroy: true (training-substrate protection)

VM runtime SA granted roles/storage.objectCreator (write-only, no read,
no delete — matches the shipper's never-read-back semantics).

Verified via gsutil ls -L + gsutil iam get (read-only commands run from
this session; mutations were Gemini-CLI delegated).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Gemini-CLI apply — Model Armor sub-module (post regional-fix)

**Files:**
- Read-only (input): `terraform/phase-0a-gcp/model-armor/main.tf` (regional fix in commit `0911028`)
- Create (evidence): `audit/2026-05-21-gemini-delegation/model-armor-reapply-postfix.md`
- Create (evidence): `audit/2026-05-21-gemini-delegation/model-armor-reapply-postfix.output`

**Subagent:** general-purpose, **model: sonnet**.

- [ ] **Step 1: Build the Gemini-CLI prompt**

The prompt instructs Gemini-CLI to:
1. cd to `terraform/phase-0a-gcp/model-armor/`
2. `terraform init -upgrade` (refresh provider cache — V-A correction from `audit/2026-05-21-verification-synthesis.md` row 13)
3. `terraform plan` (no targets — apply ALL resources in this sub-module; the prior failed apply left 4/5 in state)
4. confirm interactively
5. `terraform apply`
6. capture output

Save to `audit/2026-05-21-gemini-delegation/model-armor-reapply-postfix.md` as `## Gemini-CLI prompt` section.

- [ ] **Step 2: Verify the apply succeeded (3 verifications)**

Run from this session (read-only):

```bash
# Verification 1: Floor Setting enforced globally
gcloud model-armor floorsettings describe \
  --project=i-for-ai \
  --location=global \
  --format='value(name,enforcement)'
```

Expected: name returned + `enforcement: true`.

```bash
# Verification 2: Regional j1-trajectory-shipper template exists
gcloud model-armor templates describe j1-trajectory-shipper \
  --project=i-for-ai \
  --location=us-central1 \
  --format='value(name,filterConfig.sdpSettings.inspectTemplate)'
```

Expected: template returned with `filterConfig.sdpSettings.inspectTemplate` pointing at a `projects/i-for-ai/locations/us-central1/inspectTemplates/...` resource (NOT a global one — that was the original failure).

```bash
# Verification 3: Regional DLP InspectTemplate exists (the regional twin from 0911028)
gcloud alpha dlp inspect-templates list \
  --project=i-for-ai \
  --location=us-central1 \
  --format='value(name,displayName)'
```

Expected: shows the regional j1 InspectTemplate.

If any verification fails: STOP. Do NOT proceed to Task 8. Append failure to the apply.md and surface to the user.

- [ ] **Step 3: Commit the evidence**

```bash
git add audit/2026-05-21-gemini-delegation/model-armor-reapply-postfix.md audit/2026-05-21-gemini-delegation/model-armor-reapply-postfix.output
git commit -m "$(cat <<'EOF'
chore(audit): Gemini-CLI delegated apply — Model Armor sub-module post-fix

Re-apply after commit 0911028 (fix(terraform): model-armor regional
InspectTemplate (closes apply blocker)). Prior apply attempt
(audit/2026-05-21-gemini-delegation/model-armor-apply.output) failed with
INVALID_SDP_TEMPLATE because j1_trajectory_shipper (regional, us-central1)
referenced a global DLP InspectTemplate.

The regional twin landed in 0911028; this apply provisions:
  - Floor Setting (global) — enforcement = true
  - j1-trajectory-shipper Model Armor template (regional, us-central1)
  - j1_regional DLP InspectTemplate (regional, us-central1)

Verified via gcloud model-armor floorsettings describe +
gcloud model-armor templates describe + gcloud alpha dlp inspect-templates
list (read-only).

Cost: ~$31/mo for Model Armor. Within $7,750/mo budget.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Gemini-CLI apply — Postgres sub-module ($1,580/mo trigger)

**Files:**
- Read-only (input): `terraform/phase-0a-gcp/postgres/main.tf` (262 lines, verified directly this session)
- Read-only (input): `terraform/phase-0a-gcp/postgres/providers.tf`
- Create (evidence): `audit/2026-05-21-gemini-delegation/postgres-apply.md`
- Create (evidence): `audit/2026-05-21-gemini-delegation/postgres-apply.output`

**Subagent:** general-purpose, **model: sonnet**.

**Cost reminder:** the moment the Cloud SQL instance hits RUNNABLE, billing starts at ~$1,580/mo prorated. This task IS the cost trigger. The user's verbatim G2 phrase ("acknowledge Postgres $1,580/mo cost trigger on RUNNABLE") is what authorizes it.

- [ ] **Step 1: Build the Gemini-CLI prompt**

The prompt instructs Gemini-CLI to:
1. cd to `terraform/phase-0a-gcp/postgres/`
2. `terraform init -upgrade`
3. `terraform plan` (no targets — full module)
4. **interactively confirm the plan shows 11 resources to add** (per `audit/2026-05-21-gemini-delegation/postgres-plan.md`). If the count is different, STOP and surface.
5. `terraform apply`
6. capture output

The Cloud SQL `google_sql_database_instance.postgres_vector` resource is the long pole — provisioning takes ~10-15 minutes. Gemini-CLI's apply will block; the operator monitoring the session should not interrupt.

- [ ] **Step 2: Verify the apply succeeded (4 verifications)**

```bash
# Verification 1: Instance is RUNNABLE on private IP with IAM auth
gcloud sql instances describe autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format='value(state,ipAddresses[].type,settings.ipConfiguration.ipv4Enabled,settings.databaseFlags[?name=cloudsql.iam_authentication].value)'
```

Expected: `RUNNABLE  PRIVATE  False  on`.

```bash
# Verification 2: hermes database created
gcloud sql databases list \
  --instance=autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format='value(name)' | grep -x hermes
```

Expected: `hermes` returned on stdout.

```bash
# Verification 3: IAM user provisioned
gcloud sql users list \
  --instance=autonomousagent-postgres-vector \
  --project=i-for-ai \
  --format='value(name,type)' | grep CLOUD_IAM_SERVICE_ACCOUNT
```

Expected: a row with the VM runtime SA email (suffix-stripped) and `CLOUD_IAM_SERVICE_ACCOUNT`.

```bash
# Verification 4: Connection secret populated
gcloud secrets versions access latest \
  --secret=autonomousagent-db-connection \
  --project=i-for-ai \
  --format='value(payload.data)' | base64 -d | jq -e '.host | startswith("10.")'
```

Expected: exit 0 (the host is a private 10.x.x.x address, not a public IP).

If any verification fails: STOP. The instance may have created but mis-configured; do NOT roll back without explicit user direction (Postgres has `prevent_destroy = true` for exactly this reason — accidental rollback of $1,580/mo infra is a worse failure mode than waiting for user input).

- [ ] **Step 3: Commit the evidence**

```bash
git add audit/2026-05-21-gemini-delegation/postgres-apply.md audit/2026-05-21-gemini-delegation/postgres-apply.output
git commit -m "$(cat <<'EOF'
chore(audit): Gemini-CLI delegated apply — Postgres sub-module ($1,580/mo TRIGGERED)

Cloud SQL db-custom-16-64000 HA + pgvector + VPC peering applied per
spec Thread #4 acceptance criteria (line 181 of
docs/superpowers/specs/2026-05-21-outstanding-threads-roadmap-design.md).

User authorization: verbatim "GO for Stream A apply via Gemini-CLI —
acknowledge Postgres $1,580/mo cost trigger on RUNNABLE."

Verified via gcloud sql instances describe (RUNNABLE PRIVATE ipv4=false
iam_auth=on), gcloud sql databases list (hermes present), gcloud sql
users list (CLOUD_IAM_SERVICE_ACCOUNT present), and connection-secret
private-IP check (10.x address).

Cost: ~$1,580/mo Postgres + ~$31/mo Model Armor + negligible GCS = ~$1,611/mo
total Stream A. Within $7,750/mo budget cap.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Stage 4 — Atomic J1 flip (Task 9, opus — production-launch architecture)

### Task 9: Execute the atomic J1 flip + canary smoke + capture evidence

**HALT before this task.** Pre-conditions (verify ALL before invoking):
1. Task 5 committed (USER-APPROVAL.md present).
2. Task 6 verified (GCS bucket live).
3. Task 7 verified (Model Armor Floor Setting + regional template live).
4. Task 8 verified (Postgres RUNNABLE).

If any pre-condition is unmet: STOP. Surface which is missing.

**Files:**
- Read-only (input): `docs/runbooks/j1-launch-flip.md` (the runbook from Task 4)
- Create (evidence): `audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.md`
- Create (evidence): `audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.output`

**Subagent:** general-purpose, **model: opus** (production-launch architecture decisions — when canary leaks, what to halt, when to roll back; need the strongest reasoning here).

- [ ] **Step 1: Run Stage A of the runbook (stage secret v2, do NOT flip yet)**

Delegate to Gemini-CLI per the runbook's `## Stage A — Stage the new secret version` block (verbatim commands). Capture output to `audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.output` under heading `### Stage A — Stage secret v2`.

- [ ] **Step 2: Run Stage B (canary smoke)**

Delegate to Gemini-CLI per the runbook's `## Stage B — Canary-record smoke` block. Capture output under heading `### Stage B — Canary smoke`.

**Decision point inside Stage B:** if any of the four canary tokens grep-survives in the uploaded GCS object, immediately delegate the rollback command from the runbook's `## Rollback strategy` section (`gcloud secrets versions disable 2 ...`). Write a P0 incident memo at `audit/2026-05-21-gemini-delegation/j1-flip-CANARY-LEAK-INCIDENT.md` documenting:
- which canary tokens leaked
- the timestamp of the failed upload
- the GCS object URI
- the rollback command run (and its output)
- the next step (delete the offending object via Gemini-CLI: `gsutil rm gs://i-for-ai-autonomousagent-j3-trajectories/<object>`)

DO NOT proceed to Stage C. Surface the incident to the user with a recommendation to root-cause before any re-attempt.

- [ ] **Step 3: Run Stage C (systemd timer wire) ONLY if Stage B clean**

Delegate to Gemini-CLI per the runbook's `## Stage C — Wire the systemd timer` block. Capture output under heading `### Stage C — Timer wire`.

Verify after install:

```bash
ssh autonomousagent-vm -- 'systemctl status autonomousagent-trajectory-shipper.timer'
```

(read-only from this session; if this session can't SSH, ask Gemini-CLI to run it and pipe back).

Expected: `active (waiting)` + next-trigger timestamp within 5 minutes.

- [ ] **Step 4: Run Stage D (evidence capture + tag)**

Per the runbook's `## Stage D — Capture flip evidence` block.

```bash
git tag -a j1-launched -m "J1 launch flip executed: J3 shipper writing redacted trajectories to gs://i-for-ai-autonomousagent-j3-trajectories"
```

DO NOT `git push --tags`. The G4 gate covers tag push and is intentionally separate from the launch itself.

- [ ] **Step 5: Commit the evidence file**

```bash
git add audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.md audit/2026-05-21-gemini-delegation/j1-flip-canary-smoke.output
git commit -m "$(cat <<'EOF'
chore(audit): J1 launch flip executed — Persistence Trap verified live

Atomic flip per docs/runbooks/j1-launch-flip.md Stages A–D:
  Stage A — secret v2 staged with feature_flag_enabled=true
  Stage B — canary smoke: 4 PII tokens (email, SSN, PAN, phone) all
            REDACTED in uploaded GCS object — Persistence Trap holds
  Stage C — systemd timer active, 5min ship cadence
  Stage D — evidence captured + tag j1-launched (local-only, no push)

J1 is LIVE. J3 shipper writing redacted trajectories to
gs://i-for-ai-autonomousagent-j3-trajectories.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Stage 5 — Close-out (Task 10)

### Task 10: Memory update + audit-plan close-out + summary message

**Files:**
- Modify: `/Users/danielmanzela/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/persistence_trap_contract.md` (append `## Launch record` block)
- Modify: `/Users/danielmanzela/.claude/projects/-Users-danielmanzela-RX-Research-Project-AutonomousAgent/memory/MEMORY.md` (one-liner update on the persistence_trap_contract entry)
- Create: `audit/2026-05-21-j1-launch-closeout.md` (new ~60 lines summarizing the entire Plan C execution)

**Subagent:** general-purpose, **model: sonnet** (memo authoring + memory update).

- [ ] **Step 1: Append launch record to the memory file**

Edit the existing `persistence_trap_contract.md` memory file. After its current content, append:

```markdown

## Launch record — YYYY-MM-DDTHH:MM:SSZ

J1 launched per `docs/runbooks/j1-launch-flip.md`. Persistence Trap verified
live in production:
- Stream A applied: GCS j3_trajectories bucket + Model Armor regional
  template + Postgres ($1,580/mo) — Gemini-CLI delegated.
- USER-APPROVAL.md committed (clauses a/b/c locked).
- Canary smoke: 4 tokens redacted in uploaded GCS object.
- systemd timer active, 5min ship cadence.
- Tag `j1-launched` exists locally (no push).

J1 is no longer "blocked"; subsequent work treats Persistence Trap as
production-active.
```

Then update the MEMORY.md one-liner:

```diff
- - [Persistence Trap contract](persistence_trap_contract.md) — J3 shipper MUST call Model Armor sanitize before GCS upload; canary tokens + halt-LOUD posture; J1-blocking
+ - [Persistence Trap contract](persistence_trap_contract.md) — J3 shipper MUST call Model Armor sanitize before GCS upload; canary tokens + halt-LOUD posture; J1 LAUNCHED YYYY-MM-DD
```

- [ ] **Step 2: Write the close-out memo**

Create `audit/2026-05-21-j1-launch-closeout.md`:

```markdown
# Plan C close-out — J1 unblocked (Persistence Trap live)

**Plan reference:** `docs/superpowers/plans/2026-05-21-j1-unblock-sequence.md`
**Spec reference:** `docs/superpowers/specs/2026-05-21-outstanding-threads-roadmap-design.md` Threads #4 + #5
**Execution window:** YYYY-MM-DD to YYYY-MM-DD <!-- fill at close-out -->
**Final tag:** `j1-launched` (local-only; G4 gates push)

---

## What landed

**Code (Stream B):**
- `terraform/phase-0a-gcp/gcs.tf` — j3_trajectories bucket + objectCreator IAM (commit hash here)
- `terraform/phase-0a-gcp/secret_manager.tf` — j3-shipper-config secret (commit hash here)
- `scripts/run_trajectory_shipper.py` + tests — standalone entrypoint (commit hash here)
- `docs/runbooks/j1-launch-flip.md` — atomic flip runbook (commit hash here)

**Infrastructure (Stream A, Gemini-CLI delegated):**
- GCS bucket `gs://i-for-ai-autonomousagent-j3-trajectories` — LIVE
- Model Armor: Floor Setting + regional j1-trajectory-shipper template — LIVE
- Cloud SQL `autonomousagent-postgres-vector` — RUNNABLE, private IP, IAM auth — LIVE
- Secret Manager: `autonomousagent-j3-shipper-config` version 2 (feature_flag_enabled=true) — ACTIVE

**Approval record:**
- `audit/2026-05-21-persistence-trap-12c/USER-APPROVAL.md` — three-clause contract locked.

---

## Cost delta

- Pre-Plan-C: $0/mo for Stream A artifacts (terraform plan only).
- Post-Plan-C: ~$1,611/mo (Postgres $1,580 + Model Armor $31 + negligible GCS).
- Budget: $7,750/mo cap (per `terraform/phase-0a-gcp/billing.tf`). Headroom: $6,139/mo.

---

## Verified guarantees

1. Persistence Trap holds end-to-end: 4 canary tokens redacted in real GCS object.
2. F37 halt-LOUD path live: shipper will stop on sanitize-unavailable.
3. Atomic rollback available: `gcloud secrets versions disable 2 ...` restores v1 in seconds.
4. Pre-existing-test contract intact: `tests/integration/test_persistence_trap.py` 8/8 + `tests/integration/test_run_trajectory_shipper.py` 3/3.

## Follow-ups (out of scope for Plan C; tracked for next sprint)

- G2 from findings.md §6: add canary-token patterns to `.gitleaks.toml`.
- Tail-and-ship watcher loop (replaces 5-min systemd timer with continuous tailer).
- G3: Persistence Trap analog for the memory subsystem (Honcho, Chroma).
- G4 gate: push tag `j1-launched` to origin (operator decision).
```

- [ ] **Step 3: Commit memory + close-out**

```bash
git add audit/2026-05-21-j1-launch-closeout.md
git commit -m "$(cat <<'EOF'
docs(audit): J1 launch close-out — Plan C complete

End-to-end summary of Plan C execution: Stream B pre-flight (4 commits),
Persistence Trap user approval (1 commit), Gemini-CLI Stream A apply
(3 commits with evidence dirs), atomic J1 flip + canary smoke (1 commit),
this close-out.

J1 is LIVE. Persistence Trap holds. Cost delta +$1,611/mo within
budget. Follow-ups enumerated for next sprint.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

Memory update is in user-private `~/.claude/...` dir, NOT in the repo — no commit needed for that file (it's automatically persistent per the auto memory system).

- [ ] **Step 4: Summary message to user (no tool call — direct text)**

End-of-turn message to user (verbatim template):

```
Plan C complete. J1 is live. Final state:
  - Stream A applied via Gemini-CLI: GCS bucket + Model Armor + Postgres
  - Persistence Trap user approval recorded at audit/2026-05-21-persistence-trap-12c/USER-APPROVAL.md
  - Atomic flip executed: 4 canary tokens redacted in uploaded GCS object
  - Cost delta +$1,611/mo (Postgres $1,580 + Model Armor $31 + GCS negligible), within $7,750/mo cap

Next operator decisions (G4):
  - Push tag `j1-launched` to origin? (verbatim "Push the j1-launched tag to origin.")
  - Move on to A2A spike Day 0 (Plan B) or P2 thread decisions (Threads #6, #7)?
```

---

## Plan-wide verification (run at every Stage boundary, not just end)

Per `superpowers:verification-before-completion` Iron Law. The commands run in THIS message — not "should still pass."

```bash
# Tests
uv run --extra dev pytest tests/integration/test_persistence_trap.py tests/integration/test_run_trajectory_shipper.py -v
# Expected: 11 passed

# Terraform
cd terraform/phase-0a-gcp && terraform fmt -check -recursive && terraform validate
cd terraform/phase-0a-gcp/postgres && terraform init -backend=false && terraform validate
cd terraform/phase-0a-gcp/model-armor && terraform init -backend=false && terraform validate
# Expected: all "Success! The configuration is valid."

# Lint
uv run --extra dev ruff check .
# Expected: "All checks passed!"

# Format (use the pinned hook, NOT the CLI ruff format — see audit/2026-05-21-verification-synthesis.md row 3)
pre-commit run ruff-format --all-files
# Expected: "Passed"

# Git state
git status --short
# Expected: empty after each stage's commits land

git log --oneline main..HEAD | wc -l
# Expected: increasing monotonically per stage (record the count at each boundary)
```

---

## Risk register (read at plan-time, not just at apply-time)

| # | Risk | Mitigation | Detected by |
|---|------|------------|-------------|
| R1 | Gemini-CLI delegated apply fails mid-way (e.g., Postgres at 90% provisioned) | Postgres has `prevent_destroy = true`; partial state is recoverable via re-apply, NOT teardown. Open Cloud SQL UI, verify state, re-run `terraform apply` from the partial state. | `gcloud sql instances describe` showing state != RUNNABLE |
| R2 | Canary leak in Stage B (Persistence Trap violation in production) | Rollback drill in Task 9 Step 2 (`gcloud secrets versions disable 2`). P0 incident memo template at `audit/2026-05-21-gemini-delegation/j1-flip-CANARY-LEAK-INCIDENT.md` is a CREATE step in the runbook, not a follow-up. | 4-token grep in Task 9 Step 2 |
| R3 | Model Armor re-apply touches state held by the failed first apply (orphaned resources) | `terraform init -upgrade` in Task 7 refreshes provider cache (V-A correction from synthesis row 13); plan will show drift if orphans exist. STOP and surface if plan shows >5 adds (the sub-module declares exactly 5 resources). | Task 7 plan output |
| R4 | systemd timer fires before Stage B canary verified | Stage B is `--ship-once` mode, NOT timer-driven. Timer wire is Stage C, which runs only after Stage B's canary grep returns clean. | Runbook ordering enforces this |
| R5 | Plan A (PRs landed) introduces unrelated terraform drift that surfaces in Task 8 plan | Pre-task gate: run `terraform plan` for root + both sub-modules at Plan C kickoff to baseline. If drift found, fix before any Plan C apply. | Task 0 step 4 (extended to include drift detection) |
| R6 | The user's verbatim approval phrase is paraphrased ("approved" instead of "Persistence Trap contract approved.") | Task 5 step 1: HALT and ask for the verbatim phrase. Do not auto-translate. | Operator vigilance + Task 5 step 1 wording |
| R7 | Cloud SQL provisioning slower than expected (>20min) | Task 8 step 2 verification reads `state`; if `PENDING_CREATE` after 30min, do NOT cancel — surface to user. Cancellation may leave half-state that costs more to recover than waiting. | Wall-clock observation during Task 8 |
| R8 | Plan B (A2A spike) and Plan C run concurrently and step on each other in audit/ dir | Plan B uses `audit/2026-05-21-a2a-spike-plan/` + audit dir for spike evidence; Plan C uses `audit/2026-05-21-gemini-delegation/` + `audit/2026-05-21-persistence-trap-12c/`. Distinct dirs — no overlap. | Distinct directory layout |

---

## End-of-plan checklist (for the executor — not the planner)

Before claiming Plan C complete:

- [ ] Tasks 1–4 all committed (4 commits to feat branch)
- [ ] Task 5 committed (USER-APPROVAL.md present)
- [ ] Tasks 6, 7, 8 each have an evidence dir AND a verification record in the commit message
- [ ] Task 9 evidence captured AND `j1-launched` tag exists locally (`git tag --list | grep -x j1-launched`)
- [ ] Task 10 close-out memo committed + memory updated
- [ ] `git log --oneline main..HEAD | wc -l` reflects all Plan C commits
- [ ] User received the Task 10 Step 4 summary message
- [ ] No `git push` was run during plan execution (G4 gate intact)
