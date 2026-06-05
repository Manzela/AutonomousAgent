"""Tests for the secret scrubber."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from lib.scrubber import Scrubber, ScrubFilter, scrub_string, _reset_singleton_for_tests

REPO_ROOT = Path(__file__).resolve().parents[2]
PATTERNS = REPO_ROOT / "config" / "scrubber-patterns.yaml"
A2A_PATTERNS = REPO_ROOT / "config" / "a2a" / "scrubber-patterns.yaml"


@pytest.fixture(scope="module")
def scrubber() -> Scrubber:
    return Scrubber.from_config(PATTERNS)


# Positives — these MUST be redacted.
@pytest.mark.parametrize(
    "text,expected_pattern",
    [
        ("My key is AKIAIOSFODNN7EXAMPLE here", "aws_access_key_id"),
        ("openai key sk-proj_aBcDeFgHiJkLmNoPqRsTu123 here", "openai_api_key"),
        ("anthropic sk-ant-api03-abcdefghijklmnopqrst here", "anthropic_api_key"),
        ("token ghp_1234567890abcdefghijklmnopqrstuvwxyz here", "github_pat"),
        (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.signaturepart_xxxx",
            "jwt",
        ),
        ('{"type": "service_account", "project_id": "x"}', "gcp_service_account_json"),
        ("-----BEGIN RSA PRIVATE KEY-----\nABCD", "private_key_pem"),
        ("Bot token 123456789:AAFmZpQXqRsTuVwXyZ-_aBcDeFgHiJkLmNoP here", "telegram_bot_token"),
    ],
)
def test_positives_are_redacted(scrubber, text, expected_pattern):
    cleaned, hits = scrubber.scrub(text, source="test")
    assert "[REDACTED:" in cleaned, f"Should have been scrubbed: {text}"
    assert any(
        h.pattern_name == expected_pattern for h in hits
    ), f"Expected pattern {expected_pattern} in hits, got {[h.pattern_name for h in hits]}"


# Negatives — these must NOT be touched.
@pytest.mark.parametrize(
    "text",
    [
        "Just a normal sentence about coding.",
        "Order #ABCD-1234 was shipped.",
        "Visit https://api.github.com/repos/foo/bar for details.",
        "The function returns sk_normal_variable_name in the codebase.",
        "AKIA-suffix-no-format-match-because-too-short",  # AKIA prefix but wrong shape
    ],
)
def test_negatives_are_not_redacted(scrubber, text):
    cleaned, hits = scrubber.scrub(text, source="test")
    # Allow the high-entropy hex pattern to fire (severity=info) but no critical hits
    critical_hits = [h for h in hits if h.severity == "critical"]
    assert critical_hits == [], f"False positive (critical) on: {text} → {critical_hits}"


def test_multiple_secrets_in_one_string(scrubber):
    text = "AKIAIOSFODNN7EXAMPLE and sk-proj_xxxxxxxxxxxxxxxxxxxx in same line"
    cleaned, hits = scrubber.scrub(text, source="test")
    assert cleaned.count("[REDACTED:") == 2
    assert {h.pattern_name for h in hits} >= {"aws_access_key_id", "openai_api_key"}


def test_source_attribution(scrubber):
    _, hits = scrubber.scrub("AKIAIOSFODNN7EXAMPLE", source="model_response")
    assert all(h.source == "model_response" for h in hits)


# ---------------------------------------------------------------------------
# C-16: Slack OAuth token scrubbing (#198)
# ---------------------------------------------------------------------------

# Slack token prefixes: xoxb (bot), xoxa (app-level), xoxp (user/legacy),
# xoxr (refresh), xoxs (service). Tokens are assembled from fragments so that
# the commit does not contain a credential-shaped literal that triggers
# push-protection scanners.
_SLACK_BOT_TOKEN = (
    "xoxb" + "-" + "123456789012" + "-" + "234567890123" + "-" + "aaaaaaaaaaaaaaaaaaaaaaaa"
)
_SLACK_USER_TOKEN = "xoxp" + "-" + "111111111111" + "-" + "222222222222" + "-" + "bbbbbbbbbbbb"
_SLACK_APP_TOKEN = "xoxa" + "-" + "2-aaaaaaaaaaaaaaaaaaaaaa"
_SLACK_REFRESH_TOKEN = "xoxr" + "-" + "1-aaaaaaaaaaaaaaaaaaaa"
_SLACK_SERVICE_TOKEN = "xoxs" + "-" + "1-2-cccccccccccccccccc" + "-" + "dddddddddddd"


@pytest.mark.parametrize(
    "text,prefix",
    [
        (f"token in payload: {_SLACK_BOT_TOKEN}", "xoxb"),
        (f"Authorization header value {_SLACK_USER_TOKEN}", "xoxp"),
        (f"app credential is {_SLACK_APP_TOKEN}", "xoxa"),
        (f"refresh with {_SLACK_REFRESH_TOKEN}", "xoxr"),
        (f"service auth {_SLACK_SERVICE_TOKEN}", "xoxs"),
    ],
)
def test_slack_token_is_redacted(scrubber, text, prefix):
    """Slack OAuth tokens (xox[baprs]-...) MUST be redacted (C-16, #198)."""
    cleaned, hits = scrubber.scrub(text, source="test")
    assert (
        "[REDACTED:slack_token]" in cleaned
    ), f"Slack token with prefix {prefix!r} was NOT redacted. Got: {cleaned!r}"
    assert any(
        h.pattern_name == "slack_token" for h in hits
    ), f"Expected hit 'slack_token' in hits, got {[h.pattern_name for h in hits]}"


@pytest.mark.parametrize(
    "text",
    [
        # 'xox' substring that does NOT match the token shape
        "the xoxford experiment yielded results",
        "proxy-xox-settings are not tokens",
        "xox alone should not match",
        # 'xox' followed by a valid prefix letter but no dash — not a token
        "xoxb without a dash is harmless text",
    ],
)
def test_slack_false_positive_benign_xox_not_redacted(scrubber, text):
    """Strings that merely contain 'xox' but are NOT Slack tokens MUST NOT be redacted."""
    cleaned, hits = scrubber.scrub(text, source="test")
    assert (
        "[REDACTED:slack_token]" not in cleaned
    ), f"False positive: benign text {text!r} was incorrectly redacted → {cleaned!r}"
    slack_hits = [h for h in hits if h.pattern_name == "slack_token"]
    assert slack_hits == [], f"False-positive slack_token hit on benign text {text!r}: {slack_hits}"


# ---------------------------------------------------------------------------
# C-16: A2A path — scrub_inbound_params redacts Slack tokens in nested dicts
# ---------------------------------------------------------------------------


def test_a2a_slack_token_redacted_in_nested_params() -> None:
    """Slack bot token nested inside A2A message params MUST be redacted (C-16, #198).

    This is the actual C-16 risk surface: a caller passing a Slack credential in
    the A2A inbound params dict.  The a2a scrubber uses [REDACTED] (no type suffix).
    Token is assembled from fragments so push-protection scanners are not triggered.
    """
    from lib.a2a.scrubber import scrub_inbound_params

    token = "xoxb" + "-" + "123456789012" + "-" + "234567890123" + "-" + "aaaaaaaaaaaaaaaaaaaaaaaa"
    params = {"message": {"parts": [{"text": f"auth header is {token} please process"}]}}

    result = scrub_inbound_params(params)

    assert token not in str(result), "Slack token MUST NOT appear verbatim after a2a scrub"
    assert "[REDACTED]" in str(result), "a2a scrubber replacement marker must be present"


# ---------------------------------------------------------------------------
# NEW-secret-scrub-1: fine-grained GitHub PATs must be redacted
# ---------------------------------------------------------------------------
# Fine-grained PAT format: github_pat_<22+ alphanum/underscore>_<59+ alphanum/underscore>
# Assembled from fragments so push-protection scanners are not triggered.
_FG_PAT = "github_" + "pat_" + "a" * 22 + "_" + "a" * 59


def test_fine_grained_github_pat_is_redacted(scrubber) -> None:
    """Fine-grained GitHub PAT (github_pat_…) MUST be redacted by the main scrubber.

    The classic PAT regex \b(ghp|gho|…)_[A-Za-z0-9]{36,}\b does NOT match the
    fine-grained prefix, so a dedicated alternative is required (gap NEW-secret-scrub-1).
    """
    text = f"Authorization: token {_FG_PAT}"
    cleaned, hits = scrubber.scrub(text, source="test")
    assert (
        "[REDACTED:github_pat]" in cleaned
    ), f"Fine-grained PAT was NOT redacted. Got: {cleaned!r}"
    assert any(
        h.pattern_name == "github_pat" for h in hits
    ), f"Expected github_pat hit, got: {[h.pattern_name for h in hits]}"


def test_classic_github_pat_still_redacted_after_regex_change(scrubber) -> None:
    """Classic PAT (ghp_…36+ chars) MUST still be redacted after the regex was extended."""
    classic = "ghp_" + "a" * 36
    text = f"token is {classic} end"
    cleaned, hits = scrubber.scrub(text, source="test")
    assert (
        "[REDACTED:github_pat]" in cleaned
    ), f"Classic PAT was NOT redacted after regex change. Got: {cleaned!r}"


def test_fine_grained_github_pat_redacted_via_scrub_string() -> None:
    """Fine-grained PAT is redacted by the scrub_string convenience helper (singleton path)."""
    _reset_singleton_for_tests()
    text = f"Credential value: {_FG_PAT}"
    result = scrub_string(text, source="test")
    assert _FG_PAT not in result, "Fine-grained PAT survived scrub_string"
    assert "[REDACTED:github_pat]" in result, f"Expected redaction token, got: {result!r}"


# ---------------------------------------------------------------------------
# NEW-secret-scrub-2: A2A scrubber covers credential formats
# ---------------------------------------------------------------------------
# All fake values use low-entropy repetitions (aaa…/bbb…) to avoid triggering
# detect-secrets high-entropy heuristics.


@pytest.fixture(scope="module")
def a2a_scrub():
    """Return scrub_inbound_params from a freshly imported a2a.scrubber module."""
    from lib.a2a.scrubber import scrub_inbound_params

    return scrub_inbound_params


@pytest.mark.parametrize(
    "label,secret",
    [
        # AWS Access Key ID — AKIA + 16 uppercase alphanum
        ("aws_access_key_id", "AKIA" + "A" * 16),
        # GCP service-account JSON marker
        ("gcp_sa_json", '"type": "service_account"'),
        # GCP / generic PEM private key header
        ("private_key_pem", "-----BEGIN RSA PRIVATE KEY-----"),
        # GitHub fine-grained PAT (gap NEW-secret-scrub-1 in A2A path)
        ("github_fg_pat", "github_" + "pat_" + "a" * 22 + "_" + "a" * 59),
        # GitHub classic PAT
        ("github_classic_pat", "ghp_" + "a" * 36),
        # Anthropic API key (sk-ant-… must not be swallowed by the sk- pattern)
        ("anthropic_api_key", "sk-ant-api03-" + "a" * 20),
        # OpenAI API key (sk-…)
        ("openai_api_key", "sk-proj-" + "a" * 20),
        # JWT: three base64url segments
        (
            "jwt",
            "eyJhbGciOiJIUzI1NiJ9" + "." + "a" * 20 + "." + "b" * 20,
        ),
    ],
)
def test_a2a_scrubber_redacts_credentials(a2a_scrub, label, secret) -> None:
    """A2A scrub_inbound_params MUST redact credential token formats (gap NEW-secret-scrub-2)."""
    params = {"message": {"parts": [{"text": f"context includes {secret} here"}]}}
    result = a2a_scrub(params)
    assert secret not in str(
        result
    ), f"A2A scrubber did NOT redact {label!r}. Secret still present in: {result!r}"
    assert "[REDACTED]" in str(result), f"Expected [REDACTED] marker for {label!r}, got: {result!r}"


def test_a2a_scrubber_phi_patterns_still_work(a2a_scrub) -> None:
    """Existing PHI patterns (SSN, email) must continue to fire after adding credential patterns."""
    params = {"msg": "SSN 123-45-6789 and email user@example.com"}
    result = a2a_scrub(params)
    assert "123-45-6789" not in str(
        result
    ), "SSN survived A2A scrub after credential patterns added"
    assert "user@example.com" not in str(result), "Email survived A2A scrub"
    assert str(result).count("[REDACTED]") >= 2, "Expected at least 2 redactions"


# ---------------------------------------------------------------------------
# NEW-obs-scrub-01: ScrubFilter scrubs exception/traceback text
# ---------------------------------------------------------------------------


def _make_exc_record(secret: str) -> logging.LogRecord:
    """Return a LogRecord whose exc_info contains a secret in the exception message."""
    try:
        raise ValueError(f"operation failed — credential value: {secret}")
    except ValueError:
        return logging.LogRecord(
            name="test.exc_scrub",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="downstream error",
            args=(),
            exc_info=sys.exc_info(),
        )


def test_scrub_filter_redacts_exception_text() -> None:
    """ScrubFilter MUST scrub secrets embedded in exception/traceback text (gap NEW-obs-scrub-01).

    GcpJsonFormatter calls formatException() on record.exc_info which bypassed
    the message scrubbing path.  The fix pre-populates record.exc_text with the
    scrubbed traceback and clears record.exc_info so formatters never re-render
    the raw exception.
    """
    _reset_singleton_for_tests()
    ant_key = "sk-ant-api03-" + "a" * 20
    record = _make_exc_record(ant_key)

    scrub_filter = ScrubFilter()
    result = scrub_filter.filter(record)

    assert result is True, "ScrubFilter.filter() must always return True (fail-open)"
    assert (
        record.exc_info is None
    ), "exc_info must be cleared so formatters use the pre-scrubbed exc_text"
    assert record.exc_text is not None, "exc_text must be populated after filter"
    assert ant_key not in record.exc_text, f"Secret survived in exc_text: {record.exc_text!r}"
    assert (
        "[REDACTED:anthropic_key]" in record.exc_text
    ), f"Expected redaction token in exc_text, got: {record.exc_text!r}"


def test_scrub_filter_exc_text_already_set_is_scrubbed() -> None:
    """When exc_text is already cached (prior handler ran formatException), it is still scrubbed."""
    _reset_singleton_for_tests()
    oa_key = "sk-proj-" + "b" * 20
    record = logging.LogRecord(
        name="test.exc_cached",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="error",
        args=(),
        exc_info=None,
    )
    record.exc_text = f"Traceback:\n  ValueError: key={oa_key}"

    ScrubFilter().filter(record)

    assert oa_key not in (
        record.exc_text or ""
    ), f"Pre-cached exc_text not scrubbed: {record.exc_text!r}"
    assert "[REDACTED:" in (
        record.exc_text or ""
    ), f"Expected redaction in cached exc_text, got: {record.exc_text!r}"


def test_scrub_filter_no_exc_info_unchanged() -> None:
    """Records without exc_info/exc_text still pass through normally (no regression)."""
    _reset_singleton_for_tests()
    record = logging.LogRecord(
        name="test.no_exc",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="normal log message",
        args=(),
        exc_info=None,
    )
    record.exc_text = None

    result = ScrubFilter().filter(record)

    assert result is True
    assert record.exc_text is None
    assert record.exc_info is None


def test_gcp_json_formatter_emits_scrubbed_exception_traceback() -> None:
    """_GcpJsonFormatter must emit the scrubbed traceback (exc_text) even when exc_info is cleared."""
    from lib.observability.otel_setup import _GcpJsonFormatter
    import json

    _reset_singleton_for_tests()
    ant_key = "sk-ant-api03-" + "a" * 20
    record = _make_exc_record(ant_key)

    # 1. Scrub filter processes the record
    ScrubFilter().filter(record)
    assert record.exc_info is None
    assert record.exc_text is not None

    # 2. Formatter formats the record
    formatter = _GcpJsonFormatter()
    formatted_str = formatter.format(record)

    # 3. Verify the formatted string contains the redacted traceback in the 'exc' field
    payload = json.loads(formatted_str)
    assert "exc" in payload
    assert ant_key not in payload["exc"]
    assert "[REDACTED:anthropic_key]" in payload["exc"]
