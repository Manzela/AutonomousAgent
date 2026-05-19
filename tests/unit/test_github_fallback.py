"""Unit tests for ``lib.durability.github_fallback`` (audit P1-4).

Covers the F32 secondary alert channel — verifies (a) dedupe wiring so a
multi-day outage doesn't spawn one issue per watcher tick, (b) fail-open
semantics on every error path (gh missing, gh nonzero, gh timeout,
JSON parse failure), and (c) the subprocess args we hand to ``gh``.
"""

from __future__ import annotations

import subprocess
from unittest import mock

from lib.durability import github_fallback


def _make_completed(returncode: int, stdout: str = "", stderr: str = ""):
    """Build a CompletedProcess mimicking what subprocess.run returns."""
    return subprocess.CompletedProcess(
        args=["gh", "stub"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_open_incident_issue_returns_none_when_gh_missing():
    """Fail-open: if gh isn't on PATH, return None and don't spawn anything."""
    with (
        mock.patch.object(github_fallback, "_gh_available", return_value=False),
        mock.patch.object(github_fallback, "_run_gh") as run_gh,
    ):
        result = github_fallback.open_incident_issue(card_id=42, title="t", body="b")
    assert result is None
    run_gh.assert_not_called()


def test_open_incident_issue_calls_gh_create_when_no_duplicate():
    """Happy path: dedupe search returns nothing → gh issue create runs."""
    new_url = "https://github.com/Manzela/AutonomousAgent/issues/9001"
    with (
        mock.patch.object(github_fallback, "_gh_available", return_value=True),
        mock.patch.object(github_fallback, "_run_gh") as run_gh,
    ):
        # First call: dedupe search returns empty list.
        # Second call: gh issue create returns the new URL on its last line.
        run_gh.side_effect = [
            _make_completed(0, stdout="[]\n"),
            _make_completed(0, stdout=f"Creating issue in Manzela/AutonomousAgent\n{new_url}\n"),
        ]
        result = github_fallback.open_incident_issue(
            card_id=42,
            title="[F32] Card 42 blocked >24h",
            body="msg body",
        )

    assert result == new_url
    assert run_gh.call_count == 2
    # Second invocation should be ``issue create`` with the expected flags.
    create_args = run_gh.call_args_list[1].args[0]
    assert create_args[0] == "issue"
    assert create_args[1] == "create"
    assert "--repo" in create_args
    assert "--title" in create_args
    assert "--body" in create_args
    assert "--label" in create_args
    label_idx = create_args.index("--label")
    assert "incident/auto" in create_args[label_idx + 1]


def test_open_incident_issue_skips_creation_on_duplicate():
    """If dedupe search finds an open incident issue, return its URL and DON'T create another."""
    existing_url = "https://github.com/Manzela/AutonomousAgent/issues/8675"
    dedupe_payload = f'[{{"url": "{existing_url}", "title": "[F32] Card 42 blocked"}}]'
    with (
        mock.patch.object(github_fallback, "_gh_available", return_value=True),
        mock.patch.object(github_fallback, "_run_gh") as run_gh,
    ):
        run_gh.side_effect = [_make_completed(0, stdout=dedupe_payload)]
        result = github_fallback.open_incident_issue(card_id=42, title="t", body="b")
    assert result == existing_url
    # Only the dedupe call should have happened — no second create.
    assert run_gh.call_count == 1
    assert run_gh.call_args_list[0].args[0][0] == "search"


def test_open_incident_issue_returns_none_on_create_nonzero():
    """gh issue create nonzero → return None, do not raise."""
    with (
        mock.patch.object(github_fallback, "_gh_available", return_value=True),
        mock.patch.object(github_fallback, "_run_gh") as run_gh,
    ):
        run_gh.side_effect = [
            _make_completed(0, stdout="[]\n"),
            _make_completed(1, stderr="HTTP 422: validation failed"),
        ]
        result = github_fallback.open_incident_issue(card_id=42, title="t", body="b")
    assert result is None


def test_open_incident_issue_returns_none_on_create_run_failure():
    """If _run_gh returns None (timeout/FileNotFoundError), fail-open."""
    with (
        mock.patch.object(github_fallback, "_gh_available", return_value=True),
        mock.patch.object(github_fallback, "_run_gh") as run_gh,
    ):
        run_gh.side_effect = [
            _make_completed(0, stdout="[]\n"),
            None,  # gh create timed out
        ]
        result = github_fallback.open_incident_issue(card_id=42, title="t", body="b")
    assert result is None


def test_run_gh_handles_timeout():
    """``_run_gh`` returns None on TimeoutExpired (does not propagate)."""
    with mock.patch(
        "lib.durability.github_fallback.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["gh"], timeout=30),
    ):
        result = github_fallback._run_gh(["issue", "list"])
    assert result is None


def test_run_gh_handles_filenotfound():
    """``_run_gh`` returns None on FileNotFoundError (gh not installed)."""
    with mock.patch(
        "lib.durability.github_fallback.subprocess.run",
        side_effect=FileNotFoundError("gh not on PATH"),
    ):
        result = github_fallback._run_gh(["issue", "list"])
    assert result is None


def test_find_open_incident_issue_returns_none_on_search_failure():
    """A flaky ``gh search`` must not block the create — return None so caller continues."""
    with mock.patch.object(github_fallback, "_run_gh", return_value=None):
        assert github_fallback._find_open_incident_issue(42) is None


def test_find_open_incident_issue_handles_invalid_json():
    """If gh prints non-JSON for some reason, treat as no-dedupe rather than raising."""
    with mock.patch.object(
        github_fallback, "_run_gh", return_value=_make_completed(0, stdout="not json {[}")
    ):
        assert github_fallback._find_open_incident_issue(42) is None


def test_find_open_incident_issue_query_includes_card_id_and_label():
    """Dedupe query must scope to (card id, incident/auto label, open state)."""
    with mock.patch.object(github_fallback, "_run_gh") as run_gh:
        run_gh.return_value = _make_completed(0, stdout="[]")
        github_fallback._find_open_incident_issue(42)
    args = run_gh.call_args.args[0]
    assert args[0] == "search"
    assert args[1] == "issues"
    query = args[2]
    assert "card-42" in query
    assert "incident/auto" in query
    assert "state:open" in query


def test_open_incident_issue_returns_none_when_no_url_in_stdout():
    """If gh create succeeds but stdout doesn't end with a URL, return None and log."""
    with (
        mock.patch.object(github_fallback, "_gh_available", return_value=True),
        mock.patch.object(github_fallback, "_run_gh") as run_gh,
    ):
        run_gh.side_effect = [
            _make_completed(0, stdout="[]\n"),
            _make_completed(0, stdout="something unexpected\nno url here\n"),
        ]
        result = github_fallback.open_incident_issue(card_id=42, title="t", body="b")
    assert result is None
