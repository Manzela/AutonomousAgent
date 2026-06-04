"""SP-05b oracles — per-node workspace isolation (primitive level).

Hermetic: real git worktrees, no docker/LLM/network.

These oracles prove the WorkspaceSession primitive contract DIRECTLY (not
through the fan_out machinery, which SP-11 covers). Each oracle maps to one
SP-05b clause:

  W1  two independent create() calls → DISTINCT ws_dir paths AND parent dirs
  W2  turn-0 state is CLEAN and at base_sha (no dirty main-tree files inherited)
  W3  bogus base ref → ok=False, never raises, no temp-dir leak (fail-open)
  W4  close() is idempotent — safe to call twice, parent dir gone after both calls
  W5  close() after an in-body exception removes worktree AND parent dir
  W6  ws_dir is OUTSIDE the repo root (not a string-prefix but a parents check)
  W7  content isolation — a file written in ws_a is ABSENT from ws_b (C9 B1 gap)

RED/GREEN design:
  W1: if create() returned the same path twice, ws_dirs would be equal → FAIL
  W2: if create() inherited main-tree dirty index, status --uall would be non-empty → FAIL
  W3: if create() raised instead of degrading, the spine would crash → FAIL
  W4: if close() raised on second call, finally-double-close would mask the error → FAIL
  W5: if close() only removed ws_dir but not parent, $TMPDIR would grow without bound → FAIL
  W6: if ws_dir were inside the repo, agent could reach ../../../secrets/ → FAIL
  W7: if worktrees shared an index or path, the written file would appear in ws_b → FAIL
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from app.core.workspace import WorkspaceSession

_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _prune_worktrees_and_refs():
    """Belt-and-suspenders: clean up any worktree or ref leaked by a failing test."""

    def _sweep():
        subprocess.run(["git", "worktree", "prune"], cwd=str(_REPO), capture_output=True)
        out = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/aa-snapshots/"],
            cwd=str(_REPO),
            capture_output=True,
            text=True,
        ).stdout
        for ref in out.split():
            subprocess.run(["git", "update-ref", "-d", ref], cwd=str(_REPO), capture_output=True)

    _sweep()
    yield
    _sweep()


# ── W1: two independent creates → distinct ws_dir AND parent paths ───────────────────
def test_two_independent_creates_have_distinct_ws_dirs():
    """W1: path-isolation proof at the primitive level.
    Two WorkspaceSession.create() calls — even with the same thread_id — must
    materialise at DIFFERENT paths. A shared worktree means one agent's writes
    clobber the other's."""
    ws_a = WorkspaceSession.create(
        repo_dir=_REPO, base_ref="HEAD", thread_id="sp05b-w1", node_id="a"
    )
    ws_b = WorkspaceSession.create(
        repo_dir=_REPO, base_ref="HEAD", thread_id="sp05b-w1", node_id="b"
    )
    try:
        assert ws_a.ok and ws_b.ok, "both sessions must be healthy"
        assert (
            ws_a.ws_dir != ws_b.ws_dir
        ), f"ws_dirs must differ: ws_a={ws_a.ws_dir} ws_b={ws_b.ws_dir}"
        assert (
            ws_a._parent != ws_b._parent
        ), f"parent dirs must differ: {ws_a._parent} == {ws_b._parent}"
    finally:
        ws_a.close()
        ws_b.close()


# ── W2: turn-0 clean state — no dirty main-tree files inherited ─────────────────────
def test_turn_0_worktree_is_clean_and_at_base_sha():
    """W2: turn-0 guarantee — a newly created worktree has NO dirty files from the main
    checkout (even though the main worktree has many M-status audit docs), and its HEAD
    is exactly the resolved base_sha, not a stale ref."""
    ws = WorkspaceSession.create(repo_dir=_REPO, base_ref="HEAD", thread_id="sp05b-w2")
    try:
        assert ws.ok
        # --uall expands untracked dirs fully + --ignored includes ignored files:
        # if the worktree leaked the main index the dirty files would appear here.
        result = subprocess.run(
            ["git", "status", "--porcelain", "-uall", "--ignored"],
            cwd=str(ws.ws_dir),
            capture_output=True,
            text=True,
        )
        dirty_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        assert not dirty_lines, (
            f"turn-0 worktree must be clean; found {len(dirty_lines)} dirty/ignored file(s): "
            f"{dirty_lines[:5]}"
        )
        # The worktree HEAD must be at the resolved base sha
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ws.ws_dir),
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert head_sha == ws.base_sha, f"worktree HEAD {head_sha!r} != base_sha {ws.base_sha!r}"
    finally:
        ws.close()


# ── W3: bogus base ref → ok=False, no raise, no temp-dir leak ───────────────────────
def test_bogus_base_ref_fails_open_no_leak():
    """W3: a non-existent ref degrades gracefully — the spine never crashes on a bad
    base_ref, and no stale temp dir is leaked in $TMPDIR."""
    before = set(Path(tempfile.gettempdir()).glob("aa-ws-sp05b-w3-*"))
    ws = WorkspaceSession.create(
        repo_dir=_REPO,
        base_ref="refs/does-not-exist/sp05b-w3-never-exists",
        thread_id="sp05b-w3",
    )
    # Must degrade, not raise
    assert ws.ok is False
    assert ws.ws_dir is None
    assert ws._parent is None
    assert ws.base_sha == ""
    # close() on a degraded session must also never raise
    ws.close()
    # No temp dir must have been left behind
    after = set(Path(tempfile.gettempdir()).glob("aa-ws-sp05b-w3-*"))
    assert after == before, f"temp dirs leaked after degraded create: {after - before}"


# ── W4: close() is idempotent — parent dir gone after both calls ─────────────────────
def test_close_is_idempotent_and_parent_removed():
    """W4: close() must be safe to call twice. A double-close in a finally/cleanup
    chain is a common pattern. Also asserts the parent temp dir is gone after the
    first call (C9 B4 fix — a close() that only removed ws_dir but not parent would
    grow $TMPDIR without bound over a long autonomous run)."""
    ws = WorkspaceSession.create(repo_dir=_REPO, base_ref="HEAD", thread_id="sp05b-w4")
    assert ws.ok
    parent = ws._parent
    ws.close()  # first close — removes worktree + parent dir
    assert not parent.exists(), f"parent dir not removed after first close: {parent}"
    ws.close()  # second close — must be a no-op, not raise


# ── W5: close() after an in-body exception removes worktree AND parent dir ──────────
def test_close_after_exception_removes_worktree_and_parent():
    """W5: exception-safety — even if code inside the workspace raises, a finally-close
    must tear down both the worktree and its parent temp dir (C9 B4 fix)."""
    ws = WorkspaceSession.create(repo_dir=_REPO, base_ref="HEAD", thread_id="sp05b-w5")
    assert ws.ok
    ws_path = str(ws.ws_dir)
    parent = ws._parent

    with pytest.raises(RuntimeError, match="simulated agent failure"):
        try:
            raise RuntimeError("simulated agent failure")
        finally:
            ws.close()

    # Both the worktree and its parent dir must be gone
    assert not Path(ws_path).exists(), f"worktree not removed after exception: {ws_path}"
    assert not parent.exists(), f"parent dir not removed after exception+close: {parent}"

    # Must not appear in 'git worktree list'
    out = subprocess.run(
        ["git", "worktree", "list"],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
    ).stdout
    assert ws_path not in out, f"worktree still registered after exception+close: {ws_path}"


# ── W6: ws_dir is outside the repo root (parents check, not string-prefix) ──────────
def test_ws_dir_is_outside_repo_root():
    """W6: the worktree must live OUTSIDE the repo tree. Uses Path.parents to avoid
    false-pass when $TMPDIR is set to a path outside the repo (C9 B6 fix)."""
    ws = WorkspaceSession.create(repo_dir=_REPO, base_ref="HEAD", thread_id="sp05b-w6")
    try:
        assert ws.ok
        ws_resolved = ws.ws_dir.resolve()
        repo_resolved = _REPO.resolve()
        tmpdir_resolved = Path(tempfile.gettempdir()).resolve()

        # repo_resolved must NOT appear in the ws_dir's parent chain
        assert (
            repo_resolved not in ws_resolved.parents
        ), f"ws_dir is INSIDE the repo: {ws_resolved} (repo: {repo_resolved})"
        # ws_dir must be under $TMPDIR
        assert str(ws_resolved).startswith(
            str(tmpdir_resolved)
        ), f"ws_dir is not under $TMPDIR: {ws_resolved} (tmpdir: {tmpdir_resolved})"
    finally:
        ws.close()


# ── W7: content isolation — file written in ws_a is ABSENT from ws_b ────────────────
def test_content_written_in_ws_a_absent_from_ws_b():
    """W7 (C9 C1 gap): the headline SP-05b guarantee. A file created inside ws_a must
    NOT be visible at the same relative path inside ws_b. If worktrees shared an index,
    a work-tree path, or a symlink to the parent, the write would bleed across — this
    proves it does not."""
    ws_a = WorkspaceSession.create(
        repo_dir=_REPO, base_ref="HEAD", thread_id="sp05b-w7", node_id="a"
    )
    ws_b = WorkspaceSession.create(
        repo_dir=_REPO, base_ref="HEAD", thread_id="sp05b-w7", node_id="b"
    )
    try:
        assert ws_a.ok and ws_b.ok

        sentinel_relpath = Path("app") / "__sp05b_sentinel_w7.txt"
        sentinel_content = "agent-a-private-content-must-not-bleed\n"

        # Write sentinel in ws_a
        (ws_a.ws_dir / sentinel_relpath).write_text(sentinel_content)

        # The same path in ws_b must NOT exist and must NOT contain the sentinel
        sentinel_in_b = ws_b.ws_dir / sentinel_relpath
        assert (
            not sentinel_in_b.exists()
        ), f"sentinel written in ws_a leaked into ws_b at {sentinel_in_b}"
        # Also assert it's absent from the main repo tree
        sentinel_in_main = _REPO / sentinel_relpath
        assert (
            not sentinel_in_main.exists()
        ), f"sentinel written in ws_a leaked into the main repo tree at {sentinel_in_main}"
    finally:
        ws_a.close()
        ws_b.close()
