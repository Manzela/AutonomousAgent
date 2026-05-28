"""Judge-panel JSONL event persistence (J1 — Framing #2).

Every consensus decision is one line in ``trajectories/judge-events.jsonl``.
Downstream RL trajectory shipper (J3) tails this file to derive reward shaping
signals for the self-RL pipeline.

Design constraints:
- **Fail-open**: any IOError / PermissionError / OSError must be logged and
  swallowed. Persistence must never break the consensus call path.
- **Append-only JSONL**: one well-formed JSON object per line, no partial
  writes. ``fcntl.flock`` on POSIX gives concurrent-safe appends from
  multiple judge-panel threads in the same process.
- **Schema-versioned**: ``schema_version`` field is bumped only via an ADR.
  J3 shipper validates this before forwarding to the RL pipeline.

This module is called by ``lib/evaluators/__init__._on_post_tool_call`` once
Task 21 wires the live judge-panel dispatch (the orchestrator-side queue is
already in place via ``orchestrator_hook.queue_judge_dispatch``).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lib.evaluators.consensus import ConsensusResult
from lib.evaluators.judge import JudgeResult

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
# Anchor to repo root, not CWD (CWD varies by launch context).
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = _REPO_ROOT / "trajectories" / "judge-events.jsonl"
WORKER_SUMMARY_MAX_CHARS = 500
JUDGE_REASONING_MAX_CHARS = 1000

_PROCESS_LOCK = threading.Lock()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _truncate(s: str, *, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"...(truncated {len(s) - max_chars} chars)"


def _judge_to_dict(j: JudgeResult) -> dict:
    return {
        "axis": j.axis,
        "score": j.score,
        "verdict": j.verdict,
        "reasoning": _truncate(j.reasoning or "", max_chars=JUDGE_REASONING_MAX_CHARS),
        "model": j.model,
    }


def _result_to_event(
    result: ConsensusResult,
    *,
    session_id: str,
    task_spec_id: str,
    worker_action_summary: str,
) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "timestamp_utc": _utcnow_iso(),
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "task_spec_id": task_spec_id,
        "worker_action_summary": _truncate(
            worker_action_summary, max_chars=WORKER_SUMMARY_MAX_CHARS
        ),
        "consensus": {
            "verdict": result.verdict,
            "accept_count": result.accept_count,
            "reject_count": result.reject_count,
            "unsure_count": result.unsure_count,
            "escalated": result.escalated,
            "rationale": result.rationale,
        },
        "judges": [_judge_to_dict(j) for j in result.judges],
        "fifth_judge": _judge_to_dict(result.fifth_judge) if result.fifth_judge else None,
    }


@contextmanager
def _file_lock(fh):  # pragma: no cover - exercised indirectly by concurrent test
    """fcntl.flock on POSIX; in-process lock on platforms without fcntl."""
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except ImportError:
        with _PROCESS_LOCK:
            yield


def _append_line(path: Path, line: str) -> None:
    """Append one line to ``path``, creating parent dirs if missing.

    Uses an OS-level advisory lock (``fcntl.flock``) plus a process-local
    mutex so concurrent threads cannot interleave partial writes. The newline
    is appended here so callers pass the bare JSON.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Use os.open + O_APPEND for atomic append semantics on POSIX, then wrap
    # in a Python file object so we can fcntl-lock it.
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    # os.fdopen takes ownership of fd and closes it when the file object is
    # closed — do NOT call os.close(fd) separately or we get EBADF.
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        with _file_lock(fh):
            fh.write(line + "\n")
            fh.flush()


def _config_enabled() -> bool:
    """Read ``evaluators.judge_events.enabled`` from config/limits.yaml.

    Defaults to True so the recorder ships data by default once Task 21
    wires the live dispatch. Config faults default to True (fail-safe for
    observability — better to write than silently drop).
    """
    try:
        import yaml

        cfg_path = Path(__file__).resolve().parents[2] / "config" / "limits.yaml"
        if not cfg_path.exists():
            return True
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        je = (cfg.get("evaluators") or {}).get("judge_events") or {}
        return bool(je.get("enabled", True))
    except Exception:  # noqa: BLE001 - config faults must not silently disable
        return True


def _config_path() -> Path:
    """Read ``evaluators.judge_events.path`` from config/limits.yaml."""
    try:
        import yaml

        cfg_path = Path(__file__).resolve().parents[2] / "config" / "limits.yaml"
        if not cfg_path.exists():
            return DEFAULT_PATH
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        je = (cfg.get("evaluators") or {}).get("judge_events") or {}
        return Path(je.get("path", str(DEFAULT_PATH)))
    except Exception:  # noqa: BLE001
        return DEFAULT_PATH


def record_consensus_event(
    result: ConsensusResult,
    *,
    session_id: str,
    task_spec_id: str,
    worker_action_summary: str,
    path: Optional[Path] = None,
    enabled: Optional[bool] = None,
) -> Optional[Path]:
    """Append one event to the judge-events JSONL log.

    Returns the resolved path on success, ``None`` if disabled or on error.
    Never raises — failures are logged at WARNING.

    Args:
        result: The 4-judge (+ optional 5th) consensus outcome.
        session_id: Hermes session id (correlates with checkpoints, REJECTED.md).
        task_spec_id: Locked TaskSpec id (correlates with anchors).
        worker_action_summary: One-line summary of the tool call being judged.
            Truncated to ``WORKER_SUMMARY_MAX_CHARS`` to bound file growth.
        path: Override target path (tests use tmp_path; production reads config).
        enabled: Override enabled flag (tests pass explicit bool; production
            reads ``evaluators.judge_events.enabled`` from config/limits.yaml).
    """
    is_enabled = enabled if enabled is not None else _config_enabled()
    if not is_enabled:
        return None

    target = path if path is not None else _config_path()

    try:
        event = _result_to_event(
            result,
            session_id=session_id,
            task_spec_id=task_spec_id,
            worker_action_summary=worker_action_summary,
        )
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        _append_line(target, line)
    except Exception as exc:  # noqa: BLE001 - fail-open: persistence is best-effort
        logger.warning(
            "judge_events: write failed (non-fatal) path=%s err=%s",
            target,
            exc,
        )
        return None
    return target
