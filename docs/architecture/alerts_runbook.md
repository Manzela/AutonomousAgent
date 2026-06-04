# Agentic Alerts Runbook (GT:O-15 / GT:O-16)

This document maps the 7 core metric thresholds to their PagerDuty and Slack fan-out configurations.

## 1. High Agent Error Rate
- **Metric:** `llm.call.errors` / `llm.calls.total` > 15% (5m)
- **Severity:** SEV-2
- **Action:** Check LiteLLM proxy status (`http://localhost:4000/health`). Verify Vertex AI quotas.

## 2. LLM Latency Spike
- **Metric:** `llm.call.duration` p99 > 30000ms (5m)
- **Severity:** SEV-3
- **Action:** Upstream provider degraded. Check `deploy/otel/collector.prod.yaml` spanmetrics.

## 3. Tool Failure Cascade
- **Metric:** `tool.call.errors` > 20/min
- **Severity:** SEV-2
- **Action:** Verify target external APIs (e.g., GitHub, Jira, local sandboxes).

## 4. Runaway Sub-Task Spawning (Sybil Prevention)
- **Metric:** Agent recursion depth > 10 OR > 100 sub-agents in 1m
- **Severity:** SEV-1
- **Action:** Immediately halt the active `session.start.count`. Check for Prompt Injection / A2A manipulation.

## 5. Cost Anomalies
- **Metric:** `llm.call.cost` spike > $5.00 per turn
- **Severity:** SEV-2
- **Action:** Check `max_tokens` settings and context window leaks.

## 6. High Memory Pressure
- **Metric:** OTel collector memory > 400 MiB limit
- **Severity:** SEV-3
- **Action:** Verify tail_sampling policies are operating correctly to drop routine traces.

## 7. Honeypot Access
- **Metric:** Canary token file access via `tests/integration/test_monitorability.py` triggered in production logs
- **Severity:** SEV-0
- **Action:** Isolate workspace. Investigate for active adversarial presence or sandbox escape.
