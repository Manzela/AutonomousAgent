"""SP-R4 judge_events retention — TTL rotation for the JSONL event log.

rotate(path, cutoff_ts) reads trajectories/judge-events.jsonl, discards
events whose timestamp_utc < cutoff_ts, and atomically rewrites the file
with only the retained events.

Events older than the retention window carry goal text, tool outputs, and
other potentially PII-adjacent data. SP-R1 scrubs credentials at write time;
this TTL prunes the full record after the retention window closes, bounding
data-at-rest exposure.

The rewrite is atomic: write to a .tmp file, then os.replace() onto the
original path. If the rewrite fails, the original file is untouched.

Timestamp comparison is instant-based (datetime.fromisoformat), safe for
both '+00:00' and 'Z' suffixes with or without microseconds.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_ts(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp to a datetime, or return None.

    Accepts '+00:00' and 'Z' suffixes, with or without microseconds.
    Returns None for empty, None, or unparseable values.
    """
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def rotate(
    path: Path,
    *,
    cutoff_ts: str,
    scrub_fn: Callable[[str], str] | None = None,
) -> tuple[int, int]:
    """Rotate the judge-events JSONL by dropping events older than cutoff_ts.

    cutoff_ts must be an ISO-8601 UTC timestamp string ('+00:00' or 'Z' suffix).
    Comparison is instant-based — safe across microsecond and suffix variants.

    scrub_fn: optional callable applied to each RETAINED raw JSON line as a
    defense-in-depth pass (e.g. pass lib.scrubber.scrub_string here). If None,
    no additional scrubbing is performed; SP-R1 scrubs at write time.

    Returns (kept_count, deleted_count).

    FAIL-OPEN on parse errors: a malformed line is logged at WARNING and kept
    (prefer retaining ambiguous data over silently dropping records).

    Does nothing and returns (0, 0) if the file does not exist.
    """
    if not path.exists():
        return 0, 0

    cutoff_dt = _parse_ts(cutoff_ts)
    if cutoff_dt is None:
        raise ValueError(f"rotate: invalid cutoff_ts: {cutoff_ts!r}")

    kept: list[str] = []
    deleted = 0
    parse_errors = 0

    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        drop = False
        try:
            event = json.loads(raw)
            ts_dt = _parse_ts(event.get("timestamp_utc"))
            if ts_dt is not None and ts_dt < cutoff_dt:
                drop = True
        except json.JSONDecodeError as exc:
            logger.warning("judge_events_retention: parse error (line kept): %s", exc)
            parse_errors += 1

        if drop:
            deleted += 1
        else:
            if scrub_fn is not None:
                raw = scrub_fn(raw)
            kept.append(raw)

    if deleted == 0 and parse_errors == 0 and scrub_fn is None:
        return len(kept), 0

    tmp = path.with_suffix(".jsonl.tmp")
    try:
        content = "\n".join(kept) + ("\n" if kept else "")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("judge_events_retention: rewrite failed (original untouched): %s", exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return len(kept), 0

    return len(kept), deleted
