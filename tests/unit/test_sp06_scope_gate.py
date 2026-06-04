"""SP-06 (slice 1) red-green oracles — the hermetic scope-root gate.

The deterministic hard root of the PRD-conformance eval gate (PRD §6 SP-06 / 2.6): a PR
whose diff touches a path OUTSIDE the locked TaskSpec's derived allowed_paths (excluding
the shared net-new test set) FAILS deterministically — independent of any LLM judge leaf
(deferred). Pure stdlib + pydantic; imports NEITHER deepeval NOR inspect_ai (so it can
never default-route a judge to OpenAI — the nightly cluster-b trap).

NON-VACUOUS: on origin/main no scope gate exists (out-of-scope changes merge freely = RED);
GREEN once app/core/eval_gate.py + scripts/ci/sp06_scope_gate.py land.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.core.eval_gate import ScopeVerdict, load_locked_plan, scope_root_verdict
from lib.anchors.spec_store import SpecStore
from lib.anchors.task_spec import Scope, TaskSpec


def _locked_spec(store: SpecStore) -> TaskSpec:
    """A locked TaskSpec whose criteria name explicit paths, so the InMemoryDecomposer
    derives deterministic allowed_paths = {app/core/widget.py, tests/unit/test_widget.py}."""
    spec = TaskSpec(
        title="widget",
        intent="add a widget to app/core",
        acceptance_criteria=[
            "implement app/core/widget.py with the widget class",
            "verify tests/unit/test_widget.py covers the widget",
        ],
        scope=Scope(in_scope=["app/core"], out_of_scope=["docs", "infra"]),
        success_metrics=["widget tests green"],
        spec_id=uuid4(),
        spec_sha="0" * 64,
        created_at=datetime.now(timezone.utc),
        created_by=0,
    )
    return store.save(spec.model_copy(update={"status": "locked"}))


def _allowed_globs(store: SpecStore, sealed: TaskSpec) -> list[str]:
    plan = load_locked_plan(store.root, str(sealed.spec_id), sealed.spec_sha)
    globs: list[str] = []
    for node in plan["nodes"]:
        globs.extend(node["allowed_paths"])
    return globs


# ── scope-root RED/GREEN ────────────────────────────────────────────────────
def test_out_of_scope_path_fails_deterministically(tmp_path):
    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    allowed = _allowed_globs(store, sealed)
    changed = [("M", "app/core/widget.py"), ("M", "docs/unrelated.md")]
    v = scope_root_verdict(changed, allowed, base="b", head="h", spec_sha=sealed.spec_sha)
    assert v.passed is False
    assert [viol["path"] for viol in v.violations] == ["docs/unrelated.md"]
    assert v.violations[0]["reason"] == "out-of-scope"


def test_in_scope_diff_passes(tmp_path):
    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    allowed = _allowed_globs(store, sealed)
    changed = [("M", "app/core/widget.py"), ("A", "tests/unit/test_widget.py")]
    v = scope_root_verdict(changed, allowed, base="b", head="h", spec_sha=sealed.spec_sha)
    assert v.passed is True
    assert v.violations == []
    assert v.tests_added is True


# ── net-new test exclusion (the 2.6 carve-out) ──────────────────────────────
def test_net_new_test_excluded(tmp_path):
    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    allowed = _allowed_globs(store, sealed)
    # a net-new test NOT named by any allowed glob — excluded because it is net-new (status A).
    changed = [("M", "app/core/widget.py"), ("A", "tests/unit/test_brand_new.py")]
    v = scope_root_verdict(changed, allowed, base="b", head="h", spec_sha=sealed.spec_sha)
    assert v.passed is True
    assert "tests/unit/test_brand_new.py" in v.net_new_tests_excluded


def test_modified_preexisting_test_not_auto_excluded(tmp_path):
    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    allowed = _allowed_globs(store, sealed)
    # a MODIFIED (not net-new) pre-existing test that no allowed glob names -> out-of-scope.
    changed = [("M", "tests/unit/test_someone_elses.py")]
    v = scope_root_verdict(changed, allowed, base="b", head="h", spec_sha=sealed.spec_sha)
    assert v.passed is False
    assert v.violations[0]["path"] == "tests/unit/test_someone_elses.py"


# ── locked-spec authority + tamper-evidence ─────────────────────────────────
def test_unsealed_spec_rejected(tmp_path):
    store = SpecStore(tmp_path)
    draft = TaskSpec(
        title="d",
        intent="d",
        acceptance_criteria=["implement app/core/x.py"],
        scope=Scope(in_scope=["app/core"], out_of_scope=["docs"]),
        success_metrics=["x"],
        spec_id=uuid4(),
        spec_sha="0" * 64,
        created_at=datetime.now(timezone.utc),
        created_by=0,
    )
    sealed_draft = store.save(draft)  # status stays 'draft'
    with pytest.raises(ValueError, match="locked"):
        load_locked_plan(store.root, str(sealed_draft.spec_id), sealed_draft.spec_sha)


def test_spec_sha_mismatch_fails(tmp_path):
    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    with pytest.raises(ValueError, match="sha"):
        load_locked_plan(store.root, str(sealed.spec_id), "deadbeef" * 8)


# ── symlink-escape hardening (C9) ───────────────────────────────────────────
def test_symlink_changed_path_is_violation_even_if_in_scope(tmp_path):
    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    allowed = _allowed_globs(store, sealed)
    # app/core/widget.py is in-scope, but as a symlink it can point out-of-scope -> violation.
    v = scope_root_verdict(
        [("A", "app/core/widget.py")],
        allowed,
        base="b",
        head="h",
        spec_sha=sealed.spec_sha,
        symlink_paths=["app/core/widget.py"],
    )
    assert v.passed is False
    assert v.violations[0]["reason"] == "symlink-escapes-scope"


def test_symlink_test_path_not_excused_by_net_new_carveout(tmp_path):
    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    allowed = _allowed_globs(store, sealed)
    # a net-new test path that is a symlink must NOT be excused by the net-new carve-out.
    v = scope_root_verdict(
        [("A", "tests/unit/test_evil.py")],
        allowed,
        base="b",
        head="h",
        spec_sha=sealed.spec_sha,
        symlink_paths=["tests/unit/test_evil.py"],
    )
    assert v.passed is False
    assert v.violations[0]["reason"] == "symlink-escapes-scope"
    assert "tests/unit/test_evil.py" not in v.net_new_tests_excluded


def test_entrypoint_flags_real_symlink(tmp_path, monkeypatch):
    from scripts.ci import sp06_scope_gate as gate

    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    # a real symlink at an otherwise in-scope path, on the checked-out head (repo_root=tmp_path)
    (tmp_path / "app" / "core").mkdir(parents=True)
    (tmp_path / "secret_target").write_text("out of scope")
    (tmp_path / "app" / "core" / "widget.py").symlink_to(tmp_path / "secret_target")
    monkeypatch.setattr(
        gate, "_git_name_status", lambda base, head, root: [("A", "app/core/widget.py")]
    )
    rc = gate.main(
        [
            "--base",
            "b",
            "--head",
            "h",
            "--spec-id",
            str(sealed.spec_id),
            "--spec-sha",
            sealed.spec_sha,
            "--store-root",
            str(tmp_path),
            "--repo-root",
            str(tmp_path),
            "--out",
            str(tmp_path / "verdict.json"),
        ]
    )
    assert rc == 1


# ── machine-readable verdict ────────────────────────────────────────────────
def test_verdict_json_schema(tmp_path):
    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    allowed = _allowed_globs(store, sealed)
    v = scope_root_verdict(
        [("M", "app/core/widget.py")], allowed, base="bbb", head="hhh", spec_sha=sealed.spec_sha
    )
    doc = json.loads(v.to_json())
    assert doc["gate"] == "SP-06.scope-root"
    assert set(doc) == {
        "gate",
        "spec_sha",
        "base",
        "head",
        "passed",
        "violations",
        "allowed_globs",
        "net_new_tests_excluded",
        "tests_added",
        "mutation_score",
        "mutation_score_passed",
    }
    assert doc["base"] == "bbb" and doc["head"] == "hhh"
    assert json.dumps(doc, sort_keys=True)  # round-trips, sorted-key stable


def test_mutation_gate_floor_enforcement(tmp_path, monkeypatch):
    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    allowed = _allowed_globs(store, sealed)
    changed = [("M", "app/core/widget.py")]

    # 1. Enforced below floor: 75% score must FAIL
    monkeypatch.setenv("MOCK_MUTMUT_SCORE", "75.0")
    v = scope_root_verdict(changed, allowed, base="b", head="h", spec_sha=sealed.spec_sha)
    assert v.passed is False
    assert v.mutation_score == 75.0
    assert v.mutation_score_passed is False
    assert any(viol["path"] == "mutation-gate" for viol in v.violations)

    # 2. Enforced above floor: 85% score must PASS
    monkeypatch.setenv("MOCK_MUTMUT_SCORE", "85.0")
    v = scope_root_verdict(changed, allowed, base="b", head="h", spec_sha=sealed.spec_sha)
    assert v.passed is True
    assert v.mutation_score == 85.0
    assert v.mutation_score_passed is True
    assert not any(viol["path"] == "mutation-gate" for viol in v.violations)


# ── hermeticity (no LLM import → no OpenAI default trap) ─────────────────────
def test_eval_gate_is_hermetic_no_llm_import():
    # Import in a FRESH interpreter so the assertion isn't polluted by other test-session
    # imports — proves the gate's OWN import chain pulls neither deepeval nor inspect_ai.
    code = (
        "import sys, app.core.eval_gate, scripts.ci.sp06_scope_gate; "
        "assert 'deepeval' not in sys.modules, 'deepeval imported by the gate'; "
        "assert 'inspect_ai' not in sys.modules, 'inspect_ai imported by the gate'; "
        "print('OK')"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ── entrypoint exit codes (git diff reader is monkeypatched) ────────────────
def test_entrypoint_exits_nonzero_on_violation(tmp_path, monkeypatch, capsys):
    from scripts.ci import sp06_scope_gate as gate

    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    monkeypatch.setattr(
        gate, "_git_name_status", lambda base, head, root: [("M", "docs/unrelated.md")]
    )
    rc = gate.main(
        [
            "--base",
            "b",
            "--head",
            "h",
            "--spec-id",
            str(sealed.spec_id),
            "--spec-sha",
            sealed.spec_sha,
            "--store-root",
            str(tmp_path),
            "--out",
            str(tmp_path / "verdict.json"),
        ]
    )
    assert rc == 1
    assert (tmp_path / "verdict.json").exists()


def test_entrypoint_exits_zero_when_in_scope(tmp_path, monkeypatch):
    from scripts.ci import sp06_scope_gate as gate

    store = SpecStore(tmp_path)
    sealed = _locked_spec(store)
    monkeypatch.setattr(
        gate,
        "_git_name_status",
        lambda base, head, root: [("M", "app/core/widget.py"), ("A", "tests/unit/test_widget.py")],
    )
    rc = gate.main(
        [
            "--base",
            "b",
            "--head",
            "h",
            "--spec-id",
            str(sealed.spec_id),
            "--spec-sha",
            sealed.spec_sha,
            "--store-root",
            str(tmp_path),
            "--out",
            str(tmp_path / "verdict.json"),
        ]
    )
    assert rc == 0


def test_scope_verdict_dataclass_shape():
    v = ScopeVerdict(
        gate="SP-06.scope-root",
        spec_sha="x",
        base="b",
        head="h",
        passed=True,
        violations=[],
        allowed_globs=["app/core/x.py"],
        net_new_tests_excluded=[],
        tests_added=False,
    )
    assert v.passed and v.to_json()
