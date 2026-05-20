"""Failure matrix mapping F-codes to trichotomy class + handler reference.

Source of truth: docs/architecture/failure-matrix.md.

Codes F1-F33 are the original AA-Atelier sweep (self-heal F1-F11, fail-soft
F12-F20, fail-loud F21-F33). Codes F34+ are runtime-detector additions tracked
in audit/2026-05-20-architecture-research-gap-analysis/audit-plan.md.
"""

from enum import Enum
from typing import Dict, Any


class TrichotomyClass(str, Enum):
    FAIL_LOUD = "fail_loud"
    FAIL_SOFT = "fail_soft"
    SELF_HEAL = "self_heal"


FAILURE_MATRIX: Dict[str, Dict[str, Any]] = {
    # === Self-heal (transient, retry with backoff) F1-F11 ===
    "F1": {
        "class": TrichotomyClass.SELF_HEAL,
        "description": "Rate limit (429)",
        "handler": "retry_with_backoff",
    },
    "F2": {
        "class": TrichotomyClass.SELF_HEAL,
        "description": "Network timeout",
        "handler": "retry_with_backoff",
    },
    "F3": {
        "class": TrichotomyClass.SELF_HEAL,
        "description": "Transient DNS resolution failure",
        "handler": "retry_with_backoff",
    },
    "F4": {
        "class": TrichotomyClass.SELF_HEAL,
        "description": "5xx from upstream LLM API",
        "handler": "retry_with_backoff",
    },
    "F5": {
        "class": TrichotomyClass.SELF_HEAL,
        "description": "Connection reset by peer",
        "handler": "retry_with_backoff",
    },
    "F6": {
        "class": TrichotomyClass.SELF_HEAL,
        "description": "Temporary tool sandbox crash",
        "handler": "restart_sandbox_and_retry",
    },
    "F7": {
        "class": TrichotomyClass.SELF_HEAL,
        "description": "Honcho/Chroma temporary unavailable",
        "handler": "retry_with_backoff",
    },
    "F8": {
        "class": TrichotomyClass.SELF_HEAL,
        "description": "Stale Vertex AI auth token",
        "handler": "refresh_adc_and_retry",
    },
    "F9": {
        "class": TrichotomyClass.SELF_HEAL,
        "description": "Race on Kanban claim_lock",
        "handler": "retry_with_backoff",
    },
    "F10": {
        "class": TrichotomyClass.SELF_HEAL,
        "description": "Checkpoint write contention",
        "handler": "retry_with_backoff",
    },
    "F11": {
        "class": TrichotomyClass.SELF_HEAL,
        "description": "Gemini thinking-tokens silent truncation (max_tokens too low)",
        "handler": "retry_with_higher_max_tokens",
    },
    # === Fail-soft (degrade and continue) F12-F20 ===
    "F12": {
        "class": TrichotomyClass.FAIL_SOFT,
        "description": "Chroma vector store down — disable semantic memory",
        "handler": "disable_chroma_for_session",
    },
    "F13": {
        "class": TrichotomyClass.FAIL_SOFT,
        "description": "OTel collector unreachable — log spans locally instead",
        "handler": "fallback_local_log",
    },
    "F14": {
        "class": TrichotomyClass.FAIL_SOFT,
        "description": "Github MCP server unavailable — skip github-tagged tools",
        "handler": "skip_tool_class",
    },
    "F15": {
        "class": TrichotomyClass.FAIL_SOFT,
        "description": "Skill extractor temporarily failing — defer extraction",
        "handler": "defer_extraction",
    },
    "F16": {
        "class": TrichotomyClass.FAIL_SOFT,
        "description": "Single evaluator judge timeout — proceed with N-1 judges",
        "handler": "drop_judge_continue_consensus",
    },
    "F17": {
        "class": TrichotomyClass.FAIL_SOFT,
        "description": "Phoenix UI down — traces still collected, viewer offline",
        "handler": "log_and_continue",
    },
    "F18": {
        "class": TrichotomyClass.FAIL_SOFT,
        "description": "Honcho metadata API slow — use cached metadata",
        "handler": "use_cached",
    },
    "F19": {
        "class": TrichotomyClass.FAIL_SOFT,
        "description": "Per-task token budget exceeded — truncate response",
        "handler": "truncate_and_warn",
    },
    "F20": {
        "class": TrichotomyClass.FAIL_SOFT,
        "description": "MEMORY/REJECTED.md inject would exceed context budget — skip inject",
        "handler": "skip_inject",
    },
    # === Fail-loud (halt + alert via Telegram + snapshot) F21-F33 ===
    "F21": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "Daily budget cap exceeded",
        "handler": "halt_alert_snapshot",
    },
    "F22": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "Critical secret leak detected by scrubber",
        "handler": "halt_alert_snapshot",
    },
    "F23": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "Sandbox escape attempt detected",
        "handler": "halt_alert_snapshot",
    },
    "F24": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "Multi-judge consensus failure (split vote, no 5th judge available)",
        "handler": "halt_alert_snapshot",
    },
    "F25": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "TaskSpec lock-time clarification loop exceeded max questions",
        "handler": "halt_alert_request_approval",
    },
    "F26": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "3-strike approach rejection (same fingerprint, REJECTED.md trigger)",
        "handler": "halt_alert_snapshot",
    },
    "F27": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "Persistent Vertex AI auth failure after retry+refresh",
        "handler": "halt_alert_snapshot",
    },
    "F28": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "Disk full on checkpoint write",
        "handler": "halt_alert_snapshot",
    },
    "F29": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "Hermes Kanban DB corruption / migration failure",
        "handler": "halt_alert_snapshot",
    },
    "F30": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "Approval-required tool fired without approval (policy violation)",
        "handler": "halt_alert_snapshot",
    },
    "F31": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "Egress allowlist violation attempt",
        "handler": "halt_alert_snapshot",
    },
    "F32": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "24h Telegram silence on blocked card → escalate to triage",
        "handler": "alert_user_escalate_kanban",
    },
    "F33": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "F-code lookup failed (unclassified exception)",
        "handler": "halt_alert_snapshot",
    },
    # === Runtime detectors F34-F35 (J4 — Framing #2) ===
    # F-LOOP: N consecutive identical-fingerprint tool calls. Fail-soft so
    # the orchestrator can inject loop-break feedback rather than halt.
    "F34": {
        "class": TrichotomyClass.FAIL_SOFT,
        "description": "F-LOOP: agent repeated same tool-call fingerprint N times without progress",
        "handler": "interrupt_with_loop_feedback",
    },
    # F-STALL: no tool-call activity for idle_timeout_s while task in_progress.
    # Fail-loud — an idle agent on an open task is usually an upstream hang.
    "F35": {
        "class": TrichotomyClass.FAIL_LOUD,
        "description": "F-STALL: no tool-call activity for idle_timeout_s while task in_progress",
        "handler": "halt_alert_snapshot",
    },
    # F-CONTEXT (J9 — Framing #2): prompt-tokens / context_length exceeded
    # warning threshold (default 0.9). Fail-soft because upstream Hermes already
    # compacts at 0.5 (`context_compressor.threshold_percent`); a 0.9 reading
    # means compaction either failed or was suppressed by anti-thrashing. The
    # handler escalates (forced compaction / Telegram / `/new` request) rather
    # than halting — the model can keep running, but every subsequent turn is
    # at risk of provider-side hard truncation.
    "F36": {
        "class": TrichotomyClass.FAIL_SOFT,
        "description": "F-CONTEXT: prompt-token usage exceeded warning threshold (compaction may be ineffective)",
        "handler": "escalate_context_pressure",
    },
}


def lookup(code: str) -> Dict[str, Any]:
    """Look up an F-code; raises KeyError if unknown."""
    return FAILURE_MATRIX[code]
