"""Tests for HERMES_A2A_ENABLED feature flag (H10) and HERMES_A2A_SA validation (H6)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


_VALID_SA = "agent-a@autonomous-agent-2026.iam.gserviceaccount.com"


# ---------------------------------------------------------------------------
# H10 — feature flag tests
# ---------------------------------------------------------------------------


def test_register_skipped_when_disabled() -> None:
    """When HERMES_A2A_ENABLED=false, register() returns early without wiring hooks."""
    from lib.a2a import register

    ctx = MagicMock()
    with patch.dict(os.environ, {"HERMES_A2A_ENABLED": "false", "HERMES_A2A_SA": _VALID_SA}):
        register(ctx)

    ctx.register_hook.assert_not_called()


def test_register_proceeds_when_enabled(caplog: pytest.LogCaptureFixture) -> None:
    """When HERMES_A2A_ENABLED=true, register() wires the on_session_start hook."""
    import logging
    from lib.a2a import register

    ctx = MagicMock()
    with (
        patch.dict(os.environ, {"HERMES_A2A_ENABLED": "true", "HERMES_A2A_SA": _VALID_SA}),
        caplog.at_level(logging.INFO, logger="lib.a2a"),
    ):
        register(ctx)

    ctx.register_hook.assert_called_once()
    hook_name, _ = ctx.register_hook.call_args.args
    assert hook_name == "on_session_start"


def test_deprecation_warning_when_flag_unset(caplog: pytest.LogCaptureFixture) -> None:
    """When HERMES_A2A_ENABLED is absent, register() emits a deprecation WARNING."""
    import logging
    from lib.a2a import register

    ctx = MagicMock()
    env = {k: v for k, v in os.environ.items() if k != "HERMES_A2A_ENABLED"}
    env["HERMES_A2A_SA"] = _VALID_SA
    with (
        patch.dict(os.environ, env, clear=True),
        caplog.at_level(logging.WARNING, logger="lib.a2a"),
    ):
        register(ctx)

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "will change to false" in m for m in warning_msgs
    ), f"Expected deprecation warning; got: {warning_msgs}"
