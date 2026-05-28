"""P1-3 Checkpoint scanner + state rehydrator.

On container start the durability plugin's ``on_session_start`` hook calls
``rehydrate_latest_for_session(ctx)`` to scan ``/data/checkpoints/`` for any
session that has NOT been marked DONE/ARCHIVED, then rehydrates the latest
non-corrupted checkpoint for that session so the agent loop can pick up
mid-flight.

Conventions:
- DONE sentinel: a file named ``.done`` inside the session's checkpoint dir.
  Sessions that complete normally drop this file; resume skips them.
- Most-recent incomplete session wins when several exist (by mtime of the
  highest-step checkpoint file). Real deployments will typically have exactly
  one incomplete session (the one whose container just restarted).
- Corruption policy: ``on_corruption: skip_and_warn`` per design-alignment
  spec L282. If the highest-step JSON fails to parse, we fall back to the
  next-latest file in the same session.

Returns ``None`` when there's nothing to resume — that includes:
- ``autoresume_enabled=False`` in ``config/limits.yaml``
- the checkpoint root doesn't exist or is empty
- every session is marked DONE
- the resume-most-recent function is called with ``ctx`` flagged off

Reuses the JSON shape written by ``lib.durability.checkpoint.Checkpoint``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lib.durability.checkpoint import DEFAULT_ROOT, _STEP_FILENAME_RE

DONE_SENTINEL = ".done"


def _list_session_dirs(root: Path) -> List[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir()]


def _list_step_files(session_dir: Path) -> List[Tuple[int, Path]]:
    """Return [(step_index, path), ...] sorted by step_index ascending."""
    out: List[Tuple[int, Path]] = []
    if not session_dir.exists():
        return out
    for p in session_dir.iterdir():
        m = _STEP_FILENAME_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), p))
    out.sort(key=lambda t: t[0])
    return out


def _is_done(session_dir: Path) -> bool:
    return (session_dir / DONE_SENTINEL).exists()


def _load_json_or_none(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def rehydrate_for_session(
    session_id: str, root_dir: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """Rehydrate the latest non-corrupted checkpoint for ``session_id``.

    Returns ``None`` if:
    - session dir doesn't exist
    - session is marked DONE
    - no parseable checkpoint files exist (after walking back from highest step)
    """
    root = Path(root_dir) if root_dir is not None else DEFAULT_ROOT
    session_dir = root / session_id
    if not session_dir.exists() or _is_done(session_dir):
        return None

    steps = _list_step_files(session_dir)
    if not steps:
        return None

    # Walk from highest step down; first parseable file wins (skip_and_warn).
    for _step, path in reversed(steps):
        payload = _load_json_or_none(path)
        if payload is not None:
            return payload
    return None


def _most_recent_incomplete_session(root: Path) -> Optional[str]:
    """Return the session_id whose highest-step checkpoint has the newest mtime,
    skipping sessions marked DONE."""
    best: Tuple[float, str] | None = None
    for session_dir in _list_session_dirs(root):
        if _is_done(session_dir):
            continue
        steps = _list_step_files(session_dir)
        if not steps:
            continue
        _highest_step, latest_path = steps[-1]
        try:
            mtime = os.path.getmtime(latest_path)
        except OSError:
            continue
        if best is None or mtime > best[0]:
            best = (mtime, session_dir.name)
    return best[1] if best else None


def rehydrate_latest_for_session(
    ctx: Any = None,
    root_dir: Optional[Path] = None,
    autoresume_enabled: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Plugin entrypoint — wired from ``lib/durability/__init__.py``.

    Behaviour:
    1. If ``autoresume_enabled=False`` (or the config flag is off) → return ``None``.
    2. Scan ``root_dir`` for sessions with no ``.done`` sentinel.
    3. Pick the session whose latest checkpoint has the newest mtime
       (covers the common "one container, one incomplete session" case
       and also breaks ties when a host has resumed multiple times).
    4. Rehydrate that session via ``rehydrate_for_session``.

    The function intentionally swallows IO/parse errors and returns ``None`` so
    a corrupt checkpoint dir can never block agent startup; the agent simply
    begins a fresh session and the operator can investigate offline.
    """
    if autoresume_enabled is False:
        return None
    if autoresume_enabled is None:
        autoresume_enabled = _autoresume_enabled_from_config()
    if not autoresume_enabled:
        return None

    root = Path(root_dir) if root_dir is not None else DEFAULT_ROOT
    try:
        session_id = _most_recent_incomplete_session(root)
        if session_id is None:
            return None
        return rehydrate_for_session(session_id, root_dir=root)
    except Exception:  # noqa: BLE001 — per docstring: IO/parse errors must not block startup
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "resume.rehydrate_latest_for_session: unexpected error scanning %s — "
            "starting fresh session",
            root,
            exc_info=True,
        )
        return None


def _autoresume_enabled_from_config() -> bool:
    """Read ``durability.checkpoint.autoresume_enabled`` from config/limits.yaml.

    Defaults to True on any read/parse failure — the safer behaviour for a
    durability subsystem is to attempt resume, not silently skip work.
    """
    try:
        import yaml  # local import so unit tests don't pay the cost
    except ImportError:
        return True
    cfg_path = Path(__file__).resolve().parents[2] / "config" / "limits.yaml"
    if not cfg_path.exists():
        return True
    try:
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        return True
    return bool(
        ((cfg.get("durability") or {}).get("checkpoint") or {}).get("autoresume_enabled", True)
    )
