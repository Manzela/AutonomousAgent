# Phase 3 Metacog Governor — API Specification

**Version:** 1.0-draft
**Date:** 2026-05-21
**Status:** Design spec (not implemented)

---

## 1. Overview

The Governor exposes three distinct API surfaces:

1. **Control Plane** (gRPC) — human operators and automation manage policies, trigger manual interventions, query incidents.
2. **Data Plane** (streaming ingest) — agents emit telemetry via OTLP, A2A messages, cost meters.
3. **Event Bus** (Pub/Sub) — downstream consumers (dashboards, alerting) subscribe to Governor decisions and lifecycle events.

All APIs require authentication. Control plane uses **mTLS** for service-to-service calls and **OIDC** for human users (Google Identity-Aware Proxy for web UI). Data plane uses **mTLS** between sidecars and Governor. Event bus uses **GCP Pub/Sub IAM** for subscription authorization.

---

## 2. Control Plane (gRPC API)

**Transport:** gRPC with TLS 1.3.
**Auth:** mTLS for service-to-service; OIDC tokens for human users (via IAP proxy).
**Port:** `:50051` (gRPC) or `:8080` (gRPC-Web for browser clients).
**Rate limits:** 100 req/sec per authenticated principal (token bucket, enforced at API gateway).

### 2.1 Policy Management

#### `PolicyService.Create`

Create a new policy. Returns the created policy with auto-assigned ID and version 1.

**Request:**

```protobuf
message CreatePolicyRequest {
  string name = 1;                // unique policy name (e.g., "loop-detection-aggressive")
  PolicySpec spec = 2;            // policy definition (see schema below)
}

message PolicySpec {
  repeated TriggerCondition triggers = 1;
  repeated ActionSpec actions = 2;
  Severity severity = 3;          // HIGH / MEDIUM / LOW

  message TriggerCondition {
    oneof condition_type {
      FCodeTrigger f_code = 1;
      MetricThresholdTrigger metric = 2;
      A2APatternTrigger a2a_pattern = 3;
    }
  }

  message FCodeTrigger {
    string f_code = 1;              // "F34" / "F35" / etc.
    int32 occurrence_count = 2;     // fire after N occurrences
    int32 window_minutes = 3;       // within this time window
  }

  message MetricThresholdTrigger {
    string metric_name = 1;         // "cost_per_agent_per_day"
    string condition = 2;           // "> 50" (string expression, parsed server-side)
  }

  message A2APatternTrigger {
    string pattern = 1;             // "delegation_loop" / "fan_out_explosion"
  }

  message ActionSpec {
    ActionVerb verb = 1;            // WARN / THROTTLE / KILL / ESCALATE / CUSTOM_WEBHOOK
    bool requires_human_approval = 2;
    map<string, string> parameters = 3;  // verb-specific (e.g., throttle rate)
  }

  enum ActionVerb {
    ACTION_VERB_UNSPECIFIED = 0;
    WARN = 1;
    THROTTLE = 2;
    KILL = 3;
    ESCALATE = 4;
    CUSTOM_WEBHOOK = 5;
  }

  enum Severity {
    SEVERITY_UNSPECIFIED = 0;
    HIGH = 1;
    MEDIUM = 2;
    LOW = 3;
  }
}
```

**Response:**

```protobuf
message CreatePolicyResponse {
  Policy policy = 1;

  message Policy {
    string id = 1;                  // auto-assigned UUID
    string name = 2;
    PolicySpec spec = 3;
    int32 version = 4;              // starts at 1
    google.protobuf.Timestamp created_at = 5;
    string created_by = 6;          // authenticated principal (email or service account)
  }
}
```

**Error codes:**
- `INVALID_ARGUMENT` — malformed spec (e.g., invalid F-code, unparsable metric condition).
- `ALREADY_EXISTS` — policy with this name already exists.
- `PERMISSION_DENIED` — caller lacks `governor.policies.create` IAM permission.

---

#### `PolicyService.Get`

Retrieve a policy by name. Returns the latest version unless `version` parameter is specified.

**Request:**

```protobuf
message GetPolicyRequest {
  string name = 1;                // policy name
  int32 version = 2;              // optional; if omitted, returns latest version
}
```

**Response:**

```protobuf
message GetPolicyResponse {
  Policy policy = 1;
}
```

**Error codes:**
- `NOT_FOUND` — no policy with this name (or version).

---

#### `PolicyService.Update`

Update an existing policy. Creates a new version (old versions remain immutable for audit).

**Request:**

```protobuf
message UpdatePolicyRequest {
  string name = 1;
  PolicySpec spec = 2;            // new spec (replaces entire spec; no partial updates)
}
```

**Response:**

```protobuf
message UpdatePolicyResponse {
  Policy policy = 1;              // version incremented
}
```

**Error codes:**
- `NOT_FOUND` — policy doesn't exist.
- `INVALID_ARGUMENT` — malformed spec.

---

#### `PolicyService.Delete`

Soft-delete a policy (marks as `deleted=true`; does not remove from DB). Deleted policies no longer trigger, but remain in DB for audit.

**Request:**

```protobuf
message DeletePolicyRequest {
  string name = 1;
}
```

**Response:**

```protobuf
message DeletePolicyResponse {
  // empty
}
```

---

#### `PolicyService.List`

List all active policies (excludes deleted).

**Request:**

```protobuf
message ListPoliciesRequest {
  int32 page_size = 1;            // max 100
  string page_token = 2;          // pagination token from previous response
}
```

**Response:**

```protobuf
message ListPoliciesResponse {
  repeated Policy policies = 1;
  string next_page_token = 2;
}
```

---

### 2.2 Manual Interventions

#### `InterventionService.ExecuteManual`

Trigger a manual intervention (bypasses policy matching; human operator directly commands an action).

**Request:**

```protobuf
message ExecuteManualInterventionRequest {
  string agent_id = 1;
  string session_id = 2;          // optional; omit to target entire agent (not just a session)
  ActionVerb action = 3;
  map<string, string> parameters = 4;
  string reasoning = 5;           // human-provided justification
}
```

**Response:**

```protobuf
message ExecuteManualInterventionResponse {
  string intervention_id = 1;     // UUID of the intervention log entry
  InterventionStatus status = 2;  // PENDING / EXECUTING / COMPLETED / FAILED

  enum InterventionStatus {
    INTERVENTION_STATUS_UNSPECIFIED = 0;
    PENDING = 1;                  // queued for execution
    EXECUTING = 2;
    COMPLETED = 3;
    FAILED = 4;
  }
}
```

**Error codes:**
- `NOT_FOUND` — agent_id doesn't exist in agent_registry.
- `PERMISSION_DENIED` — caller lacks `governor.interventions.execute` IAM permission.

---

### 2.3 Incident Query

#### `IncidentService.Get`

Retrieve details of a specific incident (anomaly detected by Analyze phase).

**Request:**

```protobuf
message GetIncidentRequest {
  string incident_id = 1;         // UUID
}
```

**Response:**

```protobuf
message GetIncidentResponse {
  Incident incident = 1;

  message Incident {
    string id = 1;
    google.protobuf.Timestamp detected_at = 2;
    string agent_id = 3;
    string session_id = 4;
    string detector = 5;          // "rule:f_code_aggregation" / "ml:isolation_forest"
    Severity severity = 6;
    string description = 7;
    google.protobuf.Struct evidence = 8;  // JSON blob (f_codes, timestamps, etc.)
    IncidentState state = 9;      // OPEN / ACKNOWLEDGED / RESOLVED

    enum IncidentState {
      INCIDENT_STATE_UNSPECIFIED = 0;
      OPEN = 1;
      ACKNOWLEDGED = 2;           // human operator viewed it
      RESOLVED = 3;               // intervention applied or manually closed
    }
  }
}
```

---

#### `IncidentService.List`

List incidents with filtering.

**Request:**

```protobuf
message ListIncidentsRequest {
  string agent_id = 1;            // optional filter
  Severity severity = 2;          // optional filter
  IncidentState state = 3;        // optional filter
  google.protobuf.Timestamp start_time = 4;  // time range filter (inclusive)
  google.protobuf.Timestamp end_time = 5;
  int32 page_size = 6;
  string page_token = 7;
}
```

**Response:**

```protobuf
message ListIncidentsResponse {
  repeated Incident incidents = 1;
  string next_page_token = 2;
}
```

---

#### `IncidentService.Acknowledge`

Mark an incident as acknowledged (human operator has reviewed it).

**Request:**

```protobuf
message AcknowledgeIncidentRequest {
  string incident_id = 1;
  string notes = 2;               // optional human notes
}
```

**Response:**

```protobuf
message AcknowledgeIncidentResponse {
  Incident incident = 1;          // state updated to ACKNOWLEDGED
}
```

---

### 2.4 Fleet View

#### `AgentRegistryService.Get`

Retrieve details of a specific agent.

**Request:**

```protobuf
message GetAgentRequest {
  string agent_id = 1;
}
```

**Response:**

```protobuf
message GetAgentResponse {
  Agent agent = 1;

  message Agent {
    string id = 1;
    google.protobuf.Timestamp last_seen_at = 2;
    repeated string capabilities = 3;  // tool names this agent supports
    HealthStatus health_status = 4;
    map<string, string> metadata = 5;  // custom key-value pairs (e.g., version, deploy region)

    enum HealthStatus {
      HEALTH_STATUS_UNSPECIFIED = 0;
      HEALTHY = 1;
      DEGRADED = 2;
      OFFLINE = 3;
    }
  }
}
```

---

#### `AgentRegistryService.List`

List all agents in the fleet.

**Request:**

```protobuf
message ListAgentsRequest {
  HealthStatus health_status = 1; // optional filter
  int32 page_size = 2;
  string page_token = 3;
}
```

**Response:**

```protobuf
message ListAgentsResponse {
  repeated Agent agents = 1;
  string next_page_token = 2;
}
```

---

#### `CapabilityService.Get`

Retrieve capabilities of a specific agent.

**Request:**

```protobuf
message GetCapabilityRequest {
  string agent_id = 1;
}
```

**Response:**

```protobuf
message GetCapabilityResponse {
  repeated Capability capabilities = 1;

  message Capability {
    string name = 1;              // tool name or feature flag
    string version = 2;           // semver
    google.protobuf.Timestamp registered_at = 3;
  }
}
```

---

## 3. Data Plane (Streaming Ingest)

### 3.1 OTLP Receiver (OpenTelemetry Protocol)

**Purpose:** Ingest spans and metrics from agent sidecars.

**Endpoints:**
- **gRPC:** `:4317` (`opentelemetry.proto.collector.trace.v1.TraceService/Export`, `opentelemetry.proto.collector.metrics.v1.MetricsService/Export`)
- **HTTP:** `:4318` (`/v1/traces`, `/v1/metrics`)

**Auth:** mTLS (sidecars present client certificates; Governor validates against CA).

**Span attributes (custom, in addition to standard OpenInference attributes):**

| Attribute | Type | Description |
|-----------|------|-------------|
| `agent_id` | string | Unique agent identifier (e.g., "hermes-1") |
| `session_id` | string | Session or task ID |
| `f_code` | string | F-code emitted during this span (if any) |
| `intervention_id` | string | If this span is the result of an intervention |

**Example OTLP trace export (simplified JSON representation):**

```json
{
  "resourceSpans": [{
    "resource": {
      "attributes": [
        {"key": "service.name", "value": {"stringValue": "hermes-agent"}},
        {"key": "agent_id", "value": {"stringValue": "hermes-1"}}
      ]
    },
    "scopeSpans": [{
      "spans": [{
        "traceId": "abc123...",
        "spanId": "def456...",
        "name": "llm.chat_completion",
        "attributes": [
          {"key": "llm.model_name", "value": {"stringValue": "claude-opus-4"}},
          {"key": "llm.token_count.prompt", "value": {"intValue": 1234}},
          {"key": "llm.token_count.completion", "value": {"intValue": 567}},
          {"key": "session_id", "value": {"stringValue": "task-abc-123"}},
          {"key": "f_code", "value": {"stringValue": "F34"}}
        ]
      }]
    }]
  }]
}
```

**Rate limits:** 50,000 spans/sec per Governor instance (batched). Sidecars that exceed this are throttled (HTTP 429 or gRPC `RESOURCE_EXHAUSTED`).

---

### 3.2 A2A Message Subscriber

**Purpose:** Ingest inter-agent A2A messages for cross-agent observability.

**Transport:** GCP Pub/Sub pull subscription on `a2a-fleet-messages` topic.

**Subscription config:**
- `enableMessageOrdering: true` (ordering key = `sender_agent_id`)
- `ackDeadlineSeconds: 60`
- `maxDeliveryAttempts: 5` (after 5 failures, message sent to dead-letter queue)

**Message schema (Pub/Sub message body is JSON):**

```json
{
  "message_id": "uuid",
  "sender_agent_id": "hermes-1",
  "receiver_agent_id": "hermes-2",
  "message_type": "DELEGATE_TASK" | "TASK_COMPLETION" | "ESCALATE" | "CAPABILITY_QUERY",
  "payload": {
    // message-type-specific payload (arbitrary JSON)
  },
  "timestamp": "2026-05-21T14:32:00Z"
}
```

**Processing:**
- Governor receives message from Pub/Sub.
- Writes to `a2a_message_log` table (Postgres, indexed by `sender_agent_id`, `receiver_agent_id`, `timestamp`).
- Analyze phase queries this table for A2A-aware anomaly patterns (delegation loop, fan-out explosion, no-response stall).
- ACKs message.

---

### 3.3 Cost Meter Ingest

**Purpose:** Ingest per-invocation cost events for budget anomaly detection.

**Transport:** Same OTLP receiver as spans, but using **OTel metrics** (not spans). Agents emit cost as a **Counter** metric named `agent.cost.total_usd`.

**Metric schema (OTel Metrics protobuf):**

```json
{
  "resourceMetrics": [{
    "resource": {
      "attributes": [
        {"key": "agent_id", "value": {"stringValue": "hermes-1"}},
        {"key": "session_id", "value": {"stringValue": "task-abc-123"}}
      ]
    },
    "scopeMetrics": [{
      "metrics": [{
        "name": "agent.cost.total_usd",
        "description": "Cumulative cost in USD for this session",
        "unit": "USD",
        "sum": {
          "dataPoints": [{
            "asDouble": 0.42,
            "timeUnixNano": 1716300720000000000
          }],
          "aggregationTemporality": "AGGREGATION_TEMPORALITY_CUMULATIVE",
          "isMonotonic": true
        }
      }]
    }]
  }]
}
```

**Processing:**
- Governor receives metric via OTLP.
- Writes to `cost_events` table (Postgres, indexed by `agent_id`, `session_id`, `timestamp`).
- Analyze phase runs daily aggregation query: `SELECT agent_id, SUM(cost_usd) FROM cost_events WHERE timestamp >= NOW() - INTERVAL '1 day' GROUP BY agent_id` → if any agent exceeds threshold (e.g., $50/day), emit anomaly.

---

### 3.4 Prometheus Scrape (Pool Metrics)

**Purpose:** Pull-based polling of agent pool metrics (Firecracker pool size, DB connection pool, request queue depth).

**Endpoints (exposed by each sidecar):**
- `http://<sidecar>:9090/metrics` (Prometheus format)

**Governor scrape config:**
- Scrape interval: 15s
- Timeout: 5s
- Targets: All sidecars in `agent_registry` with `health_status=HEALTHY`

**Example metrics (Prometheus exposition format):**

```
# HELP agent_firecracker_pool_size Number of active Firecracker microVMs
# TYPE agent_firecracker_pool_size gauge
agent_firecracker_pool_size{agent_id="hermes-1"} 3

# HELP agent_db_connection_pool_active Active database connections
# TYPE agent_db_connection_pool_active gauge
agent_db_connection_pool_active{agent_id="hermes-1"} 7

# HELP agent_request_queue_depth Queued requests waiting for worker
# TYPE agent_request_queue_depth gauge
agent_request_queue_depth{agent_id="hermes-1"} 12
```

**Processing:**
- Governor scrapes metrics from each sidecar every 15s.
- Writes to in-memory time-series store (Prometheus TSDB embedded in Governor, or external Prometheus server).
- Analyze phase queries this data for pool-exhaustion anomalies (e.g., `agent_request_queue_depth > 100` for >60s → anomaly).

---

## 4. Event Bus (Pub/Sub for Downstream Consumers)

**Purpose:** Governor publishes lifecycle events for downstream consumers (dashboards, alerting, audit logs).

**Transport:** GCP Pub/Sub topics.

### 4.1 Topics

| Topic name | Purpose | Message schema |
|------------|---------|----------------|
| `governor-decisions` | Every intervention decision (warn / throttle / kill / escalate) | `InterventionDecision` (JSON) |
| `governor-incidents` | Incident lifecycle events (created / acknowledged / resolved) | `IncidentEvent` (JSON) |
| `governor-policy-changes` | Policy creation / update / deletion | `PolicyChangeEvent` (JSON) |

### 4.2 Message Schemas

#### `InterventionDecision` (published to `governor-decisions`)

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

**Downstream consumers:**
- **Grafana dashboard** — subscribes to visualize intervention rate over time.
- **PagerDuty integration** — subscribes to create incidents for high-severity interventions.
- **Audit log ingestion** — subscribes to write to long-term cold storage (GCS) for compliance.

---

#### `IncidentEvent` (published to `governor-incidents`)

```json
{
  "incident_id": "uuid",
  "event_type": "CREATED" | "ACKNOWLEDGED" | "RESOLVED",
  "agent_id": "hermes-1",
  "session_id": "task-abc-123",
  "severity": "HIGH" | "MEDIUM" | "LOW",
  "detector": "rule:f_code_aggregation",
  "description": "F34 (F-LOOP) fired 4 times in 8 minutes",
  "timestamp": "2026-05-21T14:32:00Z"
}
```

**Downstream consumers:**
- **Slack/Telegram alerting bot** — subscribes to send real-time alerts for `severity=HIGH` incidents.
- **Incident timeline dashboard** — subscribes to build a chronological view of all incidents.

---

#### `PolicyChangeEvent` (published to `governor-policy-changes`)

```json
{
  "policy_name": "loop-detection-aggressive",
  "event_type": "CREATED" | "UPDATED" | "DELETED",
  "version": 3,
  "changed_by": "user:daniel@example.com",
  "timestamp": "2026-05-21T14:30:00Z"
}
```

**Downstream consumers:**
- **Audit trail** — subscribes to log all policy changes for compliance review.
- **Config drift detector** — subscribes to compare policy state with Infrastructure-as-Code (Terraform) definitions.

---

## 5. Authentication & Authorization

### 5.1 Control Plane (gRPC)

**Service-to-service (mTLS):**
- Clients (e.g., automation scripts, CI/CD pipelines) present client certificates signed by the Governor's CA.
- Governor validates certificate chain + checks Common Name (CN) against allowlist of service principals.

**Human users (OIDC):**
- Web UI (gRPC-Web) fronted by **Google Identity-Aware Proxy (IAP)**.
- User authenticates via Google SSO; IAP injects `X-Goog-IAP-JWT-Assertion` header.
- Governor validates JWT signature + extracts user email → maps to IAM permissions.

**IAM permissions (GCP-style):**
- `governor.policies.create` — create new policies
- `governor.policies.update` — update existing policies
- `governor.policies.delete` — delete policies
- `governor.policies.get` — read policies
- `governor.interventions.execute` — trigger manual interventions
- `governor.incidents.get` — read incidents
- `governor.incidents.acknowledge` — acknowledge incidents

**Permission binding example (Terraform):**

```hcl
resource "google_project_iam_binding" "governor_admin" {
  project = "my-project"
  role    = "roles/governor.admin"  # custom role with all permissions above
  members = [
    "user:daniel@example.com",
    "serviceAccount:ci-pipeline@my-project.iam.gserviceaccount.com"
  ]
}
```

### 5.2 Data Plane (mTLS)

**Sidecar → Governor:**
- Sidecars present client certificates (auto-provisioned via Workload Identity on GCP).
- Governor validates certificate + extracts `agent_id` from certificate SAN (Subject Alternative Name).
- Rate limits enforced per `agent_id`.

### 5.3 Event Bus (GCP Pub/Sub IAM)

**Publisher (Governor → Pub/Sub):**
- Governor runs as a GCP service account with `roles/pubsub.publisher` on topics `governor-decisions`, `governor-incidents`, `governor-policy-changes`.

**Subscribers (downstream consumers):**
- Each consumer has a dedicated service account with `roles/pubsub.subscriber` on the relevant topic.
- Subscriptions are pull-based; consumers authenticate via ADC (Application Default Credentials).

---

## 6. Rate Limits

| API | Limit | Enforcement | Exceeded response |
|-----|-------|-------------|-------------------|
| Control Plane (gRPC) | 100 req/sec per principal | Token bucket at API gateway | gRPC `RESOURCE_EXHAUSTED` |
| OTLP receiver | 50,000 spans/sec per Governor instance | Batched processing + queue | HTTP 429 or gRPC `RESOURCE_EXHAUSTED` |
| A2A subscriber | No limit (Pub/Sub handles backpressure) | Pub/Sub flow control | N/A (Pub/Sub buffers) |
| Prometheus scrape | 1 req per sidecar per 15s | Governor-side scrape loop | N/A (Governor controls scrape) |

**Overload shedding:**
- If Governor's internal queue depth exceeds 10,000 spans, new OTLP requests are rejected with `RESOURCE_EXHAUSTED`.
- Sidecars implement exponential backoff (500ms, 1s, 2s, 4s, 8s) before retrying.

---

## 7. Versioning & Backward Compatibility

**API versioning:** All gRPC services include version in package name (e.g., `governor.v1.PolicyService`, `governor.v2.PolicyService`). Breaking changes require a new major version. Minor/patch changes are backward-compatible additions (new fields with default values).

**Policy schema versioning:** Each policy has a `version` field. When a policy is updated, a new row is inserted with incremented version. Old versions remain in DB for audit. Only the latest version is active unless explicitly pinned.

**OTLP/OTel schema stability:** Governor consumes standard OpenTelemetry schemas (stable as of OTel v1.0). Custom span attributes (e.g., `agent_id`, `f_code`) are additive; removing one would break telemetry (requires deprecation notice + 6-month sunset period).

---

**Next:** See `observability-spec.md` for signals consumed/emitted and dashboarding conventions.
