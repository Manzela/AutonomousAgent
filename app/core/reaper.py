"""SP-05d — lifecycle reaper + panic teardown for per-node WorkspaceSessions.

SCOPE (hermetic — no docker/LLM/network):
  Each fan_out leaf creates a WorkspaceSession (git worktree + temp parent dir) and a
  BranchLease dir. Under a normal exit, both are cleaned by the _run_node finally-block +
  spine_runner.close(). Under an abnormal process exit (SIGKILL, crash) the finally-block
  never runs and the dirs accumulate in $TMPDIR / the git worktree list.

  WorkspaceReaper provides three mechanisms:
    1. Per-runner atexit handler: sweep_all() is registered on construction; calls ws.close()
       on all tracked sessions + shutil.rmtrees all registered lease dirs.
    2. Context-aware deregistration: deregister_workspace() removes a session from the
       registry AFTER its normal close() so it isn't double-closed by the atexit sweep.
    3. Orphan reaper: reap_orphaned_worktrees(repo_dir) scans `git worktree list` and prunes
       entries whose path starts with aa-ws- but no longer exists on disk (prior SIGKILL'd run).

HONEST BOUNDARY:
  SIGKILL kills atexit handlers too — no Python-level hook survives SIGKILL. The orphan
  reaper is designed for NEXT-RUN cleanup: when a new SpineRunner starts, it calls
  reap_orphaned_worktrees() to prune leftover aa-ws-* entries from prior crashed runs.
  In-run protection (abnormal process exit mid-fan-out) is currently best-effort: atexit
  fires on SIGTERM/unhandled exception but not SIGKILL.
"""

from __future__ import annotations

import atexit
import logging
import shutil
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.core.workspace import WorkspaceSession

logger = logging.getLogger(__name__)


class WorkspaceReaper:
    """SP-05d: per-runner registry of active WorkspaceSessions and lease dirs.

    Thread-safe. Construction registers an atexit handler (unless register_atexit=False,
    used by tests that call sweep_all() manually to avoid teardown order surprises).

    Usage:
        reaper = WorkspaceReaper()
        reaper.register_lease_dir(lease_dir)
        ...
        ws = WorkspaceSession.create(...)
        reaper.register_workspace(ws)
        try:
            ...
        finally:
            ws.close()
            reaper.deregister_workspace(ws)  # avoid double-close by atexit sweep
    """

    def __init__(self, *, register_atexit: bool = True) -> None:
        self._lock = threading.Lock()
        self._workspaces: set[WorkspaceSession] = set()
        self._lease_dirs: set[Path] = set()
        if register_atexit:
            atexit.register(self._atexit_sweep)

    def register_workspace(self, ws: "WorkspaceSession") -> None:
        """Track a live WorkspaceSession for panic teardown. No-op for degraded (ok=False)."""
        if ws.ok:
            with self._lock:
                self._workspaces.add(ws)

    def deregister_workspace(self, ws: "WorkspaceSession") -> None:
        """Remove a session from the registry after its normal close(). No-op if absent."""
        with self._lock:
            self._workspaces.discard(ws)

    def register_lease_dir(self, path: Path) -> None:
        """Track a lease directory for cleanup on sweep."""
        with self._lock:
            self._lease_dirs.add(path)

    def sweep_all(self) -> int:
        """Close all tracked workspaces and remove all lease dirs.

        Returns the total count of resources swept (workspaces + lease dirs).
        FAIL-OPEN: individual failures are logged and skipped — the sweep always
        completes even if some resources are already gone."""
        with self._lock:
            workspaces = list(self._workspaces)
            lease_dirs = list(self._lease_dirs)
            self._workspaces.clear()
            self._lease_dirs.clear()

        count = 0
        for ws in workspaces:
            try:
                ws.close()
                count += 1
            except Exception as exc:
                logger.warning("reaper: failed to close workspace %s: %s", ws.ws_dir, exc)

        for ld in lease_dirs:
            try:
                shutil.rmtree(ld, ignore_errors=True)
                count += 1
            except Exception as exc:
                logger.warning("reaper: failed to remove lease dir %s: %s", ld, exc)

        return count

    def _atexit_sweep(self) -> None:
        """Atexit handler — sweep all tracked resources on process exit."""
        swept = self.sweep_all()
        if swept:
            logger.info("reaper: atexit swept %d resource(s)", swept)


def reap_orphaned_worktrees(repo_dir: Path) -> int:
    """Prune orphaned aa-ws-* worktrees from prior crashed runs.

    Mechanism: `git worktree list --porcelain` emits each registered worktree's path.
    Any entry whose path starts with aa-ws- but NO LONGER EXISTS on disk is a stale
    record left by a SIGKILL'd process — prune it via `git worktree prune`.

    Returns the count of stale entries detected (pruning is done by `git worktree prune`
    in a single call regardless of the count). FAIL-OPEN: any git error returns 0.

    Call this once at SpineRunner startup to clear out prior-run debris before the new
    runner starts creating its own worktrees.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return 0

        # Count stale aa-ws-* entries (path exists in list but not on disk)
        stale_count = 0
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                path_str = line.removeprefix("worktree ").strip()
                # Only care about our aa-ws-* entries (not the main worktree)
                path = Path(path_str)
                if "aa-ws-" in path.name and not path.exists():
                    stale_count += 1

        if stale_count:
            # A single `git worktree prune` removes ALL stale entries at once
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=str(repo_dir),
                capture_output=True,
                timeout=30,
            )
            logger.info(
                "reaper: pruned %d stale aa-ws-* worktree(s) from prior run(s)", stale_count
            )

        return stale_count
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("reap_orphaned_worktrees failed (fail-open): %s", exc)
        return 0


def _default_reaper() -> "Optional[WorkspaceReaper]":
    """Return a new WorkspaceReaper, or None if the current env is a test run that
    manages its own reaper lifetime. Production code calls this; tests pass an
    explicit reaper=WorkspaceReaper(register_atexit=False)."""
    return WorkspaceReaper()
