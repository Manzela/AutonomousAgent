"""Regression tests: outbound alert payloads must be scrubbed.

Audit P2 #34: a Kanban card title or description may carry a secret pattern
(the user pasted an API key, a stack trace embedded an Authorization header,
etc.). Three outbound paths interpolate raw card payload into a message that
is sent to Telegram or GitHub Issues — both of which retain the message
indefinitely. The credential scrubber (lib/scrubber.py) is already wired into
LiteLLM (lib/scrubber_callback.py) for prompts/responses, but NOT into these
alert sites.

These tests assert the secret pattern never escapes the process boundary at
the three call sites:
    1. lib.durability.escalation.emit_escalation  (F32 Telegram escalation)
    2. lib.durability.github_fallback.open_incident_issue (GH issue fallback)
    3. lib.kanban.telegram_bridge.send_alert (general Kanban alert path)

All three tests use the AWS access key pattern (severity=critical) so the
exact replacement token is `[REDACTED:aws_access_key_id]` and the original
string `AKIA...` MUST be absent from the sent payload. We use a synthetic
fake key (`AKIA0000000000000000`) that matches the regex shape but contains
no real credential.

HTTP / subprocess clients are mocked — we never call out to Telegram or gh.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PATTERNS = REPO_ROOT / "config" / "scrubber-patterns.yaml"

# A synthetic AWS access key id — shape-matches lib/scrubber.py's
# `aws_access_key_id` pattern (\bAKIA[0-9A-Z]{16}\b) but is not a real
# credential. Used so the exact pre-scrub substring is well-defined.
FAKE_SECRET = "AKIA0000000000000000"
SECRET_PREFIX = "AKIA"
REDACTED_TOKEN = "[REDACTED:aws_access_key_id]"


@pytest.fixture(autouse=True)
def _point_scrubber_at_real_patterns(monkeypatch):
    """Force the lazy-loaded scrub_string singleton to use the in-repo
    patterns YAML rather than the container path. Also reset the cached
    singleton between tests so monkeypatched env vars take effect.
    """
    monkeypatch.setenv("SCRUBBER_PATTERNS_PATH", str(PATTERNS))
    import lib.scrubber as scrubber_mod

    scrubber_mod._reset_singleton_for_tests()
    yield
    scrubber_mod._reset_singleton_for_tests()


# ---------------------------------------------------------------------------
# Site 1 — lib.kanban.telegram_bridge.send_alert
# ---------------------------------------------------------------------------


def test_send_alert_scrubs_secret_before_post(monkeypatch):
    """send_alert must redact secrets in `msg` before posting to Telegram."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_HOME_CHAT_ID", "999")

    from lib.kanban import telegram_bridge

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_client.__enter__.return_value.post.return_value = mock_response

    with patch.object(telegram_bridge.httpx, "Client", return_value=mock_client):
        result = telegram_bridge.send_alert(
            card_id=42,
            msg=f"Card 42 failed: please rotate {FAKE_SECRET} immediately",
        )

    assert result is True, "send_alert should succeed against the mocked client"
    post_kwargs = mock_client.__enter__.return_value.post.call_args.kwargs
    sent_body = post_kwargs["json"]["text"]
    assert SECRET_PREFIX + "0" not in sent_body, f"raw secret leaked: {sent_body!r}"
    assert FAKE_SECRET not in sent_body, f"raw secret leaked: {sent_body!r}"
    assert REDACTED_TOKEN in sent_body, f"redaction token missing: {sent_body!r}"


# ---------------------------------------------------------------------------
# Site 2 — lib.durability.escalation.emit_escalation
# ---------------------------------------------------------------------------


def test_emit_escalation_scrubs_title_before_send(monkeypatch):
    """A blocked card whose title carries a secret must be redacted in the
    escalation message that emit_escalation sends to Telegram."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_HOME_CHAT_ID", "999")

    from lib.durability import escalation
    from lib.kanban import telegram_bridge

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_client.__enter__.return_value.post.return_value = mock_response

    title_with_secret = f"Deploy stuck: rotate {FAKE_SECRET} for prod"

    with patch.object(telegram_bridge.httpx, "Client", return_value=mock_client):
        escalation.emit_escalation(card_id=7, title=title_with_secret, age_h=25.5)

    post_calls = mock_client.__enter__.return_value.post.call_args_list
    assert post_calls, "expected at least one Telegram POST"
    sent_body = post_calls[0].kwargs["json"]["text"]
    assert FAKE_SECRET not in sent_body, f"raw secret leaked to Telegram: {sent_body!r}"
    assert REDACTED_TOKEN in sent_body, f"redaction token missing: {sent_body!r}"


# ---------------------------------------------------------------------------
# Site 3 — lib.durability.github_fallback.open_incident_issue
# ---------------------------------------------------------------------------


def test_open_incident_issue_scrubs_title_and_body_before_gh_create(monkeypatch):
    """open_incident_issue must redact secrets in BOTH `title` and `body`
    before invoking `gh issue create` (which would otherwise persist the
    secret in the GitHub Issues database)."""
    from lib.durability import github_fallback

    title_with_secret = f"Card stuck >24h ({FAKE_SECRET})"
    body_with_secret = (
        f"Card payload included header `Authorization: AWS4-HMAC-SHA256 "
        f"Credential={FAKE_SECRET}/...`"
    )

    # Dedupe search returns 0 results (force the create path).
    dedupe_result = MagicMock(returncode=0, stdout="[]", stderr="")
    create_result = MagicMock(
        returncode=0,
        stdout="https://github.com/Manzela/AutonomousAgent/issues/9999\n",
        stderr="",
    )

    with (
        patch.object(github_fallback, "_gh_available", return_value=True),
        patch.object(
            github_fallback.subprocess, "run", side_effect=[dedupe_result, create_result]
        ) as mock_run,
    ):
        url = github_fallback.open_incident_issue(
            card_id=7, title=title_with_secret, body=body_with_secret
        )

    assert url == "https://github.com/Manzela/AutonomousAgent/issues/9999"
    assert mock_run.call_count == 2, "expected dedupe search + create"

    # Inspect the create-issue invocation specifically.
    create_args = mock_run.call_args_list[1].args[0]
    assert (
        create_args[1] == "issue" and create_args[2] == "create"
    ), f"second gh call should be `issue create`: {create_args!r}"
    # The argv joined together must NOT contain the raw secret anywhere
    # (title arg, body arg, label list, etc.).
    joined = " ".join(create_args)
    assert FAKE_SECRET not in joined, f"raw secret leaked to gh argv: {joined!r}"
    assert REDACTED_TOKEN in joined, f"redaction token missing from gh argv: {joined!r}"
