# Phase 3 Metacog Governor — Design Specification

**Date:** 2026-05-21
**Status:** Design complete (not implemented)
**Strategic Context:** ADR-0008 Q7 disposition — failure-matrix detectors (F34/F35/F36) cover H1 2026; standalone Governor deferred to Phase 3 when A2A multi-agent coordination creates demand for centralized behavioral observability.

---

## Executive Summary

The Phase 3 Metacog Governor is a standalone service that monitors the entire agent fleet, detects behavioral anomalies, recommends interventions to a human operator, and executes interventions when authorized. It follows the **MAPE-K** (Monitor → Analyze → Plan → Execute, with shared Knowledge) autonomic computing pattern from IBM's 2003 seminal work on self-adaptive systems.

**Key capabilities:**
- **Cross-agent observability** — detects fleet-level patterns (delegation loops, fan-out explosions) that no single agent sees.
- **Centralized policy enforcement** — consistent intervention rules across all agents.
- **Learning loop** — incident database captures past interventions so the Governor improves over time.
- **A2A-aware anomaly detection** — subscribes to inter-agent A2A messages for coordination-level behavioral telemetry.

**Deployment model:** Standalone service with sidecars. Agents emit telemetry to local sidecars (low latency); sidecars forward to central Governor (unified view). Control plane actions flow back via A2A control messages.

**Key trade-off:** Introducing the Governor creates a single point of failure for intervention decisions. Mitigated via HA deployment (3 replicas, leader election), circuit breakers, and explicit fail-open/fail-closed semantics per action type.

---

## Design Documents

| File | Purpose | Size |
|------|---------|------|
| **architecture.md** | MAPE-K component breakdown, deployment model, A2A integration, ASCII diagrams | ~2,100 words |
| **apis.md** | gRPC control plane, OTLP data plane, Pub/Sub event bus, auth, rate limits | ~1,200 words |
| **observability-spec.md** | Signals consumed/emitted, dashboards, alerting, span attribute conventions | ~800 words |
| **policy-language.md** | Declarative YAML DSL, policy examples, validation, lifecycle | ~900 words |
| **failure-modes.md** | SPoF mitigations, fail-open/fail-closed table, circuit breakers | ~1,000 words |
| **scaling.md** | Single-instance capacity, sharding, Postgres HA, cost planning | ~600 words |
| **phasing.md** | Dependencies (A2A, Postgres), trigger criteria, implementation timeline | ~700 words |

**Total:** 7 documents, ~7,300 words, 3,670 lines of markdown.

---

## Recommended Deployment Model

**Hybrid: standalone Governor + sidecars for data plane.**

```
Agent Fleet (N agents)
  ├─ Agent 1 → Sidecar 1 ─┐
  ├─ Agent 2 → Sidecar 2 ─┼─→ Governor (standalone, 3 replicas, leader election)
  └─ Agent N → Sidecar N ─┘     ├─ OTLP receiver (telemetry ingest)
                                ├─ A2A subscriber (cross-agent observability)
                                ├─ MAPE-K loop (Monitor→Analyze→Plan→Execute)
                                └─ Postgres (Knowledge: policies, incidents, interventions)
                                          ↓
                                  A2A Control Plane (Pub/Sub)
                                          ↓
                          (interventions back to agents)
```

**Data plane:** Agents → sidecars → Governor (push-based OTLP + A2A Pub/Sub).
**Control plane:** Governor → agents (A2A control messages: warn/throttle/kill/escalate).

**Why sidecars?** Decouples agents from Governor's network location; sidecars buffer telemetry locally if Governor is down (fail-soft).

---

## Key Trade-Off

**SPoF Risk:** Governor is a single point of failure for intervention decisions.

**Mitigation (7 distinct mechanisms):**
1. **HA deployment** — 3 replicas, leader election via Postgres advisory locks, <30s failover.
2. **Reasoning transparency** — every intervention logged with full policy + evidence chain.
3. **Human review SLA** — auto-kill only for critical security violations; all other kills require human approval.
4. **Circuit breaker** — if Governor kills >10 agents/hour, it auto-pauses and alerts operator.
5. **Agent safe-mode** — if telemetry can't reach Governor for >60s, agents refuse high-cost operations.
6. **Fail-open/fail-closed table** — explicit semantics per action (warn = fail-open, kill = fail-closed).
7. **Dry-run mode** — new policies run in dry-run for 24 hours before activation (catches false positives).

---

## Hard Dependencies on Prior Phases

**Phase 3 Governor cannot ship until:**

1. **A2A integration (Q4 2026)** — Governor's cross-agent anomaly detection requires observing inter-agent A2A messages. Without A2A, Governor can only see per-agent behavior (same as in-process F34/F35/F36 detectors). **This is the critical path.**

2. **Postgres migration (Phase 2)** — Governor's Knowledge base (policy registry, incident queue, intervention log) requires Postgres. Expected completion: Q2 2026 (before A2A).

**Earliest possible Governor trigger:** Q4 2026 (after A2A ships + 30-day stability period).

---

## Trigger Criteria (All Must Be True)

1. Fleet size ≥10 active agents.
2. Cross-agent A2A traffic >100 messages/day.
3. A2A v1 stable in production for ≥30 days.
4. ≥1 incident in last 90 days where fleet-level view would have prevented escalation.

**If criteria met:** Proceed with Governor implementation (~4-6 weeks).
**If criteria not met:** Re-evaluate quarterly; defer until evidence-based trigger exists.

---

## Implementation Timeline (When Triggered)

| Phase | Duration | Deliverables |
|-------|----------|--------------|
| **3.0 Design & Spec** | 1 week | This document (7 files: architecture, apis, observability, policy-language, failure-modes, scaling, phasing) |
| **3.1 Scaffold Service** | 2 weeks | Governor binary, OTLP receiver, A2A subscriber, Postgres schema |
| **3.2 Monitor + Analyze** | 1 week | Telemetry ingest + Tier 1 rule engine (F-code patterns, metric thresholds, A2A patterns) |
| **3.3 Plan + Execute** | 1 week | Policy matching + intervention execution (warn/throttle/kill/escalate) |
| **3.4 HA Deployment** | 1 week | 3 replicas + leader election + load balancer |
| **3.5 ML Tier 2 (optional)** | 1 week | Isolation Forest anomaly detector (unsupervised drift detection) |
| **3.6 Dashboards + Alerting** | Parallel | Grafana dashboards + Telegram/PagerDuty alerting |
| **Total** | **4-6 weeks** | Production-ready Governor service |

**Post-launch:** 24-hour survival checkpoint (P0-A from Audit v2). If successful, Governor remains in production. If fails, rollback to in-process F34/F35/F36 detectors.

---

## Design Quality Bar

**MAPE-K rigor:**
- All phases (Monitor, Analyze, Plan, Execute, Knowledge) are distinct subsystems with well-defined inputs/outputs.
- Knowledge base is shared across all phases (Postgres schema `governor_schema`).
- Feedback loop: intervention outcomes feed back into policy tuning (Phase 4 learning loop extension).

**Failure modes rigor:**
- 7 distinct failure scenarios identified (Governor down, Governor misbehaves, telemetry broken, Postgres down, A2A Pub/Sub down, ML false positives, leader election split-brain).
- Every failure mode has: detection method, mitigation, fail-open or fail-closed semantics.
- SPoF risk explicitly addressed with HA + circuit breakers + agent safe-mode.

**A2A integration:**
- Governor's A2A-aware anomaly patterns: delegation loop, fan-out explosion, no-response stall.
- A2A-ON vs A2A-OFF degraded modes specified (pre-A2A, Governor runs in degraded mode with A2A detectors disabled).
- Control plane uses A2A messages for intervention delivery (GOVERNOR_KILL_SESSION, GOVERNOR_THROTTLE, etc.).

**References:**
- IBM autonomic computing seminal paper (Kephart & Chess 2003, "The Vision of Autonomic Computing").
- SEAMS community (self-adaptive systems, MAPE-K variants).
- OpenTelemetry Protocol Specification v1.0.0 (OTLP receiver patterns).
- GCP Pub/Sub documentation (message ordering, exactly-once delivery).

---

## Status

**Design phase:** ✅ Complete (2026-05-21).
**Implementation phase:** ⏳ Blocked on A2A integration (Q4 2026 earliest).
**Deployment:** ⏳ Contingent on trigger criteria (fleet size ≥10, A2A traffic >100/day, 30-day A2A stability, ≥1 incident).

---

**Next steps:**
1. Wait for A2A integration to ship (Q4 2026, per ADR-0008 Q4 disposition).
2. Monitor trigger criteria quarterly (Q4 2026, Q1 2027, Q2 2027).
3. When criteria met: proceed with Phase 3.1 (scaffold service, 2 weeks).
