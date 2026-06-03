"""SP-R6 — branch-lease + global-thread-cap primitives (PRD §6 SP-R6 L302).

Oracles prove the two acceptance behaviours directly against the primitives (the SP-11
fan-out dispatcher that will consume them is deferred — no caller exists yet):
  - "two graphs targeting one branch → second serializes (no lost-update)" → same-branch
    holders never overlap (max-concurrent == 1); different branches DO overlap (no false
    serialization).
  - "exceeding the global cap queues rather than fans out" → with cap N, M>N acquirers run
    at most N at a time and ALL eventually complete (the rest queued, not dropped).

NON-VACUOUS: each concurrency oracle uses a threading.Barrier so all workers hit the acquire
at the SAME instant — a no-op lease/cap would let max-concurrent exceed the bound and FAIL.
Hermetic: pure threads + fcntl.flock + a BoundedSemaphore, no docker/network.
"""

from __future__ import annotations

import threading

import pytest

from lib.durability.branch_lease import BranchLease, GlobalThreadCap, LeaseBusy, _slug


class _MaxConcurrency:
    """Thread-safe live + peak active-count tracker."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.total = 0
        self._lk = threading.Lock()

    def enter(self) -> None:
        with self._lk:
            self.active += 1
            self.total += 1
            self.peak = max(self.peak, self.active)

    def exit(self) -> None:
        with self._lk:
            self.active -= 1


def _run(n: int, body) -> None:
    """Run ``body(i)`` in n threads released simultaneously via a barrier (forces contention)."""
    barrier = threading.Barrier(n)

    def worker(i: int) -> None:
        barrier.wait()
        body(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert all(not t.is_alive() for t in threads), "a worker deadlocked"


# ── branch lease: same branch serializes (no lost-update window) ─────────────────────
def test_same_branch_serializes(tmp_path):
    lease = BranchLease(tmp_path)
    mc = _MaxConcurrency()

    def body(_i):
        with lease.hold("agent/thread-1/node-A"):
            mc.enter()
            threading.Event().wait(0.02)  # hold briefly to expose overlap
            mc.exit()

    _run(6, body)
    assert mc.total == 6  # all ran
    assert mc.peak == 1  # but NEVER two at once on the same branch (serialized)


# ── different branches do NOT serialize (no false contention) ────────────────────────
def test_different_branches_run_concurrently(tmp_path):
    lease = BranchLease(tmp_path)
    mc = _MaxConcurrency()

    def body(i):
        with lease.hold(f"agent/thread-1/node-{i}"):  # distinct branch per worker
            mc.enter()
            threading.Event().wait(0.05)
            mc.exit()

    _run(4, body)
    assert mc.peak >= 2, "distinct branches must NOT serialize"


# ── non-blocking acquire detects contention ──────────────────────────────────────────
def test_try_hold_raises_when_branch_held(tmp_path):
    lease = BranchLease(tmp_path)
    held = threading.Event()
    release = threading.Event()

    def holder():
        with lease.hold("br"):
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    assert held.wait(5)
    with pytest.raises(LeaseBusy):  # branch is held → non-blocking acquire raises
        with lease.hold("br", blocking=False):
            pass
    release.set()
    t.join(5)
    # once released, the branch can be re-acquired non-blocking
    with lease.hold("br", blocking=False):
        pass


# ── global cap: M>N acquirers run at most N at a time, and ALL complete (queued) ──────
def test_global_cap_queues_over_cap(tmp_path):
    cap = GlobalThreadCap(3)
    mc = _MaxConcurrency()

    def body(_i):
        with cap.slot():
            mc.enter()
            threading.Event().wait(0.02)
            mc.exit()

    _run(9, body)
    assert mc.total == 9  # every acquirer eventually ran (queued, not dropped)
    assert mc.peak == 3  # never more than the cap active at once


def test_cap_nonblocking_raises_when_full():
    cap = GlobalThreadCap(1)
    with cap.slot():  # the only slot is taken
        with pytest.raises(LeaseBusy):
            with cap.slot(blocking=False):
                pass
    # released → available again
    with cap.slot(blocking=False):
        pass


def test_cap_rejects_invalid_size():
    with pytest.raises(ValueError):
        GlobalThreadCap(0)


# ── in-process fallback (no fcntl) still serializes the same branch ───────────────────
def test_inprocess_fallback_serializes(tmp_path, monkeypatch):
    import lib.durability.branch_lease as bl

    monkeypatch.setattr(bl, "_fcntl", None)  # simulate a no-fcntl platform
    lease = bl.BranchLease(tmp_path)
    mc = _MaxConcurrency()

    def body(_i):
        with lease.hold("same-branch"):
            mc.enter()
            threading.Event().wait(0.02)
            mc.exit()

    _run(5, body)
    assert mc.peak == 1 and mc.total == 5  # fallback path serializes too


# ── branch slug: a '/'-bearing branch maps to ONE safe lockfile segment ──────────────
def test_branch_slug_is_filesystem_safe():
    assert "/" not in _slug("agent/thread-1/node-A")
    assert _slug("agent/thread-1/node-A") == "agent-thread-1-node-A"
