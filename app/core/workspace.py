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

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_GIT_TIMEOUT_S = 30


def _slug(s: str) -> str:
    """Sanitise a thread_id/node_id into ONE valid git-ref path segment.

    git refs forbid '..', a leading/trailing '.', '.lock', '/', and many chars. So map
    everything outside [A-Za-z0-9_-] to '-' (NOTE: '.' is EXCLUDED, so no '..'/'.lock'/
    leading-dot can form — a hostile node_id like '../../HEAD' collapses to 'HEAD', not a
    traversal), then collapse dash runs and trim, leaving a non-empty single segment with no
    '/' (a '/' would make a NESTED ref — ref 'a' blocks locking 'a/b'). The snapshot ref is
    therefore always exactly refs/aa-snapshots/<seg>/<seg>."""
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", s)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or "snap"


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
        snapshot_ref: Optional[str] = None,
    ) -> None:
        self.ws_dir = ws_dir
        self._parent = parent
        self._repo_dir = repo_dir
        self.base_sha = base_sha
        self.ok = ok
        self.snapshot_ref = snapshot_ref

    @classmethod
    def create(
        cls, *, repo_dir: Path, base_ref: str, thread_id: str, node_id: Optional[str] = None
    ) -> "WorkspaceSession":
        """Materialise a detached worktree at ``base_ref`` under $TMPDIR. Graceful-degrades
        (ok=False, ws_dir=None) on any git error so the spine never crashes.

        ``node_id`` (SP-11) is woven into the temp prefix for debuggability only — mkdtemp is
        ALREADY unique per call, so concurrent same-thread fan-out leaves never collide on the
        worktree PATH (and ``--detach`` means no branch ref to race). The per-node isolation
        that matters is each leaf passing its OWN node_id to :meth:`snapshot` + the SP-R6 lease."""
        try:
            base_sha = _git(["rev-parse", base_ref], cwd=repo_dir).stdout.strip()
            # mkdtemp creates the parent; point the worktree at a NON-EXISTENT subdir so
            # `git worktree add` (which refuses a pre-existing target) creates it itself.
            tag = f"{thread_id}-{node_id}-" if node_id else f"{thread_id}-"
            parent = Path(tempfile.mkdtemp(prefix=f"aa-ws-{tag}"))
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

    # ── SP-R7: lossless snapshot / rehydrate (the FS resume piece) ───────────────────
    def snapshot(self, *, thread_id: str, node_id: str) -> Optional[dict[str, Any]]:
        """Snapshot the live worktree as a DURABLE, content-addressed git object — call
        BEFORE close() (the ordering is load-bearing: the ref must outlive the worktree).

        Supports either GCS bucket storage (SPINE_SNAPSHOT_STORE="gcs") or the standard
        committed agent git branch ref store as default fallback."""
        if not self.ok or self.ws_dir is None:
            return None

        # Check SPINE_SNAPSHOT_STORE env var
        store_type = os.environ.get("SPINE_SNAPSHOT_STORE", "").lower()
        if store_type == "gcs":
            try:
                from app.adapters.gcp.gcs_snapshotter import GcsWorkspaceSnapshotter

                bucket = os.environ.get("SPINE_SNAPSHOT_BUCKET", "autonomous-agent-snapshots")
                kms_key = os.environ.get("SPINE_SNAPSHOT_KMS_KEY")
                snapshotter = GcsWorkspaceSnapshotter(bucket_name=bucket, kms_key_name=kms_key)
                result = snapshotter.snapshot(self.ws_dir, thread_id, node_id)
                if result:
                    result["base_sha"] = self.base_sha
                    self.snapshot_ref = result["ref"]
                return result
            except Exception as exc:
                logger.warning(
                    "WorkspaceSession.snapshot to GCS failed, falling back to git ref: %s", exc
                )

        # Fallback to standard git branch snapshotting
        try:
            _git(["add", "-A"], cwd=self.ws_dir)
            tree = _git(["write-tree"], cwd=self.ws_dir).stdout.strip()
            # Pin a deterministic identity inline: commit-tree (like git commit) REQUIRES a
            # committer name/email and exits 128 ("empty ident") wherever user.name/email are
            # unset — e.g. a fresh CI runner (works locally only by accident of host config).
            # These snapshot objects never reach main, so a fixed synthetic identity is correct;
            # the digest is from ls-tree, so identity/date never affect it.
            commit = _git(
                [
                    "-c",
                    "user.name=aa-snapshot",
                    "-c",
                    "user.email=aa-snapshot@autonomousagent.invalid",
                    "commit-tree",
                    tree,
                    "-p",
                    self.base_sha,
                    "-m",
                    f"aa-snapshot {thread_id}/{node_id}",
                ],
                cwd=self.ws_dir,
            ).stdout.strip()
            ref = f"refs/aa-snapshots/{_slug(thread_id)}/{_slug(node_id)}"
            # update-ref into the COMMON store (idempotent OVERWRITE per (tid,node) bounds leak).
            _git(["update-ref", ref, commit], cwd=self._repo_dir)
            manifest = _git(["ls-tree", "-r", "--full-tree", tree], cwd=self.ws_dir).stdout
            digest = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("WorkspaceSession.snapshot failed (fail-open): %s", exc)
            return None
        self.snapshot_ref = ref
        return {"kind": "branch", "ref": ref, "digest": digest}

    @classmethod
    def rehydrate(
        cls, *, repo_dir: Path, ref: str | dict[str, Any], thread_id: str
    ) -> "WorkspaceSession":
        """Materialise a FRESH worktree from a snapshot ref (resume into a new sandbox).
        Graceful-degrades (ok=False) on any git error so a bad/expired ref never crashes
        the spine. The fresh worktree has a DIFFERENT path from the killed original."""
        kind = "branch"
        ref_str = ""
        base_sha = "HEAD"

        if isinstance(ref, dict):
            kind = ref.get("kind", "branch")
            ref_str = ref.get("ref", "")
            base_sha = ref.get("base_sha", "HEAD")
        elif isinstance(ref, str):
            ref_str = ref
            kind = "gcs" if ref.startswith("gs://") else "branch"

        if kind == "gcs":
            try:
                # Extract node_id from the GCS URL to ensure correct prefix matching
                # GCS ref structure: gs://{bucket}/workspaces/{thread_id}/{node_id}.tar.gz
                node_id = "snap"
                parts = ref_str.replace("gs://", "").split("/")
                if len(parts) >= 4:
                    node_id = parts[-1].replace(".tar.gz", "")

                # 1. Create a fresh clean workspace session at base_sha
                ws = cls.create(
                    repo_dir=repo_dir, base_ref=base_sha, thread_id=thread_id, node_id=node_id
                )
                if not ws.ok or ws.ws_dir is None:
                    return ws

                # 2. Download GCS archive and extract it over the newly created ws_dir (skipping .git)
                from app.adapters.gcp.gcs_snapshotter import GcsWorkspaceSnapshotter

                bucket = os.environ.get("SPINE_SNAPSHOT_BUCKET", "autonomous-agent-snapshots")
                snapshotter = GcsWorkspaceSnapshotter(bucket_name=bucket)
                success = snapshotter.rehydrate(ws.ws_dir, thread_id, node_id)
                if not success:
                    ws.close()
                    return cls(ws_dir=None, parent=None, repo_dir=repo_dir, base_sha="", ok=False)

                ws.snapshot_ref = ref_str
                try:
                    _git(["add", "-A"], cwd=ws.ws_dir)
                except Exception:
                    pass
                return ws
            except Exception as exc:
                logger.warning("WorkspaceSession.rehydrate from GCS failed: %s", exc)
                return cls(ws_dir=None, parent=None, repo_dir=repo_dir, base_sha="", ok=False)

        # Standard git branch rehydration
        try:
            snap_sha = _git(
                ["rev-parse", "--verify", f"{ref_str}^{{commit}}"], cwd=repo_dir
            ).stdout.strip()
            parent = Path(tempfile.mkdtemp(prefix=f"aa-rehydrate-{_slug(thread_id)}-"))
            ws_dir = parent / "wt"
            _git(["worktree", "add", "--detach", str(ws_dir), snap_sha], cwd=repo_dir)
            return cls(
                ws_dir=ws_dir,
                parent=parent,
                repo_dir=repo_dir,
                base_sha=snap_sha,
                ok=True,
                snapshot_ref=ref_str,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("WorkspaceSession.rehydrate degraded (no worktree): %s", exc)
            return cls(ws_dir=None, parent=None, repo_dir=repo_dir, base_sha="", ok=False)
