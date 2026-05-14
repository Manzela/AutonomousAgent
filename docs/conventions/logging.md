# Logging Convention

All services emit structured JSON logs to stdout. The OTel collector ships them to the configured backend (local files in dev, Cloud Logging in Phase 2 prod).

## Format

Every log line is a single JSON object:

```json
{
  "ts": "2026-05-14T18:32:11.234Z",
  "level": "info",
  "service": "hermes-agent",
  "phase": 1,
  "env": "dev",
  "session_id": "abc123",
  "turn_id": 42,
  "event": "tool.dispatch",
  "tool": "shell",
  "tier": "shell_sandbox",
  "msg": "dispatching shell command",
  "trace_id": "...",
  "span_id": "..."
}
```

## Required fields

- `ts` — RFC 3339 UTC
- `level` — `debug` | `info` | `warning` | `error` | `critical`
- `service` — service name matching OTel `service.name` resource attribute
- `event` — short snake_case event name (corresponds to OTel span name when applicable)
- `msg` — human-readable summary

## Optional fields by event class

- `session_id`, `turn_id` — for any agent-loop event
- `tool`, `tier` — for tool dispatch events
- `cost_usd`, `tokens_in`, `tokens_out`, `model_id` — for model.call events
- `trace_id`, `span_id` — automatically injected when an OTel context exists
- `error.type`, `error.message`, `error.stack` — when level=error/critical

## Severity levels

| Level | When to use | Routes to |
|---|---|---|
| `debug` | Verbose internals; off by default in prod | Local files only |
| `info` | Normal operational events (turn started, tool dispatched) | Local + Cloud Logging |
| `warning` | Degraded behavior, retries, fallbacks | Local + Cloud Logging |
| `error` | Operation failed but service is still up | Local + Cloud Logging + alert if rate exceeds threshold |
| `critical` | Service is down or security boundary crossed | Local + Cloud Logging + immediate Telegram alert |

## What to NEVER log

- Plaintext secrets (use the scrubber even on log strings)
- Full conversation contents (log session_id + turn_id; the persisted DB has the content)
- User PII without explicit need

## What to ALWAYS log

- Every tool dispatch + result class
- Every model call with token + cost telemetry
- Every approval-gate decision (allow/deny/timeout)
- Every scrubber hit (separate log file `secret-leak-attempts.log` for audit)
- Every restart, panic, snapshot, recovery event
- Every Phase-3+ trajectory shipment outcome
- Every Phase-4 RL preflight, approval, run lifecycle event

## Local dev rotation

`limits.yaml` `local_logs_dev`:
- `rotate_size_mb: 100`
- `keep_files: 5`

Logs at `logs/` are gitignored.

## Phase 2 prod retention

`limits.yaml` `log_retention`:
- Cloud Logging hot: 30 days
- GCS coldline after 30 days: another 11 months
- Hard delete after 365 days
