"""Integration tests: PHI scrubber wired into jsonrpc_dispatch (server.py).

Per HAND-OFF.md production checklist item:
  'Wire scrub_inbound_params into jsonrpc_dispatch before handler dispatch'

Verifies that PHI in inbound message params is redacted at the A2A boundary
before any handler sees the content — pattern: SSN, email are stripped.
Tests use TestClient so no network is involved.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from lib.a2a.server import app

client = TestClient(app)


def test_ssn_in_message_is_scrubbed_before_handler() -> None:
    """SSN in inbound message/send params is redacted before bridge sees it."""
    from dataclasses import dataclass

    @dataclass
    class _Spec:
        id: str = "spec-scrub-001"

    captured_params: list[dict] = []

    def _capture_bridge(params, identity=None, **kw):
        captured_params.append(params)
        return _Spec()

    with (
        patch("lib.a2a.server.bridge_inbound_to_taskspec", side_effect=_capture_bridge),
        patch("lib.a2a.server.bridge_taskspec_status_to_a2a", return_value="SUBMITTED"),
    ):
        resp = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "USER",
                        "parts": [{"text": "My SSN is 123-45-6789, please help"}],
                    }
                },
            },
        )

    assert resp.status_code == 200
    assert len(captured_params) == 1
    param_text = str(captured_params[0])
    assert "123-45-6789" not in param_text, "SSN must be scrubbed before handler"
    assert "[REDACTED]" in param_text


def test_email_in_message_is_scrubbed_before_handler() -> None:
    """Email address in inbound params is redacted before bridge sees it."""
    from dataclasses import dataclass

    @dataclass
    class _Spec:
        id: str = "spec-scrub-002"

    captured_params: list[dict] = []

    def _capture_bridge(params, identity=None, **kw):
        captured_params.append(params)
        return _Spec()

    with (
        patch("lib.a2a.server.bridge_inbound_to_taskspec", side_effect=_capture_bridge),
        patch("lib.a2a.server.bridge_taskspec_status_to_a2a", return_value="SUBMITTED"),
    ):
        resp = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "USER",
                        "parts": [{"text": "Contact patient@hospital.org for records"}],
                    }
                },
            },
        )

    assert resp.status_code == 200
    assert "patient@hospital.org" not in str(captured_params[0])
    assert "[REDACTED]" in str(captured_params[0])


def test_ssn_in_stream_params_is_scrubbed() -> None:
    """PHI in POST /stream params is redacted before the handler sees it."""
    captured_params: list[dict] = []

    async def _capture_stream(params: dict) -> Any:
        captured_params.append(params)
        from fastapi.responses import StreamingResponse as _SR

        async def _gen():
            yield b"data: {}\n\n"

        return _SR(_gen(), media_type="text/event-stream")

    with patch("lib.a2a.server.handle_stream_message", side_effect=_capture_stream):
        with client.stream(
            "POST",
            "/stream",
            json={"message": {"role": "USER", "parts": [{"text": "SSN: 999-88-7777"}]}},
        ) as resp:
            resp.read()

    assert len(captured_params) == 1
    assert "999-88-7777" not in str(captured_params[0])
    assert "[REDACTED]" in str(captured_params[0])


def test_ssn_in_subscribe_params_is_scrubbed() -> None:
    """PHI in POST /subscribe params is redacted before the handler sees it."""
    captured_params: list[dict] = []

    async def _capture_subscribe(params: dict) -> Any:
        captured_params.append(params)
        from fastapi.responses import StreamingResponse as _SR

        async def _gen():
            yield b"data: {}\n\n"

        return _SR(_gen(), media_type="text/event-stream")

    with patch("lib.a2a.server.handle_subscribe_task", side_effect=_capture_subscribe):
        with client.stream(
            "POST",
            "/subscribe",
            json={"id": "task-001", "patient_email": "phi@hospital.org"},
        ) as resp:
            resp.read()

    assert len(captured_params) == 1
    assert "phi@hospital.org" not in str(captured_params[0])
    assert "[REDACTED]" in str(captured_params[0])


def test_clean_params_pass_through_unchanged() -> None:
    """Non-PHI params reach the handler unchanged."""
    from dataclasses import dataclass

    @dataclass
    class _Spec:
        id: str = "spec-scrub-003"

    captured_params: list[dict] = []

    def _capture_bridge(params, identity=None, **kw):
        captured_params.append(params)
        return _Spec()

    with (
        patch("lib.a2a.server.bridge_inbound_to_taskspec", side_effect=_capture_bridge),
        patch("lib.a2a.server.bridge_taskspec_status_to_a2a", return_value="SUBMITTED"),
    ):
        resp = client.post(
            "/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "message/send",
                "params": {
                    "message": {
                        "role": "USER",
                        "parts": [{"text": "Compute trajectory delta-v for canary mission"}],
                    }
                },
            },
        )

    assert resp.status_code == 200
    text = captured_params[0]["message"]["parts"][0]["text"]
    assert text == "Compute trajectory delta-v for canary mission"
    assert "[REDACTED]" not in text
