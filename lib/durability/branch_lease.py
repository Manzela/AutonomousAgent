"""SP-R6 — repo/branch concurrency primitives: a per-branch advisory lease + a global
thread cap.

PRD §6 SP-R6 (L302): "concurrent goals/fan-out nodes that could touch the same files/branch
acquire an advisory lock/lease (per-path/per-branch); a global cap bounds simultaneous active
threads. Acceptance: two graphs targeting one branch → second serializes (no lost-update);
exceeding the global cap queues rather than fans out."

This module ships those two PRIMITIVES, hermetically. The INTEGRATION (acquiring the lease/cap
inside the parallel fan-out dispatcher) is deferred: SP-11 fan-out does not exist yet
(no asyncio.gather / Send dispatch in app/core/graph.py), so there is no caller to wire into.
The §12(e) end-to-end thread-isolation oracle (two REAL fan-out graphs serialize) lands with
SP-11; here the contention + cap behaviour is proven directly against the primitives.

  - ``BranchLease`` — a per-branch advisory lease via ``fcntl.flock`` (the SAME mechanism
    lib/durability/checkpoint.py uses to serialise writers; CROSS-PROCESS and cross-thread
    safe, so it gives the PRD's "no lost-update" on a shared branch). Different branches do
    not contend (distinct lock files). Graceful in-process fallback (a per-branch
    ``threading.Lock``) where ``fcntl`` is absent (Windows).
  - ``GlobalThreadCap`` — a process-global cap on simultaneously-active acquisitions via a
    ``threading.BoundedSemaphore``; ``.slot()`` BLOCKS (queues) when ``cap`` are held, so
    exceeding the cap queues rather than fans out.

DEFERRED (named, not silently dropped): the SP-11 dispatcher that consumes these; a
CROSS-PROCESS global cap (the BoundedSemaphore is process-local — correct for the
single-process spine; a file-slot scheme is the multi-process tier); the SP-R2 aggregate-SPEND
cap (a separate budget concern, blocked on SP-11); async (``asyncio.Semaphore`` /
``run_in_executor``) integration for the async fan-out (the sync primitives here are what the
deferred dispatcher wraps).
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

try:
    import fcntl as _fcntl
except ImportError:  # Windows / no-fcntl platforms — use the in-process fallback
    _fcntl = None  # type: ignore[assignment]


def _slug(branch: str) -> str:
    """A safe single lockfile name for a branch (which may contain '/' like
    'agent/<thread>/<node>'): map everything outside [A-Za-z0-9_-] to '-', collapse, trim."""
    s = re.sub(r"[^A-Za-z0-9_-]", "-", branch)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "branch"


class LeaseBusy(RuntimeError):
    """Raised by a non-blocking acquisition when the lease/slot is already held."""


class BranchLease:
    """Per-branch advisory lease. Concurrent holders of the SAME branch serialise (max one at a
    time → no lost-update); different branches proceed concurrently."""

    def __init__(self, lease_dir: Path | str) -> None:
        self._dir = Path(lease_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        # In-process fallback locks (only used when fcntl is unavailable), keyed by branch slug.
        self._inproc: dict[str, threading.Lock] = {}
        self._inproc_guard = threading.Lock()

    def _lockpath(self, branch: str) -> Path:
        return self._dir / f"{_slug(branch)}.lock"

    def _inproc_lock(self, branch: str) -> threading.Lock:
        key = _slug(branch)
        with self._inproc_guard:
            lk = self._inproc.get(key)
            if lk is None:
                lk = threading.Lock()
                self._inproc[key] = lk
            return lk

    @contextmanager
    def hold(self, branch: str, *, blocking: bool = True) -> Iterator[None]:
        """Hold the branch lease for the duration of the ``with`` block. With ``blocking=True``
        a contending acquirer WAITS until the holder releases (serialises); with
        ``blocking=False`` a contending acquirer raises :class:`LeaseBusy` immediately."""
        if _fcntl is None:  # in-process fallback (no cross-process safety, no fcntl)
            lk = self._inproc_lock(branch)
            if not lk.acquire(blocking=blocking):
                raise LeaseBusy(f"branch lease busy: {branch!r}")
            try:
                yield
            finally:
                lk.release()
            return

        fd = os.open(str(self._lockpath(branch)), os.O_CREAT | os.O_RDWR, 0o600)
        flags = _fcntl.LOCK_EX | (0 if blocking else _fcntl.LOCK_NB)
        try:
            try:
                _fcntl.flock(fd, flags)
            except BlockingIOError as exc:  # LOCK_NB on a held lock
                raise LeaseBusy(f"branch lease busy: {branch!r}") from exc
            try:
                yield
            finally:
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            os.close(fd)


class GlobalThreadCap:
    """A process-global cap on simultaneously-active acquisitions. ``.slot()`` blocks (queues)
    while ``cap`` holders are active, so exceeding the cap queues rather than fans out."""

    def __init__(self, cap: int) -> None:
        if cap < 1:
            raise ValueError(f"cap must be >= 1, got {cap}")
        self.cap = cap
        self._sem = threading.BoundedSemaphore(cap)

    @contextmanager
    def slot(self, *, blocking: bool = True, timeout: Optional[float] = None) -> Iterator[None]:
        """Occupy one of the ``cap`` slots for the ``with`` block. With ``blocking=True`` an
        over-cap acquirer WAITS for a slot (queues); with ``blocking=False`` it raises
        :class:`LeaseBusy`."""
        acquired = self._sem.acquire(blocking=blocking, timeout=timeout)
        if not acquired:
            raise LeaseBusy("global thread cap full")
        try:
            yield
        finally:
            self._sem.release()
