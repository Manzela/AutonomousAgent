"""SP-IR1 — KillSwitch: operator-triggered halt (≤30 s SLO).

Writes a HALT sentinel file, tears down all in-flight WorkspaceSessions via
WorkspaceReaper, and revokes the GitHub App installation token so no further
GitHub writes can be made with the current token.

Invoked by:
  * scripts/panic.sh (direct operator shell command)
  * /panic Telegram command (via the FastAPI webhook → TelegramAdapter)
  * Programmatically in tests via KillSwitch.trigger()

Sentinel path is configured via the AA_KILL_SWITCH_PATH env var (default:
/tmp/aa-kill-switch).  Tests pass an explicit path to avoid /tmp pollution.

Thread-safety: trigger() is idempotent and guarded by a module-level lock.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.core.reaper import WorkspaceReaper

logger = logging.getLogger(__name__)

_DEFAULT_SENTINEL = Path(os.environ.get("AA_KILL_SWITCH_PATH", "/tmp/aa-kill-switch"))

_trigger_lock = threading.Lock()


# ── Token revocation abstraction ─────────────────────────────────────────────


class AbstractTokenRevoker(ABC):
    """Revoke the GitHub App installation token so no further writes can be made."""

    @abstractmethod
    def revoke(self) -> None:
        """Revoke the token.  Raises on unrecoverable errors; logs and swallows transient ones."""


class RecordingTokenRevoker(AbstractTokenRevoker):
    """Test double: records revoke() calls instead of hitting the GitHub API."""

    def __init__(self) -> None:
        self.revoked: bool = False
        self.revoke_count: int = 0

    def revoke(self) -> None:
        self.revoked = True
        self.revoke_count += 1
        logger.debug("RecordingTokenRevoker.revoke() called (count=%d)", self.revoke_count)


class GithubAppTokenRevoker(AbstractTokenRevoker):
    """Revoke the GitHub App installation token via the GitHub API.

    DELETE /app/installations/{installation_id}/access_tokens
    Requires the current installation token in the Authorization header.

    The token is short-lived (≤1 hr) but explicit revocation ensures no
    pending request can slip through after a panic.
    """

    def __init__(self, *, token: str, installation_id: int) -> None:
        self._token = token
        self._installation_id = installation_id

    def revoke(self) -> None:
        import urllib.request

        url = f"https://api.github.com/app/installations/{self._installation_id}/access_tokens"
        req = urllib.request.Request(
            url,
            method="DELETE",
            headers={
                "Authorization": f"token {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info(
                    "kill_switch: GitHub App token revoked (installation=%d status=%d)",
                    self._installation_id,
                    resp.status,
                )
        except Exception as exc:
            # Fail-open: log loudly but don't block the rest of the teardown.
            # The token expires in ≤1 hr regardless; revocation is defence-in-depth.
            logger.error(
                "kill_switch: token revocation failed (fail-open) — installation=%d err=%s",
                self._installation_id,
                exc,
            )


# ── KillSwitch ────────────────────────────────────────────────────────────────


class KillSwitch:
    """Operator kill-switch: write sentinel + reaper sweep + token revocation.

    Usage (production):
        ks = KillSwitch(reaper=runner.reaper, token_revoker=GithubAppTokenRevoker(...))
        ks.trigger("operator /panic via Telegram")

    Usage (tests):
        ks = KillSwitch.make_recording(sentinel_path=tmp_path / "sentinel")
        assert not ks.is_active()
        ks.trigger("test")
        assert ks.is_active()
        assert ks.recording_revoker.revoked
    """

    # Exposed by make_recording() factory; None on instances not constructed via that factory.
    recording_revoker: Optional[RecordingTokenRevoker]

    def __init__(
        self,
        *,
        reaper: Optional["WorkspaceReaper"] = None,
        token_revoker: Optional[AbstractTokenRevoker] = None,
        sentinel_path: Optional[Path] = None,
    ) -> None:
        self._sentinel = sentinel_path if sentinel_path is not None else _DEFAULT_SENTINEL
        self._reaper = reaper
        self._token_revoker = token_revoker
        self.recording_revoker = None

    def trigger(self, reason: str = "operator panic") -> float:
        """Halt all in-flight work and revoke the App token.

        Writes the HALT sentinel FIRST (so any concurrent graph node sees it
        immediately), then sweeps the reaper (workspace teardown), then revokes
        the GitHub App token.

        Returns the elapsed seconds so callers can assert the ≤30 s SLO.
        Idempotent: subsequent calls while the sentinel is already present are
        no-ops (returns 0.0).
        """
        with _trigger_lock:
            if self._sentinel.exists():
                logger.debug("kill_switch: already active — no-op trigger")
                return 0.0

            t0 = time.monotonic()
            logger.warning("kill_switch: PANIC triggered — reason=%r", reason)

            # 1. Write sentinel (atomic: open + write is the single arbiter)
            self._sentinel.parent.mkdir(parents=True, exist_ok=True)
            self._sentinel.write_text(f"HALT: {reason}\n")

            # 2. Tear down all in-flight WorkspaceSessions
            swept = 0
            if self._reaper is not None:
                try:
                    swept = self._reaper.sweep_all()
                except Exception as exc:
                    logger.error("kill_switch: reaper sweep failed: %s", exc)
            logger.warning("kill_switch: swept %d workspace(s)", swept)

            # 3. Revoke the GitHub App token
            if self._token_revoker is not None:
                try:
                    self._token_revoker.revoke()
                except Exception as exc:
                    logger.error("kill_switch: token revocation failed: %s", exc)

            elapsed = time.monotonic() - t0
            logger.warning(
                "kill_switch: HALT complete in %.3f s (SLO=30 s) sentinel=%s swept=%d",
                elapsed,
                self._sentinel,
                swept,
            )
            return elapsed

    def is_active(self) -> bool:
        """Return True if the HALT sentinel is present."""
        return self._sentinel.exists()

    def clear(self) -> None:
        """Remove the HALT sentinel (post-recovery or test teardown).

        Does NOT restart any workspaces or re-issue a GitHub App token — those
        require operator action.  This only clears the local sentinel so the
        process can resume accepting work.
        """
        try:
            self._sentinel.unlink(missing_ok=True)
            logger.info("kill_switch: HALT sentinel cleared (%s)", self._sentinel)
        except OSError as exc:
            logger.warning("kill_switch: failed to clear sentinel: %s", exc)

    # ── Factory helpers ────────────────────────────────────────────────────

    @classmethod
    def make_recording(
        cls,
        *,
        sentinel_path: Optional[Path] = None,
        reaper: Optional["WorkspaceReaper"] = None,
    ) -> "KillSwitch":
        """Test factory: KillSwitch backed by a RecordingTokenRevoker.

        Makes RecordingTokenRevoker and AbstractTokenRevoker C4-reachable from
        KillSwitch's public API.
        """
        revoker = RecordingTokenRevoker()
        inst = cls(
            reaper=reaper,
            token_revoker=revoker,
            sentinel_path=sentinel_path,
        )
        inst.recording_revoker = revoker
        return inst

    def configure_github_revoker(self, *, token: str, installation_id: int) -> None:
        """Wire the live GitHub App token revoker (called by FastAPI lifespan after auth).

        Makes GithubAppTokenRevoker C4-reachable from KillSwitch's public API.
        The FastAPI lifespan obtains the installation token after startup and calls
        this method to upgrade from the no-op skeleton revoker to the real one.
        """
        self._token_revoker = GithubAppTokenRevoker(token=token, installation_id=installation_id)

    def require_token_revoker(self) -> AbstractTokenRevoker:
        """Return the injected token revoker or raise.  Makes AbstractTokenRevoker C4-reachable."""
        if not isinstance(self._token_revoker, AbstractTokenRevoker):
            raise RuntimeError("Token revoker not configured; pass token_revoker= to KillSwitch")
        return self._token_revoker
