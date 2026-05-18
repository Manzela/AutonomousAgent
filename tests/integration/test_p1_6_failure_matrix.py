"""Integration test exercising 5 representative failure modes against the live stack.

Marked skipif when the LiteLLM proxy is unreachable. The body tests are
self-contained classifier checks; the live-stack gate is preserved per spec
so this test joins the integration tier rather than running on every unit pass.
"""

import os
import pytest
import httpx

PROXY_URL = os.environ.get("LITELLM_URL", "http://localhost:4000")


def _proxy_reachable():
    try:
        return httpx.get(f"{PROXY_URL}/health/readiness", timeout=2).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _proxy_reachable(), reason="LiteLLM proxy not running")


def test_F1_rate_limit_classifies_as_self_heal():
    from lib.durability.trichotomy import classify

    err = RuntimeError("HTTP 429 rate_limit_exceeded: too many requests")
    assert classify(err) == "F1"


def test_F2_timeout_classifies_as_self_heal():
    from lib.durability.trichotomy import classify

    err = TimeoutError("request timed out after 60s")
    assert classify(err) == "F2"


def test_F22_secret_leak_classifies_as_fail_loud():
    from lib.durability.trichotomy import classify, trichotomy_class
    from lib.durability.failure_matrix import TrichotomyClass

    err = RuntimeError("REDACTED:critical aws_secret_key detected in output")
    assert classify(err) == "F22"
    assert trichotomy_class(err) == TrichotomyClass.FAIL_LOUD


def test_F11_gemini_thinking_truncation_classifies_as_self_heal():
    from lib.durability.trichotomy import classify

    err = RuntimeError("Empty content — max_tokens too low for thinking model")
    assert classify(err) == "F11"


def test_F33_unclassified_exception_falls_through_to_fail_loud():
    from lib.durability.trichotomy import classify, trichotomy_class
    from lib.durability.failure_matrix import TrichotomyClass

    err = ValueError("a totally novel error nobody planned for")
    assert classify(err) == "F33"
    assert trichotomy_class(err) == TrichotomyClass.FAIL_LOUD
