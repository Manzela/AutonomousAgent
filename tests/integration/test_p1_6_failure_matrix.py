"""5 representative failure modes asserted against ``lib.durability.trichotomy.classify``.

These bodies are SELF-CONTAINED classifier checks — they build an exception and assert its
``classify`` / ``trichotomy_class`` verdict. They make NO network call, so the previous
``skipif(not _proxy_reachable())`` gate was spurious: it skipped 5 deterministic tests in the
nightly whenever the LiteLLM proxy health-check didn't answer, for no reason (the bodies never
touch the proxy). The gate is removed so the tests run unconditionally; the ``integration``
marker keeps them in the integration tier (off the unit pass) per the original intent.
"""

import pytest

pytestmark = pytest.mark.integration


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
