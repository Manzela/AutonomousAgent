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


# ---------------------------------------------------------------------------
# H6 — SA validation tests
# ---------------------------------------------------------------------------


def test_missing_sa_raises() -> None:
    """HERMES_A2A_SA unset → RuntimeError when A2A is enabled."""
    from lib.a2a import register

    ctx = MagicMock()
    env = {k: v for k, v in os.environ.items() if k != "HERMES_A2A_SA"}
    env["HERMES_A2A_ENABLED"] = "true"
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(RuntimeError, match="HERMES_A2A_SA"):
            register(ctx)


def test_malformed_sa_raises() -> None:
    """Non-SA-shaped string → RuntimeError."""
    from lib.a2a import register

    ctx = MagicMock()
    with patch.dict(os.environ, {"HERMES_A2A_ENABLED": "true", "HERMES_A2A_SA": "not-an-email"}):
        with pytest.raises(RuntimeError, match="HERMES_A2A_SA"):
            register(ctx)


def test_trailing_hyphen_in_name_raises() -> None:
    """SA name ending in hyphen (invalid per GCP constraints) → RuntimeError."""
    from lib.a2a import register

    ctx = MagicMock()
    bad_sa = "bad-name-@autonomous-agent-2026.iam.gserviceaccount.com"
    with patch.dict(os.environ, {"HERMES_A2A_ENABLED": "true", "HERMES_A2A_SA": bad_sa}):
        with pytest.raises(RuntimeError, match="HERMES_A2A_SA"):
            register(ctx)


def test_valid_sa_does_not_raise() -> None:
    """Well-formed SA email → no exception."""
    from lib.a2a import register

    ctx = MagicMock()
    with patch.dict(os.environ, {"HERMES_A2A_ENABLED": "true", "HERMES_A2A_SA": _VALID_SA}):
        register(ctx)  # must not raise

    ctx.register_hook.assert_called_once()


def test_sa_validation_skipped_when_disabled() -> None:
    """When A2A is disabled, missing SA must not raise — validation is gated."""
    from lib.a2a import register

    ctx = MagicMock()
    env = {k: v for k, v in os.environ.items() if k != "HERMES_A2A_SA"}
    env["HERMES_A2A_ENABLED"] = "false"
    with patch.dict(os.environ, env, clear=True):
        register(ctx)  # must not raise even without HERMES_A2A_SA

    ctx.register_hook.assert_not_called()
