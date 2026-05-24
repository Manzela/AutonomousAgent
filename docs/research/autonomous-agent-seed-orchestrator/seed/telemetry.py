"""
Telemetry sink.

OTEL-shaped JSON events emitted on a single in-process queue. Two consumers:
  - `drain()` returns a snapshot list (test/inspection use)
  - `dump_jsonl(path)` flushes-and-rotates to a JSONL file

The sink is intentionally simple — production deploys would replace this
with an actual OTEL exporter. The seed keeps the contract minimal so it can
be swapped without touching call sites.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterable


class TelemetrySink:
    """Lock-free emit; lock-protected drain/dump. Bounded ring buffer."""

    def __init__(self, capacity: int = 16_384) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._counter = 0

    def emit(self, event: str, attrs: dict[str, Any] | None = None) -> None:
        """Append an event. Thread-safe (deque.append is atomic in CPython)."""
        rec = {
            "ts": time.time(),
            "event": event,
            "attrs": dict(attrs) if attrs else {},
        }
        # Counter for stable ordering between same-ts events.
        with self._lock:
            self._counter += 1
            rec["seq"] = self._counter
        self._buf.append(rec)

    def drain(self) -> list[dict[str, Any]]:
        """Return a list copy of buffered events. Does not clear the buffer."""
        with self._lock:
            return list(self._buf)

    def dump_jsonl(self, path: Path, *, clear: bool = False) -> int:
        """Write all buffered events as JSONL to `path`. Returns count written."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            events: Iterable[dict[str, Any]] = list(self._buf)
            if clear:
                self._buf.clear()
        n = 0
        with path.open("a", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, default=_json_default))
                f.write("\n")
                n += 1
        return n


def _json_default(o: Any) -> Any:
    # Lossy fallback for non-JSON-serialisable values (e.g., numpy types).
    try:
        return float(o)
    except (TypeError, ValueError):
        return repr(o)
