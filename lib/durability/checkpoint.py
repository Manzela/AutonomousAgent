"""P1-3 Per-step checkpoint writer.

Writes JSON state snapshots every N tool steps to
``/data/checkpoints/{session_id}/step-{N}.json`` so a 48h run can survive
container restart, OOM, or OS update without losing in-flight work.

Extends the Hermes ``batch_runner._save_checkpoint`` pattern
(``hermes-agent/batch_runner.py:715`` at pin ``ddb8d8f``) from batch-script
context to live agent-loop context: instead of dumping at start/end of a
batch, we dump every N steps during a session and apply rolling retention.

Configuration (``config/limits.yaml durability.checkpoint.*``):
- ``interval_steps``     — write every N steps (default 5)
- ``retention_count``    — keep the most recent N files (default 50)
- ``keep_every_nth``     — sparse-tier: keep every Nth older file (default 100)
- ``autoresume_enabled`` — resume on container start (default True)

Disk-full errors on write raise ``OSError`` whose message matches the F28
classifier in ``lib.durability.trichotomy`` (``"no space left"``).

Schema is versioned (``schema_version: 1``) so future migrations can detect skew.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import fcntl as _fcntl
except ImportError:  # Windows / environments without fcntl
    _fcntl = None  # type: ignore[assignment]


DEFAULT_ROOT = Path("/data/checkpoints")
SCHEMA_VERSION = 1
_STEP_FILENAME_RE = re.compile(r"^step-(\d+)\.json$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Checkpoint:
    """Per-session checkpoint writer.

    One instance per live session. Holds the session-scoped config (interval +
    retention parameters) so the agent loop just calls ``maybe_write(step, state)``
    at every ``post_tool_call`` boundary.
    """

    def __init__(
        self,
        session_id: str,
        taskspec_id: str,
        root_dir: Optional[Path] = None,
        interval_steps: int = 5,
        retention_count: int = 50,
        keep_every_nth: int = 100,
    ):
        self.session_id = session_id
        self.taskspec_id = taskspec_id
        self.root_dir = Path(root_dir) if root_dir is not None else DEFAULT_ROOT
        self.interval_steps = max(1, int(interval_steps))
        self.retention_count = max(1, int(retention_count))
        self.keep_every_nth = max(1, int(keep_every_nth))

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------
    @property
    def session_dir(self) -> Path:
        return self.root_dir / self.session_id

    def step_path(self, step: int) -> Path:
        return self.session_dir / f"step-{step}.json"

    # ------------------------------------------------------------------
    # Write entrypoints
    # ------------------------------------------------------------------
    def maybe_write(self, step: int, state: Optional[Dict[str, Any]] = None) -> Optional[Path]:
        """Write a checkpoint iff ``step`` is a multiple of ``interval_steps``.

        Returns the written ``Path`` on a write, or ``None`` when the cadence
        gate didn't fire. Mirrors Hermes' ``_save_checkpoint`` gating but the
        gate moves from batch-loop (every batch) to step-loop (every N steps).
        """
        if step <= 0 or step % self.interval_steps != 0:
            return None
        return self.write(step=step, state=state)

    def write(self, step: int, state: Optional[Dict[str, Any]] = None) -> Path:
        """Unconditionally write a checkpoint at ``step``.

        Used by SIGTERM handler (graceful shutdown) where we want a snapshot
        regardless of cadence, and by tests that drive the writer directly.

        Raises:
            OSError: Disk-full or other filesystem error. Caller is expected to
            classify via ``lib.durability.trichotomy.classify(err)`` — disk-full
            messages match F28 (FAIL_LOUD).
        """
        self.session_dir.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "step_index": int(step),
            "taskspec_id": self.taskspec_id,
            "timestamp": _utc_now_iso(),
            "tool_call_history": list((state or {}).get("tool_call_history", [])),
        }
        # Pass-through any extra fields the caller supplied without overwriting
        # the required schema keys.
        if state:
            for k, v in state.items():
                if k not in payload:
                    payload[k] = v

        path = self.step_path(step)
        # Single-writer invariant: in normal operation exactly one process (the
        # agent loop or the SIGTERM handler) writes checkpoints for a given
        # session. Concurrent writers are defended against at two layers:
        #
        # 1. PID-unique tmp path — prevents concurrent writers from corrupting
        #    each other's in-flight JSON via a shared .tmp file. `os.replace` is
        #    atomic on POSIX; last-replace-wins (the later state survives).
        # 2. Per-session flock on `.write.lock` — serialises writers so loser
        #    data is never silently dropped. Falls back gracefully on Windows /
        #    tmpfs mounts that return EPERM for fcntl.LOCK_EX.
        #
        # (P2-1 remediation: audit/2026-05-27-ground-truth/findings.md)
        tmp_path = path.with_name(f"{path.stem}.{os.getpid()}{path.suffix}.tmp")
        lock_path = self.session_dir / ".write.lock"
        lock_fd: int = -1
        try:
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            if _fcntl is not None:
                try:
                    _fcntl.flock(lock_fd, _fcntl.LOCK_EX)
                except OSError:
                    pass  # best-effort; os.replace atomicity is the safety net
        except OSError:
            lock_fd = -1  # lock file creation failed; proceed without locking

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    # Best-effort fsync; on tmpfs/fakefs this may be unavailable.
                    pass
            os.replace(tmp_path, path)
            # fsync the parent directory so the rename is durable across crashes.
            try:
                dirfd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(dirfd)
                finally:
                    os.close(dirfd)
            except (OSError, AttributeError):
                # Best-effort: directory fsync is unsupported on tmpfs/Windows.
                pass
        except OSError:
            # Clean up the stale temp file if it exists; re-raise so the
            # trichotomy classifier (F28 for disk-full) sees the original error.
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise
        finally:
            if lock_fd >= 0:
                if _fcntl is not None:
                    try:
                        _fcntl.flock(lock_fd, _fcntl.LOCK_UN)
                    except OSError:
                        pass
                try:
                    os.close(lock_fd)
                except OSError:
                    pass

        self._apply_retention()
        return path

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------
    def _list_step_files(self) -> List[Path]:
        if not self.session_dir.exists():
            return []
        out = []
        for p in self.session_dir.iterdir():
            m = _STEP_FILENAME_RE.match(p.name)
            if m:
                out.append(p)
        return out

    def _apply_retention(self) -> None:
        """Cap directory size: keep last ``retention_count`` files + every
        ``keep_every_nth`` older file (sparse tier).

        Example with retention_count=50, keep_every_nth=100 after 250 writes:
        kept = {201..250} (last 50) ∪ {100, 200} (sparse) = 52 files.
        """
        files = self._list_step_files()
        if not files:
            return
        indices = sorted((int(_STEP_FILENAME_RE.match(p.name).group(1)), p) for p in files)
        all_indices = [i for i, _ in indices]
        highest = all_indices[-1]
        recent_cutoff = highest - self.retention_count + 1

        keep: set[int] = set()
        for idx in all_indices:
            if idx >= recent_cutoff:
                keep.add(idx)  # recent tier
            elif idx % self.keep_every_nth == 0:
                keep.add(idx)  # sparse tier

        for idx, path in indices:
            if idx not in keep:
                try:
                    path.unlink()
                except OSError:
                    # Best-effort prune; don't fail the write because a prune failed.
                    pass
