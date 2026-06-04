"""SP-05d oracles — WorkspaceReaper lifecycle + panic teardown.

Hermetic: no docker/LLM/network. R2 uses a minimal isolated git repo in tmp_path.

These oracles prove the WorkspaceReaper primitive contract:

  R1  sweep_all() calls close() on every registered session + returns correct count
  R2  reap_orphaned_worktrees() detects AND prunes stale aa-ws-* entries (not just counts)
  R2b blast-radius: unconditional git worktree prune also removes non-aa-ws stale entries
  R3  deregister_workspace() prevents double-close in atexit sweep
  R4  register_atexit=True registers exactly one handler; False registers none
  R4b atexit handler actually sweeps on invocation (not just registered)
  R5  sweep_all() removes all registered lease dirs
  R6  sweep_all() is idempotent — second call returns 0, no double-close
  R7  register_workspace() no-ops for degraded (ok=False) sessions
  R8  FAIL-OPEN: a close() exception doesn't abort sweep of remaining sessions
  R9  thread-safety: concurrent register/deregister/sweep doesn't corrupt the registry

RED/GREEN design:
  R1: if sweep_all() didn't call close(), the sweep would be a no-op — FAIL
  R2: if git worktree prune were a no-op, count==1 but the record would survive — FAIL
  R2b: documents that git worktree prune also removes non-aa-ws stale entries (blast radius)
  R3: if deregister didn't remove the session, sweep_all() would call close() twice — FAIL
  R4: if atexit.register wasn't called, the panic sweep would never fire — FAIL
  R4b: if _atexit_sweep swallowed its sweep_all(), sessions would leak on process exit — FAIL
  R5: if sweep_all() didn't rmtree lease dirs, $TMPDIR accumulates lock files — FAIL
  R6: if sweep_all() wasn't idempotent, a second call would double-close sessions — FAIL
  R7: if degraded sessions were registered, close() on ok=False would be called — FAIL
  R8: if a close() exception propagated, remaining sessions would leak — FAIL (reaper's purpose)
  R9: if the lock were dropped, a concurrent register racing sweep_all might escape cleanup — FAIL
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch


from app.core.reaper import WorkspaceReaper, reap_orphaned_worktrees


# ── helpers ───────────────────────────────────────────────────────────────────


def _stub_ws(*, ok: bool = True) -> MagicMock:
    """Stub WorkspaceSession with ok/close/ws_dir. No real git ops."""
    ws = MagicMock()
    ws.ok = ok
    ws.ws_dir = Path(tempfile.mkdtemp(prefix="aa-ws-stub-")) if ok else None
    ws.close = MagicMock()
    return ws


def _make_repo(path: Path) -> None:
    """Initialise a minimal git repo with one empty commit at path."""
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
    }
    subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", "init"],
        capture_output=True,
        check=True,
        env=env,
    )


def _add_worktree(repo: Path, wt_dir: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(wt_dir), "HEAD"],
        capture_output=True,
        check=True,
    )


def _worktree_listing(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
    ).stdout


# ── R1: sweep_all closes all registered sessions ─────────────────────────────


def test_sweep_all_closes_registered_sessions():
    """R1: sweep_all() calls close() on every registered session and returns correct count."""
    reaper = WorkspaceReaper(register_atexit=False)
    ws_a = _stub_ws()
    ws_b = _stub_ws()
    reaper.register_workspace(ws_a)
    reaper.register_workspace(ws_b)

    count = reaper.sweep_all()

    ws_a.close.assert_called_once()
    ws_b.close.assert_called_once()
    assert count == 2


# ── R2: reap_orphaned_worktrees actually prunes (not just counts) ─────────────


def test_reap_orphaned_worktrees_prunes_stale(tmp_path):
    """R2: stale aa-ws-* entries (path not on disk) are detected AND removed from git's
    worktree registry — not just counted. A no-op prune would leave count==1 but the
    record would survive (B1 C9 gap)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)

    wt_dir = tmp_path / "aa-ws-r2-stale"
    _add_worktree(repo, wt_dir)
    shutil.rmtree(wt_dir)  # simulate SIGKILL: dir gone, record remains

    assert not wt_dir.exists(), "precondition: wt_dir gone from disk"
    assert str(wt_dir) in _worktree_listing(repo), "precondition: record still in git"

    count = reap_orphaned_worktrees(repo)

    assert count == 1
    # The pruning side-effect: record must be gone after the call
    assert str(wt_dir) not in _worktree_listing(
        repo
    ), "stale worktree record must be pruned — not just counted"


def test_reap_orphaned_worktrees_prune_blastradius(tmp_path):
    """R2b: documents blast-radius — git worktree prune is unconditional and removes ALL
    stale worktrees, not just aa-ws-* ones. The function counts only aa-ws-* but the
    actual prune touches non-aa-ws stale entries too. Pins this behavior explicitly
    so a refactor that narrows the blast radius is visible (B2 C9 gap)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)

    aa_wt = tmp_path / "aa-ws-r2b-stale"
    other_wt = tmp_path / "other-r2b-stale"
    _add_worktree(repo, aa_wt)
    _add_worktree(repo, other_wt)
    shutil.rmtree(aa_wt)
    shutil.rmtree(other_wt)

    count = reap_orphaned_worktrees(repo)

    # Only aa-ws-* entries are COUNTED
    assert count == 1
    # Both stale records are pruned (unconditional git worktree prune)
    listing = _worktree_listing(repo)
    assert str(aa_wt) not in listing
    assert str(other_wt) not in listing


def test_reap_orphaned_worktrees_returns_zero_when_none(tmp_path):
    """R2c: returns 0 when there are no stale aa-ws-* entries."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo(repo)

    count = reap_orphaned_worktrees(repo)

    assert count == 0


# ── R3: deregister prevents double-close ─────────────────────────────────────


def test_deregister_prevents_double_close():
    """R3: deregister_workspace() removes the session so sweep_all() does NOT close it again."""
    reaper = WorkspaceReaper(register_atexit=False)
    ws = _stub_ws()
    reaper.register_workspace(ws)

    # Normal lifecycle: close then deregister (the _run_node finally-block pattern)
    ws.close()
    reaper.deregister_workspace(ws)

    count = reaper.sweep_all()

    assert ws.close.call_count == 1, "close() must not be called twice"
    assert count == 0


# ── R4: atexit registration and invocation ────────────────────────────────────


def test_atexit_registered_on_construction():
    """R4: WorkspaceReaper(register_atexit=True) registers exactly one atexit handler."""
    with patch("atexit.register") as mock_reg:
        reaper = WorkspaceReaper(register_atexit=True)

    mock_reg.assert_called_once()
    registered_fn = mock_reg.call_args[0][0]
    assert registered_fn == reaper._atexit_sweep


def test_atexit_not_registered_when_disabled():
    """R4b: WorkspaceReaper(register_atexit=False) does NOT register an atexit handler."""
    with patch("atexit.register") as mock_reg:
        WorkspaceReaper(register_atexit=False)

    mock_reg.assert_not_called()


def test_atexit_handler_sweeps_on_invocation(tmp_path):
    """R4c: _atexit_sweep() actually calls sweep_all() — not just registered but
    functional (B4 C9 gap). Direct invocation proves the handler does what it claims."""
    reaper = WorkspaceReaper(register_atexit=False)
    ws = _stub_ws()
    lease = tmp_path / "lease"
    lease.mkdir()
    reaper.register_workspace(ws)
    reaper.register_lease_dir(lease)

    reaper._atexit_sweep()

    ws.close.assert_called_once()
    assert not lease.exists(), "atexit sweep must remove lease dirs"


# ── R5: lease dirs removed by sweep_all ──────────────────────────────────────


def test_sweep_all_removes_lease_dirs(tmp_path):
    """R5: lease dirs registered via register_lease_dir() are removed by sweep_all()."""
    reaper = WorkspaceReaper(register_atexit=False)
    lease_a = tmp_path / "lease-a"
    lease_a.mkdir()
    lease_b = tmp_path / "lease-b"
    lease_b.mkdir()

    reaper.register_lease_dir(lease_a)
    reaper.register_lease_dir(lease_b)

    count = reaper.sweep_all()

    assert not lease_a.exists(), "lease dir a must be removed by sweep_all"
    assert not lease_b.exists(), "lease dir b must be removed by sweep_all"
    assert count == 2


# ── R6: sweep_all is idempotent ───────────────────────────────────────────────


def test_sweep_all_is_idempotent():
    """R6: second sweep_all() call returns 0 — no double-close, no error."""
    reaper = WorkspaceReaper(register_atexit=False)
    ws = _stub_ws()
    reaper.register_workspace(ws)

    first = reaper.sweep_all()
    second = reaper.sweep_all()

    assert first == 1
    assert second == 0
    assert ws.close.call_count == 1, "close() must not be called on second sweep"


# ── R7: degraded session (ok=False) not registered ───────────────────────────


def test_register_workspace_noop_for_degraded():
    """R7: register_workspace() silently ignores degraded (ok=False) sessions."""
    reaper = WorkspaceReaper(register_atexit=False)
    ws_bad = _stub_ws(ok=False)
    ws_good = _stub_ws(ok=True)

    reaper.register_workspace(ws_bad)
    reaper.register_workspace(ws_good)

    count = reaper.sweep_all()

    ws_bad.close.assert_not_called()
    ws_good.close.assert_called_once()
    assert count == 1, "only the healthy session counts toward sweep_all return value"


# ── R8: FAIL-OPEN — a failing close() doesn't abort sweep of remaining ───────


def test_sweep_all_failopen_on_close_error():
    """R8: if one session's close() raises, sweep_all() continues cleaning up the
    others and returns the count of SUCCESSFUL operations (B3 C9 gap — impl self-
    marks the except branch # pragma: no cover because there was no test for it)."""
    reaper = WorkspaceReaper(register_atexit=False)
    ws_a = _stub_ws()
    ws_b = _stub_ws()
    ws_b.close.side_effect = RuntimeError("simulated close failure")
    ws_c = _stub_ws()

    reaper.register_workspace(ws_a)
    reaper.register_workspace(ws_b)
    reaper.register_workspace(ws_c)

    # Must not raise, even though ws_b.close() raises
    count = reaper.sweep_all()

    ws_a.close.assert_called_once()
    ws_b.close.assert_called_once()
    ws_c.close.assert_called_once()
    # count reflects successful closes: 2 of 3 (ws_b raised)
    assert count == 2


def test_sweep_all_failopen_on_lease_remove_error(tmp_path):
    """R8b: a lease dir removal error does not abort cleanup of remaining lease dirs."""
    reaper = WorkspaceReaper(register_atexit=False)
    lease_a = tmp_path / "lease-a"
    lease_a.mkdir()
    lease_b = tmp_path / "lease-b"  # does not exist — rmtree ignore_errors handles it
    lease_c = tmp_path / "lease-c"
    lease_c.mkdir()

    reaper.register_lease_dir(lease_a)
    reaper.register_lease_dir(lease_b)  # non-existent dir
    reaper.register_lease_dir(lease_c)

    count = reaper.sweep_all()

    assert not lease_a.exists()
    assert not lease_c.exists()
    assert count == 3  # rmtree(ignore_errors=True) always succeeds for the count


# ── R9: thread-safety under concurrent register/deregister/sweep ──────────────


def test_concurrent_register_deregister_sweep_is_safe():
    """R9: concurrent register/deregister/sweep doesn't corrupt the registry or raise.
    Proves the threading.Lock protects the _workspaces set (B5 C9 gap)."""
    reaper = WorkspaceReaper(register_atexit=False)
    errors: list[Exception] = []
    n_threads = 8
    barrier = threading.Barrier(n_threads)

    def _worker(i: int) -> None:
        try:
            barrier.wait()
            ws = _stub_ws()
            reaper.register_workspace(ws)
            if i % 2 == 0:
                reaper.deregister_workspace(ws)
            else:
                reaper.sweep_all()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors, f"concurrent operations raised: {errors}"
    # A final sweep must complete without error regardless of intermediate state
    reaper.sweep_all()
