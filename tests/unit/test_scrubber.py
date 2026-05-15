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
