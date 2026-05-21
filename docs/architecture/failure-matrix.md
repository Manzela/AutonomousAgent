# Hermes Failure Matrix (37 Modes)

The system enforces a **Fail-Loud / Fail-Soft / Self-Heal** trichotomy. Every tool and system error must be explicitly classified and handled according to this matrix.

> **Source of truth:** the codified F-code → trichotomy class + handler mapping lives in
> `lib/durability/failure_matrix.py`. This document mirrors that table for human readers and must be kept in lockstep with it.
>
> Lockstep enforcement (all in `tests/unit/test_failure_matrix.py`):
>
> - `test_baseline_codes_f1_to_f33_present` — locks the F1-F33 AA-Atelier baseline; any future deletion regresses.
> - `test_loop_and_stall_codes_present` — locks F34 (F-LOOP) and F35 (F-STALL) added by J4 (Framing #2).
> - `test_context_code_present` — locks F36 (F-CONTEXT) added by J9 (Framing #2).
> - `test_model_armor_sanitize_code_present` — locks F37 (Model Armor sanitize unavailable) added by Stream B (ADR-0008 Q6).
> - `test_every_code_maps_to_valid_class` + `test_no_duplicate_codes` — invariant guards over every row.
>
> **Current count: 37** (F1-F33 baseline + F34-F36 runtime detectors + F37 Model Armor PII gate). Adding a new F-code requires (1) a row in `FAILURE_MATRIX` in code, (2) a row in §2 below, and (3) a new `test_*_code_present` assertion mirroring the J4/J9 pattern.

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

### Runtime detectors (F34-F36)

Added by Framing #2 audit. Unlike F1-F33 (which are *classifications of
raised exceptions*), these fire from active detectors running in the
orchestrator's hooks/watchdog. See `lib/durability/runtime_detectors.py`.

| Code | Description | Class | Handler |
|---|---|---|---|
| F34 | **F-LOOP** — agent repeated same tool-call fingerprint N times without progress | **Fail-Soft** | `interrupt_with_loop_feedback` |
| F35 | **F-STALL** — no tool-call activity for `idle_timeout_s` while task in_progress | **Fail-Loud** | `halt_alert_snapshot` |
| F36 | **F-CONTEXT** — prompt-token usage exceeded warn threshold (compaction may be ineffective) | **Fail-Soft** | `escalate_context_pressure` |

**F36 / F-CONTEXT semantics.** Upstream Hermes' `context_compressor` already
triggers compaction at 0.5 of the model's context window
(`threshold_percent`). A reading at 0.9 (`config/limits.yaml →
durability.context_detector.warn_threshold`) means compaction either failed,
got suppressed by the anti-thrashing guard, or never ran — the detector
surfaces that pathological state to the orchestrator/operator. Re-arms
when the next observed ratio drops below threshold, so a single session
can fire F36 multiple times if it keeps bouncing across the warn line.

### Stream B PII gate (F37)

| Code | Description | Class | Handler |
|---|---|---|---|
| F37 | **Model Armor sanitize unavailable** — `templates.sanitize` failed or timed out while the J1 trajectory shipper was preparing a judge verdict for GCS persistence | **Fail-Loud** | `halt_alert_snapshot` |

**F37 / Model Armor PII gate semantics.** When the trajectory shipper cannot
sanitize a verdict via Model Armor, the only safe action is to halt the
shipper, snapshot, and alert. Writing un-redacted verdicts to GCS would
persist PII into the RLAIF training substrate; Phase 4 RL will memorize the
leak, and we cannot delete training-time PII from a model after the fact.
Fail-soft (`fallback_local_log` of the redacted intent without the payload)
would defer the failure mode but not eliminate it — the shipper backlog
would build up and operators would be tempted to drain it with redaction
disabled. The shipper code MUST `dispatch("F37", ...)` rather than
`try: sanitize() except: continue`. See
`audit/2026-05-20-model-armor-j1-runbook/runbook.md` for the runtime
sanitize contract and
`audit/2026-05-21-persistence-trap-12c/test-contract.md` for the
regression test that proves a broken-sanitize path fails loud.

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
