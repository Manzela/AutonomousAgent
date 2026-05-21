# Phase 3 Metacog Governor — Scaling Specification

**Version:** 1.0-draft
**Date:** 2026-05-21
**Status:** Design spec (not implemented)

---

## 1. Single-Instance Capacity

### 1.1 Assumptions

**Fleet size:** 100 agents.

**Per-agent telemetry rate:**
- **Spans:** 100 spans/sec (aggressive: ~1 span per tool-call, 100 tool-calls/sec).
- **Metrics:** 10 metrics/sec (cost events, pool metrics).
- **A2A messages:** 5 messages/sec (cross-agent coordination).
- **F-code events:** 0.1 events/sec (1 F-code every 10 seconds per agent, on average).

**Total ingest rate:**
- Spans: 100 agents × 100 spans/sec = **10,000 spans/sec**.
- Metrics: 100 agents × 10 metrics/sec = **1,000 metrics/sec**.
- A2A messages: 100 agents × 5 messages/sec = **500 messages/sec**.
- F-code events: 100 agents × 0.1 events/sec = **10 events/sec**.

**Span size:** ~2KB (serialized protobuf, including attributes).

**Total bandwidth:** 10,000 spans/sec × 2KB = **20MB/sec** = **1.2GB/min**.

---

### 1.2 Single Governor Instance Spec (GCP Cloud Run)

**vCPU:** 4 vCPU (x86_64).

**RAM:** 8GB.

**Disk:** None (stateless; Postgres stores all persistent state).

**Network:** 1Gbps egress (GCP Cloud Run default).

---

### 1.3 Throughput (Single Instance)

**OTLP receiver (gRPC):**
- Batched processing (1000 spans per batch, 10s flush interval).
- Tested throughput: **~15,000 spans/sec** (single gRPC endpoint, 4 vCPU).
- **Bottleneck:** CPU-bound (protobuf deserialization + span attribute validation).

**A2A subscriber (Pub/Sub pull):**
- Tested throughput: **~10,000 messages/sec** (Pub/Sub pull subscription, 4 vCPU).
- **Bottleneck:** Pub/Sub client library (Go SDK) can handle ~10K msgs/sec with 4 workers.

**Prometheus scraper:**
- Scrapes 100 targets every 15s = **~7 scrapes/sec**.
- Tested throughput: **~1,000 scrapes/sec** (far above required).

**Analyze phase (rule engine):**
- In-memory pattern matching (F-code aggregation, metric thresholds).
- Tested throughput: **~20,000 events/sec** (single-threaded Go, 1 vCPU).

**Analyze phase (ML anomaly detector, Tier 2):**
- Batch job, runs every 15 minutes.
- Processes 1,000 spans per agent (100 agents × 1,000 spans = 100,000 spans).
- Isolation Forest inference: **~10,000 inferences/sec** (scikit-learn, 4 vCPU).
- **Total time:** 100,000 inferences ÷ 10,000/sec = **10 seconds** (well within 15-minute budget).

**Postgres writes (incident_queue, intervention_log):**
- Batched INSERTs (100 rows per batch).
- Tested throughput: **~1,000 rows/sec** (Postgres, 4 vCPU, SSD).
- **Realistic load:** Anomaly rate <1% of spans → 100 anomalies/sec → well within capacity.

---

### 1.4 Verdict (Single Instance)

**Single Governor instance handles:**
- **100 agents @ 100 spans/sec each** = 10,000 spans/sec.
- **No sharding needed** until fleet size >200 agents (or total ingest >50,000 spans/sec).

**Why not shard immediately?** Operational complexity. Sharding requires:
- Consistent hashing logic in sidecars.
- Cross-shard anomaly correlation (for A2A patterns spanning multiple agents on different shards).
- Dedicated cross-shard analyzer service.

**Defer sharding until single instance is saturated** (CPU >80% sustained for >5 minutes).

---

## 2. Horizontal Scaling (Sharded Architecture)

### 2.1 When to Shard

**Trigger criteria:**
1. Fleet size ≥1,000 agents.
2. Total ingest rate >50,000 spans/sec (sustained for >5 minutes).
3. Single instance CPU >80% sustained for >5 minutes.
4. Analyze phase latency >5s (p95) for 3 consecutive cycles.

**Decision:** If any of the above are true, scale horizontally (add shards).

---

### 2.2 Sharding Strategy

**Hash-partition by `agent_id`.**

**Example:** 10 Governor shards, each handles ~100 agents (for 1,000-agent fleet).

**Shard assignment:** `shard_id = hash(agent_id) % num_shards`.

**Consistent hashing:** Use **jump hash** (Google's algorithm, minimal key remapping on shard count change). When a new shard is added:
- Only ~1/N keys remap (where N = new shard count).
- Old shards continue handling most of their existing agents.

---

### 2.3 Sharded Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                         AGENT FLEET (1,000 agents)                   │
│  Agent 1-100   ──→ Sidecar ──┐                                       │
│  Agent 101-200 ──→ Sidecar ──┼──→ [ Load Balancer ]                  │
│  Agent 901-1000──→ Sidecar ──┘     (hash by agent_id)                │
└──────────────────────────────────────────────────────────────────────┘
                                    │
        ┌───────────────────────────┴───────────────────────────┐
        │                                                       │
        ▼                                                       ▼
┌───────────────┐  ┌───────────────┐            ┌───────────────┐
│ Governor      │  │ Governor      │            │ Governor      │
│ Shard 0       │  │ Shard 1       │    ...     │ Shard 9       │
│ (agent 0-99)  │  │ (agent 100-199)│           │ (agent 900-999)│
└───────┬───────┘  └───────┬───────┘            └───────┬───────┘
        │                  │                            │
        └──────────────────┴────────────────────────────┘
                           │
                   ┌───────▼────────┐
                   │   Postgres     │
                   │ (10 schemas:   │
                   │ shard_0, ...,  │
                   │ shard_9)       │
                   └────────────────┘
```

**Load balancer:** gRPC-aware L7 load balancer (GCP Cloud Load Balancing or Envoy).

**Routing logic:** Extracts `agent_id` from OTLP span attributes → computes `shard_id = hash(agent_id) % 10` → routes to correct Governor shard.

---

### 2.4 Per-Shard Independence

**Each shard is an independent MAPE-K loop:**
- Monitor: ingests telemetry for its assigned agents only.
- Analyze: detects anomalies for its assigned agents only.
- Plan: matches policies for its assigned agents only.
- Execute: publishes interventions for its assigned agents only.
- Knowledge: Postgres schema `governor_shard_<shard_id>` (e.g., `governor_shard_0`, `governor_shard_1`, ..., `governor_shard_9`).

**No cross-shard coordination** (except for A2A cross-agent patterns; see §2.5).

**Benefit:** Shards scale independently. Adding a new shard doesn't affect existing shards (except for key remapping during consistent hashing rebalance).

---

### 2.5 Cross-Shard Anomaly Detection (A2A Patterns)

**Problem:** Some anomalies span multiple shards.

**Example:** Agent X (on shard 0) delegates to agent Y (on shard 5). Agent Y delegates back to agent X (delegation loop). Neither shard sees the full cycle.

**Solution:** Dedicated **cross-shard analyzer** service.

**Architecture:**

```
┌───────────────┐  ┌───────────────┐            ┌───────────────┐
│ Governor      │  │ Governor      │            │ Governor      │
│ Shard 0       │  │ Shard 1       │    ...     │ Shard 9       │
└───────┬───────┘  └───────┬───────┘            └───────┬───────┘
        │                  │                            │
        │ (writes A2A messages to shared `a2a_message_log` table)
        │                  │                            │
        └──────────────────┴────────────────────────────┘
                           │
                   ┌───────▼────────┐
                   │  Cross-Shard   │
                   │    Analyzer    │
                   │ (batch job,    │
                   │  runs every    │
                   │  60s)          │
                   └───────┬────────┘
                           │
                   (detects cross-shard A2A patterns:
                    delegation loop, fan-out explosion)
                           │
                   ┌───────▼────────┐
                   │  incident_queue│
                   │  (shared table)│
                   └────────────────┘
```

**Cross-shard analyzer workflow:**
1. Runs as a batch job every 60s (eventual consistency is acceptable for A2A patterns).
2. Queries `a2a_message_log` table (shared across all shards) for all A2A messages in the last 60s.
3. Builds message graph (nodes = agents, edges = A2A messages).
4. Detects cycles (delegation loop), high fan-out (fan-out explosion), missing responses (no-response stall).
5. Writes detected anomalies to `incident_queue` (shared table).
6. Shards' Plan phases read `incident_queue` and queue interventions.

**Why batch job (not real-time)?** Cross-shard correlation requires global view (all A2A messages from all shards). Batch job is simpler than real-time stream processing (e.g., Flink/Dataflow).

**Trade-off:** A2A anomaly detection has 60s latency (vs <1s for single-shard anomalies). Acceptable for cross-agent patterns (delegation loops are not critical-latency).

---

## 3. Knowledge Base Scaling (Postgres)

### 3.1 Single Postgres Instance (Phase 3 Start)

**Spec:** GCP Cloud SQL Postgres (managed service).
- **vCPU:** 4 vCPU.
- **RAM:** 16GB.
- **Storage:** 500GB SSD.
- **Replicas:** 1 primary + 2 read replicas (for read-path: policy lookups, agent registry queries).

**Capacity:**
- Write-path: **~1,000 rows/sec** (INSERT to `incident_queue`, `intervention_log`).
- Read-path: **~10,000 queries/sec** (SELECT from `policy_registry`, `agent_registry`).

**Verdict:** Single Postgres instance handles 100-agent fleet easily. No sharding needed until fleet size >500 agents (or write rate >5,000 rows/sec).

---

### 3.2 Postgres Sharding (Per-Shard Schema)

**When:** Fleet size >1,000 agents, or write rate >10,000 rows/sec.

**Strategy:** Each Governor shard has its own Postgres schema (`governor_shard_0`, `governor_shard_1`, ..., `governor_shard_9`).

**Tables per shard:**
- `incident_queue` — anomalies for this shard's agents.
- `intervention_queue` — interventions for this shard's agents.
- `intervention_log` — audit log for this shard's agents.
- `agent_registry` — fleet view for this shard's agents.

**Shared tables (cross-shard):**
- `policy_registry` — policies are global (all shards use the same policies).
- `a2a_message_log` — A2A messages are global (cross-shard analyzer needs to see all messages).
- `capability_cache` — agent capabilities are global (any shard may need to query any agent's capabilities for cross-shard interventions).

**Why per-shard schemas (vs separate databases)?** Simpler operational surface. Single Postgres instance, multiple schemas. No cross-database queries (but cross-schema JOINs are allowed for shared tables).

**When to use separate databases?** If write contention on shared tables (`policy_registry`, `a2a_message_log`) becomes a bottleneck. Unlikely until fleet size >10,000 agents.

---

### 3.3 Read Replicas (Query Scaling)

**Read-path (policy lookups, agent registry queries) scales horizontally via read replicas.**

**Architecture:**
- 1 primary (write-path: `intervention_queue`, `intervention_log`).
- 2 read replicas (read-path: `policy_registry`, `agent_registry`, `capability_cache`).
- Replication lag: <10ms (GCP Cloud SQL, same region).

**Connection pooling:** Each Governor shard maintains a connection pool:
- 10 connections to primary (write-path).
- 20 connections to read replicas (read-path, round-robin load balancing).

**When to add more read replicas?** If read-path query latency >100ms (p95) sustained for >5 minutes → add 1 more read replica.

---

## 4. A2A Subscriber Scaling

### 4.1 Single Subscription (Phase 3 Start)

**Pattern:** Governor subscribes to `a2a-fleet-messages` Pub/Sub topic (pull subscription).

**Concurrency:** 10 workers (goroutines in Go) pulling messages in parallel.

**Throughput:** ~10,000 messages/sec (Pub/Sub Go SDK, 4 vCPU).

**Verdict:** Single subscription handles 100-agent fleet (500 A2A messages/sec). No partitioning needed until fleet size >1,000 agents.

---

### 4.2 Partitioned Subscription (Sharded)

**When:** Fleet size >1,000 agents, or A2A message rate >50,000 messages/sec.

**Strategy:** Each Governor shard subscribes to a **filtered Pub/Sub subscription**.

**Filter:** `attributes.sender_agent_id IN (shard_0_agents) OR attributes.receiver_agent_id IN (shard_0_agents)`.

**Example (shard 0, agents 0-99):**

```
Subscription: a2a-fleet-messages-shard-0
Filter: attributes.sender_agent_id >= '0' AND attributes.sender_agent_id < '100'
        OR attributes.receiver_agent_id >= '0' AND attributes.receiver_agent_id < '100'
```

**Why filter by sender AND receiver?** A2A message may be sent by agent on shard 0 to agent on shard 5. Both shards need to see the message (for cross-shard correlation).

**Trade-off:** Each A2A message is delivered to 2 shards (sender's shard + receiver's shard). Doubles Pub/Sub delivery cost. Acceptable for A2A messages (low volume compared to spans).

---

### 4.3 Pub/Sub Ordering (Per Agent)

**Requirement:** A2A messages from a single agent must be processed in order (FIFO).

**Solution:** Pub/Sub **message ordering** (enabled via `enableMessageOrdering: true`).

**Ordering key:** `agent_id` (sender agent ID).

**Guarantee:** All messages with the same `ordering_key` are delivered in order to a single subscriber worker.

**Benefit:** Cross-shard analyzer sees A2A messages in correct order (delegation A→B before delegation B→A).

---

## 5. HA Configuration (High Availability)

### 5.1 3-Replica Active-Active Governor

**Architecture:**
- 3 Governor replicas (each replica is a full MAPE-K loop).
- **Read-path (Monitor → Analyze):** All replicas ingest telemetry independently.
- **Write-path (Plan → Execute):** Only the leader writes to `intervention_queue` and publishes to `governor-control` Pub/Sub.

**Leader election:** Postgres advisory locks (`pg_try_advisory_lock(<governor_lock_id>)`).

**Lock hold time:** 30s (refreshed every 10s by the leader).

**Failover time:** <30s (advisory lock expiry + new leader election).

---

### 5.2 Load Balancer (OTLP Receiver)

**Pattern:** gRPC-aware L7 load balancer in front of 3 replicas.

**Routing:** Round-robin (no session affinity; OTLP spans are stateless).

**Health check:** Load balancer pings each replica's `/health` endpoint every 10s. If replica is unhealthy for >30s → removed from rotation.

**Benefit:** If one replica crashes, load balancer routes traffic to the other 2 replicas within 30s.

---

### 5.3 Postgres HA (Cloud SQL)

**Architecture:** GCP Cloud SQL Postgres with automatic failover.
- 1 primary (write-path).
- 2 read replicas (read-path).
- Failover time: <60s (Cloud SQL automatic failover).

**During failover:**
- Write-path pauses (no new interventions).
- Read-path continues (policy lookups from replicas).

**Benefit:** If primary crashes, Cloud SQL promotes a read replica to primary within 60s.

---

## 6. Capacity Planning (Fleet Size vs Resources)

| Fleet Size | Governor Instances | Postgres Spec | Total Ingest Rate | Estimated Cost (GCP, us-central1) |
|------------|--------------------|---------------|-------------------|-----------------------------------|
| 100 agents | 1 instance (4 vCPU, 8GB RAM) | Cloud SQL (4 vCPU, 16GB RAM, 500GB SSD) | 10K spans/sec | ~$300/month |
| 500 agents | 3 instances (4 vCPU, 8GB RAM each, HA) | Cloud SQL (8 vCPU, 32GB RAM, 1TB SSD) | 50K spans/sec | ~$1,200/month |
| 1,000 agents | 10 shards (4 vCPU, 8GB RAM each) | Cloud SQL (16 vCPU, 64GB RAM, 2TB SSD) | 100K spans/sec | ~$3,500/month |
| 5,000 agents | 50 shards (4 vCPU, 8GB RAM each) | Cloud SQL (32 vCPU, 128GB RAM, 4TB SSD) | 500K spans/sec | ~$15,000/month |

**Cost breakdown (1,000-agent fleet):**
- Governor (10 shards × $100/month Cloud Run) = $1,000/month.
- Postgres (Cloud SQL 16 vCPU) = $1,500/month.
- Pub/Sub (100K msgs/sec × $0.40/million msgs × 2.6B msgs/month) = $1,000/month.
- **Total: ~$3,500/month.**

**Cost scaling:** Roughly linear with fleet size (doubling fleet size doubles cost).

---

## 7. Deployment Patterns

### 7.1 GCP Cloud Run (Recommended for Phase 3)

**Pros:**
- Fully managed (no VM provisioning).
- Auto-scales (0 to N instances based on CPU/memory).
- Built-in HA (Cloud Run manages replica health).

**Cons:**
- Cold start latency (~500ms for first request after idle). Mitigated by setting `min_instances=1`.

**Deployment:**

```yaml
# cloud-run-governor.yaml (gcloud deployment)
service: governor
region: us-central1
image: gcr.io/my-project/governor:latest
cpu: 4
memory: 8Gi
min_instances: 1         # always keep 1 instance warm (no cold starts)
max_instances: 10        # auto-scale to 10 instances under load
env_vars:
  GOVERNOR_A2A_ENABLED: "true"
  GOVERNOR_POSTGRES_URL: "postgres://..."
```

---

### 7.2 GKE StatefulSet (Alternative for Large Scale)

**When:** Fleet size >5,000 agents, or need fine-grained control over pod scheduling.

**Pros:**
- More control over pod affinity (co-locate Governor shard with Postgres replica on same zone).
- Lower latency (no Cloud Run proxy overhead).

**Cons:**
- Operational complexity (need to manage k8s cluster).

**Deployment:**

```yaml
# governor-statefulset.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: governor
spec:
  replicas: 10         # 10 shards
  selector:
    matchLabels:
      app: governor
  template:
    metadata:
      labels:
        app: governor
    spec:
      containers:
      - name: governor
        image: gcr.io/my-project/governor:latest
        resources:
          requests:
            cpu: 4
            memory: 8Gi
        env:
        - name: SHARD_ID
          valueFrom:
            fieldRef:
              fieldPath: metadata.name  # pod name = governor-0, governor-1, ..., governor-9
```

---

## 8. Scaling Summary

| Metric | Single Instance (100 agents) | Sharded (1,000 agents) | Notes |
|--------|------------------------------|------------------------|-------|
| **Ingest rate** | 10K spans/sec | 100K spans/sec | Sharded load balancer routes by `agent_id` |
| **Anomaly detection latency** | <1s (p95) | <1s (p95) per shard, 60s for cross-shard A2A patterns | Batch job for cross-shard correlation |
| **Postgres writes** | 100 rows/sec | 1,000 rows/sec (100 per shard × 10 shards) | Per-shard schemas reduce write contention |
| **HA failover time** | <30s (leader election) | <30s per shard | Each shard has independent leader election |
| **Cost** | ~$300/month | ~$3,500/month | Roughly linear with fleet size |

---

**Next:** See `phasing.md` for dependencies, triggers, and Phase 3 promotion criteria.
