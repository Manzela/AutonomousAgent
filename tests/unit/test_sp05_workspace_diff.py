"""SP-05 (F-1) — the execute node drives a REAL per-node git worktree, runs the
deterministic applier INSIDE it, and extracts a REAL git diff that eval_gate (SP-06)
scope-scores. This is the first time eval_gate ever sees a non-empty diff.

Scope (HONEST — see the PR body's 'claims that would be false'): confinement here is
git-diff SCOPE-SCORING (detect + halt), NOT kernel FS-confinement (no chroot/mount-ns;
real EROFS is SP-05c/docker, integration-tier). The per-node 'agent work' is a
deterministic in-repo applier, NOT the external Hermes binary (which host-execs +
raises). Egress is a default-deny DECISION with an EMPTY live allowlist.

NON-VACUOUS: on the pre-F-1 base, execute injects a getpid probe → ExecutionResult.artifacts
is always () → changed_paths is empty → eval_gate trivially PASSES and never blocks. These
oracles need the real worktree+diff to go green.

macOS/no-docker: the real LocalSubprocessSandbox cannot spawn (RLIMIT_AS unsettable), so the
worktree oracles drive a real-child double WITHOUT the rlimit preexec, exactly as slice-1
(test_sp05_executor_sandbox.py) does. Live EROFS/egress enforcement is integration-tier.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import pytest

from app.core.graph import (
    _build_nodes,
    _default_capability,
    _node_output_relpath,
    _route_after_eval_gate,
)
from app.core.orchestrator import (
    _EGRESS_ALLOWED_PHASES,
    _egress_for_phase,
    _safe_sandbox_workdir,
)
from app.core.sandbox import AbstractSandbox, SandboxResult
from app.core.workspace import WorkspaceSession

_CFG = {"configurable": {"thread_id": "t-sp05"}}


@pytest.fixture(autouse=True)
def _prune_worktrees():
    """Belt-and-suspenders: prune any worktree a crashed test left registered, so the
    cleanup oracle can't be polluted by a sibling's leak."""
    repo = Path(__file__).resolve().parents[2]
    yield
    subprocess.run(["git", "worktree", "prune"], cwd=str(repo), capture_output=True)


# ── doubles (both implement AbstractSandbox — the ABC is NOT collapsed) ──────────────
class _Recorder(AbstractSandbox):
    """Records run() kwargs; canned result, no subprocess."""

    def __init__(self, *, returncode: int = 0) -> None:
        self.calls: list[dict] = []
        self._rc = returncode

    async def run(
        self, *, cmd, workdir=None, env=None, network_allowed=False, **kw
    ) -> SandboxResult:
        self.calls.append(
            {"cmd": cmd, "workdir": workdir, "env": env, "network_allowed": network_allowed}
        )
        return SandboxResult(
            returncode=self._rc, stdout="", stderr="", duration_s=0.0, killed=False
        )


class _RealChild(AbstractSandbox):
    """Spawns a REAL child with cwd=workdir, WITHOUT the rlimit preexec (macOS-safe)."""

    async def run(
        self,
        *,
        cmd,
        workdir: Optional[Path] = None,
        env=None,
        stdin=None,
        timeout_s: float = 60.0,
        cpu_seconds: int = 30,
        memory_mb: int = 512,
        max_files: int = 256,
        network_allowed: bool = False,
    ) -> SandboxResult:
        if network_allowed:
            raise PermissionError("test sandbox enforces no network")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workdir) if workdir else None,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        rc = proc.returncode if proc.returncode is not None else -1
        return SandboxResult(
            returncode=rc, stdout=out.decode(), stderr=err.decode(), duration_s=0.0, killed=False
        )


def _plan(allowed: list[str], phase: str = "draft") -> dict:
    return {
        "nodes": [
            {
                "id": "n0",
                "phase": phase,
                "summary": "s",
                "depends_on": [],
                "acceptance_ref": "0",
                "allowed_paths": allowed,
            }
        ],
        "edges": [],
    }


def _state(plan: dict, **extra) -> dict:
    return {"thread_id": "t-sp05", "goal": "g", "plan": plan, "base_ref": "HEAD", **extra}


async def _run_execute(sandbox, plan: dict):
    nodes = _build_nodes(_default_capability(), sandbox=sandbox)
    return await nodes["execute"](_state(plan), _CFG)


async def _run_eval_gate(plan: dict, changed_paths: list, symlink_paths=None):
    nodes = _build_nodes(_default_capability(), sandbox=None)
    st = _state(plan, changed_paths=changed_paths, symlink_paths=symlink_paths or [])
    delta = await nodes["eval_gate"](st, _CFG)
    return delta, _route_after_eval_gate({**st, **delta})


# ── in-scope PASS: full execute → real worktree diff → eval_gate passes → ship ──────
async def test_in_scope_real_diff_passes_and_routes_to_ship():
    plan = _plan(["app/sp05_probe.py"])
    delta = await _run_execute(_RealChild(), plan)
    # execute produced a REAL git diff (status A on the written file) — NOT empty.
    assert delta["changed_paths"] == [["A", "app/sp05_probe.py"]], delta["changed_paths"]
    eg, route = await _run_eval_gate(plan, delta["changed_paths"])
    assert eg["eval_verdict"]["passed"] is True
    assert route == "ship_gate"


# ── out-of-scope BLOCK (the load-bearing oracle): eval_gate halts a real out-of-scope diff ──
async def test_out_of_scope_diff_blocks_and_routes_to_halt():
    # Non-test out-of-scope path so the net-new-test exclusion cannot absorb it.
    plan = _plan(["app/sp05_probe.py"])
    eg, route = await _run_eval_gate(plan, [["A", "app/OUT_OF_SCOPE.py"]])
    assert eg["eval_verdict"]["passed"] is False
    reasons = [v["reason"] for v in eg["eval_verdict"]["violations"]]
    assert "out-of-scope" in reasons
    assert route == "__halt__"  # the FIRST time eval_gate blocks a non-empty diff


# ── workdir is forwarded to the sandbox, under the system tempdir ───────────────────
async def test_workdir_forwarded_and_under_tempdir():
    rec = _Recorder()
    await _run_execute(rec, _plan(["app/sp05_probe.py"]))
    assert rec.calls, "sandbox.run was never called"
    wd = rec.calls[0]["workdir"]
    assert wd is not None, "workdir was not forwarded to sandbox.run"
    wd_resolved = Path(wd).resolve()
    assert str(wd_resolved).startswith(str(Path(tempfile.gettempdir()).resolve()))


def test_workdir_guard_refuses_repo_root_and_accepts_tempdir():
    repo_root = Path(__file__).resolve().parents[2]
    assert _safe_sandbox_workdir(str(repo_root)) is None, "the live repo root must NOT be forwarded"
    assert _safe_sandbox_workdir(None) is None
    assert _safe_sandbox_workdir("/no/such/dir/xyz") is None
    d = tempfile.mkdtemp(prefix="aa-guard-")
    try:
        assert _safe_sandbox_workdir(d) == Path(d).resolve()
    finally:
        os.rmdir(d)


# ── per-phase egress: default-deny + empty live allowlist + execute path denied ─────
def test_egress_decision_default_deny_empty_allowlist():
    assert _EGRESS_ALLOWED_PHASES == frozenset(), "live egress allowlist must be EMPTY this slice"
    for p in ("research", "draft", "refine", "verify", "ship"):
        assert _egress_for_phase(p) is False
    assert _egress_for_phase("anything") is False


async def test_execute_path_requests_no_egress():
    rec = _Recorder()
    await _run_execute(rec, _plan(["app/sp05_probe.py"]))
    assert rec.calls[0]["network_allowed"] is False


# ── cleanup: no worktree leak on success OR failure ─────────────────────────────────
async def test_worktree_cleanup_no_leak_on_success_and_failure():
    repo = Path(__file__).resolve().parents[2]

    def _aa_worktrees() -> list[str]:
        out = subprocess.run(
            ["git", "worktree", "list"], cwd=str(repo), capture_output=True, text=True
        ).stdout
        return [ln for ln in out.splitlines() if "aa-ws-" in ln]

    before = _aa_worktrees()
    # success path
    await _run_execute(_RealChild(), _plan(["app/sp05_probe.py"]))
    # forced-FAILED path: an allowed_paths whose derived write the applier refuses ('..')
    await _run_execute(_RealChild(), _plan(["../../etc/evil"]))
    after = _aa_worktrees()
    assert after == before, f"worktree leaked: {set(after) - set(before)}"
    # the primary checkout stays clean (no probe file, no aa-ws junk)
    st = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo), capture_output=True, text=True
    ).stdout
    assert not [ln for ln in st.splitlines() if "sp05_probe" in ln or "aa-ws-" in ln], st


# ── applier refuses path-escape (defense-in-depth, NOT EROFS) ───────────────────────
@pytest.mark.parametrize("bad", ["../../etc/evil", "/etc/evil", "a/../../b"])
def test_applier_refuses_path_escape(bad, tmp_path):
    import json

    applier = str(Path(__file__).resolve().parents[2] / "app" / "core" / "_workspace_apply.py")
    r = subprocess.run(
        [sys.executable, applier, json.dumps([{"path": bad, "content": "x"}])],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0, f"escape path {bad!r} was not refused"
    # nothing got written outside CWD
    assert not Path("/etc/evil").exists()


# ── anti-fabrication: the diff is GIT-derived, never the child's stdout ──────────────
async def test_diff_is_git_truth_not_stdout():
    repo = Path(__file__).resolve().parents[2]
    ws = WorkspaceSession.create(repo_dir=repo, base_ref="HEAD", thread_id="antifab")
    try:
        assert ws.ok
        # A child that writes app/REAL.py but LIES in stdout about app/LIE.py.
        cmd = [
            sys.executable,
            "-c",
            "open('app/REAL_sp05.py','w').write('x'); print('wrote app/LIE_sp05.py')",
        ]
        subprocess.run(cmd, cwd=str(ws.ws_dir), check=True, capture_output=True, text=True)
        paths = {d["path"] for d in ws.diff()}
        assert "app/REAL_sp05.py" in paths, "the real write must appear"
        assert "app/LIE_sp05.py" not in paths, "a stdout claim must NOT fabricate a change"
    finally:
        ws.close()


# ── symlink-escape: a symlink in the diff ALWAYS violates (detection + wiring) ───────
async def test_workspace_detects_symlink_in_diff():
    repo = Path(__file__).resolve().parents[2]
    ws = WorkspaceSession.create(repo_dir=repo, base_ref="HEAD", thread_id="sym")
    try:
        assert ws.ok
        os.symlink("../../pyproject.toml", ws.ws_dir / "app" / "sp05_link.py")
        diff = ws.diff()
        syms = ws.symlinks([(d["status"], d["path"]) for d in diff])
        assert any("sp05_link.py" in s for s in syms)
    finally:
        ws.close()


async def test_eval_gate_blocks_symlink_changed_path():
    plan = _plan(["app/sp05_probe.py"])
    # an in-scope path that is ALSO a symlink → must still violate (escape vector).
    eg, route = await _run_eval_gate(
        plan, [["A", "app/sp05_probe.py"]], symlink_paths=["app/sp05_probe.py"]
    )
    assert eg["eval_verdict"]["passed"] is False
    assert any(v["reason"] == "symlink-escapes-scope" for v in eg["eval_verdict"]["violations"])
    assert route == "__halt__"


# ── the forbidden host-exec (invoke_hermes_cli) is NOT on the execute callgraph ─────
async def test_invoke_hermes_cli_not_called_by_execute(monkeypatch):
    import lib.hermes_bridge as hb

    def _boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("invoke_hermes_cli (host-exec, unscrubbed env) MUST NOT be called")

    monkeypatch.setattr(hb, "invoke_hermes_cli", _boom)
    # non-vacuous: execute DOES run a real child here, so 'not called' is meaningful.
    delta = await _run_execute(_RealChild(), _plan(["app/sp05_probe.py"]))
    assert delta["changed_paths"] == [["A", "app/sp05_probe.py"]]


# ── back-compat: sandbox=None preserves the legacy in-process empty-artifacts path ──
async def test_sandbox_none_preserves_legacy_empty_artifacts():
    nodes = _build_nodes(_default_capability(), sandbox=None)
    delta = await nodes["execute"](_state(_plan(["app/sp05_probe.py"])), _CFG)
    assert delta["changed_paths"] == []  # no sandbox → no workspace → empty diff (unchanged)


# ── derivation: in-scope by construction (file → exact; dir → under-prefix) ──────────
def test_node_output_relpath_lands_in_scope():
    from app.core.eval_gate import _matches_any

    for allowed in (["app/sp05_probe.py"], ["app/foo"], ["lib/bar/baz.py"]):
        rel = _node_output_relpath({"allowed_paths": allowed})
        assert _matches_any(rel, allowed), f"{rel} not in scope of {allowed}"
