"""Unit tests for the failure-matrix handler dispatch layer.

Closes risk R4 in ``audit/2026-05-19-resume-orchestration/audit-plan.md``
("matrix names 16 handlers; none implemented"):

* ``test_all_handlers_dispatchable`` walks every entry in
  ``failure_matrix.FAILURE_MATRIX`` and asserts the named handler resolves
  to a callable in :data:`HANDLER_REGISTRY`. This is the acceptance
  criterion called out by name in the P0-5 plan item.
* Per-handler tests cover the three baseline implementations.
* Stub-delegation tests cover the trichotomy-class fallback path used by
  the remaining 13 named handlers.
* Dispatch-level tests cover the F33 fallback for unknown F-codes.
"""

import json
from unittest import mock

import pytest

from lib.durability.failure_matrix import FAILURE_MATRIX, TrichotomyClass
from lib.durability.handlers import (
    HANDLER_REGISTRY,
    LOOP_FEEDBACK_TEMPLATE,
    HandlerResult,
    dispatch,
    escalate_context_pressure,
    fallback_local_log,
    halt_alert_snapshot,
    interrupt_with_loop_feedback,
    retry_with_backoff,
)


# ----------------------------------------------------------------------
# R4 acceptance criterion
# ----------------------------------------------------------------------


def test_all_handlers_dispatchable():
    """Every handler named in FAILURE_MATRIX resolves to a callable."""
    for f_code, entry in FAILURE_MATRIX.items():
        name = entry["handler"]
        assert name in HANDLER_REGISTRY, f"{f_code}: handler {name!r} not registered"
        assert callable(HANDLER_REGISTRY[name]), f"{f_code}: handler {name!r} not callable"


def test_registry_contains_all_three_baseline_handlers():
    assert HANDLER_REGISTRY["retry_with_backoff"] is retry_with_backoff
    assert HANDLER_REGISTRY["halt_alert_snapshot"] is halt_alert_snapshot
    assert HANDLER_REGISTRY["fallback_local_log"] is fallback_local_log


# ----------------------------------------------------------------------
# Baseline handler — retry_with_backoff
# ----------------------------------------------------------------------


def test_retry_with_backoff_returns_retry_action():
    result = retry_with_backoff("F1", attempt=1, jitter_range_pct=0)
    assert isinstance(result, HandlerResult)
    assert result.action == "retry"
    assert result.f_code == "F1"
    assert result.handler == "retry_with_backoff"
    assert result.delay_ms > 0


def test_retry_with_backoff_attempt_grows_delay():
    """With jitter pinned to 0 the delay must be strictly non-decreasing."""
    d1 = retry_with_backoff("F1", attempt=1, jitter_range_pct=0).delay_ms
    d2 = retry_with_backoff("F1", attempt=2, jitter_range_pct=0).delay_ms
    d3 = retry_with_backoff("F1", attempt=3, jitter_range_pct=0).delay_ms
    assert d1 <= d2 <= d3


def test_retry_with_backoff_respects_max_delay():
    result = retry_with_backoff(
        "F1",
        attempt=100,
        base_delay_ms=500,
        max_delay_ms=2000,
        jitter_range_pct=0,
    )
    assert result.delay_ms <= 2000


# ----------------------------------------------------------------------
# Baseline handler — fallback_local_log
# ----------------------------------------------------------------------


def test_fallback_local_log_writes_jsonl(tmp_path):
    result = fallback_local_log(
        "F13",
        payload={"span_name": "tool.dispatch", "duration_ms": 12},
        log_dir=tmp_path,
    )
    assert result.action == "continue"
    assert result.f_code == "F13"
    assert result.handler == "fallback_local_log"

    # Locate the JSONL file written into <tmp_path>/<UTC date>/f13.jsonl.
    jsonl_files = list(tmp_path.rglob("f13.jsonl"))
    assert len(jsonl_files) == 1
    line = jsonl_files[0].read_text(encoding="utf-8").strip()
    record = json.loads(line)
    assert record["f_code"] == "F13"
    assert record["payload"] == {"span_name": "tool.dispatch", "duration_ms": 12}
    assert record["timestamp_utc"].endswith("Z")


def test_fallback_local_log_appends_multiple_records(tmp_path):
    fallback_local_log("F13", payload={"i": 1}, log_dir=tmp_path)
    fallback_local_log("F13", payload={"i": 2}, log_dir=tmp_path)
    jsonl_files = list(tmp_path.rglob("f13.jsonl"))
    lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["payload"] == {"i": 1}
    assert json.loads(lines[1])["payload"] == {"i": 2}


# ----------------------------------------------------------------------
# Baseline handler — halt_alert_snapshot
# ----------------------------------------------------------------------


def test_halt_alert_snapshot_calls_telegram_and_card_transition():
    with (
        mock.patch("lib.kanban.telegram_bridge.send_alert") as mock_send,
        mock.patch("lib.kanban.telegram_bridge.update_card_status") as mock_update,
    ):
        result = halt_alert_snapshot(
            "F22",
            error=RuntimeError("scrubber tripped"),
            session_id="sess-1",
            task_id="task-9",
            card_id=42,
        )
    assert result.action == "halt"
    assert result.f_code == "F22"
    assert result.handler == "halt_alert_snapshot"
    assert "F22" in result.message
    assert "scrubber tripped" in result.message
    mock_send.assert_called_once()
    mock_update.assert_called_once()
    assert mock_update.call_args.kwargs.get("status") == "blocked"


def test_halt_alert_snapshot_telegram_failure_is_isolated():
    """Telegram going down must NOT prevent the card transition or return."""
    with (
        mock.patch(
            "lib.kanban.telegram_bridge.send_alert",
            side_effect=RuntimeError("telegram api down"),
        ),
        mock.patch("lib.kanban.telegram_bridge.update_card_status") as mock_update,
    ):
        result = halt_alert_snapshot(
            "F27",
            session_id="sess-2",
        )
    assert result.action == "halt"
    mock_update.assert_called_once()


def test_halt_alert_snapshot_card_transition_failure_is_isolated():
    """Kanban update_card_status failing must NOT raise."""
    with (
        mock.patch("lib.kanban.telegram_bridge.send_alert"),
        mock.patch(
            "lib.kanban.telegram_bridge.update_card_status",
            side_effect=sqlite3_error(),
        ),
    ):
        result = halt_alert_snapshot("F28", session_id="sess-3")
    assert result.action == "halt"


def sqlite3_error():
    import sqlite3

    return sqlite3.OperationalError("database is locked")


def test_halt_alert_snapshot_writes_checkpoint_when_provided():
    checkpoint = mock.MagicMock()
    with (
        mock.patch("lib.kanban.telegram_bridge.send_alert"),
        mock.patch("lib.kanban.telegram_bridge.update_card_status"),
    ):
        halt_alert_snapshot(
            "F29",
            session_id="sess-4",
            checkpoint=checkpoint,
            state={"step": 7, "scratch": "..."},
        )
    checkpoint.maybe_write.assert_called_once()
    assert checkpoint.maybe_write.call_args.kwargs["step"] == 7


def test_halt_alert_snapshot_no_session_skips_card_transition():
    with (
        mock.patch("lib.kanban.telegram_bridge.send_alert") as mock_send,
        mock.patch("lib.kanban.telegram_bridge.update_card_status") as mock_update,
    ):
        halt_alert_snapshot("F33", error=ValueError("boom"))
    mock_send.assert_called_once()
    mock_update.assert_not_called()


# ----------------------------------------------------------------------
# Dispatch entrypoint
# ----------------------------------------------------------------------


def test_dispatch_routes_to_registered_handler():
    result = dispatch("F1", attempt=1, jitter_range_pct=0)
    assert result.handler == "retry_with_backoff"
    assert result.action == "retry"


def test_dispatch_unknown_f_code_falls_through_to_f33():
    """Unknown F-codes must route to F33's halt_alert_snapshot."""
    with (
        mock.patch("lib.kanban.telegram_bridge.send_alert"),
        mock.patch("lib.kanban.telegram_bridge.update_card_status"),
    ):
        result = dispatch("F999")
    assert result.f_code == "F33"
    assert result.handler == "halt_alert_snapshot"
    assert result.action == "halt"


# ----------------------------------------------------------------------
# Stub delegation — trichotomy class fallback
# ----------------------------------------------------------------------


def test_stub_for_self_heal_class_delegates_to_retry(monkeypatch):
    """F8 → refresh_adc_and_retry (stub) → SELF_HEAL → retry_with_backoff."""
    assert FAILURE_MATRIX["F8"]["class"] == TrichotomyClass.SELF_HEAL
    result = dispatch("F8", attempt=1, jitter_range_pct=0)
    assert result.action == "retry"
    assert result.f_code == "F8"


def test_stub_for_fail_soft_class_delegates_to_local_log(tmp_path):
    """F14 → skip_tool_class (stub) → FAIL_SOFT → fallback_local_log."""
    assert FAILURE_MATRIX["F14"]["class"] == TrichotomyClass.FAIL_SOFT
    result = dispatch("F14", payload={"tool": "github_search"}, log_dir=tmp_path)
    assert result.action == "continue"
    assert result.f_code == "F14"
    # The stub delegated to fallback_local_log, so a JSONL file should exist.
    assert list(tmp_path.rglob("f14.jsonl"))


def test_stub_for_fail_loud_class_delegates_to_halt():
    """F25 → halt_alert_request_approval (stub) → FAIL_LOUD → halt_alert_snapshot."""
    assert FAILURE_MATRIX["F25"]["class"] == TrichotomyClass.FAIL_LOUD
    with (
        mock.patch("lib.kanban.telegram_bridge.send_alert"),
        mock.patch("lib.kanban.telegram_bridge.update_card_status"),
    ):
        result = dispatch("F25", session_id="sess-9")
    assert result.action == "halt"
    assert result.f_code == "F25"


def test_stub_logs_warning_identifying_handler_name(caplog):
    """Operators need the unimplemented handler name in logs to prioritize."""
    import logging

    with (
        caplog.at_level(logging.WARNING, logger="lib.durability.handlers"),
        mock.patch("lib.kanban.telegram_bridge.send_alert"),
        mock.patch("lib.kanban.telegram_bridge.update_card_status"),
    ):
        dispatch("F25")
    warn_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("halt_alert_request_approval" in m for m in warn_messages)


# ----------------------------------------------------------------------
# HandlerResult dataclass
# ----------------------------------------------------------------------


def test_handler_result_defaults():
    r = HandlerResult(action="retry")
    assert r.action == "retry"
    assert r.delay_ms == 0
    assert r.f_code is None
    assert r.handler is None
    assert r.message is None
    assert r.extra == {}


# ----------------------------------------------------------------------
# F34 — interrupt_with_loop_feedback (production handler)
# ----------------------------------------------------------------------


def test_interrupt_with_loop_feedback_returns_continue_action(tmp_path):
    result = interrupt_with_loop_feedback(
        "F34",
        session_id="sess-loop-1",
        tool_name="github_search",
        repeat_count=5,
        fingerprint="abc123",
        log_dir=tmp_path,
    )
    assert isinstance(result, HandlerResult)
    assert result.action == "continue"
    assert result.f_code == "F34"
    assert result.handler == "interrupt_with_loop_feedback"


def test_interrupt_with_loop_feedback_message_includes_tool_and_count(tmp_path):
    result = interrupt_with_loop_feedback(
        "F34",
        tool_name="github_search",
        repeat_count=7,
        log_dir=tmp_path,
    )
    assert "github_search" in result.message
    assert "7" in result.message


def test_interrupt_with_loop_feedback_extra_carries_orchestrator_payload(tmp_path):
    """The orchestrator reads extra['loop_break_feedback'] to inject into the next turn."""
    result = interrupt_with_loop_feedback(
        "F34",
        session_id="sess-loop-2",
        tool_name="bash",
        repeat_count=5,
        fingerprint="deadbeef",
        log_dir=tmp_path,
    )
    assert result.extra["loop_break_feedback"] == result.message
    assert result.extra["tool_name"] == "bash"
    assert result.extra["repeat_count"] == 5
    assert result.extra["fingerprint"] == "deadbeef"


def test_interrupt_with_loop_feedback_writes_jsonl_forensic_log(tmp_path):
    interrupt_with_loop_feedback(
        "F34",
        session_id="sess-loop-3",
        tool_name="grep",
        repeat_count=5,
        log_dir=tmp_path,
    )
    jsonl_files = list(tmp_path.rglob("f34.jsonl"))
    assert len(jsonl_files) == 1
    record = json.loads(jsonl_files[0].read_text(encoding="utf-8").strip())
    assert record["f_code"] == "F34"
    assert record["payload"]["session_id"] == "sess-loop-3"
    assert record["payload"]["tool_name"] == "grep"
    assert record["payload"]["repeat_count"] == 5
    assert "loop_break_feedback" in record["payload"]


def test_interrupt_with_loop_feedback_handles_missing_optional_fields(tmp_path):
    """Sparse dispatch (no tool_name, no repeat_count) must not raise."""
    result = interrupt_with_loop_feedback("F34", log_dir=tmp_path)
    assert result.action == "continue"
    assert "<unknown tool>" in result.message
    assert "N" in result.message  # placeholder for repeat count


def test_interrupt_with_loop_feedback_forensic_log_failure_is_isolated():
    """A broken local-log write must NOT raise — handler returns normally."""
    with mock.patch(
        "lib.durability.handlers.fallback_local_log",
        side_effect=OSError("disk full"),
    ):
        result = interrupt_with_loop_feedback(
            "F34",
            tool_name="x",
            repeat_count=5,
        )
    assert result.action == "continue"
    assert result.handler == "interrupt_with_loop_feedback"


def test_interrupt_with_loop_feedback_logs_warning_with_evidence(caplog, tmp_path):
    import logging

    with caplog.at_level(logging.WARNING, logger="lib.durability.handlers"):
        interrupt_with_loop_feedback(
            "F34",
            session_id="sess-x",
            tool_name="curl",
            repeat_count=5,
            log_dir=tmp_path,
        )
    warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("interrupt_with_loop_feedback" in m for m in warn_msgs)
    assert any("curl" in m for m in warn_msgs)


def test_interrupt_with_loop_feedback_preserves_detector_payload(tmp_path):
    """Caller-supplied payload dict survives into the forensic record."""
    interrupt_with_loop_feedback(
        "F34",
        tool_name="x",
        repeat_count=5,
        payload={"custom_field": "custom_value"},
        log_dir=tmp_path,
    )
    record = json.loads(list(tmp_path.rglob("f34.jsonl"))[0].read_text(encoding="utf-8").strip())
    assert record["payload"]["detector_payload"] == {"custom_field": "custom_value"}


def test_loop_feedback_template_is_exported_for_external_consumers():
    """Orchestrator code may want to format the same string without invoking
    the handler — e.g. to inject loop-break guidance preemptively."""
    s = LOOP_FEEDBACK_TEMPLATE.format(tool_name="x", repeat_count=5)
    assert "x" in s and "5" in s


# ----------------------------------------------------------------------
# F36 — escalate_context_pressure (production handler)
# ----------------------------------------------------------------------


def test_escalate_context_pressure_returns_continue_action(tmp_path):
    with (
        mock.patch("lib.kanban.telegram_bridge.send_alert"),
    ):
        result = escalate_context_pressure(
            "F36",
            session_id="sess-ctx-1",
            prompt_tokens=190_000,
            context_length=200_000,
            model="claude-opus-4-7",
            log_dir=tmp_path,
        )
    assert isinstance(result, HandlerResult)
    assert result.action == "continue"
    assert result.f_code == "F36"
    assert result.handler == "escalate_context_pressure"


def test_escalate_context_pressure_message_includes_ratio_and_model(tmp_path):
    with mock.patch("lib.kanban.telegram_bridge.send_alert"):
        result = escalate_context_pressure(
            "F36",
            prompt_tokens=180_000,
            context_length=200_000,
            model="claude-opus-4-7",
            log_dir=tmp_path,
        )
    assert "claude-opus-4-7" in result.message
    assert "90.0%" in result.message
    assert "200,000" in result.message
    assert "180,000" in result.message


def test_escalate_context_pressure_reads_payload_when_kwargs_missing(tmp_path):
    """Observability shim dispatches via payload={...}; handler must accept that form."""
    with mock.patch("lib.kanban.telegram_bridge.send_alert"):
        result = escalate_context_pressure(
            "F36",
            session_id="sess-ctx-payload",
            payload={
                "model": "vertex_ai/claude-sonnet-4-6",
                "prompt_tokens": 195_000,
                "context_length": 200_000,
            },
            log_dir=tmp_path,
        )
    assert "vertex_ai/claude-sonnet-4-6" in result.message
    assert "97.5%" in result.message
    assert result.extra["ratio"] == pytest.approx(0.975)


def test_escalate_context_pressure_calls_telegram_send_alert(tmp_path):
    with mock.patch("lib.kanban.telegram_bridge.send_alert") as mock_send:
        escalate_context_pressure(
            "F36",
            session_id="sess-ctx-tg",
            prompt_tokens=190_000,
            context_length=200_000,
            model="claude-opus-4-7",
            card_id=42,
            log_dir=tmp_path,
        )
    mock_send.assert_called_once()
    # Card id is the first positional arg per telegram_bridge.send_alert signature.
    args, kwargs = mock_send.call_args
    assert args[0] == 42
    # The message is the second positional arg.
    assert "claude-opus-4-7" in args[1]


def test_escalate_context_pressure_does_not_call_update_card_status(tmp_path):
    """FAIL_SOFT — handler MUST NOT transition the card to BLOCKED."""
    with (
        mock.patch("lib.kanban.telegram_bridge.send_alert"),
        mock.patch("lib.kanban.telegram_bridge.update_card_status") as mock_update,
    ):
        escalate_context_pressure(
            "F36",
            session_id="sess-ctx-noblock",
            prompt_tokens=190_000,
            context_length=200_000,
            model="claude-opus-4-7",
            card_id=42,
            log_dir=tmp_path,
        )
    mock_update.assert_not_called()


def test_escalate_context_pressure_telegram_failure_is_isolated(tmp_path):
    with mock.patch(
        "lib.kanban.telegram_bridge.send_alert",
        side_effect=RuntimeError("telegram api down"),
    ):
        result = escalate_context_pressure(
            "F36",
            prompt_tokens=190_000,
            context_length=200_000,
            model="claude-opus-4-7",
            log_dir=tmp_path,
        )
    assert result.action == "continue"
    # Forensic log still written despite Telegram failure.
    assert list(tmp_path.rglob("f36.jsonl"))


def test_escalate_context_pressure_writes_jsonl_forensic_log(tmp_path):
    with mock.patch("lib.kanban.telegram_bridge.send_alert"):
        escalate_context_pressure(
            "F36",
            session_id="sess-ctx-jsonl",
            prompt_tokens=180_000,
            context_length=200_000,
            model="claude-opus-4-7",
            log_dir=tmp_path,
        )
    jsonl_files = list(tmp_path.rglob("f36.jsonl"))
    assert len(jsonl_files) == 1
    record = json.loads(jsonl_files[0].read_text(encoding="utf-8").strip())
    assert record["f_code"] == "F36"
    assert record["payload"]["session_id"] == "sess-ctx-jsonl"
    assert record["payload"]["model"] == "claude-opus-4-7"
    assert record["payload"]["prompt_tokens"] == 180_000
    assert record["payload"]["context_length"] == 200_000
    assert record["payload"]["ratio"] == pytest.approx(0.9)
    assert "escalation_message" in record["payload"]


def test_escalate_context_pressure_extra_has_stable_flags(tmp_path):
    """Hermes loop layer checks extra['context_pressure'] to force compaction."""
    with mock.patch("lib.kanban.telegram_bridge.send_alert"):
        result = escalate_context_pressure(
            "F36",
            prompt_tokens=190_000,
            context_length=200_000,
            model="claude-opus-4-7",
            log_dir=tmp_path,
        )
    assert result.extra["context_pressure"] is True
    assert result.extra["recommended_action"] == "force_compaction_or_request_new_session"
    assert result.extra["ratio"] == pytest.approx(0.95)
    assert result.extra["model"] == "claude-opus-4-7"


def test_escalate_context_pressure_handles_degenerate_inputs(tmp_path):
    """Missing/zero context_length must NOT crash the handler."""
    with mock.patch("lib.kanban.telegram_bridge.send_alert"):
        result = escalate_context_pressure(
            "F36",
            session_id="sess-degen",
            prompt_tokens=None,
            context_length=None,
            model=None,
            log_dir=tmp_path,
        )
    assert result.action == "continue"
    assert result.f_code == "F36"
    assert result.extra["ratio"] == 0.0
    assert "incomplete" in result.message.lower()


def test_escalate_context_pressure_forensic_log_failure_is_isolated():
    """Local-log failure does not break handler return."""
    with (
        mock.patch("lib.kanban.telegram_bridge.send_alert"),
        mock.patch(
            "lib.durability.handlers.fallback_local_log",
            side_effect=OSError("disk full"),
        ),
    ):
        result = escalate_context_pressure(
            "F36",
            prompt_tokens=190_000,
            context_length=200_000,
            model="claude-opus-4-7",
        )
    assert result.action == "continue"


def test_escalate_context_pressure_logs_warning(caplog, tmp_path):
    import logging

    with (
        caplog.at_level(logging.WARNING, logger="lib.durability.handlers"),
        mock.patch("lib.kanban.telegram_bridge.send_alert"),
    ):
        escalate_context_pressure(
            "F36",
            session_id="sess-warn",
            prompt_tokens=180_000,
            context_length=200_000,
            model="claude-opus-4-7",
            log_dir=tmp_path,
        )
    warn_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("escalate_context_pressure" in m for m in warn_msgs)


# ----------------------------------------------------------------------
# Registry promotion — F34/F36 stubs replaced by production handlers
# ----------------------------------------------------------------------


def test_F34_handler_in_registry_is_production_not_stub():
    assert HANDLER_REGISTRY["interrupt_with_loop_feedback"] is interrupt_with_loop_feedback
    # A stub would have __name__ starting with "stub_"
    assert not HANDLER_REGISTRY["interrupt_with_loop_feedback"].__name__.startswith("stub_")


def test_F36_handler_in_registry_is_production_not_stub():
    assert HANDLER_REGISTRY["escalate_context_pressure"] is escalate_context_pressure
    assert not HANDLER_REGISTRY["escalate_context_pressure"].__name__.startswith("stub_")


def test_dispatch_F34_routes_to_production_handler(tmp_path):
    result = dispatch(
        "F34",
        session_id="sess-d34",
        tool_name="grep",
        repeat_count=5,
        log_dir=tmp_path,
    )
    assert result.handler == "interrupt_with_loop_feedback"
    assert result.action == "continue"
    assert "grep" in result.message
    assert "5" in result.message


def test_dispatch_F36_routes_to_production_handler(tmp_path):
    with mock.patch("lib.kanban.telegram_bridge.send_alert"):
        result = dispatch(
            "F36",
            session_id="sess-d36",
            prompt_tokens=190_000,
            context_length=200_000,
            model="claude-opus-4-7",
            log_dir=tmp_path,
        )
    assert result.handler == "escalate_context_pressure"
    assert result.action == "continue"
    assert "claude-opus-4-7" in result.message
