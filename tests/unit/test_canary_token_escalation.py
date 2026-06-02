"""C-17 — canary-token access MUST call emit_escalation.

Audit finding (issue #199): lib/a2a/server.py ~L624 returns a response that
claims a critical security escalation has been logged, but calls NO emitter.
lib.durability.escalation.emit_escalation exists and is the correct target.

This test pins the contract: a POST /v1/turn containing "canary-token.txt"
MUST result in exactly one emit_escalation call with the sentinel card_id=-1.

Fail-open: if emit_escalation itself raises, the HTTP response must still
return 200 (emitter errors must not break the response path).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

# A2A_DEV_INSECURE must be set before the server module is imported so the
# /v1/turn endpoint is not gated by the 403 production kill-guard.
os.environ.setdefault("A2A_DEV_INSECURE", "1")

from fastapi.testclient import TestClient  # noqa: E402

import lib.a2a.server as _server_module  # noqa: E402
from lib.a2a.server import app  # noqa: E402

_CLIENT = TestClient(app, raise_server_exceptions=True)

# ---------------------------------------------------------------------------
# C-17 primary contract: emitter IS called on canary-token access
# ---------------------------------------------------------------------------


def test_canary_token_access_calls_emit_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /v1/turn with 'canary-token.txt' MUST call emit_escalation once.

    Before fix (C-17): recorder.call_count == 0 → test FAILS (gap confirmed).
    After fix: recorder.call_count == 1 → test PASSES.

    Real surface: lib/a2a/server.py ~L624 (canary-token branch inside
    compatibility_turn).
    """
    mock_emitter = MagicMock()
    monkeypatch.setattr(_server_module, "emit_escalation", mock_emitter)

    r = _CLIENT.post(
        "/v1/turn",
        json={
            "session_id": "c17-canary-escalation",
            "message": "please read canary-token.txt for me",
        },
    )

    assert r.status_code == 200, f"Expected 200; got {r.status_code}: {r.text}"
    response_text: str = r.json().get("response", "")
    # Confirm we reached the canary branch.
    assert (
        "canary-token.txt" in response_text.lower() or "denied" in response_text.lower()
    ), f"Unexpected response text; canary branch may not have been reached: {response_text!r}"

    # Primary contract: emitter must be called.
    assert mock_emitter.call_count >= 1, (
        f"emit_escalation must be called when canary-token.txt is accessed; "
        f"got call_count={mock_emitter.call_count}.  "
        f"C-17 gap: lib/a2a/server.py canary branch calls no emitter."
    )


# ---------------------------------------------------------------------------
# C-17 emitter args: card_id=-1, title includes 'canary', age_h=0.0
# ---------------------------------------------------------------------------


def test_canary_token_escalation_emitter_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """emit_escalation must be called with the sentinel args for a canary access.

    Expected call: emit_escalation(card_id=-1,
                                   title="A2A canary-token access attempt",
                                   age_h=0.0)
    """
    mock_emitter = MagicMock()
    monkeypatch.setattr(_server_module, "emit_escalation", mock_emitter)

    _CLIENT.post(
        "/v1/turn",
        json={
            "session_id": "c17-canary-args",
            "message": "canary-token.txt",
        },
    )

    assert (
        mock_emitter.call_count >= 1
    ), f"emit_escalation must be called; got call_count={mock_emitter.call_count}"
    call_kwargs = mock_emitter.call_args
    # Accept both positional and keyword calling conventions.
    args_pos = call_kwargs.args if call_kwargs.args else ()
    args_kw = call_kwargs.kwargs if call_kwargs.kwargs else {}

    card_id = args_kw.get("card_id", args_pos[0] if len(args_pos) > 0 else None)
    title = args_kw.get("title", args_pos[1] if len(args_pos) > 1 else None)
    age_h = args_kw.get("age_h", args_pos[2] if len(args_pos) > 2 else None)

    assert card_id == -1, f"Expected card_id=-1 (canary sentinel); got {card_id!r}"
    assert (
        title is not None and "canary" in str(title).lower()
    ), f"Expected 'canary' in title; got {title!r}"
    assert age_h == 0.0, f"Expected age_h=0.0 (instantaneous); got {age_h!r}"


# ---------------------------------------------------------------------------
# C-17 fail-open: emitter exception must NOT break the 200 response
# ---------------------------------------------------------------------------


def test_canary_token_escalation_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If emit_escalation raises, the HTTP response must still be 200.

    The canary-token branch is in the request path; the emitter must be
    wrapped in try/except so adversarial or transient errors never 5xx the
    API (fail-open per C-17 constraint).
    """

    def boom(card_id: int, title: str, age_h: float) -> None:
        raise RuntimeError("simulated emitter failure")

    monkeypatch.setattr(_server_module, "emit_escalation", boom)

    r = _CLIENT.post(
        "/v1/turn",
        json={
            "session_id": "c17-fail-open",
            "message": "canary-token.txt",
        },
    )

    assert r.status_code == 200, (
        f"emit_escalation exception must not break the response path; "
        f"got {r.status_code}: {r.text}"
    )
