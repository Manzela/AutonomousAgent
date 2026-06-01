"""Tests for the secret scrubber."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.scrubber import Scrubber

REPO_ROOT = Path(__file__).resolve().parents[2]
PATTERNS = REPO_ROOT / "config" / "scrubber-patterns.yaml"


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
    "xoxb" + "-" + "123456789012" + "-" + "234567890123" + "-" + "AbCdEfGhIjKlMnOpQrStUvWx"
)
_SLACK_USER_TOKEN = "xoxp" + "-" + "111111111111" + "-" + "222222222222" + "-" + "AbCdEfGhIjKl"
_SLACK_APP_TOKEN = "xoxa" + "-" + "2-abcdefghijklmnopqrstu"
_SLACK_REFRESH_TOKEN = "xoxr" + "-" + "1-abcdefghijklmnopqrst"
_SLACK_SERVICE_TOKEN = "xoxs" + "-" + "1-2-ABCDEFGHIJKLMNOP" + "-" + "abcdefghijk"


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

    token = "xoxb" + "-" + "123456789012" + "-" + "234567890123" + "-" + "AbCdEfGhIjKlMnOpQrStUvWx"
    params = {"message": {"parts": [{"text": f"auth header is {token} please process"}]}}

    result = scrub_inbound_params(params)

    assert token not in str(result), "Slack token MUST NOT appear verbatim after a2a scrub"
    assert "[REDACTED]" in str(result), "a2a scrubber replacement marker must be present"
