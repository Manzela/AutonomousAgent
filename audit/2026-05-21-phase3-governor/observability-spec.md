# Phase 3 Metacog Governor — Observability Specification

**Version:** 1.0-draft
**Date:** 2026-05-21
**Status:** Design spec (not implemented)

---

## 1. Overview

The Governor consumes telemetry from the agent fleet and emits its own telemetry so the Governor's actions are observable. This document specifies:

1. **Signals consumed** — what the Governor ingests from agents.
2. **Signals emitted** — what the Governor publishes for downstream consumers.
3. **Dashboard conventions** — how to visualize Governor and fleet health.
4. **Alerting patterns** — when to escalate to humans (PagerDuty, Telegram).

---

## 2. Signals Consumed (from Agent Fleet)

### 2.1 F-code Events

**Source:** Agents emit structured JSON events when any F1-F36 failure is detected.

**Transport:** Forwarded via sidecar to Governor's OTLP receiver (as a custom OTel event, or as a span attribute if the F-code occurred during a span).

**Schema:**

```json
{
  "f_code": "F34",
  "agent_id": "hermes-1",
  "session_id": "task-abc-123",
  "timestamp": "2026-05-21T14:32:00Z",
  "handler": "interrupt_with_loop_feedback",
  "context": {
    "tool_name": "Bash",
    "tool_args_fingerprint": "sha256:abc123...",
    "occurrence_count": 4
  }
}
```

**Governor's use:** Analyze phase pattern-matches F-code events against policies (e.g., "if F34 occurs 3+ times in 10 minutes → anomaly").

**Retention:** Raw F-code events stored in `f_code_events` table (Postgres) for 90 days; then archived to GCS.

---

### 2.2 OpenInference Spans

**Source:** Per-LLM-call trace trees emitted by agents via OTLP.

**Transport:** Sidecar → Governor OTLP receiver (gRPC `:4317` or HTTP `:4318`).

**Standard attributes (OpenInference convention):**

| Attribute | Type | Example | Description |
|-----------|------|---------|-------------|
| `llm.model_name` | string | `claude-opus-4` | Model invoked |
| `llm.token_count.prompt` | int | `1234` | Prompt tokens |
| `llm.token_count.completion` | int | `567` | Completion tokens |
| `llm.input_messages` | JSON array | `[{"role": "user", "content": "..."}]` | Input messages |
| `llm.output_messages` | JSON array | `[{"role": "assistant", "content": "..."}]` | Output messages |

**Custom attributes (Governor-specific):**

| Attribute | Type | Example | Description |
|-----------|------|---------|-------------|
| `agent_id` | string | `hermes-1` | Unique agent identifier |
| `session_id` | string | `task-abc-123` | Session or task ID |
| `f_code` | string | `F34` | F-code emitted during this span (if any) |
| `intervention_id` | string | `uuid` | If this span is the result of an intervention |
| `cost_usd` | float | `0.0042` | Per-call cost in USD |

**Governor's use:**
- Analyze phase queries spans for behavioral patterns (e.g., session duration >30 minutes without tool-call activity → F35 stall).
- ML anomaly detector (Tier 2) builds feature vectors from span attributes (session duration, tool-call diversity, token usage rate).

**Retention:** Spans stored in Governor's embedded Prometheus TSDB (7 days hot, then exported to Phoenix or Jaeger for long-term storage).

---

### 2.3 A2A Messages (Inter-Agent Communication)

**Source:** A2A Pub/Sub topic (`a2a-fleet-messages`), subscribed by Governor.

**Schema:**

```json
{
  "message_id": "uuid",
  "sender_agent_id": "hermes-1",
  "receiver_agent_id": "hermes-2",
  "message_type": "DELEGATE_TASK",
  "payload": {
    "task_id": "abc-123",
    "task_description": "Investigate PR #42"
  },
  "timestamp": "2026-05-21T14:32:00Z"
}
```

**Governor's use:**
- Analyze phase detects A2A-aware anomalies:
  - **Delegation loop** — message graph has a cycle (A → B → A).
  - **Fan-out explosion** — agent sends >10 delegation messages in <60s.
  - **No-response stall** — agent sends `DELEGATE_TASK` but receiver never sends back completion/failure message within 10 minutes.

**Retention:** A2A messages stored in `a2a_message_log` table (Postgres) for 30 days; then archived to GCS.

---

### 2.4 Cost Meters

**Source:** Agents emit cumulative cost as OTel metrics (Counter named `agent.cost.total_usd`).

**Transport:** Sidecar → Governor OTLP receiver.

**Metric schema (OTel Metrics protobuf, simplified as JSON):**

```json
{
  "metric_name": "agent.cost.total_usd",
  "agent_id": "hermes-1",
  "session_id": "task-abc-123",
  "value": 0.42,
  "timestamp": "2026-05-21T14:32:00Z"
}
```

**Governor's use:** Analyze phase runs daily aggregation query to detect agents exceeding budget threshold (e.g., >$50/day).

**Retention:** Cost events stored in `cost_events` table (Postgres) indefinitely (financial audit requirement).

---

### 2.5 Pool Metrics (Prometheus Scrape)

**Source:** Each sidecar exposes Prometheus `/metrics` endpoint.

**Governor scrapes at 15s intervals.**

**Metrics (Prometheus exposition format):**

```
agent_firecracker_pool_size{agent_id="hermes-1"} 3
agent_db_connection_pool_active{agent_id="hermes-1"} 7
agent_request_queue_depth{agent_id="hermes-1"} 12
```

**Governor's use:** Analyze phase detects pool-exhaustion anomalies (e.g., `agent_request_queue_depth > 100` for >60s → agent is overloaded).

**Retention:** Pool metrics stored in Prometheus TSDB (30 days).

---

## 3. Signals Emitted (from Governor)

### 3.1 Governor Decisions (Intervention Events)

**Published to:** `governor-decisions` Pub/Sub topic.

**Schema:**

```json
{
  "intervention_id": "uuid",
  "incident_id": "uuid",
  "agent_id": "hermes-1",
  "session_id": "task-abc-123",
  "action_verb": "KILL",
  "approved_by": "user:telegram_12345" | "auto",
  "executed_at": "2026-05-21T14:35:00Z",
  "outcome": "success" | "failed" | "agent_offline",
  "reasoning": {
    "policy_name": "loop-detection-aggressive",
    "policy_version": 3,
    "evidence": {
      "f_codes": ["F34", "F34", "F34", "F34"],
      "timestamps": ["...", "...", "...", "..."]
    }
  }
}
```

**Consumers:**
- **Grafana dashboard** — intervention rate over time (aggregated by `action_verb`, `outcome`).
- **PagerDuty integration** — creates incidents for `action_verb=KILL` with `outcome=failed`.
- **Audit log ingestion** — writes to GCS for long-term compliance storage.

**Why reasoning transparency?** Every intervention must be auditable. If an agent is killed, operator can trace back to the exact policy + evidence that triggered the kill.

---

### 3.2 Audit Log (Immutable, Append-Only)

**Stored in:** Postgres `intervention_log` table.

**Also exported to:** GCS (Parquet format) for long-term cold storage (90 days → GCS → BigQuery external table for historical queries).

**Schema:**

```sql
CREATE TABLE intervention_log (
  id UUID PRIMARY KEY,
  anomaly_id UUID REFERENCES incident_queue(id),
  agent_id TEXT NOT NULL,
  session_id TEXT,
  action_verb TEXT NOT NULL,
  approved_by TEXT,
  executed_at TIMESTAMPTZ NOT NULL,
  outcome TEXT,
  reasoning JSONB
);
```

**Immutability guarantee:** No `UPDATE` or `DELETE` allowed (enforced by Postgres RLS policy). Only `INSERT` permitted.

**Consumers:**
- **Compliance review** — auditors query historical interventions for regulatory reporting.
- **Governor learning loop** — ML model trains on past interventions to improve future anomaly detection (e.g., "intervention X was a false positive → downweight this pattern").

---

### 3.3 Dashboards (Grafana)

#### Dashboard 1: Fleet Health Overview

**Panels:**

1. **Agent count by health status** (pie chart) — `HEALTHY` / `DEGRADED` / `OFFLINE`.
   - Data source: `agent_registry` table, query: `SELECT health_status, COUNT(*) FROM agent_registry GROUP BY health_status`.
2. **Total intervention rate** (time series) — interventions per hour, stacked by `action_verb` (WARN / THROTTLE / KILL / ESCALATE).
   - Data source: `intervention_log` table, query: `SELECT DATE_TRUNC('hour', executed_at) AS hour, action_verb, COUNT(*) FROM intervention_log WHERE executed_at >= NOW() - INTERVAL '24 hours' GROUP BY hour, action_verb ORDER BY hour`.
3. **Anomaly detection rate** (time series) — anomalies per hour, by detector type (`rule:*` vs `ml:*`).
   - Data source: `incident_queue` table, query: `SELECT DATE_TRUNC('hour', detected_at) AS hour, detector, COUNT(*) FROM incident_queue WHERE detected_at >= NOW() - INTERVAL '24 hours' GROUP BY hour, detector ORDER BY hour`.

#### Dashboard 2: Per-Agent Health

**Panels:**

1. **Agent uptime** (gauge) — time since `last_seen_at` for selected agent.
   - Data source: `agent_registry` table, query: `SELECT NOW() - last_seen_at AS uptime FROM agent_registry WHERE id = $agent_id`.
2. **F-code histogram** (bar chart) — count of each F-code emitted by selected agent in last 24 hours.
   - Data source: `f_code_events` table, query: `SELECT f_code, COUNT(*) FROM f_code_events WHERE agent_id = $agent_id AND timestamp >= NOW() - INTERVAL '24 hours' GROUP BY f_code ORDER BY COUNT(*) DESC`.
3. **Session duration distribution** (histogram) — distribution of session durations for selected agent.
   - Data source: OpenInference spans (Prometheus TSDB), query: `histogram_quantile(0.95, sum(rate(llm_duration_seconds_bucket{agent_id="$agent_id"}[1h])) by (le))`.

#### Dashboard 3: Incident Timeline

**Panel:** Timeline visualization of all incidents (OPEN / ACKNOWLEDGED / RESOLVED) in last 7 days, color-coded by severity.

**Data source:** `incident_queue` table, query:

```sql
SELECT
  id,
  detected_at,
  agent_id,
  severity,
  state,
  description
FROM incident_queue
WHERE detected_at >= NOW() - INTERVAL '7 days'
ORDER BY detected_at DESC
```

**Interactivity:** Clicking an incident opens a drill-down panel showing:
- Full evidence JSON.
- All interventions triggered by this incident (from `intervention_log`).
- Relevant A2A messages (from `a2a_message_log`) if A2A-aware anomaly.

---

### 3.4 Alerts (PagerDuty / Telegram)

#### Alert 1: High-Severity Incident Created

**Trigger:** `IncidentEvent` published to `governor-incidents` topic with `severity=HIGH`.

**Action:** Send Telegram alert to operator (via existing Telegram bot integration, reused from F21/F32 handlers).

**Message format:**

```
🚨 GOVERNOR ALERT — High-Severity Incident
Agent: hermes-1
Incident ID: abc-123
Description: F34 (F-LOOP) fired 4 times in 8 minutes
Detected at: 2026-05-21 14:32:00 UTC
Policy: loop-detection-aggressive (v3)

Action required: Review incident in dashboard.
Link: https://governor.example.com/incidents/abc-123
```

**De-duplication:** Same incident ID only alerts once (tracked in Telegram bot's in-memory cache, TTL 1 hour).

---

#### Alert 2: Intervention Failed

**Trigger:** `InterventionDecision` published to `governor-decisions` topic with `outcome=failed`.

**Action:** Create PagerDuty incident (severity: HIGH if `action_verb=KILL`, MEDIUM otherwise).

**PagerDuty payload:**

```json
{
  "routing_key": "<pagerduty_integration_key>",
  "event_action": "trigger",
  "dedup_key": "<intervention_id>",
  "payload": {
    "summary": "Governor intervention failed: KILL on hermes-1",
    "severity": "critical",
    "source": "governor",
    "custom_details": {
      "agent_id": "hermes-1",
      "session_id": "task-abc-123",
      "action_verb": "KILL",
      "reasoning": "..."
    }
  }
}
```

---

#### Alert 3: Governor Unhealthy (Self-Health Check)

**Trigger:** Governor's own health check detects degraded state (e.g., Postgres unreachable, OTLP receiver queue depth >10,000).

**Action:** Send Telegram alert + create PagerDuty incident (severity: CRITICAL).

**Message:**

```
🔥 GOVERNOR HEALTH CRITICAL
Issue: Postgres unreachable for >60s
Last successful write: 2026-05-21 14:30:00 UTC
Impact: No new interventions can be executed (Monitor/Analyze continue)
Action required: Check Postgres connectivity immediately.
```

---

## 4. Governor's Own Telemetry (Observing the Observer)

**Problem:** If the Governor is misbehaving, who detects it?

**Solution:** The Governor emits its own telemetry so it can be monitored externally.

### 4.1 Governor Span Attributes (OTel)

Every MAPE-K phase operation emits an OTel span. Example:

**Span name:** `governor.analyze.rule_engine`

**Attributes:**

| Attribute | Type | Example | Description |
|-----------|------|---------|-------------|
| `governor.phase` | string | `analyze` | MAPE-K phase (monitor / analyze / plan / execute) |
| `governor.detector` | string | `rule:f_code_aggregation` | Detector that ran |
| `governor.anomaly_count` | int | `3` | Number of anomalies detected in this cycle |
| `governor.latency_ms` | float | `42.5` | Processing latency |
| `governor.error` | bool | `false` | If this cycle errored |

**Trace propagation:** If an intervention is triggered by an incident, the intervention span's parent is the incident span. Full trace: `agent span → incident span → intervention span`.

**Export destination:** Governor's own spans exported to Phoenix (OTLP) for visualization in the same Phoenix UI where agent spans live. This gives a unified trace view: agent behavior → Governor detection → Governor intervention.

---

### 4.2 Governor Metrics (Prometheus)

Governor exposes `/metrics` endpoint (Prometheus format) for external scraping.

**Metrics:**

```
# HELP governor_incidents_total Total incidents detected
# TYPE governor_incidents_total counter
governor_incidents_total{severity="high",detector="rule:f_code_aggregation"} 42

# HELP governor_interventions_total Total interventions executed
# TYPE governor_interventions_total counter
governor_interventions_total{action_verb="kill",outcome="success"} 5
governor_interventions_total{action_verb="kill",outcome="failed"} 1

# HELP governor_queue_depth Current queue depth for each MAPE-K phase
# TYPE governor_queue_depth gauge
governor_queue_depth{phase="incident_queue"} 7
governor_queue_depth{phase="intervention_queue"} 2

# HELP governor_cycle_latency_seconds MAPE-K cycle latency (p50, p95, p99)
# TYPE governor_cycle_latency_seconds histogram
governor_cycle_latency_seconds_bucket{phase="analyze",le="0.05"} 100
governor_cycle_latency_seconds_bucket{phase="analyze",le="0.1"} 200
...
```

**Who scrapes this?** External Prometheus server (not the Governor itself) scrapes Governor's `/metrics` every 15s. Grafana queries this Prometheus for Governor health dashboards.

---

### 4.3 Governor Health Endpoint

**Endpoint:** `GET /health` (HTTP, port `:8080`)

**Response (healthy):**

```json
{
  "status": "healthy",
  "checks": {
    "postgres": "ok",
    "otlp_receiver": "ok",
    "a2a_subscriber": "ok",
    "leader_election": "ok (leader)"
  },
  "timestamp": "2026-05-21T14:32:00Z"
}
```

**Response (degraded):**

```json
{
  "status": "degraded",
  "checks": {
    "postgres": "unreachable (last success: 65s ago)",
    "otlp_receiver": "ok",
    "a2a_subscriber": "ok",
    "leader_election": "ok (follower)"
  },
  "timestamp": "2026-05-21T14:32:00Z"
}
```

**Who monitors this?** External uptime monitor (e.g., Healthchecks.io, existing F32 pattern) pings `/health` every 60s. If status ≠ `healthy` for >2 minutes → Telegram alert.

---

## 5. Span Attribute Conventions (Governor-Specific)

To ensure Governor's actions are observable and correlatable with agent behavior, all Governor-emitted spans follow these conventions:

| Attribute | Namespace | Type | Description |
|-----------|-----------|------|-------------|
| `governor.phase` | `governor.*` | string | MAPE-K phase (monitor / analyze / plan / execute) |
| `governor.detector` | `governor.*` | string | Detector that ran (e.g., `rule:f_code_aggregation`, `ml:isolation_forest`) |
| `governor.anomaly_id` | `governor.*` | string | UUID of the anomaly (if this span is processing an anomaly) |
| `governor.intervention_id` | `governor.*` | string | UUID of the intervention (if this span is executing an intervention) |
| `governor.policy_name` | `governor.*` | string | Policy that matched (if applicable) |
| `governor.policy_version` | `governor.*` | int | Policy version |
| `governor.error` | `governor.*` | bool | If this cycle errored |

**Correlation:** Agent spans include `intervention_id` attribute if the span is the result of a Governor intervention. This allows tracing: intervention span → agent span (effect).

---

## 6. Retention Policies

| Data type | Hot storage (Postgres / TSDB) | Cold storage (GCS) | Total retention |
|-----------|-------------------------------|---------------------|-----------------|
| F-code events | 90 days | Parquet in GCS, then BigQuery external table | Indefinite |
| OpenInference spans | 7 days (Prometheus TSDB) | Phoenix / Jaeger (30 days) | 30 days |
| A2A messages | 30 days | Parquet in GCS | Indefinite |
| Cost events | Indefinite (financial audit) | N/A | Indefinite |
| Pool metrics | 30 days (Prometheus TSDB) | N/A | 30 days |
| Intervention log | Indefinite (audit requirement) | Parquet in GCS after 90 days | Indefinite |
| Incident queue | 7 days (OPEN/ACKNOWLEDGED), purge RESOLVED after 7d | Parquet in GCS | Indefinite |

**Cold storage export pattern:** Nightly cron job exports Postgres tables to GCS (Parquet format). BigQuery external tables point to GCS for historical queries (>90 days).

---

**Next:** See `policy-language.md` for declarative policy DSL syntax and examples.
