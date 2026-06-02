"""Append-only HITL decision-record (SP-01 §9 non-repudiation).

Extends the judge_events.py append-only discipline (os.O_APPEND + fcntl.flock),
keyed by interrupt_id, fail-open. PLAIN JSONL now; the tamper-evident hash-chain
is deferred to the SP-27 monitor layer / the #192 TamperEvidentLedger follow-up.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional, Union

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _REPO_ROOT / "trajectories" / "decision-record.jsonl"


def _default_path() -> Path:
    """Resolve the durable decision-record path. SPINE_DECISION_RECORD_PATH overrides
    (lets tests stay hermetic instead of polluting the repo's trajectories/ dir, and
    lets ops relocate the trail) — mirrors the judge_events config-driven path."""
    env = os.environ.get("SPINE_DECISION_RECORD_PATH")
    return Path(env) if env else DEFAULT_PATH


def _append_line(path: Path, line: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def append_decision(
    decision: dict[str, Any],
    *,
    path: Optional[Union[Path, str]] = None,
    enabled: Optional[bool] = None,
) -> Optional[Path]:
    """Append one HITL decision as a JSONL record keyed by interrupt_id.
    Fail-open: returns the written path, or None on any failure / when disabled."""
    if enabled is False:
        return None
    target = Path(path) if path is not None else _default_path()
    try:
        record = {
            "schema_version": SCHEMA_VERSION,
            "interrupt_id": decision["interrupt_id"],
            "verb": decision["verb"],
            "actor": decision.get("actor", "<unknown>"),
            "reason": decision.get("reason", ""),
            "ts": decision.get("ts", ""),
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        _append_line(target, json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        return target
    except Exception as exc:  # noqa: BLE001 - fail-open audit append
        logger.warning("decision_record append failed (fail-open): %s", exc)
        return None
