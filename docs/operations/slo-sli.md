# Hermes Agent — SLO / SLI Definitions

**Version**: 1.0
**Date**: 2026-06-04
**Owner**: Platform Team

## Overview

This document defines Service Level Objectives (SLOs) and Service Level Indicators (SLIs) for the Hermes Agent production service. These SLOs govern the error budget and are the basis for operational alerting and incident response.

## Service Level Indicators (SLIs)

### 1. Availability

| SLI | Measurement | Source |
|-----|------------|--------|
| **Spine Availability** | Proportion of `GET /healthz` requests returning 200 | Cloud Run health check probes |
| **API Success Rate** | Proportion of non-5xx responses across `/goal`, `/resume` | OTel `http.server.request.duration` metric |

### 2. Latency

| SLI | Measurement | Source |
|-----|------------|--------|
| **Goal Intake p50** | 50th percentile time from `/goal` request to first `__interrupt__` response | OTel `http.server.request.duration` |
| **Goal Intake p95** | 95th percentile of the same | OTel |
| **Goal Intake p99** | 99th percentile of the same | OTel |
| **Resume Latency p50** | 50th percentile of `/resume` request to response | OTel |
| **Resume Latency p95** | 95th percentile | OTel |
| **Fan-out Leaf Duration p95** | 95th percentile of individual leaf execution in fan_out | `model.call` span duration |

### 3. Cost Efficiency

| SLI | Measurement | Source |
|-----|------------|--------|
| **Per-Transaction LLM Cost** | Average `cost_usd` per `/goal` → ship lifecycle | `cost_accumulator` reducer + budget_verdict |
| **Daily Aggregate Spend** | UTC-day sum of all LLM costs | LiteLLM `LiteLLM_SpendLogs` Postgres table |
| **Budget Utilization** | Fraction of SPINE_BUDGET_USD consumed per graph | `budget_verdict.spend_usd / budget_verdict.cap_usd` |

### 4. Quality / Safety

| SLI | Measurement | Source |
|-----|------------|--------|
| **Eval Gate Pass Rate** | Proportion of fan_out waves passing eval_gate | `decision_record` entries with `scope_root_verdict` |
| **Golden Eval Pass Rate** | Weekly golden eval suite pass rate | `scheduled-golden-eval.yml` workflow |
| **PII Leak Rate** | Count of PII patterns detected in ship_effect outputs | Scrubber log entries (`lib/scrubber.py`) |
| **Model Armor Block Rate** | Proportion of inputs/outputs blocked by Model Armor | Model Armor API metrics |

## Service Level Objectives (SLOs)

### Tier 1 — Critical (Error Budget: 0.1%)

| SLO | Target | Window | Burn Rate Alert |
|-----|--------|--------|-----------------|
| Spine Availability | 99.9% | 30-day rolling | 14.4x (2hr), 6x (6hr), 3x (1d), 1x (3d) |
| API Success Rate | 99.5% | 30-day rolling | Same burn-rate windows |

### Tier 2 — Latency (Error Budget: 1%)

| SLO | Target | Window |
|-----|--------|--------|
| Goal Intake p50 | ≤ 2s | 7-day rolling |
| Goal Intake p95 | ≤ 10s | 7-day rolling |
| Goal Intake p99 | ≤ 30s | 7-day rolling |
| Resume p50 | ≤ 1s | 7-day rolling |
| Resume p95 | ≤ 5s | 7-day rolling |

### Tier 3 — Cost (Advisory, no error budget)

| SLO | Target | Window |
|-----|--------|--------|
| Per-Transaction LLM Cost | ≤ $0.50 (P95) | 7-day rolling |
| Daily Aggregate Spend | ≤ $50 (default daily cap F21) | UTC day |

### Tier 4 — Quality (Error Budget: 5%)

| SLO | Target | Window |
|-----|--------|--------|
| Golden Eval Pass Rate | ≥ 95% | Weekly |
| PII Leak Rate | 0 occurrences | 30-day rolling |
| Eval Gate Pass Rate | ≥ 80% | 30-day rolling |

## Error Budget Policy

### Budget Calculation

```
Error Budget = 1 - SLO Target
Available Error Budget (30d) = Error Budget × Total Good Minutes in 30 Days
Remaining Budget = Available Budget - Consumed Budget
```

### Policy

| Budget Status | Actions |
|--------------|---------|
| **> 50% remaining** | Normal velocity. Ship features freely. |
| **25-50% remaining** | Reduce deployment frequency. Prioritize reliability work. |
| **10-25% remaining** | Feature freeze. All engineering on reliability. |
| **< 10% remaining** | Incident response mode. Rollback if needed. |
| **Exhausted (0%)** | Full stop. Post-mortem required before resuming. |

## Alerting Thresholds

| Alert | Condition | Channel | Severity |
|-------|-----------|---------|----------|
| **Availability Critical** | < 99.9% over 2hr window | PagerDuty + Telegram | P1 |
| **Latency Degradation** | p95 > 15s over 1hr | Telegram | P2 |
| **Budget Exhaustion** | Daily spend > 80% of cap | Telegram | P2 |
| **Drift Alert** | Golden eval < 95% | GitHub Issue + Telegram | P2 |
| **PII Leak** | Any scrubber match in output | PagerDuty | P1 |

## Review Schedule

- **Weekly**: Review SLI dashboards in team standup
- **Monthly**: Error budget review with stakeholders
- **Quarterly**: SLO target revision based on operational data
