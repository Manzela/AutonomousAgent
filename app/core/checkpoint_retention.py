"""SP-R4 LangGraph checkpoint retention — GC expired threads.

gc_checkpoints(saver, expired_thread_ids) deletes all checkpoints for the
specified thread IDs. Callers decide which threads are "expired" (by age or
count policy); this module provides the mechanical delete+count interface.

find_threads_older_than() is the age-based selector: it checks the newest
checkpoint per thread against a caller-supplied ISO-8601 cutoff timestamp.
Comparison is instant-based (datetime.fromisoformat), safe for both
'+00:00' and 'Z' suffixes with or without microseconds.

Both functions are FAIL-OPEN: errors on individual threads are logged and
skipped so a single corrupted thread never blocks GC of the rest.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)


def _parse_ts(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 UTC timestamp string to a datetime, or return None.

    Accepts both '+00:00' and 'Z' UTC suffixes, with or without microseconds.
    Returns None for empty, None, or unparseable strings (safe fallback: treat
    as non-expired rather than accidentally deleting a checkpoint with a bad ts).
    """
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


async def gc_checkpoints(
    saver: BaseCheckpointSaver,
    expired_thread_ids: Sequence[str],
) -> int:
    """Delete all checkpoints for each thread in expired_thread_ids.

    Returns the number of threads successfully deleted.
    """
    count = 0
    for tid in expired_thread_ids:
        try:
            await saver.adelete_thread(tid)
            count += 1
        except Exception as exc:
            logger.warning("checkpoint_retention: failed to gc thread %s: %s", tid, exc)
    return count


async def find_threads_older_than(
    saver: BaseCheckpointSaver,
    *,
    cutoff_ts: str,
    candidate_thread_ids: Sequence[str],
) -> list[str]:
    """Return the subset of candidate_thread_ids whose newest checkpoint predates cutoff_ts.

    cutoff_ts must be an ISO-8601 UTC timestamp string ('+00:00' or 'Z' suffix).
    Comparison is instant-based via datetime.fromisoformat — safe across microsecond
    and UTC-suffix variants.

    Threads with no checkpoints, or whose newest checkpoint has an absent/unparseable
    'ts' field, are excluded (KEEP is the safe posture for uncertain age).
    """
    cutoff_dt = _parse_ts(cutoff_ts)
    if cutoff_dt is None:
        raise ValueError(f"find_threads_older_than: invalid cutoff_ts: {cutoff_ts!r}")

    expired = []
    for tid in candidate_thread_ids:
        config = {"configurable": {"thread_id": tid}}
        latest_dt: datetime | None = None
        async for ckpt_tuple in saver.alist(config, limit=1):
            latest_dt = _parse_ts(ckpt_tuple.checkpoint.get("ts"))
            break
        if latest_dt is not None and latest_dt < cutoff_dt:
            expired.append(tid)
    return expired
