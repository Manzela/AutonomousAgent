"""Tests for L4: _emit_audit_log uses logging instead of print."""

from __future__ import annotations

import json
import logging


def test_emit_audit_log_goes_to_audit_logger(caplog) -> None:
    """_emit_audit_log must emit to the 'a2a.audit' logger at INFO level."""
    from lib.a2a.auth import _emit_audit_log

    with caplog.at_level(logging.INFO, logger="a2a.audit"):
        _emit_audit_log(
            "accepted", None, None, None, None, peer_sa="sa@proj.iam.gserviceaccount.com"
        )

    audit_records = [r for r in caplog.records if r.name == "a2a.audit"]
    assert audit_records, f"Expected record on 'a2a.audit' logger; got records: {[(r.name, r.message) for r in caplog.records]}"
    assert audit_records[0].levelno == logging.INFO


def test_emit_audit_log_message_is_valid_json_with_correct_shape(caplog) -> None:
    """The log message must be valid JSON containing decision, peer_agent_id, ts, event."""
    from lib.a2a.auth import _emit_audit_log

    with caplog.at_level(logging.INFO, logger="a2a.audit"):
        _emit_audit_log(
            "rejected_not_allowlisted",
            None,
            None,
            None,
            None,
            peer_sa="evil@bad-proj.iam.gserviceaccount.com",
        )

    record = next(r for r in caplog.records if r.name == "a2a.audit")
    entry = json.loads(record.getMessage())
    assert entry["decision"] == "rejected_not_allowlisted"
    assert entry["peer_agent_id"] == "evil@bad-proj.iam.gserviceaccount.com"
    assert entry["event"] == "auth_decision"
    assert "ts" in entry


def test_emit_audit_log_does_not_write_to_stdout(capsys) -> None:
    """After replacing print(), _emit_audit_log must not write directly to stdout."""
    from lib.a2a.auth import _emit_audit_log

    _emit_audit_log("accepted", None, None, None, None)
    captured = capsys.readouterr()
    assert captured.out == "", f"print() detected in _emit_audit_log output: {captured.out!r}"
