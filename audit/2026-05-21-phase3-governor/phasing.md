# Phase 3 Metacog Governor — Phasing & Dependencies

**Version:** 1.0-draft
**Date:** 2026-05-21
**Status:** Design spec (not implemented)

---

## 1. Strategic Context

**From ADR-0008 Q7 disposition (2026-05-20):**

> **Decision:** Keep the current J4 path (F34/F35/F36 detectors in `lib/durability/runtime_detectors.py`) for H1 2026. Plan a standalone **Metacog Governor service** for Phase 3 when multi-agent coordination (Q4 A2A) demands centralized behavioral observability.

**Rationale:**
- J4 detectors (F34/F35/F36) already shipped — cover immediate behavioral-anomaly surface (loops, stalls, context pressure).
- Standalone Governor requires full MAPE-K loop service (~4-6 weeks new service) — overkill for single-agent H1 scope.
- **A2A-as-priority (Q4)** creates the eventual trigger: multi-agent coordination needs centralized cross-agent behavioral telemetry that in-process failure-matrix cannot provide.

**Phase 3 Governor is the evolution of Component 7 (Metacog Governor) from basic failure detection to fleet-level autonomic control.**

---

## 2. Dependencies

### 2.1 Hard Dependencies (Blockers)

Phase 3 Governor **cannot ship** until these are complete:

#### Dependency 1: A2A Integration (Agent-to-Agent Coordination)

**From:** ADR-0008 Q4 disposition (A2A is a production priority, NOT deferred).

**Why Governor depends on A2A:**
- Governor's cross-agent anomaly detection (delegation loop, fan-out explosion, no-response stall) requires observing inter-agent A2A messages.
- Without A2A, Governor can only see per-agent behavior (same as in-process F34/F35/F36 detectors).
- A2A message stream is the Governor's observability substrate for fleet-level coordination patterns.

**Specific requirements:**
1. A2A Pub/Sub topic (`a2a-fleet-messages`) exists and agents are publishing A2A messages.
2. A2A message schema includes `sender_agent_id`, `receiver_agent_id`, `message_type`, `payload`, `timestamp`.
3. A2A has been stable in production for ≥30 days (proven reliable before Governor depends on it).

**Phase 2 timeline (per Q4 disposition):** A2A spike starts in early Phase 2 (parallel with Postgres provisioning). A2A integration completes in H2 2026 (Q3-Q4).

**Implication:** Governor cannot start until Q4 2026 at earliest (after A2A ships).

---

#### Dependency 2: Postgres Migration (Phase 2)

**From:** ADR-0008 Q3 disposition (SQLite → PostgreSQL in Phase 2, as foundation for all Phase 2 features).

**Why Governor depends on Postgres:**
- Governor's Knowledge base (policy registry, incident queue, intervention log, agent registry) requires SQL database.
- Postgres pgvector extension may be used in future for semantic policy search (e.g., "find all policies that mention F34").
- Single Postgres instance serves both Governor and agent Kanban (no separate datastore).

**Specific requirements:**
1. Postgres provisioned (GCP Cloud SQL or self-hosted).
2. Schema `governor_schema` created (separate from agent Kanban schema to avoid table name collisions).
3. Postgres has been stable in production for ≥14 days (proven reliable before Governor depends on it).

**Phase 2 timeline (per Q3 disposition):** Postgres migration is the **first work-packet in Phase 2** (before any other Phase 2 feature work). Estimated duration: ~1.5 weeks.

**Implication:** Postgres will be ready before A2A (A2A is later in Phase 2 timeline). Postgres is not the critical path for Governor; A2A is.

---

### 2.2 Soft Dependencies (Recommended, Not Blockers)

Phase 3 Governor **should wait** for these, but can ship without them (degraded mode):

#### Soft Dependency 1: OpenInference Span Instrumentation (Existing)

**Current state:** Agents already emit OpenInference spans via OTLP (per J11 dual-emit shipped in Phase 1).

**Why Governor benefits:**
- Analyze phase queries spans for behavioral patterns (session duration, tool-call diversity, token usage rate).
- ML anomaly detector (Tier 2) builds feature vectors from span attributes.

**If missing:** Governor can still run with F-code events + A2A messages only (but Tier 2 ML detector would be disabled).

**Verdict:** Not a blocker (already shipped in Phase 1).

---

#### Soft Dependency 2: Phoenix UI (Existing)

**Current state:** Phoenix UI deployed, agents export spans to Phoenix (per Phase 1 observability stack).

**Why Governor benefits:**
- Governor's own spans exported to Phoenix (unified trace view: agent behavior → Governor detection → Governor intervention).
- Operator can manually investigate incidents via Phoenix UI.

**If missing:** Governor can still run (self-health dashboard in Grafana suffices), but operator experience is degraded (no unified trace view).

**Verdict:** Not a blocker (already shipped in Phase 1).

---

#### Soft Dependency 3: Cost Metering (Existing)

**Current state:** Per-invocation cost tracking exists (cost_usd calculated from LLM token usage).

**Why Governor benefits:**
- Analyze phase detects budget-exceeded anomalies (e.g., agent daily cost >$50).

**If missing:** Governor can still run without cost anomaly detection (policies that reference `cost_per_agent_per_day` metric would be disabled).

**Verdict:** Not a blocker (already exists in Phase 1; just needs to be emitted as OTel metric for Governor to ingest).

---

## 3. Interim Plan (H1 2026 — Before A2A Ships)

**Problem:** Governor's Phase 3 design assumes A2A exists, but A2A won't ship until H2 2026 (Q4). What happens in H1 2026?

**Solution:** Rely on in-process failure detectors (F34/F35/F36) per J4.

### 3.1 H1 2026 Failure Detection (In-Process)

**Already shipped (J4):**
- **F34 (F-LOOP):** Agent repeated same tool-call fingerprint N times without progress → interrupt with loop feedback.
- **F35 (F-STALL):** No tool-call activity for `idle_timeout_s` while task in_progress → halt + alert + snapshot.
- **F36 (F-CONTEXT):** Prompt-token usage exceeded warn threshold (compaction ineffective) → escalate context pressure.

**Location:** `lib/durability/runtime_detectors.py` (per-agent, in-process).

**Coverage:**
- ✅ Single-agent loops, stalls, context pressure.
- ❌ Cross-agent patterns (delegation loop, fan-out explosion) — requires A2A observability (not available in H1).

**Verdict:** Sufficient for single-agent Phase 1 scope. Cross-agent coordination is deferred until Phase 2 (when A2A ships).

---

### 3.2 No Standalone Governor in H1 2026

**Decision:** Do NOT build standalone Governor until A2A ships (Q4 2026).

**Justification:**
- Standalone Governor without A2A can only detect per-agent anomalies (same as in-process F34/F35/F36).
- Building Governor before A2A would target an empty data plane (no cross-agent telemetry to observe).
- Effort better spent on A2A integration spike (Q4 disposition) + Postgres migration (Q3 disposition).

**What changes in H1 2026:** Nothing. F34/F35/F36 continue as-is. No new Governor service.

---

## 4. Phase 3 Trigger Criteria

**When to promote from in-process detectors (F34/F35/F36) to standalone Governor service?**

### 4.1 Trigger Criteria (All Must Be True)

| Criterion | Threshold | Verification Method |
|-----------|-----------|---------------------|
| **1. Fleet size** | ≥10 active agents | Query `agent_registry` table; count agents with `health_status=HEALTHY` |
| **2. Cross-agent A2A traffic** | A2A Pub/Sub topic exists; agents publishing >100 A2A messages/day | Query Pub/Sub metrics for `a2a-fleet-messages` topic |
| **3. A2A stability** | A2A v1 stable in production for ≥30 days | Check deployment date of A2A integration; no critical incidents in last 30 days |
| **4. Incident history** | ≥1 incident in last 90 days where fleet-level view would have prevented escalation | Review incident log; identify incidents that crossed agent boundaries (e.g., delegation loop) |

**Why ≥10 agents?** Standalone Governor's overhead (new service, HA deployment, Postgres schema) is justified only when fleet size demands centralized control. Below 10 agents, in-process F34/F35/F36 suffices.

**Why >100 A2A messages/day?** Governor's A2A-aware anomaly detection requires meaningful A2A traffic. If agents rarely communicate, cross-agent patterns won't occur → no value from Governor's A2A observability.

**Why 30 days A2A stability?** Governor depends on A2A for cross-agent observability. If A2A is unstable (frequent outages, message delivery failures), Governor would be unreliable.

**Why ≥1 incident?** Evidence-based trigger. If no incidents would have benefited from fleet-level view, Governor is premature.

---

### 4.2 Phase 3 Promotion Decision Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│  START: H2 2026 (Q4) — A2A has shipped, Postgres migrated         │
└─────────────────────────────────────────────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │ Check Trigger Criteria │
              └────────┬───────────────┘
                       │
        ┌──────────────┴──────────────┐
        │ All 4 criteria met?         │
        └──────────────┬──────────────┘
                       │
           ┌───────────┴───────────┐
           │                       │
          YES                     NO
           │                       │
           ▼                       ▼
┌──────────────────────┐  ┌──────────────────────┐
│ Phase 3 Governor     │  │ Wait 30 days, re-    │
│ design → implement   │  │ evaluate criteria    │
│ (~4-6 weeks)         │  │ (loop back to start) │
└──────────────────────┘  └──────────────────────┘
           │
           ▼
┌──────────────────────┐
│ Governor deployed    │
│ (standalone service) │
└──────────────────────┘
```

**Decision maker:** Tech lead (Daniel) reviews trigger criteria quarterly (Q4 2026, Q1 2027, Q2 2027, ...).

**If criteria NOT met by Q4 2026:** Wait until Q1 2027, re-evaluate. If still not met, defer Governor to Q2 2027.

**If criteria met by Q4 2026:** Proceed with Governor design → implementation (this spec is the design; implementation is ~4-6 weeks).

---

## 5. Implementation Phases (When Governor is Triggered)

### 5.1 Phase 3.0 — Design & Spec (This Document)

**Duration:** 1 week (already complete; this is the deliverable).

**Outputs:**
- `architecture.md` — MAPE-K breakdown, deployment model, A2A integration.
- `apis.md` — gRPC control plane, OTLP data plane, Pub/Sub event bus.
- `observability-spec.md` — signals consumed/emitted, dashboards, alerting.
- `policy-language.md` — YAML policy DSL, examples, validation.
- `failure-modes.md` — SPoF mitigations, fail-open/fail-closed semantics.
- `scaling.md` — single-instance capacity, sharding, Postgres HA.
- `phasing.md` — dependencies, triggers, promotion criteria (this document).

---

### 5.2 Phase 3.1 — Scaffold Service (Week 1-2)

**Work:**
1. Scaffold Governor gRPC service (Go + protobuf).
2. Implement OTLP receiver (gRPC + HTTP endpoints).
3. Implement A2A Pub/Sub subscriber (pull subscription on `a2a-fleet-messages`).
4. Implement Prometheus scraper (target discovery from `agent_registry` table).
5. Implement Postgres schema `governor_schema` (tables: `policy_registry`, `incident_queue`, `intervention_queue`, `intervention_log`, `agent_registry`, `capability_cache`).
6. Implement health endpoint (`/health`) + metrics endpoint (`/metrics`).

**Deliverables:**
- Governor binary (Go, containerized).
- Dockerfile + Cloud Run deployment config.
- Initial Postgres schema migration script.

**Test:** Deploy Governor to staging environment; verify OTLP receiver accepts spans, A2A subscriber receives messages, Prometheus scraper fetches metrics.

---

### 5.3 Phase 3.2 — Monitor + Analyze Phases (Week 3)

**Work:**
1. Implement Monitor phase:
   - Ingest spans/metrics from OTLP receiver → write to Prometheus TSDB (embedded).
   - Ingest A2A messages from Pub/Sub subscriber → write to `a2a_message_log` table.
   - Ingest F-code events (from OTLP spans with `f_code` attribute) → write to `f_code_events` table.
   - Ingest cost meters (OTel metrics) → write to `cost_events` table.
2. Implement Analyze phase (Tier 1 — rule engine):
   - Pattern-match F-code events against policies (e.g., "F34 occurred 3 times in 10 minutes").
   - Pattern-match metrics against thresholds (e.g., "cost_per_agent_per_day > 50").
   - Detect A2A patterns (delegation loop, fan-out explosion, no-response stall).
   - Write detected anomalies to `incident_queue` table.

**Deliverables:**
- Monitor phase fully functional (all telemetry ingested + persisted).
- Analyze phase Tier 1 (rule engine) functional (anomalies detected + written to `incident_queue`).

**Test:** Inject synthetic F-code events (e.g., emit F34 3 times in 5 minutes) → verify anomaly appears in `incident_queue`.

---

### 5.4 Phase 3.3 — Plan + Execute Phases (Week 4)

**Work:**
1. Implement Plan phase:
   - Read `incident_queue` (anomalies from Analyze phase).
   - Match anomalies against policies in `policy_registry` (YAML policies loaded into memory, refreshed every 60s).
   - Queue interventions to `intervention_queue` table.
   - For interventions requiring human approval (`requires_human_approval=true`), emit `pending_intervention` event to Pub/Sub → Telegram bot sends inline keyboard to operator.
2. Implement Execute phase:
   - Read `intervention_queue` (approved interventions).
   - Publish intervention messages to `governor-control` Pub/Sub topic (A2A control plane).
   - Write intervention outcome to `intervention_log` table (immutable audit log).
   - Emit `InterventionDecision` events to `governor-decisions` Pub/Sub topic (for downstream consumers: Grafana, PagerDuty).

**Deliverables:**
- Plan phase functional (policies matched, interventions queued).
- Execute phase functional (interventions published to A2A, audit log written).

**Test:** Create policy "kill session if F34 occurs 3 times in 10 minutes with human approval" → inject F34 events → verify pending intervention appears in Telegram → approve → verify `GOVERNOR_KILL_SESSION` message published to `governor-control` topic.

---

### 5.5 Phase 3.4 — HA Deployment + Leader Election (Week 5)

**Work:**
1. Implement leader election via Postgres advisory locks.
2. Deploy 3 Governor replicas (active-active for Monitor/Analyze, active-passive for Plan/Execute).
3. Configure gRPC load balancer (round-robin for OTLP receiver).
4. Configure external health monitor (Healthchecks.io pings `/health` every 60s).

**Deliverables:**
- HA deployment (3 replicas, leader election working).
- Failover tested (kill leader → new leader elected within 30s).

**Test:** Kill leader replica → verify new leader elected → verify interventions continue being executed.

---

### 5.6 Phase 3.5 — ML Anomaly Detector (Tier 2) (Week 6, Optional)

**Work:**
1. Implement Tier 2 ML anomaly detector (Isolation Forest / One-class SVM).
2. Train model on historical spans (last 10,000 spans per agent).
3. Run batch job every 15 minutes (detects unsupervised drift).
4. Write ML-detected anomalies to `incident_queue` (same table as Tier 1 rule-engine anomalies).

**Deliverables:**
- ML detector functional (anomalies detected + written to `incident_queue`).
- Model retraining pipeline (triggered when false-positive rate >20%).

**Test:** Inject synthetic anomalous behavior (e.g., agent starts calling a new tool in a loop) → verify ML detector flags agent as anomalous.

**Optional:** If Tier 2 ML detector proves low-value in production (high false-positive rate, rarely detects novel patterns), defer to Phase 4 or deprecate entirely.

---

### 5.7 Phase 3.6 — Dashboards + Alerting (Parallel with 3.2-3.5)

**Work:**
1. Create Grafana dashboards:
   - Fleet Health Overview (agent count by health status, intervention rate, anomaly detection rate).
   - Per-Agent Health (uptime, F-code histogram, session duration distribution).
   - Incident Timeline (all incidents in last 7 days, color-coded by severity).
2. Wire alerting:
   - High-severity incident created → Telegram alert.
   - Intervention failed → PagerDuty incident.
   - Governor unhealthy (Postgres unreachable, OTLP receiver queue depth >10K) → Telegram alert.

**Deliverables:**
- 3 Grafana dashboards (importable JSON).
- Telegram alerting integration (reuses existing bot from F21/F32 handlers).
- PagerDuty integration (HTTP POST to PagerDuty API).

**Test:** Trigger high-severity incident → verify Telegram alert received. Kill Postgres → verify Governor health alert received.

---

## 6. Post-Launch (Phase 3 → Phase 4)

### 6.1 24-Hour Survival Checkpoint (P0-A from Audit v2)

**From:** Audit 2026-05-20 state-of-repo v2, P0-A survival analysis.

**Requirement:** Governor must survive 24 hours in production with:
- No crashes (all 3 replicas healthy).
- No false-positive kills (intervention log reviewed; all kills were justified by policy + evidence).
- Failover tested (manually kill leader replica during business hours; verify new leader elected within 30s).

**Verification:** Run Governor in production for 24 hours (Friday → Saturday, low-traffic period). Review logs, metrics, intervention log. If any of the above fail → rollback, investigate, fix, re-deploy.

**If successful:** Governor remains in production. If fails → Governor is rolled back to in-process F34/F35/F36 detectors; root cause investigated.

---

### 6.2 Learning Loop (Governor Improves Over Time)

**Phase 4 extension (future):**
- Governor's `intervention_log` is used as RLAIF substrate (similar to J1 trajectory shipper for judge verdicts).
- ML model trains on historical interventions: "intervention X was manually rejected by operator → downweight this pattern in Tier 2 detector."
- Governor's policies auto-tune: "policy Y triggered 100 times but was manually rejected 80 times → reduce occurrence threshold or disable policy."

**Why Phase 4?** Requires J1 trajectory infrastructure (GCS bucket, Model Armor scrubbing) to be operational first (per Q6 disposition). Governor learning loop is additive, not blocking Phase 3 launch.

---

## 7. Rollback Plan

**If Governor causes production incident (e.g., false-positive kills, runaway intervention rate):**

1. **Immediate:** Pause Execute phase (set `GOVERNOR_EXECUTE_ENABLED=false` environment variable; Plan phase continues but no interventions are executed).
2. **Investigation:** Review `intervention_log` table; identify which policy caused false positives.
3. **Fix:** Update policy (reduce occurrence threshold, add human approval requirement, or delete policy).
4. **Re-enable:** Set `GOVERNOR_EXECUTE_ENABLED=true`; monitor for 1 hour.
5. **If issue persists:** Rollback to in-process F34/F35/F36 detectors (shut down Governor; agents continue using `lib/durability/runtime_detectors.py`).

**Rollback time:** <5 minutes (environment variable flip). No data loss (all incidents/interventions logged in Postgres; can be replayed after fix).

---

## 8. Summary

### 8.1 Dependencies

**Hard blockers:**
- A2A integration (Q4 2026).
- Postgres migration (Q2 2026, not blocking; A2A is the critical path).

**Soft dependencies (recommended):**
- OpenInference spans (already shipped).
- Phoenix UI (already shipped).
- Cost metering (already exists; just needs OTel metric emission).

---

### 8.2 Trigger Criteria (All Must Be True)

1. Fleet size ≥10 active agents.
2. Cross-agent A2A traffic >100 messages/day.
3. A2A stable for ≥30 days.
4. ≥1 incident in last 90 days where fleet-level view would have prevented escalation.

**Earliest possible trigger date:** Q4 2026 (after A2A ships + 30-day stability period).

---

### 8.3 Implementation Timeline (When Triggered)

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| 3.0 Design & Spec | 1 week | This document (7 files: architecture, apis, observability-spec, policy-language, failure-modes, scaling, phasing) |
| 3.1 Scaffold Service | 2 weeks | Governor binary, OTLP receiver, A2A subscriber, Postgres schema |
| 3.2 Monitor + Analyze | 1 week | Telemetry ingest + Tier 1 rule engine |
| 3.3 Plan + Execute | 1 week | Policy matching + intervention execution |
| 3.4 HA Deployment | 1 week | 3 replicas + leader election + load balancer |
| 3.5 ML Tier 2 (optional) | 1 week | Isolation Forest anomaly detector |
| 3.6 Dashboards + Alerting | Parallel | Grafana dashboards + Telegram/PagerDuty alerting |
| **Total** | **4-6 weeks** | Production-ready Governor service |

---

### 8.4 Post-Launch

- **24-hour survival checkpoint** (P0-A).
- **Learning loop** (Phase 4 extension).
- **Rollback plan** (pause Execute phase, investigate, fix policy, re-enable).

---

**Phase 3 Governor is the evolution of Component 7 from basic failure detection (F34/F35/F36) to fleet-level autonomic control (MAPE-K). Depends on A2A (Q4 2026). Triggered when fleet size ≥10 agents + A2A traffic + incident history.**
