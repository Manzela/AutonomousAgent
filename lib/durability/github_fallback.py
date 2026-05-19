"""F32 secondary alert channel — open a GitHub issue when Telegram is silent.

The F32 row in ``failure_matrix.py`` is FAIL_LOUD: a card stuck in
``blocked`` past the SLA must page the operator. The primary channel is
Telegram (``lib.kanban.telegram_bridge.send_alert``). When the Telegram
publish fails (no token configured, network partition, Telegram outage,
chat id mis-set, etc.), we degrade to opening a GitHub issue tagged
``incident/auto`` so the alert is at least durable and notifies via
GitHub's own notification fanout.

Why GitHub via ``gh`` CLI rather than the REST API directly:

- ``gh`` is already authenticated in every Hermes container (the same
  PAT used by repo automation) — no extra secret to provision.
- ``gh`` handles rate limits, retries, and GitHub Enterprise rewrites
  for us. The fallback is itself best-effort; we don't want to rebuild
  that layer.
- Tests can mock ``subprocess.run`` cleanly; no httpx mocking gymnastics.

All public functions are fail-open: if ``gh`` is unavailable, returns
non-zero, or times out, we log a WARNING and return ``None`` /
``False``. The watcher sidecar must keep ticking — losing this
secondary channel is bad but not catastrophic (the card is still
visible in the Kanban UI).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

# Default repo for issue creation. Matches the github-mcp ``--repo``
# arg in ``deploy/docker-compose.yml`` so the fallback writes to the
# same repo the operator sees in their notification feed.
_DEFAULT_REPO = "Manzela/AutonomousAgent"

# Label that marks issues opened by this fallback. Used both at create
# time and at dedupe time so a single stuck card doesn't spawn an
# issue on every watcher tick (every 10 min for 24h+ = 144 issues).
_AUTO_INCIDENT_LABEL = "incident/auto"

# Timeout for every ``gh`` invocation. Generous (gh itself can take
# 5-10s on a cold start) but bounded so the watcher loop doesn't stall.
_GH_TIMEOUT_S = 30


def _gh_available() -> bool:
    """Return True iff the ``gh`` CLI is on PATH.

    Tests patch this to force the unavailable branch without monkeying
    with ``PATH``.
    """
    return shutil.which("gh") is not None


def _run_gh(args: Sequence[str]) -> Optional[subprocess.CompletedProcess]:
    """Invoke ``gh`` with the given args. Returns ``None`` on any failure.

    Never raises — caller is in a fail-open path. Captures stdout/stderr
    so the logs from a failed call include enough context to diagnose
    (gh prints its errors to stderr in a structured form).
    """
    try:
        return subprocess.run(  # noqa: S603 — args list is internally constructed
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        logger.warning("github_fallback: gh %s timed out after %ss", args[0], _GH_TIMEOUT_S)
        return None
    except FileNotFoundError:
        # _gh_available() should have caught this; defensive double-check.
        logger.warning("github_fallback: gh CLI not found on PATH")
        return None
    except Exception as exc:  # noqa: BLE001 — fallback must not raise
        logger.warning("github_fallback: gh %s unexpected failure: %s", args[0], exc)
        return None


def _find_open_incident_issue(card_id: object, repo: str = _DEFAULT_REPO) -> Optional[str]:
    """Return the URL of an existing open incident issue for ``card_id``, if any.

    Dedupe key: the literal string ``card-{card_id}`` in the title plus
    the ``incident/auto`` label plus ``state:open``. The watcher fires
    every 10 min while a card is blocked >24h; without this, a single
    multi-day outage would spawn hundreds of duplicate issues.
    """
    query = f"card-{card_id} label:{_AUTO_INCIDENT_LABEL} state:open repo:{repo} in:title"
    result = _run_gh(["search", "issues", query, "--json", "url,title", "--limit", "1"])
    if result is None or result.returncode != 0:
        # Search failed — fall through to creating a new issue. A
        # duplicate is worse than a silent drop but better than
        # blocking the alert on a flaky search.
        if result is not None and result.stderr:
            logger.debug("github_fallback: dedupe search failed: %s", result.stderr.strip())
        return None
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        logger.debug("github_fallback: dedupe search returned non-JSON: %r", result.stdout)
        return None
    if not payload:
        return None
    first = payload[0]
    url = first.get("url") if isinstance(first, dict) else None
    if url:
        logger.info(
            "github_fallback: dedupe hit — existing open incident issue for card=%s at %s",
            card_id,
            url,
        )
    return url


def open_incident_issue(
    card_id: object,
    title: str,
    body: str,
    labels: Sequence[str] = (_AUTO_INCIDENT_LABEL,),
    repo: str = _DEFAULT_REPO,
) -> Optional[str]:
    """Open (or reuse) a GitHub issue as the F32 secondary alert channel.

    Returns the issue URL on success (whether newly created or
    deduplicated to an existing open issue), or ``None`` if the
    fallback couldn't be delivered. Never raises.

    Workflow:

    1. Bail early if the ``gh`` CLI isn't available.
    2. Search for an existing open issue with the ``card-{id}`` token
       in its title and the ``incident/auto`` label. If found, return
       its URL without creating a duplicate.
    3. Otherwise, create a new issue via ``gh issue create``. The
       returned URL is parsed from gh's stdout (gh prints the URL on
       the last line of a successful create).
    """
    if not _gh_available():
        logger.warning(
            "github_fallback: gh CLI unavailable — cannot publish F32 fallback for card=%s",
            card_id,
        )
        return None

    existing = _find_open_incident_issue(card_id, repo=repo)
    if existing:
        return existing

    labels_csv = ",".join(labels)
    args = [
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--body",
        body,
        "--label",
        labels_csv,
    ]
    result = _run_gh(args)
    if result is None or result.returncode != 0:
        stderr = (result.stderr if result is not None else "") or "(no output)"
        logger.warning(
            "github_fallback: gh issue create failed for card=%s — %s",
            card_id,
            stderr.strip(),
        )
        return None

    # ``gh issue create`` prints the new issue URL as the last non-empty
    # line of stdout on success.
    out = (result.stdout or "").strip()
    url = out.splitlines()[-1].strip() if out else ""
    if url.startswith("http"):
        logger.info("github_fallback: opened incident issue for card=%s at %s", card_id, url)
        return url
    logger.warning(
        "github_fallback: gh issue create succeeded but no URL parsed (stdout=%r)",
        out,
    )
    return None


__all__ = ["open_incident_issue"]
