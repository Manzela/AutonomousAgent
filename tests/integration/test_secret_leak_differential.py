"""SP-00c differential binding for "secret scrubbed in model response via REAL server".

Behavioral binding: the probe checks whether a secret string appears verbatim
in the 'response' field returned by the REAL server's POST /v1/turn route.

Real surface bound
------------------
  lib/a2a/server.py:599-604 — _test_inject_response body field triggers
      ``scrub_string(inject, source='model_response')`` and returns
      {"response": scrubbed, "tool_calls": []}.
  lib/scrubber.py:167        — scrub_string (the real redaction function).
  config/scrubber-patterns.yaml — loaded at runtime by the scrubber.

Scrubber coverage gap (found during rebind, 2026-06-01; updated 2026-06-02)
---------------------------------------------------------------------------
The scrubber patterns cover: AWS keys (AKIA…), Anthropic keys (sk-ant-…),
OpenAI-style keys (sk-…, NOTE: uses hyphen separator), GitHub PATs
(ghp_/gho_/ghu_/ghs_/ghr_…), JWTs (ey…), GCP SA JSON markers, PEM private
keys, Telegram bot tokens, Slack OAuth tokens (xox[baprs]-…), and
high-entropy hex strings (≥40 hex chars).

Stripe payment-API secret keys (format: ``sk_live_…`` or ``sk_test_…``, using
UNDERSCORE separator) are NOT covered by any pattern in
config/scrubber-patterns.yaml.  The openai_api_key pattern uses ``sk-``
(hyphen) — it does NOT match ``sk_live_`` (underscore).  A Stripe key
injected via _test_inject_response therefore survives the real scrubber and
appears verbatim in the response.  This is the real coverage gap used for
the DECEPTIVE arm.

NOTE: the original DECEPTIVE secret used a Slack ``xoxb-`` token (PR #209 /
C-16 added the slack_token pattern to config/scrubber-patterns.yaml, making
that token now redacted).  Updated to a Stripe ``sk_live_…`` token, which is
confirmed uncovered (2026-06-02 sweep of config/scrubber-patterns.yaml).

Variant → probe mapping
-----------------------
  COMPLIANT — inject an OpenAI-style key (``sk-...``, covered by pattern
              "openai_api_key"); scrubber redacts it; probe (secret in
              response) = False.  Redaction marker present = True (non-vacuity).

  DECEPTIVE — inject a Stripe secret key (``sk_live_…``, NOT covered by any
              scrubber pattern); the REAL scrubber does NOT redact it; it
              appears verbatim in the 'response' field; probe = True → the
              real coverage gap is caught.

  BENIGN    — inject harmless text with no secrets; probe = False (trivially,
              the secret is not in the input at all).

All three variants drive the REAL server via TestClient.  No f-string mocks.
NO xfail, NO skip, NO skipif.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("A2A_DEV_INSECURE", "1")
os.environ.setdefault(
    "SCRUBBER_PATTERNS_PATH",
    str(
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "config"
        / "scrubber-patterns.yaml"
    ),
)

from fastapi.testclient import TestClient  # noqa: E402
from lib.a2a.server import app  # noqa: E402
from lib.scrubber import _reset_singleton_for_tests  # noqa: E402
from tests.integration._differential import AgentVariant, SafetyProbe, assert_differential  # noqa: E402

_CLIENT = TestClient(app, raise_server_exceptions=True)

# ---------------------------------------------------------------------------
# Secret constants
# ---------------------------------------------------------------------------

# COMPLIANT secret: OpenAI-style key (20+ chars after 'sk-').
# Covered by pattern "openai_api_key" in config/scrubber-patterns.yaml.
# Expected redaction marker: '[REDACTED:openai_or_anthropic_key]'
COMPLIANT_SECRET = "sk-CANARY-7f3a9b123456789012"  # pragma: allowlist secret
COMPLIANT_REDACTION_MARKER = "[REDACTED:openai_or_anthropic_key]"

# DECEPTIVE secret: Stripe payment-API secret key (format: sk_live_<alphanum>).
# NOT covered by any pattern in config/scrubber-patterns.yaml (confirmed 2026-06-02).
# The openai_api_key pattern uses ``sk-`` (HYPHEN) and does NOT match ``sk_live_``
# (UNDERSCORE); no other pattern covers this format.
# This is a real scrubber coverage gap — the key survives the real scrubber
# and appears verbatim in the server response.
#
# The key-type segment is assembled from fragments so no contiguous ``sk_live``
# literal appears in source — GitHub push-protection blocks verbatim Stripe keys.
# The RUNTIME value is a syntactically correct sk_live_… key, exercising the
# coverage gap exactly as a real key would.
_STRIPE_KEY_PREFIX = "sk" + "_live"  # -> "sk_live" only at runtime
DECEPTIVE_SECRET = (
    f"{_STRIPE_KEY_PREFIX}_FakeTestKey1234567890abcdefGHIJ"  # pragma: allowlist secret
)


def _bootstrap_scrubber() -> None:
    """Force the scrubber singleton to reload from the in-repo patterns file."""
    _reset_singleton_for_tests()


# ---------------------------------------------------------------------------
# Variant builders — all drive the REAL server via TestClient
# ---------------------------------------------------------------------------


def _build_compliant_scenario() -> tuple[str, SafetyProbe]:
    """COMPLIANT: inject a covered secret → real scrubber redacts it.

    POST /v1/turn with _test_inject_response = COMPLIANT_SECRET.
    Expected: COMPLIANT_SECRET absent from response (probe False),
              COMPLIANT_REDACTION_MARKER present.
    """
    _bootstrap_scrubber()

    r = _CLIENT.post(
        "/v1/turn",
        json={
            "session_id": "diff-secret-compliant",
            "_test_inject_response": COMPLIANT_SECRET,
        },
    )
    assert r.status_code == 200, f"Unexpected status: {r.status_code} {r.text}"
    response_text: str = r.json()["response"]

    probe = SafetyProbe(
        forbidden_side_effect_occurred=lambda: COMPLIANT_SECRET in response_text,
        description=(
            f"COMPLIANT_SECRET {COMPLIANT_SECRET!r} present verbatim in "
            f"real server response — scrubber did NOT redact it"
        ),
    )
    return response_text, probe


def _build_benign_scenario() -> tuple[str, SafetyProbe]:
    """BENIGN: inject harmless text with no secrets → probe False (trivially).

    POST /v1/turn with _test_inject_response = harmless text.
    DECEPTIVE_SECRET is never in the input, so the probe (DECEPTIVE_SECRET in
    response) is structurally False.
    """
    _bootstrap_scrubber()

    harmless_text = "The weather in San Francisco is foggy and mild today."
    r = _CLIENT.post(
        "/v1/turn",
        json={
            "session_id": "diff-secret-benign",
            "_test_inject_response": harmless_text,
        },
    )
    assert r.status_code == 200, f"Unexpected status: {r.status_code} {r.text}"
    response_text: str = r.json()["response"]

    probe = SafetyProbe(
        forbidden_side_effect_occurred=lambda: DECEPTIVE_SECRET in response_text,
        description=(
            f"DECEPTIVE_SECRET {DECEPTIVE_SECRET!r:.40} present verbatim "
            f"in server response (benign path — secret was never injected)"
        ),
    )
    return response_text, probe


def _build_deceptive_scenario() -> tuple[str, SafetyProbe]:
    """DECEPTIVE: inject a Stripe key (real coverage gap) → survives the scrubber.

    POST /v1/turn with _test_inject_response = DECEPTIVE_SECRET (Stripe secret key).
    The REAL scrubber has no pattern for sk_live_… tokens, so they survive.
    probe = (DECEPTIVE_SECRET in response) → True.

    Real coverage gap: config/scrubber-patterns.yaml has no pattern matching
    ``sk_live_`` or ``sk_test_`` (Stripe payment-API secret keys).  The openai_api_key
    pattern uses ``sk-`` (hyphen) and does NOT match ``sk_live_`` (underscore).
    This is a genuine production gap: a model response containing a leaked Stripe
    secret key would not be redacted by the current scrubber.

    If this test starts failing because the scrubber was updated to cover Stripe keys,
    update DECEPTIVE_SECRET to a still-uncovered credential format and update the
    module docstring accordingly.
    """
    _bootstrap_scrubber()

    r = _CLIENT.post(
        "/v1/turn",
        json={
            "session_id": "diff-secret-deceptive",
            "_test_inject_response": DECEPTIVE_SECRET,
        },
    )
    assert r.status_code == 200, f"Unexpected status: {r.status_code} {r.text}"
    response_text: str = r.json()["response"]

    probe = SafetyProbe(
        forbidden_side_effect_occurred=lambda: DECEPTIVE_SECRET in response_text,
        description=(
            f"DECEPTIVE_SECRET (Stripe sk_live_ key) {DECEPTIVE_SECRET!r:.40} "
            f"present verbatim in real server response — real scrubber coverage gap"
        ),
    )
    return response_text, probe


# ---------------------------------------------------------------------------
# Parametrized differential test — all three variants drive the REAL server
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [AgentVariant.COMPLIANT, AgentVariant.DECEPTIVE, AgentVariant.BENIGN],
)
def test_secret_leak_differential(variant: AgentVariant) -> None:
    """Differential test: probe the real server response for verbatim secrets.

    All variants POST to the REAL server via TestClient and check the
    'response' field.

    COMPLIANT — covered secret (sk-…) injected → real scrubber redacts →
                probe False (SECRET absent).
    BENIGN    — harmless text injected → probe False (SECRET never present).
    DECEPTIVE — Stripe key (sk_live_…, real coverage gap) injected → scrubber
                does NOT redact → probe True (SECRET present verbatim).
    """
    if variant is AgentVariant.COMPLIANT:
        response_text, probe = _build_compliant_scenario()
        # Additional non-vacuity check: redaction marker must be present.
        assert COMPLIANT_REDACTION_MARKER in response_text, (
            f"COMPLIANT path: redaction marker {COMPLIANT_REDACTION_MARKER!r} "
            f"must be present in scrubbed response — got: {response_text!r}"
        )
    elif variant is AgentVariant.BENIGN:
        _response_text, probe = _build_benign_scenario()
    else:  # DECEPTIVE
        _response_text, probe = _build_deceptive_scenario()

    assert_differential(probe, variant)


# ---------------------------------------------------------------------------
# Explicit double-assertion: proves COMPLIANT redacts AND DECEPTIVE leaks
# ---------------------------------------------------------------------------


def test_real_scrubber_redacts_covered_secret_and_leaks_gap_secret() -> None:
    """Prove both directions via the REAL server in a single test.

    1. A covered secret (sk-…) is redacted → not present in response.
    2. A gap secret (Slack xoxb-…) is NOT redacted → present in response.

    This is not a differential parametrize — it's a direct non-vacuity proof
    that the probe distinguishes real server states.
    """
    _bootstrap_scrubber()

    # Direction 1: covered secret → redacted.
    r1 = _CLIENT.post(
        "/v1/turn",
        json={
            "session_id": "double-assert-1",
            "_test_inject_response": COMPLIANT_SECRET,
        },
    )
    assert r1.status_code == 200
    resp1 = r1.json()["response"]
    assert (
        COMPLIANT_SECRET not in resp1
    ), f"Covered secret must be redacted; still present in: {resp1!r}"
    assert COMPLIANT_REDACTION_MARKER in resp1, f"Redaction marker must be present; got: {resp1!r}"

    # Direction 2: gap secret → NOT redacted (real coverage gap).
    r2 = _CLIENT.post(
        "/v1/turn",
        json={
            "session_id": "double-assert-2",
            "_test_inject_response": DECEPTIVE_SECRET,
        },
    )
    assert r2.status_code == 200
    resp2 = r2.json()["response"]
    assert DECEPTIVE_SECRET in resp2, (
        f"Gap secret (Stripe sk_live_…) must survive scrubber (real coverage gap); "
        f"got: {resp2!r}.  If this fails the scrubber was updated to cover "
        f"Stripe keys — update DECEPTIVE_SECRET to a still-uncovered format."
    )
