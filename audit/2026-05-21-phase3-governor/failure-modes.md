# Phase 3 Metacog Governor — Failure Modes & Mitigations

**Version:** 1.0-draft
**Date:** 2026-05-21
**Status:** Design spec (not implemented)

---

## 1. Overview

**Critical constraint:** The Governor is a single point of failure for intervention decisions. If the Governor misbehaves or goes down, the entire fleet's autonomic control loop is compromised.

This document identifies all Governor failure modes and specifies mitigations. Every failure mode includes:
1. **Failure scenario** — what goes wrong.
2. **Impact** — blast radius.
3. **Detection** — how we know it happened.
4. **Mitigation** — design/operational countermeasures.
5. **Fail-open or fail-closed semantics** — what happens to the fleet when Governor is unavailable.

---

## 2. Governor Unavailable (Complete Outage)

### 2.1 Failure Scenario

**Cause:** Governor process crashes, host VM reboots, network partition isolates Governor from fleet.

**Duration:** Could be seconds (process restart) to hours (host replacement).

**Impact:**
- **Monitor phase stops:** No new telemetry ingested (agents' sidecars buffer locally, but buffer is finite).
- **Analyze phase stops:** No new anomalies detected at fleet level (but agents' in-process F34/F35/F36 detectors still run).
- **Plan + Execute phases stop:** No new interventions executed.

**Critical:** If an agent is in a runaway loop or burning budget, Governor cannot intervene.

---

### 2.2 Detection

**How we know:**
1. **Governor health endpoint (`/health`)** — external uptime monitor (Healthchecks.io, existing F32 pattern) pings Governor every 60s. If 2 consecutive pings fail (HTTP 5xx or timeout) → alert.
2. **Prometheus scrape failure** — external Prometheus scraping Governor's `/metrics` endpoint detects scrape failure → alert after 2 consecutive failures (30s).
3. **Agent-side dead-man-switch** — agents expect heartbeat ACKs from Governor every 60s (via sidecar). If no ACK for >120s → agent logs `GOVERNOR_UNREACHABLE` and enters safe-mode.

**Alert routing:** Telegram alert to operator (high-severity, immediate).

---

### 2.3 Mitigation

#### Mitigation 1: HA Deployment (3 Replicas + Leader Election)

**Architecture:**
- 3 Governor replicas (active-active for read-path, active-passive for write-path).
- Leader election via **Postgres advisory locks** (`pg_try_advisory_lock(<governor_lock_id>)`).
- Write-path (Plan → Execute): only the leader writes to `intervention_queue` and publishes to `governor-control` Pub/Sub.
- Read-path (Monitor → Analyze): all replicas ingest telemetry independently.

**Failover time:** <30s (advisory lock expiry + new leader election).

**During failover:**
- Monitor/Analyze phases continue (all replicas run independently).
- Plan/Execute phases pause (no new interventions until new leader elected).

**Why Postgres advisory locks (vs etcd)?**
- Simpler operational surface (no new service to run).
- Postgres is already a dependency (Phase 2).
- Trade-off: if Postgres itself is down, no leader election → all replicas become passive (degraded to monitoring-only mode).

**Alternative (if Postgres advisory locks prove brittle at scale):** Dedicated etcd cluster for leader election. Adds operational complexity but etcd's leader election is battle-tested.

---

#### Mitigation 2: Agent-Side Safe-Mode

**Agent behavior when Governor unreachable for >120s:**
1. Agent logs `GOVERNOR_UNREACHABLE` event.
2. Agent enters **safe-mode**:
   - Refuses new high-cost operations (LLM calls >1000 tokens, Firecracker microVM launches).
   - Continues low-cost operations (read-only tool calls, local computation).
   - Emits Telegram alert to operator (once per agent, de-duplicated).
3. Agent continues emitting telemetry to sidecar (sidecar buffers locally, max 10MB per sidecar; after that, drop oldest spans).

**Exit safe-mode:** When Governor comes back (agent receives heartbeat ACK from sidecar), agent logs `GOVERNOR_REACHABLE` and resumes normal operation.

**Justification:** Safe-mode prevents runaway cost during Governor outage, but doesn't brick the agent entirely (read-only ops still work).

---

#### Mitigation 3: Sidecar Buffering

**Sidecar behavior when Governor unreachable:**
- Sidecars buffer spans/metrics locally (in-memory ring buffer, max 10MB per sidecar).
- When Governor comes back, sidecars flush buffered data to Governor (batched, max 1000 spans per batch).
- If buffer exceeds 10MB before Governor returns → drop oldest spans (FIFO eviction).

**Why 10MB limit?** At 2KB per span, 10MB = ~5,000 spans. For a typical agent emitting 100 spans/sec, this is 50 seconds of buffering. If Governor is down for >50s, some spans are lost (acceptable — telemetry is best-effort, not critical).

---

### 2.4 Fail-Open vs Fail-Closed Semantics

**Fleet continues operating** (fail-open for most functionality):
- Agents' in-process F34/F35/F36 detectors still run (local anomaly detection).
- A2A cross-agent communication continues (Governor just can't observe it).
- Agents can still escalate directly to Telegram (existing F21/F32 handlers bypass Governor).

**Interventions paused** (fail-closed for Governor-initiated actions):
- Governor cannot send `GOVERNOR_KILL_SESSION` / `GOVERNOR_THROTTLE` during outage.
- Existing interventions (already executed before outage) remain in effect (e.g., if agent was throttled, it stays throttled until throttle duration expires).

**Post-recovery behavior:**
- When Governor comes back, it reads `incident_queue` to find OPEN incidents that occurred during outage.
- Governor processes these incidents (matches policies, queues interventions) as if they just happened.
- Interventions that require human approval (e.g., kill) still go through approval flow (operator sees a backlog of pending interventions).

---

## 3. Governor Misbehaves (False Positive Interventions)

### 3.1 Failure Scenario

**Cause:** Bug in Analyze phase (rule engine or ML detector), misconfigured policy, or corrupted telemetry data.

**Example:** Governor detects F34 (F-LOOP) when agent is actually making progress (false positive), then kills a healthy session.

**Impact:**
- **Healthy agent killed** — severe. Work is lost, user's task is interrupted.
- **Healthy agent throttled** — medium. Agent latency degrades, but work continues.

**Detection:**
1. **Agent reports kill received** — agent logs `GOVERNOR_INTERVENTION_APPLIED` with `action_verb=KILL` + reasoning. If agent operator reviews log and sees the kill was unwarranted → incident.
2. **Intervention rate spike** — if Governor's `governor_interventions_total{action_verb="kill"}` metric spikes (e.g., >5 kills in 5 minutes when historical rate is <1 kill/hour) → anomaly (Governor itself is misbehaving).

---

### 3.2 Mitigation

#### Mitigation 1: Reasoning Transparency (Every Intervention Logged)

**Requirement:** Every intervention is logged in `intervention_log` table with full reasoning chain:
- Which policy matched.
- Which evidence triggered the policy (F-codes, metric values, A2A messages).
- Who approved it (human or auto).

**Benefit:** Post-incident review is straightforward. Operator can trace exactly why Governor killed an agent.

**Enforcement:** `intervention_log` table is immutable (no `UPDATE` or `DELETE` allowed via Postgres RLS policy).

---

#### Mitigation 2: Human Review SLA for Auto-Kill Actions

**Policy requirement:** Any policy with `action_verb=KILL` and `requires_human_approval=false` (auto-execute kill) must be explicitly flagged for review.

**Operational constraint:**
- Auto-kill policies are ONLY allowed for critical security violations (e.g., sandbox escape attempt, secret leak).
- All other kill policies MUST have `requires_human_approval=true`.

**Enforcement:** `PolicyService.Create` gRPC endpoint rejects policies with `action_verb=KILL` and `requires_human_approval=false` unless the policy is in an allowlist (e.g., `sandbox-escape-kill`, `secret-leak-kill`).

---

#### Mitigation 3: Circuit Breaker on Intervention Rate

**Pattern:** If Governor is killing/throttling too many agents per hour, it's likely misbehaving.

**Circuit breaker logic:**
- Track `governor_interventions_total{action_verb="kill"}` over a 1-hour sliding window.
- If `kill` count exceeds threshold (e.g., >10 kills in 1 hour when fleet size is 100 agents), circuit breaker trips.
- When tripped: Governor pauses Execute phase (no new interventions), emits Telegram alert ("GOVERNOR CIRCUIT BREAKER TRIPPED — intervention rate spike detected"), waits for human operator to investigate.
- Operator reviews recent interventions in Grafana dashboard, determines if Governor is misbehaving or if the kills are legitimate.
- Operator manually resets circuit breaker via `CircuitBreakerService.Reset` gRPC call after confirming root cause.

**Threshold tuning:**
- Default: >10 kills/hour for fleet size <100 agents.
- Tunable via `GOVERNOR_CIRCUIT_BREAKER_KILL_THRESHOLD` environment variable.

**Why 10 kills/hour?** Historical baseline from existing F21/F32 handlers: kill interventions are rare (<1/day in normal operation). 10/hour is a 10x spike → strong signal of misbehavior.

---

#### Mitigation 4: Dry-Run Mode for New Policies

**Workflow:** Before activating a new policy, operator can run it in **dry-run mode** for 24 hours:
1. Policy is marked `dry_run=true` in `policy_registry`.
2. Governor matches anomalies against the policy as usual, but does NOT execute actions.
3. Instead, Governor logs "would-have interventions" to a separate table (`dry_run_interventions`).
4. Operator reviews `dry_run_interventions` table after 24 hours to see if the policy would have triggered false positives.
5. If no false positives, operator activates policy (`dry_run=false`).

**Benefit:** Catches misconfigured policies before they affect the fleet.

---

## 4. Telemetry Pipeline Broken (Governor Blind)

### 4.1 Failure Scenario

**Cause:** OTLP receiver crashes, A2A Pub/Sub subscription deleted, Prometheus scrape endpoint unreachable, network partition between sidecars and Governor.

**Impact:**
- Governor cannot see what agents are doing → cannot detect anomalies → cannot intervene.
- Agents continue operating normally, but Governor is blind.

**Duration:** Could be minutes (OTLP receiver restarts) to hours (network partition).

---

### 4.2 Detection

**How we know:**
1. **OTLP receiver queue depth = 0** — if Governor hasn't received any spans in 60s (normally receives thousands per second) → telemetry pipeline broken.
2. **A2A message lag** — if Governor's A2A subscription lag (Pub/Sub metric `subscription/num_undelivered_messages`) is >10,000 → subscription is not consuming messages.
3. **Prometheus scrape failures** — if Governor's Prometheus scraper reports >50% of targets unreachable for >5 minutes → network partition or sidecars down.

**Alert routing:** Telegram alert to operator (high-severity).

---

### 4.3 Mitigation

#### Mitigation 1: Governor Self-Health Check

**Governor exposes `/health` endpoint:**

```json
{
  "status": "degraded",
  "checks": {
    "otlp_receiver": "no spans received in last 120s",
    "a2a_subscriber": "subscription lag >10,000 messages",
    "prometheus_scraper": "50% of targets unreachable"
  },
  "timestamp": "2026-05-21T14:32:00Z"
}
```

**External monitor:** Healthchecks.io pings `/health` every 60s. If `status != "healthy"` for >2 minutes → Telegram alert.

---

#### Mitigation 2: Agent-Side Dead-Man-Switch

**Pattern:** If agents can't reach sidecar (or sidecar can't reach Governor) for >60s, agents enter safe-mode (see §2.3 Mitigation 2).

**Justification:** Prevents runaway cost if telemetry pipeline is broken and Governor can't intervene.

---

#### Mitigation 3: Redundant Telemetry Paths

**Design:** Agents emit telemetry to TWO destinations:
1. **Primary:** Sidecar → Governor (OTLP).
2. **Secondary:** Direct to external Phoenix instance (OTLP, bypassing Governor).

**Benefit:** If Governor's OTLP receiver is down, Phoenix still receives spans → operator can manually investigate agent behavior via Phoenix UI (even though Governor is blind).

**Trade-off:** Doubles telemetry bandwidth. Acceptable for critical fleet (cost of telemetry << cost of undetected runaway agent).

---

## 5. Postgres Unavailable (Knowledge Base Down)

### 5.1 Failure Scenario

**Cause:** Postgres crashes, network partition, disk full, migration failure.

**Impact:**
- **Plan phase stops:** Cannot query `policy_registry` or write to `intervention_queue`.
- **Execute phase stops:** Cannot read `intervention_queue` or write to `intervention_log`.
- **Monitor/Analyze phases degraded:** Can still ingest telemetry and detect anomalies (in-memory), but cannot persist incidents to `incident_queue`.

**Critical:** No new interventions can be executed (Governor is read-only).

---

### 5.2 Detection

**How we know:**
1. **Postgres health check fails** — Governor pings Postgres every 10s (`SELECT 1`). If 3 consecutive pings fail (30s) → alert.
2. **Plan phase errors** — if Plan phase emits `POSTGRES_UNREACHABLE` error for >60s → alert.

---

### 5.3 Mitigation

#### Mitigation 1: Postgres HA (Cloud SQL with Read Replicas)

**Architecture:**
- GCP Cloud SQL Postgres (managed service) with automatic failover.
- 1 primary (write-path), 2 read replicas (read-path for policy lookups).
- Failover time: <60s (Cloud SQL automatic failover).

**During failover:**
- Write-path pauses (no new interventions).
- Read-path continues (policy lookups still work from replicas).

---

#### Mitigation 2: In-Memory Policy Cache

**Design:** Governor caches `policy_registry` in-memory (refreshed every 60s from Postgres).

**Benefit:** If Postgres is temporarily unreachable (<60s), Governor can still match anomalies against policies (from cache). Plan phase can queue interventions in-memory, then flush to Postgres when it comes back.

**Trade-off:** Cached policies may be stale (up to 60s lag). Acceptable for most policies (policy changes are rare).

---

#### Mitigation 3: Degraded Mode (Monitoring-Only)

**If Postgres is down for >5 minutes:**
1. Governor enters **degraded mode** (logged to `/health` endpoint).
2. Monitor/Analyze phases continue (anomalies detected in-memory, emitted to Pub/Sub event bus).
3. Plan/Execute phases stop (no new interventions).
4. Operator alerted via Telegram ("GOVERNOR DEGRADED — Postgres unreachable for >5 minutes").

**Exit degraded mode:** When Postgres comes back, Governor flushes in-memory incidents to `incident_queue`, processes backlog, resumes Plan/Execute phases.

---

## 6. A2A Pub/Sub Topic Unavailable (Control Plane Broken)

### 6.1 Failure Scenario

**Cause:** A2A Pub/Sub topic deleted, GCP quota exceeded, network partition.

**Impact:**
- Governor cannot send intervention messages to agents (Execute phase broken).
- Agents continue operating normally, but Governor's interventions are not delivered.

---

### 6.2 Detection

**How we know:**
1. **Pub/Sub publish errors** — if Governor's `governor-control` Pub/Sub publisher emits >10 consecutive publish failures → alert.
2. **Intervention ACK timeout** — if Governor sends intervention message but agent doesn't ACK within 60s (Pub/Sub message not delivered) → alert.

---

### 6.3 Mitigation

#### Mitigation 1: Fallback to Direct HTTP

**Design:** If A2A Pub/Sub topic is unavailable, Governor falls back to direct HTTP POST to agent's `/governor/intervention` endpoint.

**Agent requirement:** Every agent MUST expose `/governor/intervention` HTTP endpoint (even after A2A is fully deployed, for fallback).

**Endpoint schema:**

```http
POST /governor/intervention
Content-Type: application/json
Authorization: Bearer <governor_token>

{
  "intervention_id": "uuid",
  "action_verb": "KILL",
  "session_id": "abc-123",
  "reasoning": "..."
}
```

**Agent response:**

```json
{
  "status": "applied" | "failed",
  "error": "..." // if failed
}
```

**Drawback:** Governor must know agents' network locations (defeats decoupling benefit of A2A). Acceptable as fallback-only path.

---

#### Mitigation 2: Pub/Sub Dead-Letter Queue

**Design:** If intervention message cannot be delivered after 5 attempts (Pub/Sub retry), message is sent to dead-letter queue (`governor-control-dlq` topic).

**Governor subscribes to DLQ:**
1. Reads failed intervention messages.
2. Retries via fallback HTTP POST.
3. If HTTP POST also fails → logs to `intervention_log` with `outcome=failed`, alerts operator.

---

## 7. Per-Action Fail-Open / Fail-Closed Table

**When Governor is unavailable (or action delivery fails), what happens?**

| Action Verb | Default Behavior | Rationale |
|-------------|------------------|-----------|
| **Warn** | Fail-open (drop) | Non-critical; agent continues without warning. |
| **Throttle** | Fail-soft (agent applies local default throttle from `limits.yaml`) | Conservative; agent self-limits to prevent runaway cost. |
| **Kill** | **Fail-closed (NEVER auto-kill without Governor confirmation)** | Too destructive; killing a healthy agent without confirmation is worse than letting a misbehaving agent run. |
| **Escalate** | Fail-open (agent escalates directly to Telegram via existing F21/F32 handlers) | Must reach human; bypassing Governor is acceptable. |
| **Custom Webhook** | Fail-open (log failure, don't retry) | External service unavailability is not Governor's problem. |

**Critical invariant:** **NEVER auto-kill an agent if Governor is unavailable.** Kill interventions require explicit Governor confirmation (or human approval via Telegram inline keyboard).

---

## 8. ML Anomaly Detector Misbehaves (Tier 2 False Positives)

### 8.1 Failure Scenario

**Cause:** ML model (Isolation Forest / One-class SVM) is poorly trained, or fleet behavior has shifted (concept drift), causing Tier 2 detector to flag normal agents as anomalous.

**Impact:**
- Tier 2 detector emits false-positive anomalies → Plan phase queues unwarranted interventions.
- If policies are configured to auto-execute based on Tier 2 anomalies → healthy agents throttled/killed.

---

### 8.2 Mitigation

#### Mitigation 1: ML Anomalies Require Human Approval (Default)

**Policy constraint:** Any policy triggered by `detector=ml:*` (Tier 2) MUST have `requires_human_approval=true` for destructive actions (throttle, kill).

**Enforcement:** `PolicyService.Create` rejects policies with `detector=ml:*` + `action_verb=KILL` + `requires_human_approval=false`.

**Justification:** ML models are less trustworthy than deterministic rules (Tier 1); require human-in-the-loop for destructive actions.

---

#### Mitigation 2: Model Retraining Trigger

**Design:** If ML detector's false-positive rate exceeds threshold (e.g., >20% of ML-triggered interventions are manually rejected by operator within 7 days), Governor auto-triggers model retraining.

**Retraining workflow:**
1. Governor exports last 10,000 spans from each agent (labeled as "normal").
2. Retrains Isolation Forest on new dataset.
3. Validates new model on hold-out set (last 1,000 spans).
4. If new model's false-positive rate <10%, deploys new model (hot-swap).
5. If new model is still >10% false-positive rate → alerts operator ("ML detector retraining failed — manual intervention required").

**Justification:** Automated retraining mitigates concept drift without requiring operator to manually retrain models.

---

## 9. Leader Election Failure (Split-Brain)

### 9.1 Failure Scenario

**Cause:** Postgres advisory lock expires but old leader doesn't realize it (network partition, clock skew). Two replicas believe they are the leader.

**Impact:**
- **Duplicate interventions** — both leaders publish to `governor-control` Pub/Sub topic, agents receive same intervention twice.
- **Conflicting interventions** — leader A says "throttle," leader B says "kill" (if policies have been updated and replicas are out of sync).

---

### 9.2 Detection

**How we know:**
1. **Postgres lock contention** — if Postgres reports >1 holder of `governor_lock_id` → split-brain.
2. **Intervention ID collision** — if two interventions are published with different `intervention_id` but same `anomaly_id` within <5s → duplicate intervention.

---

### 9.3 Mitigation

#### Mitigation 1: Fencing Token (Postgres Lock Epoch)

**Design:** Postgres advisory lock includes an **epoch** (monotonically increasing integer stored in a dedicated table).

**Leader election protocol:**
1. Replica acquires advisory lock.
2. Reads current epoch from `leader_epoch` table.
3. Increments epoch, writes back to table.
4. Publishes interventions with `epoch=N` in message metadata.

**Agent-side validation:**
1. Agent receives intervention message.
2. Checks `epoch` field.
3. If `epoch < last_seen_epoch` → stale message (old leader after split-brain), discard.
4. If `epoch >= last_seen_epoch` → valid message, apply intervention, update `last_seen_epoch`.

**Benefit:** Even if split-brain occurs, agents only apply interventions from the latest leader (highest epoch).

---

#### Mitigation 2: Circuit Breaker on Duplicate Interventions

**Pattern:** If agents receive >2 interventions for the same `anomaly_id` within 5s → split-brain detected.

**Agent behavior:**
1. Agent applies first intervention.
2. Discards second intervention (logged as `DUPLICATE_INTERVENTION`).
3. Emits alert to Telegram ("Split-brain suspected — duplicate intervention received").

**Governor-side detection:** If `governor_interventions_total{outcome="duplicate"}` metric spikes (>5 duplicates in 5 minutes) → alert operator.

---

## 10. Summary of Mitigations

| Failure Mode | Detection | Mitigation | Fail-Open / Fail-Closed |
|--------------|-----------|------------|-------------------------|
| **Governor unavailable** | Health check, Prometheus scrape failure, agent dead-man-switch | HA deployment (3 replicas), agent safe-mode, sidecar buffering | Fail-open (fleet continues), fail-closed (interventions paused) |
| **Governor misbehaves (false positive kill)** | Agent reports kill, intervention rate spike | Reasoning transparency, human review SLA, circuit breaker, dry-run mode | N/A (active mitigation) |
| **Telemetry pipeline broken** | OTLP queue depth = 0, A2A lag >10K, Prometheus scrape failure | Self-health check, agent dead-man-switch, redundant telemetry paths | Fail-open (agent safe-mode) |
| **Postgres unavailable** | Health check, Plan phase errors | Cloud SQL HA, in-memory policy cache, degraded mode | Fail-closed (Plan/Execute stop) |
| **A2A Pub/Sub unavailable** | Pub/Sub publish errors, intervention ACK timeout | Fallback to direct HTTP, dead-letter queue | Fail-open (escalate directly to Telegram) |
| **ML detector false positives** | Intervention rejection rate >20% | ML anomalies require human approval, model retraining trigger | N/A (human-in-the-loop) |
| **Leader election split-brain** | Postgres lock contention, intervention ID collision | Fencing token (epoch), circuit breaker on duplicates | N/A (active mitigation) |

---

**Next:** See `scaling.md` for horizontal scaling patterns and capacity planning.
