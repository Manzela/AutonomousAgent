"""SP-01 spine oracles.

Skeleton: goal_intake -> sign_off[interrupt] -> seal_spec -> execute
          -> ship_gate[interrupt] -> ship_effect -> END.
"""

from __future__ import annotations

import operator
from typing import Annotated

from typing_extensions import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import pytest

from app.adapters.inmemory.checkpointer import InMemoryCheckpointer
from app.core import graph_state as gs
from app.core.schemas import AgentCapability, ExecutionResult, TaskStatus
from app.core.spine_runner import SpineRunner


@pytest.fixture(autouse=True)
def _isolate_decision_record(tmp_path, monkeypatch):
    """Keep these tests hermetic: the spine's nodes call append_decision() with the
    default path, which would otherwise write the durable trail into the real repo
    trajectories/ dir. Redirect it to a per-test tmp file."""
    monkeypatch.setenv("SPINE_DECISION_RECORD_PATH", str(tmp_path / "decision-record.jsonl"))


def _stub_capability(status=TaskStatus.COMPLETED):
    async def _invoke(request):
        return ExecutionResult(task_id=request.task_id, status=status, cost_usd=0.01)

    return AgentCapability(
        agent_id="stub-agent",
        version="1",
        phase="draft",
        description="skeleton stub capability",
        invoke=_invoke,
    )


# ── Task 0: harness smoke ───────────────────────────────────────────────────
async def test_async_node_interrupt_idmap_resume_and_pregel_task_id():
    class S(TypedDict):
        log: Annotated[list, operator.add]
        seen_ptid: str
        decision: str

    async def gate(state, config):
        d = interrupt({"q": "ok?"})
        return {"decision": d, "log": ["gate resumed"]}

    async def effect(state, config):
        ptid = config["configurable"]["__pregel_task_id"]
        return {"seen_ptid": ptid, "log": [f"effect ptid={bool(ptid)}"]}

    g = StateGraph(S)
    g.add_node("gate", gate)
    g.add_node("effect", effect)
    g.add_edge(START, "gate")
    g.add_edge("gate", "effect")
    g.add_edge("effect", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "smoke"}}

    r1 = await app.ainvoke({"log": [], "seen_ptid": "", "decision": ""}, cfg, durability="sync")
    intr = r1["__interrupt__"][0]
    assert intr.id and intr.value == {"q": "ok?"}
    assert app.get_state(cfg).next == ("gate",)

    r2 = await app.ainvoke(Command(resume={intr.id: "APPROVE"}), cfg, durability="sync")
    assert r2["decision"] == "APPROVE"
    assert r2["seen_ptid"]  # __pregel_task_id was non-empty inside the node


# ── Task 4: skeleton happy path ─────────────────────────────────────────────
async def test_skeleton_happy_path_two_interrupts_seal_execute_ship():
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-happy"

    r1 = await runner.start(thread_id=tid, goal="ship a hello-world endpoint")
    assert "__interrupt__" in r1
    signoff = r1["__interrupt__"][0]
    assert signoff.id and signoff.value["gate"] == "sign_off"
    assert runner.get_state(tid).next == ("sign_off",)  # nothing built before approval

    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=signoff.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "lgtm"},
    )
    assert "__interrupt__" in r2  # paused again at ship_gate
    ship = r2["__interrupt__"][0]
    assert ship.id != signoff.id and ship.value["gate"] == "ship"
    assert runner.get_state(tid).next == ("ship_gate",)
    assert r2.get("spec_sha")  # seal_spec ran (sha-pinned)
    assert any(t["status"] == "completed" for t in r2.get("tasks", []))  # execute ran

    r3 = await runner.resume(
        thread_id=tid,
        interrupt_id=ship.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "ship it"},
    )
    assert "__interrupt__" not in r3  # reached END
    assert runner.get_state(tid).next == ()
    # exactly-once: the ship effect is witnessed once in the ledger
    ship_receipts = [r for r in r3["ledger"] if r["action_kind"] == "ship"]
    assert len(ship_receipts) == 1
    assert r3["execution_counts"][gs._key_str(gs.ledger_key(ship_receipts[0]))] == 1
    # two HITL decisions recorded in state (sign_off + ship), both keyed by the real id
    verbs = [d["verb"] for d in r3["decision_record"]]
    assert verbs.count("APPROVE") == 2
    assert r3["decision_record"][0]["interrupt_id"] == signoff.id
    assert r3["decision_record"][1]["interrupt_id"] == ship.id


# ── Task 4: SP-R1 scrub-before-persist oracle (review-added) ────────────────
async def test_secret_in_goal_absent_from_persisted_checkpoint():
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-scrub"
    secret = "sk-ABCDEF0123456789abcdef0123456789abcd"  # pragma: allowlist secret
    await runner.start(thread_id=tid, goal=f"deploy using {secret} please")
    persisted_goal = runner.get_state(tid).values["goal"]
    assert secret not in persisted_goal  # scrubbed before it entered the checkpoint
    assert runner.get_state(tid).values["scrubbed"] is True


# ── Task 5: DoD-1 exactly-once ──────────────────────────────────────────────
def test_apply_once_guard_is_load_bearing_on_reentry():
    """The ledger guard is NON-VACUOUS: on re-entry with the receipt present it
    SKIPs the external effect (count stays 1). Negative control proves the guard —
    not langgraph — is what dedups: with no receipt in state the effect repeats."""
    from app.core.graph import apply_once

    calls = []
    state = {"ledger": []}
    d1 = apply_once(
        state,
        tid="T",
        ptid="p",
        kind="ship",
        node_label="ship_effect",
        effect=lambda: calls.append(1),
    )
    assert calls == [1] and "ledger" in d1  # first entry: effect ran, receipt written

    state2 = {
        "ledger": gs._ledger_union(state["ledger"], d1["ledger"])
    }  # accumulate as reducer would
    d2 = apply_once(
        state2,
        tid="T",
        ptid="p",
        kind="ship",
        node_label="ship_effect",
        effect=lambda: calls.append(1),
    )
    assert calls == [1]  # re-entry: SKIP'd, external effect NOT repeated
    assert "ledger" not in d2 and "SKIP" in d2["audit"][0]

    # NEGATIVE CONTROL: with no receipt in state the guard cannot fire -> effect repeats.
    apply_once(
        {"ledger": []},
        tid="T",
        ptid="p",
        kind="ship",
        node_label="ship_effect",
        effect=lambda: calls.append(1),
    )
    assert calls == [1, 1]  # the guard (not langgraph) is what makes it exactly-once


async def test_external_effect_before_interrupt_double_acts_RED():
    """Why the node-split exists: a REAL external effect placed BEFORE interrupt()
    in a node body re-runs on resume -> at-least-once (==2). (A state-channel write
    would be rolled back to 1 — verified — which is exactly why the proof must use
    an external observable.)"""
    seen = {"n": 0}

    class S(TypedDict):
        decision: str

    async def bad_gate(state, config):
        seen["n"] += 1  # EXTERNAL effect before interrupt -> at-least-once
        d = interrupt({"q": "?"})
        return {"decision": d}

    g = StateGraph(S)
    g.add_node("bad_gate", bad_gate)
    g.add_edge(START, "bad_gate")
    g.add_edge("bad_gate", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "red"}}
    r1 = await app.ainvoke({"decision": ""}, cfg, durability="sync")
    intr = r1["__interrupt__"][0]
    await app.ainvoke(Command(resume={intr.id: "APPROVE"}), cfg, durability="sync")
    assert seen["n"] == 2  # RED: the external effect double-acted (anti-pattern)


async def test_ship_effect_exactly_once_under_crash_resume():
    """DoD-1: drive to ship_gate, simulate process death (a fresh runner over the
    SAME checkpointer provider), resume APPROVE; the ship effect is witnessed exactly
    once. (Run-once here is enforced by langgraph re-running only the interrupted
    node; the apply_once guard's forward-compat idempotency is proven separately by
    test_apply_once_guard_is_load_bearing_on_reentry.)"""
    cp = InMemoryCheckpointer()
    tid = "goal-eo"
    runner = SpineRunner(cp, capability=_stub_capability())
    r1 = await runner.start(thread_id=tid, goal="ship X", durability="sync")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
        durability="sync",
    )
    ship = r2["__interrupt__"][0]

    runner2 = SpineRunner(cp, capability=_stub_capability())  # crash + restart, same saver
    r3 = await runner2.resume(
        thread_id=tid,
        interrupt_id=ship.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
        durability="sync",
    )
    ship_receipts = [r for r in r3["ledger"] if r["action_kind"] == "ship"]
    assert len(ship_receipts) == 1
    counts = list(r3["execution_counts"].values())
    assert counts and all(c == 1 for c in counts)  # every effect witnessed exactly once


# ── Task 6: REJECT + REPLAN ─────────────────────────────────────────────────
async def test_reject_at_sign_off_halts_nothing_built():
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-reject"
    r1 = await runner.start(thread_id=tid, goal="do X")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "REJECT", "actor": "op", "reason": "no"},
    )
    assert "__interrupt__" not in r2  # no ship gate reached
    assert runner.get_state(tid).next == ()  # halted at END
    assert not r2.get("spec_sha")  # seal_spec never ran (nothing built before approval)
    assert not [r for r in r2.get("ledger", []) if r["action_kind"] == "ship"]
    assert any("HALTED" in a for a in r2["audit"])
    assert r2["decision_record"][-1]["verb"] == "REJECT"  # audited


async def test_replan_at_sign_off_marks_fork_old_thread_immutable():
    """REPLAN marks the fork (replan_parent set) and leaves the old thread + its
    (unsealed) spec immutable. The actual continue-as-new child-thread spawn is the
    deferred REPLAN native-time-travel layer."""
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    old = "goal-replan"
    r1 = await runner.start(thread_id=old, goal="do X")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=old,
        interrupt_id=so.id,
        decision={"verb": "REPLAN", "actor": "op", "reason": "redo"},
    )
    assert r2["replan_parent"] == old
    assert runner.get_state(old).next == ()  # old thread terminal
    assert not r2.get("spec_sha")  # nothing sealed on REPLAN
    # a fresh thread_id starts independently on the same saver (continue-as-new)
    r3 = await runner.start(thread_id="goal-replan-2", goal="do X (replanned)")
    assert "__interrupt__" in r3
    # old thread state is preserved/immutable
    assert runner.get_state(old).values["thread_id"] == old
    assert runner.get_state(old).values["replan_parent"] == old


# ── Task 7: approval-receipt invariant + TIMEOUT->safe-default REJECT ────────
async def test_crash_after_approve_does_not_reprompt_sign_off():
    """Approval-receipt invariant: once sign_off is APPROVE'd the graph advances past
    it (checkpoint persists the decision), so a resume after a crash surfaces the
    NEXT gate (ship), never sign_off again, and the sign_off decision is durable once."""
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    tid = "goal-receipt"
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "y"},
        durability="sync",
    )
    assert runner.get_state(tid).next == ("ship_gate",)  # advanced, NOT back at sign_off
    sign_off_iid = r2["sign_off"]["interrupt_id"]
    assert sum(1 for d in r2["decision_record"] if d["interrupt_id"] == sign_off_iid) == 1


async def test_timeout_maps_to_safe_default_reject():
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-timeout"
    r1 = await runner.start(thread_id=tid, goal="g")
    so = r1["__interrupt__"][0]
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=so.id,
        decision={"verb": "TIMEOUT", "actor": "system", "reason": "no response"},
    )
    assert runner.get_state(tid).next == ()  # halted (TIMEOUT -> safe-default reject)
    assert not r2.get("spec_sha")


# ── Task 8: DoD-4 steering primitive + DoD-17 workspace rehydrate ───────────
async def test_dod4_steering_dedup_survives_resume_and_arbitrate_picks_reject():
    """DoD-4 (scoped to the proven primitive): pre-normalized SteeringEvents dedup on
    (channel, origin_id), SURVIVE the resume boundary at-most-once, and arbitrate()
    deterministically picks REJECT over APPROVE for one interrupt_id. Wiring arbitrate()
    as the graph's routing signal (so REJECT steering overrides an APPROVE resume) is
    the deferred SP-17 bus; this proves the reducer + arbitration only."""
    cp = InMemoryCheckpointer()
    runner = SpineRunner(cp, capability=_stub_capability())
    tid = "goal-steer"
    r1 = await runner.start(thread_id=tid, goal="g", durability="sync")
    iid = r1["__interrupt__"][0].id
    events = [
        {
            "channel": "telegram",
            "origin_id": "m1",
            "verb": "APPROVE",
            "interrupt_id": iid,
            "ts": "1",
        },
        {
            "channel": "telegram",
            "origin_id": "m1",
            "verb": "APPROVE",
            "interrupt_id": iid,
            "ts": "1",
        },
        {"channel": "board", "origin_id": "c9", "verb": "REJECT", "interrupt_id": iid, "ts": "2"},
    ]
    await runner._app.ainvoke(
        Command(
            resume={iid: {"verb": "APPROVE", "actor": "op", "reason": "y", "interrupt_id": iid}},
            update={"steering_events": events},
        ),
        runner._cfg(tid),
        durability="sync",
    )
    survived = runner.get_state(tid).values["steering_events"]
    keys = {(e["channel"], e["origin_id"]) for e in survived}
    assert keys == {("telegram", "m1"), ("board", "c9")}  # dedup survived resume
    assert sum(1 for e in survived if (e["channel"], e["origin_id"]) == ("telegram", "m1")) == 1
    assert gs.arbitrate(survived, iid) == "REJECT"  # reject beats approve (deterministic C15)


async def test_dod17_real_edit_snapshot_kill_rehydrate_byte_equal():
    """DoD-17 / SP-R7: a REAL workspace edit survives a kill (worktree teardown) and
    rehydrates BYTE-EQUAL into a FRESH sandbox — the second (filesystem) resume piece,
    independent of DoD-1's conversation counter.

    REPLACES the prior test, which injected digest="deadbeef"*8 via Command(update=) and
    asserted the SAME literal survived the in-memory checkpoint — i.e. it round-tripped a
    STATE STRING through the checkpointer (re-proving DoD-1), and NEVER touched a filesystem.

    HONESTY: WorkspaceSession.close() is the HERMETIC stand-in for a kill; a live mid-execute
    SIGKILL (after >=1 edit, before pytest) is integration-tier — the execute node is one-shot
    with no mid-body interrupt and there is no docker/live container on a macOS dev host."""
    import hashlib
    import re
    import subprocess
    from pathlib import Path

    from app.core.workspace import WorkspaceSession

    repo = Path(__file__).resolve().parents[2]

    def _g(*a):
        return subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True)

    ws = WorkspaceSession.create(repo_dir=repo, base_ref="HEAD", thread_id="dod17")
    assert ws.ok
    # >=1 REAL file edit (a secret-free sentinel under an existing dir).
    edited = ws.ws_dir / "app" / "__spr7_resume.txt"
    text = "# sp-r7 resume sentinel\nVALUE = 42\n"
    edited.write_text(text)
    pre = edited.read_bytes()

    ref = ws.snapshot(thread_id="dod17", node_id="n0")
    assert ref and ref["kind"] == "branch"
    assert ref["ref"].startswith("refs/aa-snapshots/") and len(ref["digest"]) == 64
    orig = ws.ws_dir
    ws.close()  # ── THE KILL ──
    assert not orig.exists()

    rs = WorkspaceSession.rehydrate(repo_dir=repo, ref=ref["ref"], thread_id="dod17")
    try:
        assert rs.ok
        # (i) rehydrated sandbox id (dir) != original
        assert rs.ws_dir != orig and rs.ws_dir.exists()
        # (ii) edited file BYTE-EQUAL to pre-kill
        assert (rs.ws_dir / "app" / "__spr7_resume.txt").read_bytes() == pre
        # (iii) zero secret material on the rehydrated FS (controlled sentinel)
        assert not re.search(
            r"sk-|AKIA|PRIVATE KEY", (rs.ws_dir / "app" / "__spr7_resume.txt").read_text()
        )
        # (iv) the digest is a re-derivable CONTENT address, not a synthetic placeholder
        tree = _g("rev-parse", ref["ref"] + "^{tree}").stdout.strip()
        manifest = _g("ls-tree", "-r", "--full-tree", tree).stdout
        assert hashlib.sha256(manifest.encode("utf-8")).hexdigest() == ref["digest"]
    finally:
        rs.close()
        _g("update-ref", "-d", ref["ref"])  # never leak the snapshot ref
        _g("worktree", "prune")


# ── SP-06: eval_gate as SHIP PRECONDITION ───────────────────────────────────
def test_route_after_eval_gate_is_fail_closed():
    """FAIL-CLOSED (C9): proceed to ship_gate ONLY on an explicit passing verdict; a failed,
    ABSENT, or MALFORMED verdict all BLOCK ship → __halt__ (an unverified change never ships)."""
    from app.core.graph import _route_after_eval_gate

    assert _route_after_eval_gate({"eval_verdict": {"passed": True}}) == "ship_gate"
    assert _route_after_eval_gate({"eval_verdict": {"passed": False}}) == "__halt__"
    assert _route_after_eval_gate({}) == "__halt__"  # no verdict → fail-closed → block
    assert _route_after_eval_gate({"eval_verdict": {}}) == "__halt__"  # malformed → block
    assert _route_after_eval_gate({"eval_verdict": {"passed": "yes"}}) == "__halt__"  # not True


def test_allowed_globs_from_plan_unions_node_paths():
    from app.core.graph import _allowed_globs_from_plan

    assert _allowed_globs_from_plan(None) == []
    plan = {
        "nodes": [
            {"allowed_paths": ["app/x.py", "app/y.py"]},
            {"allowed_paths": ["app/y.py", "lib/z.py"]},
        ],
        "edges": [],
    }
    assert _allowed_globs_from_plan(plan) == ["app/x.py", "app/y.py", "lib/z.py"]


async def test_eval_gate_passes_on_empty_diff_and_records_verdict():
    """Walking skeleton: execute reports no diff → eval_gate's scope verdict trivially PASSES,
    the run reaches ship_gate, and the verdict is recorded in state."""
    runner = SpineRunner(InMemoryCheckpointer(), capability=_stub_capability())
    tid = "goal-eval-pass"
    r1 = await runner.start(thread_id=tid, goal="ship a hello-world endpoint")
    r2 = await runner.resume(
        thread_id=tid,
        interrupt_id=r1["__interrupt__"][0].id,
        decision={"verb": "APPROVE", "actor": "op", "reason": "lgtm"},
    )
    assert runner.get_state(tid).next == ("ship_gate",)  # eval_gate passed → reached ship_gate
    assert r2["changed_paths"] == []  # execute AUTHORITATIVELY produced an empty diff (C9)
    assert r2["eval_verdict"]["passed"] is True
    assert r2["eval_verdict"]["gate"] == "SP-06.scope-root"
    assert r2["eval_verdict"]["violations"] == []
    assert any("eval_gate passed=True" in a for a in r2["audit"])


async def test_eval_gate_node_fails_verdict_and_router_blocks_on_out_of_scope_diff():
    """SP-06 ship-precondition NON-VACUOUS proof, exercised at the node level: the skeleton's
    execute cannot produce a real diff until the SP-05 drive, so we drive the REAL eval_gate
    node + router directly. An out-of-scope changed_paths against the locked plan's allowed
    scope yields a FAILING verdict, and the router turns that into a ship BLOCK (__halt__);
    an in-scope-only diff passes and proceeds to ship_gate."""
    from app.core.graph import _build_nodes, _route_after_eval_gate

    eval_gate = _build_nodes(_stub_capability())["eval_gate"]
    plan = {
        "nodes": [{"allowed_paths": ["app/feature.py", "tests/test_feature.py"]}],
        "edges": [],
    }
    # out-of-scope: app/feature.py is allowed, infra/main.tf is NOT
    bad = await eval_gate(
        {
            "goal": "scoped change",
            "spec_sha": "deadbeef",
            "plan": plan,
            "changed_paths": [["M", "app/feature.py"], ["M", "infra/main.tf"]],
        },
        {},
    )
    assert bad["eval_verdict"]["passed"] is False
    assert [v["path"] for v in bad["eval_verdict"]["violations"]] == ["infra/main.tf"]
    assert _route_after_eval_gate({"eval_verdict": bad["eval_verdict"]}) == "__halt__"

    # in-scope-only diff → passing verdict → proceed to ship_gate
    ok = await eval_gate(
        {"goal": "g", "spec_sha": "d", "plan": plan, "changed_paths": [["M", "app/feature.py"]]},
        {},
    )
    assert ok["eval_verdict"]["passed"] is True
    assert _route_after_eval_gate({"eval_verdict": ok["eval_verdict"]}) == "ship_gate"


# ── C9 review fix: deterministic spec_id (idempotent re-seal) ────────────────
def test_spec_id_is_deterministic_for_idempotent_reseal():
    """C9 finding 3: seal_spec derives spec_id from (thread_id, __pregel_task_id) so a
    crash-retry of the node (same stable ptid) overwrites the SAME SpecStore file rather
    than minting a duplicate spec on each retry."""
    from app.core.graph import _spec_id_for

    a = _spec_id_for("T", "ptid-1")
    assert _spec_id_for("T", "ptid-1") == a  # deterministic: same (tid,ptid) -> same id
    assert _spec_id_for("T", "ptid-2") != a  # distinct ptid -> distinct id
    assert _spec_id_for("U", "ptid-1") != a  # distinct thread -> distinct id


# ── F-2 gap fix: rehydrate workspaces on resume ──────────────────────────────
async def test_resume_rehydrates_workspace_session():
    """Verify that SpineRunner.resume() rehydrates workspace sessions from the checkpoint's
    workspace_ref and workspace_refs and registers them in the reaper."""
    from unittest.mock import MagicMock, patch, ANY
    from app.core.spine_runner import SpineRunner
    from app.adapters.inmemory.checkpointer import InMemoryCheckpointer

    runner = SpineRunner(InMemoryCheckpointer())

    mock_values = {
        "workspace_ref": {"kind": "branch", "ref": "refs/aa-snapshots/t1/n1", "digest": "d1"},
        "workspace_refs": [
            {"kind": "branch", "ref": "refs/aa-snapshots/t1/n1", "digest": "d1"},
            {"kind": "branch", "ref": "refs/aa-snapshots/t1/n2", "digest": "d2"},
        ],
    }
    mock_snapshot = MagicMock()
    mock_snapshot.values = mock_values

    with (
        patch.object(runner, "get_state", return_value=mock_snapshot),
        patch.object(runner._app, "ainvoke", return_value={}),
        patch("app.core.workspace.WorkspaceSession.rehydrate") as mock_rehydrate,
    ):
        ws1 = MagicMock()
        ws1.ok = True
        ws1.snapshot_ref = "refs/aa-snapshots/t1/n1"

        ws2 = MagicMock()
        ws2.ok = True
        ws2.snapshot_ref = "refs/aa-snapshots/t1/n2"

        mock_rehydrate.side_effect = [ws1, ws2]

        await runner.resume(
            thread_id="t1",
            interrupt_id="intr1",
            decision={"verb": "APPROVE"},
        )

        # Verify call count is 2 (once for n1, once for n2)
        assert mock_rehydrate.call_count == 2
        mock_rehydrate.assert_any_call(
            repo_dir=ANY,
            ref="refs/aa-snapshots/t1/n1",
            thread_id="t1",
        )
        mock_rehydrate.assert_any_call(
            repo_dir=ANY,
            ref="refs/aa-snapshots/t1/n2",
            thread_id="t1",
        )

        # Verify they are tracked in reaper
        assert runner._reaper.get_workspace_by_ref("refs/aa-snapshots/t1/n1") == ws1
        assert runner._reaper.get_workspace_by_ref("refs/aa-snapshots/t1/n2") == ws2
