# Parallel SDLC Delegation Design — AutonomousAgent Wave 1+2+3
**Date:** 2026-05-25
**Status:** Approved — ready for implementation
**Author:** Claude Code (Sonnet 4.6) + brainstorming skill

---

## Problem

After the previous session (compacted 2026-05-21 → 2026-05-25), the following work remains:

1. **J1 launch blocked**: three Terraform applies never ran in `autonomous-agent-2026` (GCS bucket, Model Armor API + template, SM secret already exists but version 1 not created via TF).
2. **A2A Days 4-10 not started**: spike plan requires SSE streaming, JWT auth, OTel traceparent, TaskSpec bridge, AgentCard, and e2e canary demo.
3. **Bootstrap tarball stale**: 9 config files were manually SCP'd to the VM but not in the tarball.
4. **Two stale `i-for-ai` references** remain in spike plan audit docs.
5. **Two Terraform variable defaults** pointed to `i-for-ai` (fixed in this session before delegation).

## Pre-Wave-1 Fixes (Applied In This Session — Verified)

| Fix | Evidence |
|-----|----------|
| `terraform/phase-0a-gcp/model-armor/variables.tf:4` — `i-for-ai` → `autonomous-agent-2026` | `grep -n "i-for-ai"` returns 0 results |
| `terraform/phase-0a-gcp/postgres/variables.tf:8` — `i-for-ai` → `autonomous-agent-2026` | same |
| `lib/a2a/server.py` ruff-formatted | `ruff format --check` clean |
| 22/22 A2A tests passing | `uv run pytest lib/a2a/tests/ -v` → `22 passed in 0.26s` |

---

## Partition Principle

**Zero shared mutable state between any two agents in the same wave.**

| Territory | Owner | No-touch for |
|-----------|-------|-------------|
| `lib/a2a/` Python files | Claude subagents SA1–SA5 | Gemini, Antigravity |
| GCP APIs (Terraform state, SM, IAM, Monitoring) | Gemini CLI (G1–G4) | Claude, Antigravity |
| `deploy/scripts/`, `deploy/docker-compose.canary.yml`, `audit/` | Antigravity (AG1, AG2) | Claude, Gemini |
| `terraform/` (HCL, `.tfvars`) | Gemini CLI only | Claude, Antigravity |

---

## Wave 1 — All Agents Launch Simultaneously (t = 0)

### G1 — Gemini CLI: Terraform Applies + J1 Launch Flip

**Scope:** GCP APIs only. No code changes.

Steps (sequential within G1):
1. `cd terraform/phase-0a-gcp && terraform init && terraform plan -out tfplan && terraform apply tfplan`
   - Provisions: `gs://autonomous-agent-2026-j3-trajectories` + IAM grant for VM runtime SA
2. `cd terraform/phase-0a-gcp/model-armor && terraform init && terraform plan && terraform apply`
   - Enables: `modelarmor.googleapis.com`, `dlp.googleapis.com`
   - Creates: floor settings (enforcement=true) + `j1-trajectory-shipper` regional template
3. Verify SM secret: `gcloud secrets describe autonomousagent-j3-shipper-config --project=autonomous-agent-2026`  <!-- pragma: allowlist secret -->
4. J1 Stage A — create SM version 2: payload `{"bucket_name":"autonomous-agent-2026-j3-trajectories","model_armor_template_resource":"projects/autonomous-agent-2026/locations/us-central1/templates/j1-trajectory-shipper","feature_flag_enabled":true}`
5. J1 Stage B — canary smoke (IAP SSH to VM, run `--dry-run`, then one-shot canary with 4 PII tokens, verify REDACTED in GCS output)

**Gate:** `gsutil ls -b gs://autonomous-agent-2026-j3-trajectories` → success; `gcloud secrets versions list autonomousagent-j3-shipper-config --project=autonomous-agent-2026` → versions 1 and 2 both ENABLED.

---

### G2 — Gemini CLI: Canary SA Provisioning

**Scope:** GCP IAM only. No code changes.

Steps:
1. `gcloud iam service-accounts create agent-canary-spike --project=autonomous-agent-2026 --display-name="A2A Spike Canary Agent"`
2. Grant Hermes runtime SA `roles/iam.serviceAccountTokenCreator` on both SAs:
   ```
   gcloud iam service-accounts add-iam-policy-binding \
     agent-canary-spike@autonomous-agent-2026.iam.gserviceaccount.com \
     --member="serviceAccount:autonomousagent-vm-runtime@autonomous-agent-2026.iam.gserviceaccount.com" \
     --role="roles/iam.serviceAccountTokenCreator" --project=autonomous-agent-2026
   ```
3. Verify: `gcloud iam service-accounts describe agent-canary-spike@autonomous-agent-2026.iam.gserviceaccount.com`

**Gate:** SA describe returns; IAM binding is present on the SA.

---

### G3 — Gemini CLI: Phase 0a Soak Monitoring

**Scope:** GCP Monitoring API only. No code changes.

Steps:
1. Create uptime check: HTTP check to VM `/health` endpoint (port 9001, Hermes) every 60s
2. Create alert policy: if uptime check fails ≥ 5 consecutive minutes → alert (email)
3. Create log-based metric: `docker-compose-hermes.service restart` events from Cloud Logging

**Gate:** `gcloud monitoring uptime-checks list --project=autonomous-agent-2026` returns 1 check; alert policy exists.

---

### AG1 — Antigravity: Bootstrap Tarball Rebuild + Stale Doc Fixes

**Scope:** `deploy/scripts/`, `audit/2026-05-21-a2a-spike-plan/` only. No `lib/a2a/` changes.

**See `docs/antigravity/2026-05-25-ag-briefing.md` for the full standalone Antigravity brief.**

Acceptance:
- `grep -rn "i-for-ai" audit/2026-05-21-a2a-spike-plan/` → 0 results
- `gsutil ls gs://autonomous-agent-2026-snapshots/bootstrap/hermes-bootstrap.tar.gz` → success
- Tarball contains `config/hermes/AGENTS.md`

---

### SA1 — Claude: `lib/a2a/auth.py` (Day 5)

**Scope:** `lib/a2a/auth.py` + `lib/a2a/tests/test_auth.py` only. Worktree: `feat/a2a-day5-auth`.

**Mandatory env:** `uv sync --extra a2a --extra dev` before any pytest invocation.
**Mandatory test path:** `uv run pytest lib/a2a/tests/ -v` (not bare `pytest` — testpaths config covers `tests/` only).

Implements:
- `mint_token(target_audience, acting_for)` → signed JWT via GCP `iam.serviceAccounts.signJwt`; `cachetools.TTLCache(maxsize=10_000, ttl=240)` keyed on `(target_audience, acting_for)` (60s before expiry)
- `verify_token(jwt_str)` → `AgentIdentity`; JWKS from `https://www.googleapis.com/service_accounts/v1/jwk/{SA_EMAIL}`; `jti` replay cache `TTLCache(maxsize=100_000, ttl=600)`
- `AgentIdentity` dataclass: `sub, audience, acting_for, expiry, jti`
- `_emit_audit_log(decision, identity, method, task_id, trace_id)` → Cloud Logging JSON (stdout, picked up by gcplogs)
- 7 tests per `audit/2026-05-21-a2a-spike-plan/spike-plan.md §Day 5`: AgentIdentity shape, TTL cache hit, jti replay rejection, missing-auth returns -32600, non-allowlisted SA rejected, audit log emitted on rejection, HIPAA fields present in log entry

**Gate:** 22 existing + 7 new = 29/29 pass; `ruff check` + `ruff format --check` clean.
**PR:** `feat(a2a): day 5 auth — JWT mint/verify + AgentIdentity + HIPAA audit log`

---

### SA2 — Claude: `lib/a2a/task_bridge.py` (Day 7)

**Scope:** `lib/a2a/task_bridge.py` + `lib/a2a/tests/test_task_bridge.py` only. Worktree: `feat/a2a-day7-bridge`.

**Mandatory env:** same as SA1.

Implements (per `audit/2026-05-21-a2a-spike-plan/spike-plan.md §Day 7`):
- `bridge_inbound_to_taskspec(a2a_task: dict, agent_identity: AgentIdentity) -> TaskSpec`
- `bridge_taskspec_status_to_a2a(spec: TaskSpec) -> TaskState` with mapping table for implemented statuses (completion + failure deferred per spike plan §Day 7 — pending evaluator integration):
  - `draft` → `SUBMITTED`, `draft_locked` → `WORKING`, `locked` → `WORKING`, `superseded` → `CANCELED`
- Cancel dispatch: on `tasks/cancel`, dispatch to `/cancel` slash command path
- Tests: mapping table completeness, bridge round-trip, cancel dispatch, trace_id included in TaskSpec metadata

**Gate:** 22 existing + new bridge tests = all pass; ruff clean.
**PR:** `feat(a2a): day 7 task bridge — TaskSpec↔A2A mapping + cancel dispatch`

---

### SA3 — Claude: `lib/a2a/client.py` OTel (Day 6)

**Scope:** `lib/a2a/client.py` additions + `lib/a2a/tests/test_client_otel.py`. Worktree: `feat/a2a-day6-otel`.

**Mandatory env:** same as SA1.

Implements (per `audit/2026-05-21-a2a-spike-plan/telemetry-design.md`):
- `opentelemetry.propagate.inject(headers)` on every outbound httpx request
- `tracestate` pass-through verbatim
- Respect sampled bit (do not force-sample)
- `current_span()` helper for callers

**Gate:** 22 existing + OTel tests = all pass; ruff clean.
**PR:** `feat(a2a): day 6 OTel — W3C traceparent propagation in outbound client`

---

## Wave 1 Merge Order

SA1 → SA2 → SA3 (auth first: SA4 imports auth.py; bridge second: SA4 imports task_bridge.py; client last: SA4 builds on client.py).
AG1 and G1–G3 merge independently (no dependencies on SA1–SA3).

---

## Wave 2 — After Wave 1 PRs Merged

### G4 — Gemini CLI: Cloud Trace Verification

Post-SA3 deploy: query `gcloud trace list --project=autonomous-agent-2026` for recent spans. Confirm OTel collector forwarding.

### AG2 — Antigravity: Canary Peer Compose Stack

**Scope:** `deploy/docker-compose.canary.yml` only. See `docs/antigravity/2026-05-25-ag-briefing.md §Task 2`.

### SA4 — Claude: `lib/a2a/server.py` Integration (Days 4–7)

**Scope:** `lib/a2a/server.py` + new test files. Worktree: `feat/a2a-day4-server`. Base: main after SA1+SA2+SA3 merged.

**Mandatory baseline:** `ruff format lib/a2a/server.py` before first edit.

TDD sequence — tests before each implementation:
1. **Day 4:** `test_server_sse.py` — `message/stream` + `tasks/subscribe` return `StreamingResponse`; emit 3 events (`status: WORKING`, `artifact_added`, `status: COMPLETED`)
2. **Day 5 wire:** JWT middleware — `401` on missing `Authorization`, `200` on valid JWT from `auth.py`; `403` on non-allowlisted SA
3. **Day 6 wire:** extract `traceparent` from inbound headers; inject to OTel context before handler dispatch; SSE: per-event child spans
4. **Day 7 wire:** `handle_send_message` calls `bridge_inbound_to_taskspec()` → real `TaskSpec`; mirror status changes via `bridge_taskspec_status_to_a2a()`

**Gate:** All accumulated Wave-1 tests (22 original + SA1's 7 auth + SA2's bridge tests + SA3's OTel tests) plus all new Day 4-7 server tests pass; `pytest tests/integration/ -m "not docker"` passes; ruff clean.
**PR:** `feat(a2a): days 4-7 server — SSE streaming + JWT auth + OTel + TaskSpec wiring`

---

## Wave 3 — After Wave 2 Merged

### SA5 — Claude: `lib/a2a/agent_card.py` + e2e Demo (Days 8–10)

**Scope:** `lib/a2a/agent_card.py` + `lib/a2a/tests/test_agent_card_signing.py` + server.py `GET /.well-known/agent-card.json` + Day 9 scrubber + Day 10 e2e demo. Worktree: `feat/a2a-day8-10`.

Uses AG2's `deploy/docker-compose.canary.yml` for the live peer in Day 9.

**Gate:** 3 signing tests (sign-then-verify, tampered rejected, expired rejected); bidirectional `message/send` + SSE visible in Cloud Trace; PII scrubber fires on canary payloads.
**PR:** `feat(a2a): days 8-10 — AgentCard + scrubber + canary e2e demo`

---

## Mandatory SDLC Gates (All SA Agents)

| Gate | Command | Pass criteria |
|------|---------|---------------|
| Env setup | `uv sync --extra a2a --extra dev` | No ModuleNotFoundError |
| Tests first | Write failing tests before implementation | Red → green verified |
| Lint | `uv run ruff check lib/a2a/` | 0 errors |
| Format | `uv run ruff format --check lib/a2a/` | 0 diffs |
| Tests | `uv run pytest lib/a2a/tests/ -v` | All pass (previous + new) |
| Branch name | `feat/<desc>` or `fix/<desc>` — no dots in `<desc>` | CI regex passes |
| PR title | `type(scope): lowercase subject` | CI regex passes |
| CI gate | `gh pr checks <number> --watch` | All green before next wave |

---

## Responsible AI Coverage

| Requirement | Owner | Mechanism |
|-------------|-------|-----------|
| PII sanitization before GCS | G1 (provisions MA) + live shipper | Model Armor INSPECT_AND_REDACT |
| Halt-LOUD on MA unavailability | Existing shipper code | Persistence Trap contract |
| Composite identity on every inter-agent call | SA1 (`auth.py`) | `acting_for` JWT claim |
| Token replay prevention | SA1 (`auth.py`) | `jti` TTLCache(100k, 600s) |
| Structured audit log on every auth event | SA1 (`auth.py`) | `_emit_audit_log` → Cloud Logging |
| Distributed tracing across agent hops | SA3 + SA4 | W3C traceparent inject + extract |
| AgentCard tamper detection | SA5 (`agent_card.py`) | JCS canonicalization (RFC 8785) |
| 72h soak monitoring | G3 | GCP Monitoring uptime check + alert |
| Bootstrap supply chain integrity | AG1 | Fresh tarball from repo HEAD + gsutil SHA metadata |

---

## Correctly Deferred (Trigger-Gated — Do Not Implement)

| Item | Trigger |
|------|---------|
| P-1 PyTorch/Unsloth policy network | $5K GPU procurement |
| P-2 Postgres CloudSQL pgvector | Phase 2 milestone |
| P-4 Firecracker sandbox | H1 decision gate |
| P-6 Governor service | Q4 2026 |
| P-7–P-17 GCP-native adapters | Individual milestone gates per INTEGRATION.md |
| Phase 0a 72h soak items (DEFER 1,2,3,5,6,10) | Timer: 24h/72h/7d/30d windows |
