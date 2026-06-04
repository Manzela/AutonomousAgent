"""Unit tests for rate limiting (P0-3) and Model Armor guardrail (P1-5).

Rate limit tests are gated behind a starlette import check since the
middleware module depends on starlette (which is installed via the `a2a`
extra, not in all environments).
"""

from __future__ import annotations


import pytest

# ── starlette availability check ─────────────────────────────────────────────

try:
    import starlette  # noqa: F401

    HAS_STARLETTE = True
except ImportError:
    HAS_STARLETTE = False

starlette_required = pytest.mark.skipif(
    not HAS_STARLETTE,
    reason="starlette not installed (install via `uv sync --extra a2a`)",
)


# ── Rate Limit Configuration Tests ──────────────────────────────────────────


@starlette_required
class TestRateLimitConfiguration:
    def test_default_limits(self):
        from app.middleware import LIMITS

        assert LIMITS["goal"] == "10/minute"
        assert LIMITS["resume"] == "30/minute"
        assert LIMITS["ops"] == "5/minute"
        assert LIMITS["healthz"] == "60/minute"
        assert LIMITS["webhook"] == "30/minute"
        assert LIMITS["default"] == "60/minute"

    def test_remote_address_extraction_xff(self):
        from app.middleware import _get_remote_address

        class MockRequest:
            headers = {"X-Forwarded-For": "1.2.3.4, 10.0.0.1"}
            client = None

        addr = _get_remote_address(MockRequest())
        assert addr == "1.2.3.4"

    def test_remote_address_extraction_direct(self):
        from app.middleware import _get_remote_address

        class MockClient:
            host = "192.168.1.1"

        class MockRequest:
            headers = {}
            client = MockClient()

        addr = _get_remote_address(MockRequest())
        assert addr == "192.168.1.1"

    def test_remote_address_extraction_fallback(self):
        from app.middleware import _get_remote_address

        class MockRequest:
            headers = {}
            client = None

        addr = _get_remote_address(MockRequest())
        assert addr == "127.0.0.1"


# ── Model Armor Guardrail Tests ─────────────────────────────────────────────


class TestModelArmorGuardrail:
    """Unit tests for lib/guardrails/model_armor.py (P1-5)."""

    def test_inactive_when_no_template(self):
        from lib.guardrails.model_armor import ModelArmorGuardrail

        g = ModelArmorGuardrail(template_name="", enabled=True)
        assert not g.active

    def test_inactive_when_disabled(self):
        from lib.guardrails.model_armor import ModelArmorGuardrail

        g = ModelArmorGuardrail(template_name="some/template", enabled=False)
        assert not g.active

    def test_screen_input_returns_allow_when_inactive(self):
        from lib.guardrails.model_armor import GuardrailAction, ModelArmorGuardrail

        g = ModelArmorGuardrail(template_name="", enabled=True)
        verdict = g.screen_input("test goal")
        assert verdict.action == GuardrailAction.ALLOW
        assert not verdict.blocked

    def test_screen_output_returns_allow_when_inactive(self):
        from lib.guardrails.model_armor import GuardrailAction, ModelArmorGuardrail

        g = ModelArmorGuardrail(template_name="", enabled=True)
        verdict = g.screen_output("test output")
        assert verdict.action == GuardrailAction.ALLOW
        assert not verdict.blocked

    def test_active_when_configured(self):
        from lib.guardrails.model_armor import ModelArmorGuardrail

        g = ModelArmorGuardrail(
            template_name="projects/p/locations/l/templates/t",
            enabled=True,
        )
        assert g.active

    def test_screen_input_failopen_on_client_unavailable(self):
        from lib.guardrails.model_armor import GuardrailAction, ModelArmorGuardrail

        g = ModelArmorGuardrail(
            template_name="projects/p/locations/l/templates/t",
            enabled=True,
        )
        # Force _init_attempted so _get_client returns None
        g._init_attempted = True
        verdict = g.screen_input("test")
        assert verdict.action == GuardrailAction.ALLOW
        assert "unavailable" in verdict.reason

    def test_screen_output_failopen_on_client_unavailable(self):
        from lib.guardrails.model_armor import GuardrailAction, ModelArmorGuardrail

        g = ModelArmorGuardrail(
            template_name="projects/p/locations/l/templates/t",
            enabled=True,
        )
        g._init_attempted = True
        verdict = g.screen_output("test output")
        assert verdict.action == GuardrailAction.ALLOW
        assert "unavailable" in verdict.reason

    def test_guardrail_verdict_blocked_property(self):
        from lib.guardrails.model_armor import GuardrailAction, GuardrailVerdict

        v_allow = GuardrailVerdict(GuardrailAction.ALLOW, "ok")
        assert not v_allow.blocked

        v_block = GuardrailVerdict(GuardrailAction.BLOCK, "bad")
        assert v_block.blocked

        v_warn = GuardrailVerdict(GuardrailAction.WARN, "hmm")
        assert not v_warn.blocked
