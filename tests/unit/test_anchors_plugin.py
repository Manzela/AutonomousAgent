"""Verify the anchors plugin registers the expected hooks + commands AND
that each slash command actually drives TaskSpec state through spec_store.

The slash command tests pin the storage dir via the
``HERMES_ANCHORS_STORAGE_DIR`` env var so they exercise the same resolver
production uses (rather than monkeypatching internals)."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lib.anchors import (
    _draft_from_intent,
    _get_skip_count,
    _get_spec_store,
    _handle_new_cli,
    _resolve_storage_dir,
    _slash_cancel,
    _slash_confirm,
    _slash_lock,
    _slash_skip,
    register,
)
from lib.anchors.task_spec import TaskSpec


@pytest.fixture
def anchors_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pin the spec storage dir for the duration of one test."""
    monkeypatch.setenv("HERMES_ANCHORS_STORAGE_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_register_wires_session_start_hook():
    ctx = MagicMock()
    register(ctx)
    hook_calls = [c for c in ctx.register_hook.call_args_list if c.args[0] == "on_session_start"]
    assert len(hook_calls) == 1


def test_register_wires_pre_tool_call_hook():
    ctx = MagicMock()
    register(ctx)
    hook_calls = [c for c in ctx.register_hook.call_args_list if c.args[0] == "pre_tool_call"]
    assert len(hook_calls) == 1


def test_register_wires_clarification_slash_commands():
    ctx = MagicMock()
    register(ctx)
    cmd_names = [c.kwargs.get("name") or c.args[0] for c in ctx.register_command.call_args_list]
    for cmd in ("lock", "skip", "cancel", "confirm"):
        assert cmd in cmd_names, f"Missing slash command: /{cmd}"


def test_register_wires_new_cli_command():
    ctx = MagicMock()
    register(ctx)
    cli_calls = [c for c in ctx.register_cli_command.call_args_list]
    cli_names = [c.kwargs.get("name") or c.args[0] for c in cli_calls]
    assert "new" in cli_names, "Missing CLI subcommand: hermes new"


# ---------------------------------------------------------------------------
# Storage resolver
# ---------------------------------------------------------------------------


def test_resolve_storage_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """HERMES_ANCHORS_STORAGE_DIR wins over config/limits.yaml."""
    monkeypatch.setenv("HERMES_ANCHORS_STORAGE_DIR", str(tmp_path / "custom"))
    assert _resolve_storage_dir() == tmp_path / "custom"


# ---------------------------------------------------------------------------
# /lock
# ---------------------------------------------------------------------------


def _seed_draft(anchors_dir: Path, intent: str = "research wholesale CPG analytics") -> TaskSpec:
    """Seed a draft spec via the production-path CLI handler."""
    store = _get_spec_store()
    spec = _draft_from_intent(intent, title="test draft", created_by=42)
    return store.save(spec)


def test_lock_with_no_draft_returns_helpful_message(anchors_dir: Path):
    out = _slash_lock("")
    assert isinstance(out, str)
    assert "no active" in out.lower() or "no draft" in out.lower()
    assert "TODO" not in out


def test_lock_transitions_draft_to_locked(anchors_dir: Path):
    seed = _seed_draft(anchors_dir)
    assert seed.status == "draft"

    out = _slash_lock("")
    assert "Locked" in out
    assert "TODO" not in out

    store = _get_spec_store()
    reloaded = store.load(seed.spec_id)
    assert reloaded.status == "locked"
    # The 6 mandatory fields must be present in the chat response in some form
    assert "intent:" in out
    assert "acceptance:" in out


def test_lock_works_from_draft_locked_state(anchors_dir: Path):
    """If user previously /confirm'd, /lock still finalizes."""
    seed = _seed_draft(anchors_dir)
    _slash_confirm("")
    store = _get_spec_store()
    confirmed = store.load(seed.spec_id)
    assert confirmed.status == "draft_locked"

    _slash_lock("")
    locked = store.load(seed.spec_id)
    assert locked.status == "locked"


def test_lock_picks_most_recent_draft(anchors_dir: Path):
    """Multiple drafts -> /lock acts on the newest one only."""
    import time

    seed_a = _seed_draft(anchors_dir, intent="first draft")
    time.sleep(0.01)  # ensure distinct created_at
    seed_b = _seed_draft(anchors_dir, intent="second draft")

    _slash_lock("")

    store = _get_spec_store()
    assert store.load(seed_a.spec_id).status == "draft"  # untouched
    assert store.load(seed_b.spec_id).status == "locked"


# ---------------------------------------------------------------------------
# /skip
# ---------------------------------------------------------------------------


def test_skip_with_no_draft_returns_helpful_message(anchors_dir: Path):
    out = _slash_skip("")
    assert "no active" in out.lower() or "no clarification" in out.lower()
    assert "TODO" not in out


def test_skip_increments_counter(anchors_dir: Path):
    seed = _seed_draft(anchors_dir)
    store = _get_spec_store()
    spec_id_str = str(seed.spec_id)

    assert _get_skip_count(store, spec_id_str) == 0
    out1 = _slash_skip("")
    assert "Skipped (1/" in out1
    assert _get_skip_count(store, spec_id_str) == 1

    out2 = _slash_skip("")
    assert "Skipped (2/" in out2
    assert _get_skip_count(store, spec_id_str) == 2


def test_skip_reports_budget_exhausted(anchors_dir: Path):
    from lib.anchors.clarification_loop import MAX_CLARIFICATION_QUESTIONS

    _seed_draft(anchors_dir)
    for _ in range(MAX_CLARIFICATION_QUESTIONS - 1):
        _slash_skip("")

    out = _slash_skip("")
    assert "budget exhausted" in out.lower()
    assert "/confirm" in out or "/lock" in out


def test_skip_does_not_advance_locked_specs(anchors_dir: Path):
    """A locked spec is not a candidate for /skip — that round is over."""
    _seed_draft(anchors_dir)
    _slash_lock("")  # draft -> locked

    out = _slash_skip("")
    # Now there's no draft to skip on
    assert "no active" in out.lower() or "no clarification" in out.lower()


# ---------------------------------------------------------------------------
# /cancel (bare)
# ---------------------------------------------------------------------------


def test_cancel_bare_with_no_draft(anchors_dir: Path):
    out = _slash_cancel("")
    assert "no active" in out.lower()
    assert "TODO" not in out


def test_cancel_bare_supersedes_active_draft(anchors_dir: Path):
    seed = _seed_draft(anchors_dir)
    out = _slash_cancel("")
    assert "Cancelled draft" in out

    store = _get_spec_store()
    reloaded = store.load(seed.spec_id)
    assert reloaded.status == "superseded"


def test_cancel_bare_does_not_touch_kanban(anchors_dir: Path):
    """Bare /cancel must NOT consult the kanban bridge (preserves PR #45's dispatch)."""
    from unittest.mock import patch

    _seed_draft(anchors_dir)
    with patch("lib.kanban.telegram_bridge.cancel_card") as mock_cancel:
        out = _slash_cancel("")
        assert mock_cancel.call_count == 0
    assert "TODO" not in out


# ---------------------------------------------------------------------------
# /confirm
# ---------------------------------------------------------------------------


def test_confirm_with_no_draft(anchors_dir: Path):
    out = _slash_confirm("")
    assert "no active" in out.lower()
    assert "TODO" not in out


def test_confirm_transitions_draft_to_draft_locked(anchors_dir: Path):
    seed = _seed_draft(anchors_dir)
    out = _slash_confirm("")
    assert "Confirmed" in out
    assert "draft_locked" in out

    store = _get_spec_store()
    reloaded = store.load(seed.spec_id)
    assert reloaded.status == "draft_locked"


def test_confirm_on_already_draft_locked_spec_hints_to_lock(anchors_dir: Path):
    """If user runs /confirm twice, second call should hint to /lock."""
    _seed_draft(anchors_dir)
    _slash_confirm("")  # draft -> draft_locked

    out2 = _slash_confirm("")
    assert "/lock" in out2
    assert "already" in out2.lower() or "draft_locked" in out2


# ---------------------------------------------------------------------------
# hermes new <intent> CLI
# ---------------------------------------------------------------------------


def test_new_cli_creates_draft_with_intent(anchors_dir: Path, capsys):
    args = argparse.Namespace(intent="audit security posture", title=None, created_by=0)
    rc = _handle_new_cli(args)
    assert rc == 0

    captured = capsys.readouterr()
    assert "Created draft TaskSpec" in captured.out
    assert "audit security posture" in captured.out
    assert "TODO" not in captured.out

    store = _get_spec_store()
    drafts = [s for s in store.list_active() if s.status == "draft"]
    assert len(drafts) == 1
    assert drafts[0].intent == "audit security posture"
    assert drafts[0].created_by == 0


def test_new_cli_rejects_empty_intent(anchors_dir: Path, capsys):
    args = argparse.Namespace(intent="   ", title=None, created_by=0)
    rc = _handle_new_cli(args)
    assert rc == 2
    captured = capsys.readouterr()
    assert "non-empty" in captured.out.lower() or "non-empty" in captured.err.lower()


def test_new_cli_custom_title_and_created_by(anchors_dir: Path):
    args = argparse.Namespace(
        intent="lengthy intent describing what we want to research about CPG analytics",
        title="Custom title",
        created_by=12345,
    )
    rc = _handle_new_cli(args)
    assert rc == 0

    store = _get_spec_store()
    drafts = [s for s in store.list_active() if s.status == "draft"]
    assert len(drafts) == 1
    assert drafts[0].title == "Custom title"
    assert drafts[0].created_by == 12345


def test_new_cli_default_title_truncates_intent(anchors_dir: Path):
    long = "a" * 200
    args = argparse.Namespace(intent=long, title=None, created_by=0)
    rc = _handle_new_cli(args)
    assert rc == 0

    store = _get_spec_store()
    drafts = [s for s in store.list_active() if s.status == "draft"]
    assert len(drafts[0].title) <= 60


# ---------------------------------------------------------------------------
# End-to-end flow: new -> confirm -> lock
# ---------------------------------------------------------------------------


def test_full_flow_new_confirm_lock(anchors_dir: Path):
    args = argparse.Namespace(intent="ship slash commands", title=None, created_by=0)
    assert _handle_new_cli(args) == 0

    store = _get_spec_store()
    [draft] = [s for s in store.list_active() if s.status == "draft"]
    assert draft.status == "draft"

    _slash_confirm("")
    reloaded = store.load(draft.spec_id)
    assert reloaded.status == "draft_locked"

    _slash_lock("")
    reloaded2 = store.load(draft.spec_id)
    assert reloaded2.status == "locked"
