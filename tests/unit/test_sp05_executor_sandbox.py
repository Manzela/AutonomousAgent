"""SP-05 (slice 1) red-green oracles — the executor runs agent work in a REAL
per-node sandbox child, NOT in the harness process.

Closes the PRD §5.1 lethal-trifecta gap: `orchestrator._execute_local` ran
`capability.invoke()` in the harness PID with the full secret env. After SP-05 the
local execute path routes through `AbstractSandbox.run()` (a child process, scrubbed
env, no network), never `invoke`.

Test doubles (both implement the `AbstractSandbox` ABC — we do NOT collapse it):
  * ``_RecordingSandbox`` — pure recorder; proves wiring/reachability, the exact env
    the orchestrator passes (no secrets), the network flag, returncode→status, and
    that ``invoke`` is bypassed. Platform-independent.
  * ``_SubprocessSandbox`` — spawns a REAL child with exactly the env the orchestrator
    passes, but WITHOUT the POSIX rlimit preexec (which is unsettable on macOS); proves
    no-in-process-exec (different PID) and that a planted harness secret is absent from
    the real child env. Platform-independent.
The production ``LocalSubprocessSandbox`` (rlimits + netns refusal) is already covered
by ``app/tests/test_inmemory_adapters.py`` on Linux CI; here we also assert its honest
network hard-refusal (which raises before spawning, so it runs on any platform).

NON-VACUOUS: on `main` before this slice `AbstractSandbox.run()` has zero callers and
`invoke` runs in-harness, so the reachability / different-PID / no-secret / no-invoke
oracles are RED, and GREEN only once the sandbox is wired.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Optional

from app.adapters.inmemory.sandbox import LocalSubprocessSandbox
from app.core.graph import _build_nodes, _default_capability
from app.core.orchestrator import execute as orchestrate
from app.core.sandbox import AbstractSandbox, SandboxResult
from app.core.schemas import AgentCapability, AgentID, ExecutionResult, TaskRequest, TaskStatus

_SECRET_RE = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CRED", re.IGNORECASE)
_PRINT_PID = [sys.executable, "-c", "import os; print(os.getpid())"]


class _RecordingSandbox(AbstractSandbox):
    """Records every run() call; returns a canned result. No subprocess."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[dict] = []
        self._rc, self._out, self._err = returncode, stdout, stderr

    async def run(
        self, *, cmd, workdir=None, env=None, network_allowed=False, **kw
    ) -> SandboxResult:
        # F-1: record workdir + network_allowed so oracles can assert the worktree is
        # forwarded and egress stays default-deny.
        self.calls.append(
            {"cmd": cmd, "workdir": workdir, "env": env, "network_allowed": network_allowed}
        )
        return SandboxResult(
            returncode=self._rc, stdout=self._out, stderr=self._err, duration_s=0.01, killed=False
        )


class _SubprocessSandbox(AbstractSandbox):
    """Spawns a REAL child with the passed env, WITHOUT the rlimit preexec (so it runs
    on macOS too). Faithful for the no-in-process-exec / no-secret-env properties."""

    async def run(
        self,
        *,
        cmd: list[str],
        workdir: Optional[Path] = None,
        env: Optional[dict] = None,
        stdin: Optional[str] = None,
        timeout_s: float = 60.0,
        cpu_seconds: int = 30,
        memory_mb: int = 512,
        max_files: int = 256,
        network_allowed: bool = False,
    ) -> SandboxResult:
        if network_allowed:
            raise PermissionError("test sandbox enforces no network")
        # F-1: forward workdir as the child's cwd so the applier writes INTO the worktree
        # (without this, the worktree oracles would silently write into the test's cwd).
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


def _cap(*, invoke=None) -> AgentCapability:
    return AgentCapability(
        agent_id=AgentID("sp05-test"),
        version="1",
        phase="draft",
        description="sp05 test capability",
        invoke=invoke,
    )


def _req(cmd, *, env: dict | None = None) -> TaskRequest:
    constraints: dict = {} if cmd is None else {"sandbox_cmd": cmd}
    if env is not None:
        constraints["sandbox_env"] = env
    return TaskRequest(task_id="sp05-task", phase="draft", summary="s", constraints=constraints)


# ── 1. callgraph reachability (C4) ──────────────────────────────────────────
async def test_execute_awaits_sandbox_run():
    sb = _RecordingSandbox()
    res = await orchestrate(_req(_PRINT_PID), _cap(), sandbox=sb)
    assert (
        len(sb.calls) == 1
    ), "AbstractSandbox.run() must be awaited exactly once (0 callers on main)"
    assert res.status == TaskStatus.COMPLETED


async def test_execute_forwards_cmd_and_disables_network():
    sb = _RecordingSandbox()
    await orchestrate(_req(_PRINT_PID), _cap(), sandbox=sb)
    assert sb.calls[0]["cmd"] == _PRINT_PID
    assert (
        sb.calls[0]["network_allowed"] is False
    ), "execute must request network_allowed=False (egress denied)"


# ── 2./3. no-in-process-exec + no-secrets, proven against a REAL child ───────
async def test_sandboxed_exec_has_different_pid():
    res = await orchestrate(_req(_PRINT_PID), _cap(), sandbox=_SubprocessSandbox())
    child_pid = int(res.output["stdout"].strip())
    assert child_pid != os.getpid(), "agent code ran in the harness process (in-process exec)"


async def test_no_harness_secrets_reach_the_child_env(monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", "sentinel-leak-xyz")
    monkeypatch.setenv("SOME_SECRET_TOKEN", "sentinel-tok")
    cmd = [
        sys.executable,
        "-c",
        "import os,re; print(os.environ.get('FAKE_API_KEY','')); "
        "print(','.join(k for k in os.environ if re.search(r'KEY|TOKEN|SECRET|PASSWORD|CRED', k, re.I)))",
    ]
    res = await orchestrate(_req(cmd), _cap(), sandbox=_SubprocessSandbox())
    lines = res.output["stdout"].splitlines()
    assert lines[0] == "", "a harness *API_KEY leaked into the sandbox child env"
    assert (lines[1] if len(lines) > 1 else "") == "", "secret-shaped env keys reached the child"


async def test_scrubbed_env_passed_to_run_has_no_secret_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-pass")
    sb = _RecordingSandbox()
    await orchestrate(_req(_PRINT_PID), _cap(), sandbox=sb)
    passed_env = sb.calls[0]["env"] or {}
    assert (
        passed_env is not None
    ), "env must be an explicit scrubbed dict, never None (None leaks os.environ)"
    leaked = [k for k in passed_env if _SECRET_RE.search(k)]
    assert not leaked, f"orchestrator passed secret-shaped env keys to the sandbox: {leaked}"


# ── 4. honest network isolation against the REAL adapter (raises pre-spawn) ──
async def test_real_sandbox_network_allowed_true_is_hard_refused():
    import pytest

    with pytest.raises(PermissionError):
        await LocalSubprocessSandbox().run(cmd=_PRINT_PID, network_allowed=True)


# ── 5. returncode → status (consumes the real exit, no fabricated COMPLETED) ─
async def test_returncode_zero_maps_completed():
    res = await orchestrate(_req(_PRINT_PID), _cap(), sandbox=_RecordingSandbox(returncode=0))
    assert res.status == TaskStatus.COMPLETED


async def test_returncode_nonzero_maps_failed():
    res = await orchestrate(_req(_PRINT_PID), _cap(), sandbox=_RecordingSandbox(returncode=1))
    assert res.status == TaskStatus.FAILED
    assert res.output["returncode"] == 1


# ── 6. sandboxed branch never calls invoke; sandbox=None preserves it ────────
async def test_sandboxed_branch_does_not_call_invoke():
    called = {"n": 0}

    async def _invoke(_req_):  # must NOT run
        called["n"] += 1
        return ExecutionResult(task_id=_req_.task_id, status=TaskStatus.COMPLETED)

    await orchestrate(_req(_PRINT_PID), _cap(invoke=_invoke), sandbox=_RecordingSandbox())
    assert called["n"] == 0, "sandboxed path must not run the in-process invoke"


async def test_sandbox_none_preserves_legacy_invoke():
    called = {"n": 0}

    async def _invoke(_req_):
        called["n"] += 1
        return ExecutionResult(task_id=_req_.task_id, status=TaskStatus.COMPLETED)

    res = await orchestrate(_req(None), _cap(invoke=_invoke), sandbox=None)
    assert called["n"] == 1, "sandbox=None must preserve the legacy in-process path (back-compat)"
    assert res.status == TaskStatus.COMPLETED


# ── 7. missing cmd fails cleanly (no crash, no fabricated COMPLETED, no run) ─
async def test_missing_sandbox_cmd_fails_cleanly():
    sb = _RecordingSandbox()
    res = await orchestrate(_req(None), _cap(), sandbox=sb)
    assert res.status == TaskStatus.FAILED
    assert "cmd" in (res.error or "")
    assert sb.calls == [], "must not invoke the sandbox with an invalid cmd"


async def test_sandbox_run_exception_maps_failed():
    """A sandbox whose run() raises (a broken impl) yields FAILED, not a crash — the
    except branch the C9 review hardened to logger.exception()."""

    class _RaisingSandbox(AbstractSandbox):
        async def run(self, *, cmd, **kw) -> SandboxResult:
            raise RuntimeError("sandbox backend exploded")

    res = await orchestrate(_req(_PRINT_PID), _cap(), sandbox=_RaisingSandbox())
    assert res.status == TaskStatus.FAILED
    assert "sandbox_exec_error" in (res.error or "")


# ── 8. spine reachability — the execute NODE drives the sandbox ──────────────
async def test_spine_execute_node_runs_in_sandbox():
    sb = _RecordingSandbox()
    nodes = _build_nodes(_default_capability(), sandbox=sb)
    out = await nodes["fan_out"](
        {"thread_id": "t1", "goal": "do x"}, {"configurable": {"thread_id": "t1"}}
    )
    assert len(sb.calls) == 1, "the spine execute node must drive AbstractSandbox.run()"
    assert out["tasks"][0]["status"] == TaskStatus.COMPLETED.value


# ── 9. Sandbox Hardening (Required Correction 1) ───────────────────────────
async def test_sandbox_hardening_write_and_mock_blocks(tmp_path):
    """Verify that LocalSubprocessSandbox blocks writes outside the allowed directory
    and blocks dynamic mock/monkeypatch of parent modules."""
    workdir = tmp_path / "worktree"
    workdir.mkdir()

    # Use a file path that is outside both workdir and standard temp dirs
    outside_file = Path.cwd() / "outside.txt"

    script = f"""import sys
import os
from pathlib import Path

# Verify write blocks outside allowed dir
try:
    with open({repr(str(outside_file))}, "w") as f:
        f.write("leak")
    print("WRITE_OUTSIDE_ALLOWED")
except PermissionError:
    print("WRITE_OUTSIDE_BLOCKED")

# Verify unittest.mock.patch blocking on parent modules
try:
    import unittest.mock
    @unittest.mock.patch("app.core.graph.orchestrate")
    def mock_fn(m):
        pass
    mock_fn()
    print("MOCK_ALLOWED")
except PermissionError:
    print("MOCK_BLOCKED")

# Verify module attribute modification blocking on parent modules
try:
    import app.core.graph
    app.core.graph.SOME_FLAG = "changed"
    print("MONKEYPATCH_ALLOWED")
except PermissionError:
    print("MONKEYPATCH_BLOCKED")
"""

    sandbox = LocalSubprocessSandbox()
    res = await sandbox.run(
        cmd=[sys.executable, "-c", script],
        workdir=workdir,
    )

    assert res.returncode == 0
    stdout = res.stdout
    assert "WRITE_OUTSIDE_BLOCKED" in stdout
    assert "MOCK_BLOCKED" in stdout
    assert "MONKEYPATCH_BLOCKED" in stdout
    assert "WRITE_OUTSIDE_ALLOWED" not in stdout
    assert "MOCK_ALLOWED" not in stdout
    assert "MONKEYPATCH_ALLOWED" not in stdout
