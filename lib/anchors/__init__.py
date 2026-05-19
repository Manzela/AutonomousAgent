"""TaskSpec + clarification loop — P1-1 plugin entry point."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from uuid import uuid4

from lib.anchors.spec_store import SpecStore
from lib.anchors.task_spec import Scope, SpecStatus, TaskSpec

logger = logging.getLogger(__name__)

# Repo root resolved relative to this file (lib/anchors/__init__.py → repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO_ROOT / "config" / "limits.yaml"

# Fallback storage dir if limits.yaml is unreadable / missing the key.
# Repo-relative so tests / local dev don't need /data/specs to exist as root.
_FALLBACK_STORAGE_DIR = _REPO_ROOT / "data" / "specs"

# Placeholder used by spec_store when a sha hasn't been stamped yet. The
# real sha is computed on save via SpecStore.save(...).
_SHA_PLACEHOLDER = "placeholder"


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _resolve_storage_dir() -> Path:
    """Resolve ``anchors.spec_storage_dir`` from config/limits.yaml.

    Falls back to a repo-relative path (``<repo>/data/specs``) on any read
    failure, so the plugin remains usable in local dev / unit tests where
    ``/data/specs`` is not writable. The HERMES_ANCHORS_STORAGE_DIR env var
    wins over both — useful for tests that monkeypatch the storage root.
    """
    env_override = os.getenv("HERMES_ANCHORS_STORAGE_DIR", "").strip()
    if env_override:
        return Path(env_override)

    try:
        import yaml  # local import keeps cold-import cheap
    except ImportError:
        return _FALLBACK_STORAGE_DIR

    if not _CONFIG_PATH.exists():
        return _FALLBACK_STORAGE_DIR

    try:
        cfg = yaml.safe_load(_CONFIG_PATH.read_text()) or {}
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("anchors: failed to parse limits.yaml (%s); using fallback", exc)
        return _FALLBACK_STORAGE_DIR

    raw = ((cfg.get("anchors") or {}).get("spec_storage_dir") or "").strip()
    if not raw:
        return _FALLBACK_STORAGE_DIR

    p = Path(raw)
    # Absolute /data/specs is the production target inside the container. In
    # local/dev environments without root rights, fall back transparently.
    if p.is_absolute():
        try:
            p.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            logger.info(
                "anchors: configured spec_storage_dir %s not writable; " "falling back to %s",
                p,
                _FALLBACK_STORAGE_DIR,
            )
            return _FALLBACK_STORAGE_DIR
    return p


def _get_spec_store() -> SpecStore:
    """Return a SpecStore rooted at the resolved storage dir."""
    return SpecStore(_resolve_storage_dir())


def _active_drafts(
    store: SpecStore,
    statuses: Iterable[SpecStatus] = ("draft", "draft_locked"),
) -> list[TaskSpec]:
    """Return active specs filtered by status, newest first."""
    wanted = set(statuses)
    specs = [s for s in store.list_active() if s.status in wanted]
    specs.sort(key=lambda s: s.created_at, reverse=True)
    return specs


def _most_recent_draft(
    store: SpecStore,
    statuses: Iterable[SpecStatus] = ("draft", "draft_locked"),
) -> Optional[TaskSpec]:
    """Return the most-recent active spec matching given statuses, or None."""
    specs = _active_drafts(store, statuses=statuses)
    return specs[0] if specs else None


def _format_spec_summary(spec: TaskSpec) -> str:
    """Short human-readable one-liner of a spec — for chat responses."""
    title = spec.title or "(no title)"
    short_id = str(spec.spec_id)[:8]
    return f"#{short_id} — {title}"


# ---------------------------------------------------------------------------
# Skip-counter sidecar (per-draft)
# ---------------------------------------------------------------------------
#
# The clarification budget lives in lib/anchors/clarification_loop.py as an
# in-memory ClarificationState. There's no session-aware driver yet (that's a
# higher-layer concern), so `/skip` persists its count via a tiny sidecar
# file next to the spec: ``<spec_id>.skips`` contains the integer count.
# When a session-aware loop arrives, it can read this file to seed
# ClarificationState.questions_asked.


def _skips_path(store: SpecStore, spec_id_str: str) -> Path:
    return store.root / f"{spec_id_str}.skips"


def _get_skip_count(store: SpecStore, spec_id_str: str) -> int:
    p = _skips_path(store, spec_id_str)
    if not p.exists():
        return 0
    try:
        return int(p.read_text().strip() or "0")
    except (ValueError, OSError):
        return 0


def _record_skip(store: SpecStore, spec_id_str: str) -> int:
    """Increment the skip counter for a spec and return the new value."""
    new_count = _get_skip_count(store, spec_id_str) + 1
    p = _skips_path(store, spec_id_str)
    tmp = p.with_suffix(".skips.tmp")
    tmp.write_text(str(new_count))
    os.replace(tmp, p)
    return new_count


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def _on_session_start(session_id: str = "", **_: Any) -> None:
    """Load the active spec for the session if one exists.

    Resolution order: session_metadata.active_spec_id → most-recent locked
    spec for the user → no active spec (fresh slate).
    """
    # Best-effort: just log whether a draft/locked spec exists for the
    # session. Full session_metadata.active_spec_id wiring is owned by the
    # Hermes-side session loader (P1-1 task 9, not in scope here).
    try:
        store = _get_spec_store()
        latest = _most_recent_draft(store, statuses=("draft", "draft_locked", "locked"))
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("anchors: on_session_start spec lookup failed: %s", exc)
        latest = None
    if latest is None:
        logger.debug("anchors: on_session_start session=%s — no active spec", session_id)
    else:
        logger.debug(
            "anchors: on_session_start session=%s — active spec %s status=%s",
            session_id,
            latest.spec_id,
            latest.status,
        )


def _on_pre_tool_call(
    tool_name: str = "",
    args: dict | None = None,
    **_: Any,
) -> dict | None:
    """Drive the clarification loop on the first user-message-style tool call.

    If no active spec is locked AND the inbound message looks like a project
    intent, redirect the agent into the clarification loop instead of letting
    the tool run. Returns a block dict to short-circuit, or None to allow.
    """
    # TODO(P1-1 task 6): wire heuristic + state machine integration. The
    # state machine + intent classifier exist; the missing piece is the
    # session-aware adapter that hooks the user-message tool call. Owned by
    # the Hermes-integration follow-up, not this PR.
    return None


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


def _slash_lock(raw_args: str) -> str:
    """`/lock` — force-lock the active draft TaskSpec.

    Operates on the most-recent ``draft`` or ``draft_locked`` spec. No-op
    (with explanatory message) if no draft exists.
    """
    store = _get_spec_store()
    draft = _most_recent_draft(store, statuses=("draft", "draft_locked"))
    if draft is None:
        return "No active draft TaskSpec to lock. Start one with `hermes new <intent>`."

    if draft.status == "locked":  # pragma: no cover — list filter excludes
        return f"Spec {_format_spec_summary(draft)} is already locked."

    locked = draft.model_copy(update={"status": "locked"})
    saved = store.save(locked)
    return (
        f"Locked {_format_spec_summary(saved)}.\n"
        f"  intent: {saved.intent}\n"
        f"  acceptance: {len(saved.acceptance_criteria)} criteria\n"
        f"  scope in: {len(saved.scope.in_scope)} / out: {len(saved.scope.out_of_scope)}\n"
        f"  sha: {saved.spec_sha[:12]}..."
    )


def _slash_skip(raw_args: str) -> str:
    """`/skip` — skip the current clarification question (counts toward budget).

    Increments a per-draft skip counter (sidecar file next to the spec). If
    the count meets/exceeds ``MAX_CLARIFICATION_QUESTIONS``, hints that the
    spec is now eligible for ``/lock``.
    """
    from lib.anchors.clarification_loop import MAX_CLARIFICATION_QUESTIONS

    store = _get_spec_store()
    draft = _most_recent_draft(store, statuses=("draft",))
    if draft is None:
        return "No active clarification round to skip. Start one with `hermes new <intent>`."

    count = _record_skip(store, str(draft.spec_id))
    summary = _format_spec_summary(draft)
    remaining = MAX_CLARIFICATION_QUESTIONS - count

    if remaining <= 0:
        return (
            f"Skipped ({count}/{MAX_CLARIFICATION_QUESTIONS}) on {summary}. "
            f"Question budget exhausted — run `/confirm` or `/lock` to advance."
        )
    return (
        f"Skipped ({count}/{MAX_CLARIFICATION_QUESTIONS}) on {summary}. "
        f"{remaining} question(s) remain in the budget."
    )


def _slash_cancel(raw_args: str) -> str:
    """`/cancel` (no arg) — abandon the current draft spec.

    With an argument it dispatches to the P1-5 Kanban bridge to archive
    the named card. Local import keeps the kanban module out of the
    fast path when only the P1-1 draft flow is in play.
    """
    arg = raw_args.strip()
    if arg:
        # Local import: avoid forcing eager import of the kanban package
        # (which lazy-loads Hermes' kanban_db) when only the draft flow is used.
        from lib.kanban import telegram_bridge

        ok = telegram_bridge.cancel_card(arg)
        if ok:
            return f"Cancelled card {arg}."
        return f"Could not cancel card {arg} (not found, already archived, or unavailable)."

    store = _get_spec_store()
    draft = _most_recent_draft(store, statuses=("draft", "draft_locked"))
    if draft is None:
        return "No active draft TaskSpec to cancel."

    cancelled = draft.model_copy(update={"status": "superseded"})
    saved = store.save(cancelled)
    return f"Cancelled draft {_format_spec_summary(saved)} (status -> superseded)."


def _slash_confirm(raw_args: str) -> str:
    """`/confirm` — transition the active draft → draft_locked.

    This is one step before ``/lock``. ``/lock`` skips this state.
    """
    store = _get_spec_store()
    draft = _most_recent_draft(store, statuses=("draft",))
    if draft is None:
        # Maybe there's already a draft_locked one — tell the user what they likely meant.
        already_locked = _most_recent_draft(store, statuses=("draft_locked",))
        if already_locked is not None:
            return (
                f"Spec {_format_spec_summary(already_locked)} is already in draft_locked. "
                f"Use `/lock` to finalize."
            )
        return "No active draft TaskSpec to confirm. Start one with `hermes new <intent>`."

    confirmed = draft.model_copy(update={"status": "draft_locked"})
    saved = store.save(confirmed)
    return (
        f"Confirmed {_format_spec_summary(saved)} (status -> draft_locked). "
        f"Run `/lock` to finalize, or send another message to keep editing."
    )


# ---------------------------------------------------------------------------
# CLI: `hermes new <intent>`
# ---------------------------------------------------------------------------


def _setup_new_cli(subparser: argparse.ArgumentParser) -> None:
    """`hermes new <intent>` — operator-side spec creation (CLI, not Telegram)."""
    subparser.add_argument("intent", help="Free-form intent string for the new TaskSpec.")
    subparser.add_argument(
        "--title",
        default=None,
        help="Optional title (defaults to first 60 chars of intent).",
    )
    subparser.add_argument(
        "--created-by",
        type=int,
        default=0,
        help="Telegram user_id of the creator (defaults to 0 for CLI-only flows).",
    )


def _draft_from_intent(intent: str, *, title: str | None = None, created_by: int = 0) -> TaskSpec:
    """Build a minimal draft TaskSpec from an intent string.

    Placeholders for the 5 fields the clarification loop is meant to fill in
    (acceptance_criteria, scope, success_metrics) are valid but explicitly
    marked TBD so a downstream lock without clarification fails closed.
    """
    intent_clean = intent.strip()
    if not intent_clean:
        raise ValueError("intent must be non-empty")
    derived_title = (title or intent_clean.splitlines()[0])[:60].strip() or "Untitled draft"

    return TaskSpec(
        title=derived_title,
        intent=intent_clean,
        acceptance_criteria=["TBD - populated via clarification loop"],
        scope=Scope(
            in_scope=["TBD - populated via clarification loop"],
            out_of_scope=["TBD - populated via clarification loop"],
        ),
        success_metrics=["TBD - populated via clarification loop"],
        constraints=[],
        spec_id=uuid4(),
        spec_sha=_SHA_PLACEHOLDER,
        created_at=datetime.now(timezone.utc),
        created_by=created_by,
        status="draft",
    )


def _handle_new_cli(args: argparse.Namespace) -> int:
    """Handler for ``hermes new <intent>``."""
    intent = (args.intent or "").strip()
    if not intent:
        print("error: intent must be non-empty", flush=True)
        return 2

    store = _get_spec_store()
    spec = _draft_from_intent(
        intent,
        title=getattr(args, "title", None),
        created_by=getattr(args, "created_by", 0),
    )
    saved = store.save(spec)
    print(
        f"Created draft TaskSpec {saved.spec_id} (status=draft)\n"
        f"  title: {saved.title}\n"
        f"  intent: {saved.intent}\n"
        f"  storage: {store.root}",
        flush=True,
    )
    return 0


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Plugin entry point — wires hooks + slash commands + CLI subcommand."""
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_command(
        "lock", handler=_slash_lock, description="Force-lock the active draft TaskSpec."
    )
    ctx.register_command(
        "skip", handler=_slash_skip, description="Skip the current clarification question."
    )
    ctx.register_command(
        "cancel",
        handler=_slash_cancel,
        description="Abandon the active draft (no arg) or cancel a card (with id, P1-5).",
    )
    ctx.register_command(
        "confirm", handler=_slash_confirm, description="Confirm a draft_locked TaskSpec -> locked."
    )
    ctx.register_cli_command(
        name="new",
        help="Create a draft TaskSpec from an intent string (operator-side).",
        setup_fn=_setup_new_cli,
        handler_fn=_handle_new_cli,
        description="Operator-side TaskSpec creation. Telegram-side equivalent is implicit (any non-slash inbound message).",
    )
