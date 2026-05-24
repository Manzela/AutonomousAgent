# Phase 3 Metacog Governor — Architecture

**Version:** 1.0-draft
**Date:** 2026-05-21
**Status:** Design spec (not implemented)
**Dependencies:** A2A integration (Q4 ADR-0008), Postgres Phase 2 (Q3 ADR-0008)
**Strategic context:** ADR-0008 Q7 disposition — failure-matrix detectors (F34/F35/F36) cover H1 2026; standalone Governor deferred to Phase 3 when multi-agent coordination creates demand for centralized behavioral observability.

---

## 1. Executive summary

The **Metacog Governor** is a standalone service that monitors the entire agent fleet, detects behavioral anomalies (loops, stalls, runaway costs, policy violations), recommends interventions to a human operator, and executes interventions when authorized. It follows the **MAPE-K** (Monitor → Analyze → Plan → Execute, with shared Knowledge) autonomic computing pattern from IBM's seminal 2003 work (Kephart & Chess, "The Vision of Autonomic Computing").

Unlike the H1 2026 in-process failure detectors (F34/F35/F36 in `lib/durability/runtime_detectors.py`), the Governor provides:
- **Cross-agent observability** — detects fleet-level patterns that no single agent sees.
- **Centralized policy enforcement** — consistent intervention rules across all agents.
- **Learning loop** — incident database captures past interventions so the Governor improves over time.
- **A2A-aware anomaly detection** — subscribes to inter-agent A2A messages for coordination-level behavioral telemetry that wasn't possible pre-A2A.

**Deployment model:** Standalone service with sidecars. Agents emit telemetry to local sidecars (low latency, no single point of failure for data plane); sidecars forward to central Governor (unified view, single source of truth for policy decisions). Control plane actions (kill/throttle/escalate) flow back via A2A control messages.

**Key trade-off:** Introducing the Governor creates a single point of failure for intervention decisions. Failure modes section (§6) addresses this with HA deployment, circuit breakers, and explicit fail-open/fail-closed semantics per action type.

---

## 2. MAPE-K architecture

MAPE-K is a canonical self-adaptive systems pattern: Monitor → Analyze → Plan → Execute, with Knowledge shared across all phases (IBM Autonomic Computing, 2003). Each phase is a distinct subsystem with well-defined inputs/outputs.

```
┌──────────────────────────────────────────────────────────────────────┐
│                         FLEET (N agents)                             │
│  Agent 1 ──→ Sidecar 1 ──┐                                          │
│  Agent 2 ──→ Sidecar 2 ──┼──→ [ Governor ]                          │
│  Agent N ──→ Sidecar N ──┘                                          │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                     ┌──────────────┴──────────────┐
                     │   MAPE-K CYCLE (Governor)   │
                     └──────────────┬──────────────┘
                                    │
        ┌───────────────────────────┴───────────────────────────┐
        │                                                       │
        ▼                                                       ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐   ┌───────────────┐
│   MONITOR     │───▶│    ANALYZE    │───▶│     PLAN      │──▶│   EXECUTE     │
│  (Ingest)     │    │  (Detect)     │    │ (Recommend)   │   │  (Intervene)  │
└───────┬───────┘    └───────┬───────┘    └───────┬───────┘   └───────┬───────┘
        │                    │                    │                   │
        └────────────────────┴────────────────────┴───────────────────┘
                                    │
                            ┌───────▼────────┐
                            │   KNOWLEDGE    │
                            │  (Incident DB, │
                            │   Policies,    │
                            │  Capabilities) │
                            └────────────────┘
```

### 2.1 Monitor (Telemetry Ingest)

**Responsibility:** Collect signals from the agent fleet.

**Data plane (push-based):**
- **OpenInference spans** — per-LLM-call trace trees, emitted via OTLP (gRPC or HTTP). Each span includes `llm.model_name`, `llm.input_messages`, `llm.output_messages`, `llm.token_count.*`, custom attributes (`f_code`, `session_id`, `agent_id`).
- **OTel metrics** — counters/histograms for tool-call rates, session durations, token budgets.
- **F-code events** — structured JSON emitted by agents when any F1-F36 failure is detected (e.g., `{"f_code": "F34", "session_id": "abc", "agent_id": "hermes-1", "timestamp": "..."}`). Forwarded via sidecar to Governor.
- **A2A message stream** — inter-agent coordination messages (agent X requests resource from agent Y, delegation, escalation). Governor subscribes to A2A Pub/Sub topic for cross-agent observability. Message schema includes `sender_agent_id`, `receiver_agent_id`, `message_type`, `payload`.
- **Cost meters** — per-invocation dollar tracking, emitted as structured events (`{"agent_id": "...", "session_id": "...", "cost_usd": 0.42}`).

**Data plane (pull-based):**
- **Prometheus scrape endpoints** — each agent sidecar exposes `/metrics` (Prometheus format). Governor scrapes at 15s intervals for pool metrics (Firecracker pool size, DB connection pool, request queue depth).

**OTLP receiver:** Governor runs a dedicated OTLP gRPC endpoint (`:4317`) and HTTP endpoint (`:4318`). Sidecars forward spans/metrics here.

**A2A subscriber:** Governor instantiates a GCP Pub/Sub pull subscription on the A2A message topic (`a2a-fleet-messages`). Subscription configured with `enableMessageOrdering: true` to preserve per-agent message order (ordering key = `agent_id`).

**Why push + pull hybrid?**
- Push (OTLP, A2A, F-code events): real-time behavioral signals need immediate delivery.
- Pull (Prometheus): pool metrics are polled state, not events; scrape model avoids buffering latency.

### 2.2 Analyze (Anomaly Detection)

**Responsibility:** Detect anomalies from ingested telemetry. Two-tier detection:

**Tier 1: Deterministic rule engine (fast path, <10ms latency).**

Policy-driven pattern matching against incoming events:
- **F-code aggregation** — if agent X emits `F34` (F-LOOP) >3 times in 10 minutes → surface as anomaly.
- **Cost threshold** — if any agent's cumulative daily cost exceeds `$50` → anomaly.
- **Cross-agent A2A pattern** — if agent X sends >10 A2A delegation messages to agent Y in 60s without a completion signal → possible delegation loop → anomaly.
- **Stall detector (fleet-level)** — if agent X has been in `task_status=IN_PROGRESS` for >30 minutes without emitting any tool-call spans → anomaly (complements in-process F35 detector).

**Tier 2: ML anomaly detector (statistical, background batch job).**

Unsupervised drift detection at fleet scale:
- **One-class SVM** or **Isolation Forest** trained on historical "normal" span attribute vectors (session duration, tool-call diversity, token usage rate, A2A message frequency).
- Runs every 15 minutes; consumes the last 1000 spans from each agent.
- Flags agents whose recent behavior vector is >3 standard deviations from the fleet norm.
- Use case: catch novel behavioral patterns that don't match any hard-coded rule (e.g., agent starts calling a new tool in a loop-like pattern not covered by F34's fingerprinting logic).

**Why two-tier?**
- Tier 1 covers known patterns with near-zero latency; policy changes (add a new rule) don't require model retraining.
- Tier 2 catches unknown-unknowns; statistical detector adapts as the fleet evolves.
- Both feed into the same Plan phase (below).

**Outputs:** Structured anomaly events, written to `Knowledge.incident_queue` (Postgres table).

```json
{
  "anomaly_id": "uuid",
  "detected_at": "2026-05-21T14:32:00Z",
  "agent_id": "hermes-1",
  "session_id": "abc-123",
  "detector": "rule:f_code_aggregation" | "ml:isolation_forest",
  "severity": "high" | "medium" | "low",
  "description": "F34 (F-LOOP) fired 4 times in 8 minutes",
  "evidence": {"f_codes": ["F34", "F34", "F34", "F34"], "timestamps": [...]}
}
```

### 2.3 Plan (Intervention Planner)

**Responsibility:** Given a detected anomaly + policy, pick an intervention.

**Policy lookup:** For each anomaly, query `Knowledge.policy_registry` (Postgres table) to find matching policies. Policies are declarative YAML (see `policy-language.md` spec). Each policy specifies:
- **Trigger** — which anomaly patterns activate this policy (F-code, metric threshold, A2A pattern match).
- **Actions** — ordered list of intervention verbs (`warn`, `throttle`, `kill`, `escalate`, `custom_webhook`).
- **Severity** — `high` / `medium` / `low` (affects human escalation SLA).

**Intervention selection logic:**
1. Match anomaly against all policies (can match multiple; policies are AND/OR composable).
2. If multiple policies match, pick the highest-severity policy.
3. Actions in the selected policy are executed in order (e.g., policy says `[warn, escalate]` → planner queues both actions).

**Human-in-the-loop mode vs auto-execute mode (per-intervention authorization):**

Each intervention action has a `requires_human_approval` flag in the policy. If true, planner emits a `pending_intervention` event and waits for human approval via Telegram inline keyboard (existing pattern from F25/F30 handlers). If false, planner immediately queues the action for Execute phase.

**Example policy (loop detection with auto-warn, manual-kill):**

```yaml
policies:
  - name: loop-detection-aggressive
    trigger:
      f_code: F34
      occurrence: 3
      window_minutes: 10
    actions:
      - warn                          # auto-execute
      - escalate                      # auto-execute (Telegram alert)
      - kill:                         # human-in-the-loop (requires approval)
          requires_human_approval: true
    severity: high
```

**Outputs:** Intervention plan (JSON), written to `Knowledge.intervention_queue` (Postgres table). Execute phase consumes this queue.

### 2.4 Execute (Intervention Executor)

**Responsibility:** Execute approved interventions. All actions are A2A control-plane messages sent to the target agent.

**Intervention verbs:**

| Verb | A2A message type | Effect on target agent | Fail-open or fail-closed if Governor down? |
|------|------------------|------------------------|-------------------------------------------|
| `warn` | `GOVERNOR_WARNING` | Agent logs warning, continues task | Fail-open (drop) |
| `throttle` | `GOVERNOR_THROTTLE` | Agent applies rate limit (e.g., max 1 tool-call per 5s) | Fail-soft (agent applies local default throttle) |
| `kill` | `GOVERNOR_KILL_SESSION` | Agent terminates session, releases resources | Fail-closed (NEVER auto-kill without Governor confirmation) |
| `escalate` | `GOVERNOR_ESCALATE` | Agent emits Telegram alert to human operator | Fail-open (agent can escalate directly to Telegram as fallback) |
| `custom_webhook` | (HTTP POST to external URL) | Custom action (e.g., PagerDuty incident) | Fail-open (external service unavailable → log and continue) |

**A2A control-plane integration:** Governor publishes intervention messages to a dedicated `governor-control` Pub/Sub topic. Each agent subscribes to this topic, filtered by `agent_id` (message ordering key). Agent receives intervention message, applies it, and ACKs the Pub/Sub message.

**Audit log (immutable, append-only):** Every intervention is written to `Knowledge.intervention_log` (Postgres table, never updated/deleted) before execution. Schema:

```sql
CREATE TABLE intervention_log (
  id UUID PRIMARY KEY,
  anomaly_id UUID REFERENCES incident_queue(id),
  agent_id TEXT NOT NULL,
  session_id TEXT,
  action_verb TEXT NOT NULL,  -- warn / throttle / kill / escalate / custom_webhook
  approved_by TEXT,           -- 'auto' | 'user:telegram_user_id'
  executed_at TIMESTAMPTZ NOT NULL,
  outcome TEXT,               -- 'success' | 'failed' | 'agent_offline'
  reasoning JSONB             -- policy match + evidence
);
```

**Reasoning transparency:** Every intervention log includes the full reasoning chain (which policy matched, which evidence triggered the policy, which human approved it if manual). This is critical for post-incident review and for the Governor's learning loop.

### 2.5 Knowledge (Shared State)

**Responsibility:** Persistent state shared across MAPE-K phases.

**Data store:** Postgres (reused from Phase 2 per ADR-0008 Q3 disposition). Separate schema `governor_schema` to avoid collision with agent Kanban tables.

**Tables:**

1. **`policy_registry`** — declarative policies (YAML serialized to JSONB column). Supports versioning (each policy update inserts a new row with incremented `version`; only the latest version is active unless explicitly pinned).

2. **`incident_queue`** — anomalies detected by Analyze phase, consumed by Plan phase. Includes TTL (incidents older than 7 days are archived to cold storage).

3. **`intervention_queue`** — intervention plans from Plan phase, consumed by Execute phase. Includes approval status (`pending` / `approved` / `rejected`).

4. **`intervention_log`** — immutable audit log of all executed interventions. Never pruned (regulatory compliance requirement for agent fleet governance).

5. **`agent_registry`** — fleet view. Each row = one agent, includes `agent_id`, `last_seen_at`, `capabilities` (JSONB array of tool names the agent supports), `health_status` (`healthy` / `degraded` / `offline`). Updated by Monitor phase via agent heartbeats.

6. **`capability_cache`** — what each agent can do. Populated from A2A capability negotiation messages. Schema: `(agent_id, capability_name, version, registered_at)`. Allows Plan phase to intelligently pick interventions (e.g., don't send `GOVERNOR_THROTTLE` to an agent that doesn't support throttling).

**Shared across phases:**
- Monitor writes to `agent_registry` (heartbeats) and `incident_queue` (anomaly events).
- Analyze reads `policy_registry`, writes to `incident_queue`.
- Plan reads `incident_queue` + `policy_registry` + `capability_cache`, writes to `intervention_queue`.
- Execute reads `intervention_queue`, writes to `intervention_log`.
- All phases read `agent_registry` for fleet topology.

---

## 3. Deployment model

### 3.1 Architecture options considered

| Model | Pros | Cons | SPoF risk |
|-------|------|------|-----------|
| **In-process (per-agent)** | Tight integration, low latency | No cross-agent view; each agent only sees its own behavior | None (distributed) |
| **Sidecar (per-agent)** | Isolated, observability via local UDS, easy rollout | Still no fleet-level view unless sidecars coordinate | None (distributed, but coordination overhead) |
| **Standalone service (centralized)** | Cross-agent view, single source of truth, fleet-level policy | New SPoF for intervention decisions | **HIGH** |

**Recommendation:** **Hybrid: standalone Governor + sidecars for data plane.**

### 3.2 Recommended deployment (hybrid)

```
┌──────────────────────────────────────────────────────────────────────┐
│                         AGENT FLEET                                  │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐            ┌─────────────┐       │
│  │  Agent 1    │  │  Agent 2    │    ...     │  Agent N    │       │
│  │             │  │             │            │             │       │
│  └──────┬──────┘  └──────┬──────┘            └──────┬──────┘       │
│         │                │                          │               │
│  ┌──────▼──────┐  ┌──────▼──────┐            ┌──────▼──────┐       │
│  │  Sidecar 1  │  │  Sidecar 2  │    ...     │  Sidecar N  │       │
│  │ (OTLP fwd,  │  │ (OTLP fwd,  │            │ (OTLP fwd,  │       │
│  │  A2A sub,   │  │  A2A sub,   │            │  A2A sub,   │       │
│  │  /metrics)  │  │  /metrics)  │            │  /metrics)  │       │
│  └──────┬──────┘  └──────┬──────┘            └──────┬──────┘       │
│         │                │                          │               │
└─────────┼────────────────┼──────────────────────────┼───────────────┘
          │                │                          │
          └────────────────┴──────────────────────────┘
                           │
                   ┌───────▼────────┐
                   │    GOVERNOR    │
                   │   (standalone) │
                   │                │
                   │  - OTLP recv   │
                   │  - A2A sub     │
                   │  - Prometheus  │
                   │  - MAPE-K loop │
                   │  - Postgres    │
                   └───────┬────────┘
                           │
                   ┌───────▼────────┐
                   │  GCP Pub/Sub   │
                   │ (A2A control)  │
                   └────────────────┘
                           │
          ┌────────────────┴──────────────────────────┐
          │                │                          │
      (back to agents via A2A control messages)
```

**Data plane (agent → sidecar → Governor):**
- Agents emit telemetry to local sidecar (via OTLP on localhost, low latency).
- Sidecars forward to Governor's OTLP receiver (batched, max 1000 spans per batch or 10s flush interval).
- If Governor is down, sidecars buffer spans locally (max 10MB per sidecar; after that, drop oldest spans). Sidecars expose a health endpoint that agents can query; if sidecar reports "Governor unreachable for >60s," agents can enter safe-mode (refuse new high-cost operations).

**Control plane (Governor → agent):**
- Governor publishes intervention messages to `governor-control` Pub/Sub topic.
- Each agent subscribes with filter `attributes.agent_id = '<my_agent_id>'`.
- Agent receives intervention, applies it, logs the action locally, ACKs Pub/Sub message.

**Why sidecars?**
- Agents don't need to know Governor's network location (sidecars abstract this).
- Sidecar is the natural rollout boundary for telemetry config changes (e.g., adding a new span attribute).
- If Governor scales horizontally in the future (sharded by agent-id), sidecars can route to the correct Governor shard without agents changing.

### 3.3 HA configuration (mitigating SPoF risk)

**Problem:** Governor is a single point of failure for intervention decisions.

**Solution:** 3-replica active-active Governor with leader election for write-path.

**Write-path (Plan → Execute):** Only the leader writes to `intervention_queue` and publishes to `governor-control` Pub/Sub. Leader election via **Postgres advisory locks** (`pg_try_advisory_lock(<governor_lock_id>)`). Lock held for 30s, refreshed every 10s by the leader. If leader dies, lock expires, another replica claims leadership within 30s.

**Read-path (Monitor → Analyze):** All replicas ingest telemetry (OTLP receiver, A2A subscriber, Prometheus scrape). All replicas run Analyze phase independently and write to `incident_queue`. Incident deduplication handled by unique constraint on `(agent_id, session_id, detector, detected_at)` — if two replicas detect the same anomaly within the same second, only one row is inserted (Postgres UPSERT with `ON CONFLICT DO NOTHING`).

**Deployment:** 3 instances on GCP Cloud Run (or GKE StatefulSet). Load balancer in front for OTLP receiver (gRPC-aware L7 LB). Each replica subscribes to A2A Pub/Sub topic independently (Pub/Sub fan-out).

**Failover time:** <30s (advisory lock expiry). During failover, no interventions are executed (Monitor/Analyze continue, but Plan/Execute paused until new leader elected).

**Alternative (if Postgres advisory locks prove brittle):** Dedicated etcd cluster for leader election. Trade-off: adds operational complexity (another service to run), but etcd's leader election is more battle-tested than Postgres advisory locks at scale.

---

## 4. A2A integration (observability + control)

### 4.1 A2A as the Governor's observability substrate

**Pre-A2A (H1 2026):** Failure detectors (F34/F35/F36) are in-process; each agent only sees its own behavior. No visibility into cross-agent coordination patterns.

**Post-A2A (Phase 3):** A2A message stream is the cross-agent observability layer. Governor subscribes to `a2a-fleet-messages` Pub/Sub topic (all inter-agent messages).

**A2A message schema (example):**

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

**Governor's A2A-aware anomaly patterns:**

1. **Delegation loop** — agent X delegates task T to agent Y, Y delegates back to X, repeat. Detector: if message graph has a cycle within 5 minutes → anomaly.
2. **Delegation fan-out explosion** — agent X delegates to 10 different agents in <60s. Detector: fan-out >10 agents in 60s → anomaly (possible resource exhaustion).
3. **No-response stall** — agent X sends `DELEGATE_TASK` to agent Y, but Y never sends a completion/failure message back within 10 minutes → anomaly (Y may be stuck).

### 4.2 A2A as the Governor's control plane

**Intervention delivery:** Governor publishes intervention messages to `governor-control` Pub/Sub topic. Each agent subscribes to this topic.

**Example intervention message:**

```json
{
  "intervention_id": "uuid",
  "agent_id": "hermes-1",
  "session_id": "abc-123",
  "action": "GOVERNOR_KILL_SESSION",
  "reasoning": "F34 (F-LOOP) fired 4 times in 8 minutes; policy loop-detection-aggressive triggered",
  "approved_by": "user:telegram_12345",
  "timestamp": "2026-05-21T14:35:00Z"
}
```

**Agent-side handler:** Each agent implements an A2A message handler for `governor-control` messages. On receiving `GOVERNOR_KILL_SESSION`, agent:
1. Logs the intervention locally (`governor_kill_session_received`).
2. Terminates the specified session (releases resources, writes checkpoint).
3. Emits a `GOVERNOR_INTERVENTION_APPLIED` A2A message back to Governor (acknowledgment).
4. ACKs the Pub/Sub message.

**Why A2A for control plane (vs direct HTTP to agent)?**
- Decouples Governor from knowing agents' network locations.
- A2A provides natural audit trail (all messages logged by A2A infrastructure).
- If Governor is temporarily down, Pub/Sub buffers intervention messages (up to 7 days by default); agents will receive them when Governor comes back.

### 4.3 A2A-OFF degraded mode

**Before A2A ships (H1 2026):** Governor cannot observe inter-agent traffic (A2A doesn't exist yet). Governor runs in **A2A-OFF mode**:
- Monitor phase skips A2A subscriber.
- Analyze phase disables all A2A-aware anomaly detectors (delegation loop, fan-out explosion, no-response stall).
- Execute phase falls back to direct HTTP POST to agent `/governor/intervention` endpoint (agents must implement this endpoint as a stopgap until A2A control plane is available).

**After A2A ships (Phase 3 trigger):** Governor upgrades to **A2A-ON mode** automatically when the `a2a-fleet-messages` Pub/Sub topic exists and agents start publishing A2A messages. Configuration flag: `GOVERNOR_A2A_ENABLED=true` (set via environment variable).

---

## 5. Scaling

### 5.1 Single-instance capacity

**Assumptions:**
- 100 agents in the fleet.
- Each agent emits 100 spans/sec (aggressive: ~1 span per tool-call, 100 tool-calls/sec per agent).
- Total ingest: **10,000 spans/sec**.
- Each span = ~2KB (serialized protobuf).
- Total bandwidth: 20MB/sec = 1.2GB/min.

**Single Governor instance capacity (GCP Cloud Run, 4 vCPU, 8GB RAM):**
- OTLP receiver (gRPC): ~15,000 spans/sec (batched).
- Analyze phase (rule engine): ~20,000 events/sec (in-memory pattern matching).
- Postgres writes (incident_queue): ~1,000 rows/sec (INSERT batched).
- **Bottleneck:** Postgres writes if every span triggers an anomaly (pathological). Realistic anomaly rate: <1% of spans → 100 anomalies/sec → well within Postgres write capacity.

**Verdict:** Single instance handles ~100 agents @ 100 spans/sec each. No sharding needed until fleet size >200 agents.

### 5.2 Sharding (horizontal scale)

**When:** Fleet size ≥1,000 agents, or total ingest >50,000 spans/sec.

**Strategy:** Hash-partition by `agent_id`.

**Architecture:**
- 10 Governor shards (each handles ~100 agents).
- Each shard is an independent MAPE-K loop with its own Postgres schema (`governor_shard_0`, ..., `governor_shard_9`).
- Sidecars route telemetry to the correct shard via consistent hashing on `agent_id`.
- A2A subscriber is partitioned: each shard subscribes to a filtered Pub/Sub subscription (`a2a-fleet-messages` topic, filter = `hash(agent_id) % 10 == shard_id`).

**Cross-shard anomaly detection:** Some anomalies span multiple shards (e.g., delegation loop between agents on different shards). Solution: dedicated **cross-shard analyzer** (separate service) that reads from all shards' `incident_queue` tables and correlates anomalies. Runs as a batch job every 60s (eventual consistency acceptable for cross-shard patterns).

### 5.3 Knowledge base scaling

**Postgres with read replicas:**
- 1 primary (write-path for intervention_queue, intervention_log).
- 2 read replicas (read-path for policy_registry, agent_registry, capability_cache).
- All replicas in the same GCP region (us-central1) for <10ms replication lag.

**Cold storage for intervention_log:** Incidents older than 90 days archived to GCP Cloud Storage (Parquet format). Governor still queries Postgres for recent incidents (<90 days); historical analysis queries (>90 days) run against BigQuery (external table pointing to GCS Parquet files).

---

## 6. Failure modes (see `failure-modes.md`)

Critical section. Governor is a SPoF; failure modes must be explicitly designed. Detailed in separate `failure-modes.md` document. Summary:

- **Governor down:** Fleet continues operating (fail-open for most interventions). F-codes still detected locally per-agent. HA config (3 replicas, leader election) keeps failover time <30s.
- **Governor misbehaves (false positive kill):** Every intervention logged with reasoning; human review SLA for any auto-kill action; circuit breaker on intervention rate (Governor that's killing >10 agents/hour gets auto-paused).
- **Telemetry pipeline broken:** Agent-side dead-man-switch — if telemetry can't reach sidecar for >60s, agent enters safe-mode (refuses new high-cost operations).

---

## 7. References

1. Kephart, J. O., & Chess, D. M. (2003). **The Vision of Autonomic Computing**. *Computer*, 36(1), 41-50. doi:10.1109/MC.2003.1160055. (IBM autonomic computing seminal paper; introduced MAPE-K loop.)

2. IBM Autonomic Computing Architecture (2006). **An Architectural Blueprint for Autonomic Computing**. IBM White Paper (4th ed.). (MAPE-K reference architecture for self-managing systems.)

3. Weyns, D., et al. (2013). **On Patterns for Decentralized Control in Self-Adaptive Systems**. *Software Engineering for Self-Adaptive Systems II*, LNCS 7475. (SEAMS community survey of MAPE-K variants and extensions.)

4. ADR-0008 (2026). **Phase 3 Multi-Agent Orchestration with RL Training**. RX-Research Project, Stream B. (Strategic context for Q7 disposition: failure-matrix now, standalone Governor in Phase 3.)

5. OpenTelemetry Protocol Specification v1.0.0. (OTLP gRPC/HTTP receiver patterns for telemetry ingest.)

6. Google Cloud Pub/Sub Documentation. **Message Ordering** and **Exactly-Once Delivery**. (A2A message stream reliable delivery patterns.)

---

**Next:** See `apis.md` for detailed API surface (control plane, data plane, event bus).
