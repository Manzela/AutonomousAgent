"""SP-05 (F-1) per-node workspace — a real, ephemeral git worktree of the repo.

``WorkspaceSession`` materialises the PRD §5.1 "one git worktree per node" as literally
as is achievable hermetically on a host with no docker/gVisor: a detached ``git worktree``
checked out at the goal's base ref, under ``$TMPDIR`` (OUTSIDE the repo tree). The per-node
command (``app.core._workspace_apply``) runs INSIDE it via ``sandbox.run(workdir=...)`` and
writes files; ``.diff()`` extracts the REAL change set (``git diff --cached --name-status``)
that the execute node hands to eval_gate (SP-06) to scope-score.

HONEST BOUNDARY (so no caller over-claims): a git worktree is NOT a security boundary —
there is NO chroot/mount-ns/read-only mount here, so a child CAN still write outside the
worktree on this host. Confinement in this slice is git-diff SCOPE-SCORING at eval_gate
(detect + halt), plus the applier's ``..``/absolute refusal and the symlink-escape verdict
as defense-in-depth. Real EROFS filesystem isolation is SP-05c (docker/gVisor),
integration-tier, NOT built here.

All git calls are best-effort and FAIL-OPEN into the spine: a host with no git / not a repo
degrades to ``ok=False`` and the execute node falls back to the empty-artifacts skeleton
path — the spine never crashes on a workspace problem.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 30


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command, capturing output. Raises CalledProcessError on non-zero."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        timeout=_GIT_TIMEOUT_S,
    )


class WorkspaceSession:
    """An ephemeral per-node git worktree. Create with :meth:`create`; always
    :meth:`close` in a ``finally``."""

    def __init__(
        self,
        *,
        ws_dir: Optional[Path],
        parent: Optional[Path],
        repo_dir: Path,
        base_sha: str,
        ok: bool,
    ) -> None:
        self.ws_dir = ws_dir
        self._parent = parent
        self._repo_dir = repo_dir
        self.base_sha = base_sha
        self.ok = ok

    @classmethod
    def create(cls, *, repo_dir: Path, base_ref: str, thread_id: str) -> "WorkspaceSession":
        """Materialise a detached worktree at ``base_ref`` under $TMPDIR. Graceful-degrades
        (ok=False, ws_dir=None) on any git error so the spine never crashes."""
        try:
            base_sha = _git(["rev-parse", base_ref], cwd=repo_dir).stdout.strip()
            # mkdtemp creates the parent; point the worktree at a NON-EXISTENT subdir so
            # `git worktree add` (which refuses a pre-existing target) creates it itself.
            parent = Path(tempfile.mkdtemp(prefix=f"aa-ws-{thread_id}-"))
            ws_dir = parent / "wt"
            _git(["worktree", "add", "--detach", str(ws_dir), base_sha], cwd=repo_dir)
            return cls(ws_dir=ws_dir, parent=parent, repo_dir=repo_dir, base_sha=base_sha, ok=True)
        except (subprocess.SubprocessError, OSError) as exc:  # git absent / not a repo / timeout
            logger.warning("WorkspaceSession.create degraded (no worktree): %s", exc)
            return cls(ws_dir=None, parent=None, repo_dir=repo_dir, base_sha="", ok=False)

    def diff(self) -> tuple[dict[str, str], ...]:
        """The REAL change set in the worktree vs base, as ({'path','status'}, ...).

        ``git add -A`` stages net-new + modified + deleted so they appear in
        ``diff --cached --name-status``; paths are repo-relative POSIX. Returns () on any
        error (fail-open) so a diff failure never blocks the spine."""
        if not self.ok or self.ws_dir is None:
            return ()
        try:
            _git(["add", "-A"], cwd=self.ws_dir)
            out = _git(["diff", "--cached", "--name-status", self.base_sha], cwd=self.ws_dir).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("WorkspaceSession.diff failed (fail-open): %s", exc)
            return ()
        changed: list[dict[str, str]] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            fields = line.split("\t")
            status = fields[0][0]  # 'A'/'M'/'D'/'R'/'C' (rename/copy carry a score: R100)
            path = fields[-1]  # for rename/copy 'R100\told\tnew' the NEW path is last
            changed.append({"path": path, "status": status})
        return tuple(changed)

    def symlinks(self, changed: list[tuple[str, str]]) -> list[str]:
        """Of the changed (status, path) pairs, the ones that are symlinks in the worktree
        — a symlink inside an allowed dir can point out-of-scope, so eval_gate must see it."""
        if not self.ok or self.ws_dir is None:
            return []
        out: list[str] = []
        for _status, path in changed:
            try:
                if (self.ws_dir / path).is_symlink():
                    out.append(path)
            except OSError:
                continue
        return out

    def close(self) -> None:
        """Tear down the worktree + temp parent. FAIL-OPEN — never raises into the spine."""
        if self.ws_dir is not None:
            try:
                _git(["worktree", "remove", "--force", str(self.ws_dir)], cwd=self._repo_dir)
            except (subprocess.SubprocessError, OSError) as exc:
                logger.warning("worktree remove failed (fail-open): %s", exc)
        if self._parent is not None:
            shutil.rmtree(self._parent, ignore_errors=True)
        try:
            _git(["worktree", "prune"], cwd=self._repo_dir)
        except (subprocess.SubprocessError, OSError):
            pass
