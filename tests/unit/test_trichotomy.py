"""Unit tests for the trichotomy classifier + retry policy."""

from lib.durability import trichotomy


class FakeRateLimitError(Exception):
    pass


class FakeTimeoutError(TimeoutError):
    pass


def test_classify_rate_limit_to_F1_self_heal():
    err = FakeRateLimitError("HTTP 429 rate_limit_exceeded")
    code = trichotomy.classify(err)
    assert code == "F1"


def test_classify_timeout_to_F2_self_heal():
    err = FakeTimeoutError("upstream timed out after 60s")
    code = trichotomy.classify(err)
    assert code == "F2"


def test_classify_unknown_exception_to_F33_fail_loud():
    err = RuntimeError("something exotic")
    code = trichotomy.classify(err)
    assert code == "F33"


def test_retry_policy_exponential_backoff_within_tolerance():
    delays = [trichotomy.backoff_delay(attempt=i) for i in range(1, 4)]
    assert 250 <= delays[0] <= 750
    assert 500 <= delays[1] <= 1500
    assert 1000 <= delays[2] <= 3000


def test_retry_policy_caps_at_max_delay():
    delay = trichotomy.backoff_delay(attempt=20)
    assert delay <= 30000
