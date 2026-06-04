"""SP-11 — parallel fan-out: the ready DAG frontier dispatched concurrently (asyncio.gather),
each node in its own worktree, under the SP-R6 global-cap + per-branch lease.

Every oracle carries its explicit RED control (cap=1 → serial; chain → 1 ready; shared
worktree → clobber; single cost key; direct-CM → frozen loop) so it provably bites. Hermetic:
real git worktrees + threads + the real branch_lease primitives, no docker/network/LLM.
"""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from app.core.graph import _build_nodes, _default_capability
from app.core.sandbox import AbstractSandbox, SandboxResult
from lib.durability.branch_lease import BranchLease, GlobalThreadCap

_REPO = Path(__file__).resolve().parents[2]
_CFG = {"configurable": {"thread_id": "t-sp11"}}


@pytest.fixture(autouse=True)
def _cleanup():
    def sweep():
        out = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/aa-snapshots/"],
            cwd=str(_REPO),
            capture_output=True,
            text=True,
        ).stdout
        for ref in out.split():
            subprocess.run(["git", "update-ref", "-d", ref], cwd=str(_REPO), capture_output=True)

    sweep()
    yield
    subprocess.run(["git", "worktree", "prune"], cwd=str(_REPO), capture_output=True)
    sweep()


class _TrackingChild(AbstractSandbox):
    """Runs the REAL applier (so the worktree gets a real diff) while recording peak concurrent
    active leaves; an optional sleep widens the overlap window so peak is observable."""

    def __init__(self, sleep: float = 0.0) -> None:
        self.active = 0
        self.peak = 0
        self._sleep = sleep
        self._lk = asyncio.Lock()

    async def run(
        self, *, cmd, workdir=None, env=None, network_allowed=False, **kw
    ) -> SandboxResult:
        if network_allowed:
            raise PermissionError("test sandbox enforces no network")
        async with self._lk:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workdir) if workdir else None,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
            if self._sleep:
                await asyncio.sleep(self._sleep)
            rc = proc.returncode if proc.returncode is not None else -1
        finally:
            async with self._lk:
                self.active -= 1
        return SandboxResult(
            returncode=rc, stdout=out.decode(), stderr=err.decode(), duration_s=0.0, killed=False
        )


def _node(nid, deps, path=None):
    return {
        "id": nid,
        "phase": "draft",
        "summary": nid,
        "depends_on": list(deps),
        "acceptance_ref": "0",
        "allowed_paths": [path or f"app/sp11_{nid}.py"],
    }


def _plan(nodes):
    return {"nodes": nodes, "edges": [(d, n["id"]) for n in nodes for d in n["depends_on"]]}


def _state(plan):
    return {"thread_id": "t-sp11", "goal": "g", "plan": plan, "base_ref": "HEAD"}


async def _run_fanout(plan, *, cap_n, sleep=0.0, sandbox=None):
    sb = sandbox or _TrackingChild(sleep=sleep)
    nodes = _build_nodes(
        _default_capability(),
        sandbox=sb,
        cap=GlobalThreadCap(cap_n),
        lease=BranchLease(tempfile.mkdtemp(prefix="aa-sp11-lease-")),
    )
    t0 = time.monotonic()
    delta = await nodes["fan_out"](_state(plan), _CFG)
    return delta, sb, time.monotonic() - t0


# ── O1/O2 concurrency: independent roots run concurrently (peak≥2) AND faster than serial ──
async def test_concurrency_peak_and_faster_than_serial():
    sleep = 0.15
    plan = _plan([_node("n0", []), _node("n1", []), _node("n2", [])])
    _, sb_par, wall_par = await _run_fanout(plan, cap_n=3, sleep=sleep)
    assert sb_par.peak == 3, f"3 independent roots must overlap, peak={sb_par.peak}"  # ≥2
    # RED control: cap=1 serializes → peak==1 AND meaningfully slower (no overlap)
    _, sb_seq, wall_seq = await _run_fanout(plan, cap_n=1, sleep=sleep)
    assert sb_seq.peak == 1, "cap=1 must serialize"
    # Per-node git-worktree overhead serializes on the repo lock either way, so it CANCELS in
    # the difference; the overlapped sleeps are the real signal — concurrency must reclaim more
    # than one node's sleep of wall-clock (serial runs 3 sleeps end-to-end, concurrent ~1).
    assert wall_seq - wall_par > sleep, (
        f"concurrent ({wall_par:.2f}s) not meaningfully faster than serial ({wall_seq:.2f}s); "
        f"overlap saved only {wall_seq - wall_par:.2f}s (< one {sleep:.2f}s sleep)"
    )


# ── O3 sequential control: a pure chain yields ONE ready node per wave (peak==1, cap=4) ──
async def test_chain_dispatches_single_ready_node():
    plan = _plan([_node("n0", []), _node("n1", ["n0"]), _node("n2", ["n1"])])
    delta, sb, _ = await _run_fanout(plan, cap_n=4, sleep=0.05)
    assert sb.peak == 1, "a dependency chain must NOT fan out (only n0 is ready)"
    assert len(delta["tasks"]) == 1  # only the single ready root dispatched this wave


# ── O6 global cap: cap=2 over 4 ready nodes → peak==2 AND all 4 complete (queued) ──────
async def test_global_cap_queues_and_completes():
    plan = _plan([_node(f"n{i}", []) for i in range(4)])
    delta, sb, _ = await _run_fanout(plan, cap_n=2, sleep=0.05)
    assert sb.peak == 2, f"cap=2 must bound active to 2, peak={sb.peak}"
    assert len(delta["tasks"]) == 4  # all queued nodes eventually ran (none dropped)


# ── O4 cross-access-denial: two nodes writing the SAME relpath land in DISTINCT worktrees ──
async def test_cross_access_denial_distinct_worktrees():
    shared = "app/sp11_shared_probe.py"
    plan = _plan([_node("a", [], shared), _node("b", [], shared)])
    delta, _, _ = await _run_fanout(plan, cap_n=2, sleep=0.0)
    # both leaves wrote the SAME relpath but in their OWN worktree → TWO distinct diff entries,
    # neither clobbered. A SHARED worktree (the RED) would yield ONE entry (second write wins).
    entries = [cp for cp in delta["changed_paths"] if cp[1] == shared]
    assert len(entries) == 2 and all(cp[0] == "A" for cp in entries)


class _CrossProbeChild(AbstractSandbox):
    """Each leaf, BEFORE writing its own uniquely-named marker, records whether ANY sibling's
    marker is already visible in its workdir. DISTINCT worktrees ⇒ a sibling's marker is NEVER
    visible (the PRD §6 SP-11 'B CANNOT read A's marker' isolation proof). The RED control: a
    mis-wired SHARED /workspace ⇒ the later leaf SEES the earlier marker ⇒ saw_sibling has a
    True ⇒ the assertion below FAILS (non-vacuous)."""

    def __init__(self) -> None:
        self.workdirs: list[str] = []
        self.saw_sibling: list[bool] = []
        self._n = 0
        self._lk = asyncio.Lock()

    async def run(
        self, *, cmd, workdir=None, env=None, network_allowed=False, **kw
    ) -> SandboxResult:
        wd = Path(workdir)
        async with self._lk:
            idx = self._n
            self._n += 1
        saw = bool(list(wd.glob("CROSS_PROBE_*")))  # any sibling's marker already here?
        (wd / f"CROSS_PROBE_{idx}").write_text("x")  # plant THIS leaf's marker
        async with self._lk:
            self.workdirs.append(str(wd))
            self.saw_sibling.append(saw)
        return SandboxResult(returncode=0, stdout="", stderr="", duration_s=0.0, killed=False)


# ── O4b cross-READ denial (load-bearing): neither concurrent leaf can SEE the other's marker ──
async def test_cross_read_denied_marker_invisible_across_worktrees():
    plan = _plan([_node("a", []), _node("b", [])])
    _, sb, _ = await _run_fanout(plan, cap_n=2, sleep=0.0, sandbox=_CrossProbeChild())
    assert len(set(sb.workdirs)) == 2, f"each leaf must get its OWN worktree path: {sb.workdirs}"
    # the load-bearing assertion: NO leaf ever saw a sibling's marker (cross-read DENIED).
    assert not any(
        sb.saw_sibling
    ), f"a leaf saw a sibling's marker — workspaces not isolated: {sb.saw_sibling}"


# ── O8 per-task cost: N leaves → N distinct cost keys f"{tid}|{node_id}" ──────────────
async def test_per_task_cost_keys():
    plan = _plan([_node("n0", []), _node("n1", []), _node("n2", [])])
    delta, _, _ = await _run_fanout(plan, cap_n=3, sleep=0.0)
    assert set(delta["cost_accumulator"]) == {"t-sp11|n0", "t-sp11|n1", "t-sp11|n2"}


# ── O8b distinct snapshot keys: every leaf records its OWN ref; ALL recorded, pairwise distinct ──
async def test_distinct_snapshot_keys_per_leaf():
    # PRD §6 SP-11 "A/B snapshot to DISTINCT keys": workspace_refs enumerates EVERY leaf's
    # refs/aa-snapshots/<tid>/<node> ref (none silently dropped — only workspace_ref==refs[0] is
    # the single one ship_effect consumes), and they are pairwise DISTINCT. (C9 finding 2.)
    plan = _plan([_node("a", []), _node("b", []), _node("c", [])])
    delta, _, _ = await _run_fanout(plan, cap_n=3, sleep=0.0)
    refs = [r["ref"] for r in delta.get("workspace_refs", [])]
    assert len(refs) == 3, f"every leaf must record its snapshot ref: {delta.get('workspace_refs')}"
    assert len(set(refs)) == 3, f"snapshot keys must be DISTINCT per node: {refs}"
    assert delta["workspace_ref"]["ref"] == refs[0]  # ship_effect's single ref is the first


# ── O11 frontier advances: feeding each wave's COMPLETED task back dispatches the NEXT node ──
async def test_fanout_advances_frontier_across_waves():
    # A pure chain n0→n1→n2: done_ids is derived from the ACCUMULATED completed tasks (NOT a
    # hardcoded empty set), so re-driving fan_out with the prior wave fed back advances the ready
    # frontier exactly ONE node per wave. RED: a hardcoded set() would re-dispatch n0 every wave
    # → [["n0"],["n0"],["n0"]] → FAIL. The graph-level eval→fan_out loop edge + cross-wave
    # worktree merge-back that would drive this end-to-end stay deferred (SP-11.yaml). (C9 finding 1/4.)
    plan = _plan([_node("n0", []), _node("n1", ["n0"]), _node("n2", ["n1"])])
    nodes = _build_nodes(
        _default_capability(),
        sandbox=_TrackingChild(),
        cap=GlobalThreadCap(4),
        lease=BranchLease(tempfile.mkdtemp(prefix="aa-sp11-lease-")),
    )
    done_tasks: list = []
    dispatched: list = []
    for _ in range(3):
        state = {
            "thread_id": "t-sp11",
            "goal": "g",
            "plan": plan,
            "base_ref": "HEAD",
            "tasks": done_tasks,
        }
        delta = await nodes["fan_out"](state, _CFG)
        dispatched.append(sorted(t["task_id"] for t in delta["tasks"]))
        done_tasks = done_tasks + list(delta["tasks"])  # accumulate completions across waves
    assert dispatched == [
        ["n0"],
        ["n1"],
        ["n2"],
    ], f"frontier must advance one node/wave: {dispatched}"


# ── O7 event-loop not blocked: a heartbeat keeps ticking while leaves WAIT on the cap ──
async def test_event_loop_not_blocked_under_cap_contention():
    plan = _plan([_node(f"n{i}", []) for i in range(3)])
    ticks = 0
    stop = asyncio.Event()

    async def heartbeat():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.005)

    hb = asyncio.create_task(heartbeat())
    # cap=1 forces 2 of 3 leaves to BLOCK on the semaphore (entered off-loop via to_thread);
    # if the bridge were direct (cm.__enter__ on the loop), the heartbeat would stall.
    await _run_fanout(plan, cap_n=1, sleep=0.05)
    stop.set()
    await hb
    assert ticks > 5, f"event loop stalled under cap contention (ticks={ticks}) — bridge broken"


# ── O10 interrupt-split: fan_out must contain NO interrupt() (a gather'd leaf can't suspend) ──
def test_fan_out_is_interrupt_free():
    src = inspect.getsource(_build_nodes)
    # locate the fan_out closure source within _build_nodes and assert no interrupt() call
    assert "async def fan_out" in src
    fan_src = src[src.index("async def fan_out") :]
    fan_src = fan_src[: fan_src.index("async def eval_gate")]
    assert "interrupt(" not in fan_src, "fan_out must not co-locate interrupt() (interrupt-split)"


# ── back-compat: sandbox=None → a single in-process leaf, no worktree, no workspace_ref ──
async def test_sandbox_none_single_leaf():
    nodes = _build_nodes(_default_capability(), sandbox=None)
    delta = await nodes["fan_out"](_state(_plan([_node("n0", [])])), _CFG)
    assert len(delta["tasks"]) == 1
    assert delta["changed_paths"] == [] and "workspace_ref" not in delta


# ── O9 exactly-once: the tasks reducer dedups duplicate task_ids (resume re-run safety) ──
def test_tasks_reducer_dedups_by_task_id():
    from app.core.graph_state import _merge_by_task_id

    wave = [{"task_id": "n0", "status": "ok"}, {"task_id": "n1", "status": "ok"}]
    # a resume that re-ran fan_out would re-emit the SAME task_ids → LWW dedup, NOT append.
    merged = _merge_by_task_id(wave, wave)
    assert sorted(t["task_id"] for t in merged) == ["n0", "n1"]  # 2, not 4
