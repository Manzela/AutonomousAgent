# Hermes Failure Matrix (33 Modes)

The system enforces a **Fail-Loud / Fail-Soft / Self-Heal** trichotomy. Every tool and system error must be explicitly classified and handled according to this matrix.

> **Source of truth:** the codified F-code → trichotomy class + handler mapping lives in
> `lib/durability/failure_matrix.py`. This document mirrors that table for human readers and
> must be kept in lockstep with it (the `test_all_33_codes_present` unit test enforces the
> code-side; doc parity is enforced via `grep -cE "^\| F[0-9]+ \|" docs/architecture/failure-matrix.md == 33` in CI as the row-count guard).

## 1. The Trichotomy Definition

1. **Fail-Loud (Escalate & Block):** Unrecoverable errors, security violations, or context exhaustion. Triggers an immediate Telegram notification to the owner. Pauses the task up to `telegram_escalation_timeout_h` (24h). If no resolution, transitions task to `BLOCKED`.
2. **Fail-Soft (Graceful Degradation):** Transient service outages, non-critical subagent failures. Logs a warning, skips the non-essential step, and continues the task with reduced fidelity.
3. **Self-Heal (Retry with Backoff):** Rate limits, malformed LLM JSON, transient network drops. Retries automatically using the exponential backoff+jitter configured in `limits.yaml` under `retries.self_heal.*`.

## 2. The 33-Mode Matrix

### Self-Heal (transient — exponential backoff with jitter)

| ID | Description | Trichotomy Classification | Resolution / Handler |
|----|-------------|---------------------------|----------------------|
| F1 | Rate limit (429) | **Self-Heal** | `retry_with_backoff` |
| F2 | Network timeout | **Self-Heal** | `retry_with_backoff` |
| F3 | Transient DNS resolution failure | **Self-Heal** | `retry_with_backoff` |
| F4 | 5xx from upstream LLM API | **Self-Heal** | `retry_with_backoff` |
| F5 | Connection reset by peer | **Self-Heal** | `retry_with_backoff` |
| F6 | Temporary tool sandbox crash | **Self-Heal** | `restart_sandbox_and_retry` |
| F7 | Honcho/Chroma temporary unavailable | **Self-Heal** | `retry_with_backoff` |
| F8 | Stale Vertex AI auth token | **Self-Heal** | `refresh_adc_and_retry` |
| F9 | Race on Kanban claim_lock | **Self-Heal** | `retry_with_backoff` |
| F10 | Checkpoint write contention | **Self-Heal** | `retry_with_backoff` |
| F11 | Gemini thinking-tokens silent truncation (max_tokens too low) | **Self-Heal** | `retry_with_higher_max_tokens` |

### Fail-Soft (degrade and continue)

| ID | Description | Trichotomy Classification | Resolution / Handler |
|----|-------------|---------------------------|----------------------|
| F12 | Chroma vector store down — disable semantic memory | **Fail-Soft** | `disable_chroma_for_session` |
| F13 | OTel collector unreachable — log spans locally instead | **Fail-Soft** | `fallback_local_log` |
| F14 | Github MCP server unavailable — skip github-tagged tools | **Fail-Soft** | `skip_tool_class` |
| F15 | Skill extractor temporarily failing — defer extraction | **Fail-Soft** | `defer_extraction` |
| F16 | Single evaluator judge timeout — proceed with N-1 judges | **Fail-Soft** | `drop_judge_continue_consensus` |
| F17 | Phoenix UI down — traces still collected, viewer offline | **Fail-Soft** | `log_and_continue` |
| F18 | Honcho metadata API slow — use cached metadata | **Fail-Soft** | `use_cached` |
| F19 | Per-task token budget exceeded — truncate response | **Fail-Soft** | `truncate_and_warn` |
| F20 | MEMORY/REJECTED.md inject would exceed context budget — skip inject | **Fail-Soft** | `skip_inject` |

### Fail-Loud (halt + alert via Telegram + snapshot)

| ID | Description | Trichotomy Classification | Resolution / Handler |
|----|-------------|---------------------------|----------------------|
| F21 | Daily budget cap exceeded | **Fail-Loud** | `halt_alert_snapshot` |
| F22 | Critical secret leak detected by scrubber | **Fail-Loud** | `halt_alert_snapshot` |
| F23 | Sandbox escape attempt detected | **Fail-Loud** | `halt_alert_snapshot` |
| F24 | Multi-judge consensus failure (split vote, no 5th judge available) | **Fail-Loud** | `halt_alert_snapshot` |
| F25 | TaskSpec lock-time clarification loop exceeded max questions | **Fail-Loud** | `halt_alert_request_approval` |
| F26 | 3-strike approach rejection (same fingerprint, REJECTED.md trigger) | **Fail-Loud** | `halt_alert_snapshot` |
| F27 | Persistent Vertex AI auth failure after retry+refresh | **Fail-Loud** | `halt_alert_snapshot` |
| F28 | Disk full on checkpoint write | **Fail-Loud** | `halt_alert_snapshot` |
| F29 | Hermes Kanban DB corruption / migration failure | **Fail-Loud** | `halt_alert_snapshot` |
| F30 | Approval-required tool fired without approval (policy violation) | **Fail-Loud** | `halt_alert_snapshot` |
| F31 | Egress allowlist violation attempt | **Fail-Loud** | `halt_alert_snapshot` |
| F32 | 24h Telegram silence on blocked card → escalate to triage | **Fail-Loud** | `alert_user_escalate_kanban` |
| F33 | F-code lookup failed (unclassified exception) | **Fail-Loud** | `halt_alert_snapshot` |

## 3. Classifier behavior

Exceptions raised inside the hot path are passed to
`lib.durability.trichotomy.classify(err)`, which pattern-matches `f"{type(err).__name__}: {err}"`
against a regex table (see `_CLASSIFIERS` in that module) and returns the most specific
F-code. Anything that doesn't match falls through to **F33 (Fail-Loud unknown)** by design —
unclassified exceptions are *never* silently swallowed.

## 4. Retry policy

Self-heal modes use exponential backoff with jitter:

```
delay_ms = clamp(
    base_delay_ms * 2^(attempt - 1) ± (jitter_range_pct * raw / 100),
    0,
    max_delay_ms,
)
```

Defaults (from `config/limits.yaml retries.self_heal.*`):

- `max_retries: 3`
- `base_delay_ms: 500`
- `max_delay_ms: 30000`
- `jitter_range_pct: 25`

After `max_retries` exhausted, the failure is re-classified as **Fail-Loud** (via the
handler returning, not by F-code change) and escalated per F-code's `handler` field.
