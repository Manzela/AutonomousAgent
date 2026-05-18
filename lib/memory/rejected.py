"""P1-4 REJECTED.md institutional-memory store.

Append/dedupe/load + intent-category-filtered list. Storage is a single
markdown file with one YAML frontmatter block per entry, intentionally
human-editable so the user can `vi /data/MEMORY/REJECTED.md` to curate.

Dedup key: ``approach_fingerprint`` per design-alignment spec L337-339:

  approach_fingerprint = sha256(json.dumps(
      [{"tool": tc.tool_name, "first_arg": _truncate(tc.first_arg, 80)}
       for tc in session.tool_calls_since_last_taskspec_lock],
      sort_keys=True))

When ``append_entry`` is called with an existing fingerprint we bump an
``occurrence_count`` counter inside the matching block instead of writing
a duplicate row.

Format of each entry (one of many in the file):

    ---
    id: rej-abcd1234
    approach_fingerprint: <sha256 hex>
    approach_summary: Human-readable one-liner
    taskspec_id: <uuid or string>
    intent_category: coding
    why_failed: |
        Free-form text describing why consensus rejected this approach.
    alternatives: |
        Free-form suggestions for what to try instead.
    occurrence_count: 1
    created_at: 2026-05-18T12:34:56+00:00
    expires_at: 2026-06-17T12:34:56+00:00
    ---

Entries past ``expires_at`` are skipped by ``load_active_entries`` but
remain on disk until the operator runs ``/forget`` or edits the file.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("/data/MEMORY/REJECTED.md")
DEFAULT_TTL_DAYS = 30
DEFAULT_MAX_INJECT = 10

# Match a complete frontmatter block (--- ... ---) followed by an optional
# blank line. Non-greedy body so adjacent blocks don't collapse.
_ENTRY_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL | re.MULTILINE)
# Match key: value or key: |  (multi-line) inside the frontmatter body
_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):\s?(.*)$")


# ---------------------------------------------------------------------------
# Fingerprint helper (verbatim formula from design-alignment spec L337-339)
# ---------------------------------------------------------------------------
def compute_fingerprint(tool_calls: Iterable[dict[str, Any]]) -> str:
    """sha256 over the JSON-canonicalized tool-call sequence.

    Each tool-call dict must have ``tool`` and ``first_arg`` keys. ``first_arg``
    is truncated to 80 chars (per spec) to absorb minor variation in long
    paths/queries without diluting the dedup signal.
    """
    normalized = [
        {"tool": tc.get("tool", ""), "first_arg": str(tc.get("first_arg", ""))[:80]}
        for tc in tool_calls
    ]
    canonical = json.dumps(normalized, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Frontmatter parse / serialize
# ---------------------------------------------------------------------------
def _serialize_entry(entry: dict[str, Any]) -> str:
    """Render a single entry as a YAML-frontmatter block.

    Multi-line free-form text fields (``why_failed``, ``alternatives``) use
    the ``|`` literal-block YAML style so the human can read them as-is.
    """
    lines = ["---"]
    block_keys = {"why_failed", "alternatives"}
    # Stable key order — makes diff review and human editing predictable.
    key_order = [
        "id",
        "approach_fingerprint",
        "approach_summary",
        "taskspec_id",
        "intent_category",
        "why_failed",
        "alternatives",
        "occurrence_count",
        "created_at",
        "expires_at",
    ]
    seen: set[str] = set()
    for k in key_order:
        if k not in entry:
            continue
        seen.add(k)
        v = entry[k]
        if k in block_keys and isinstance(v, str) and ("\n" in v or len(v) > 60):
            lines.append(f"{k}: |")
            for ln in v.splitlines() or [""]:
                lines.append(f"    {ln}")
        else:
            lines.append(f"{k}: {v}")
    # Pass-through any extra keys not in the canonical order
    for k, v in entry.items():
        if k in seen:
            continue
        if k in block_keys and isinstance(v, str) and ("\n" in v or len(v) > 60):
            lines.append(f"{k}: |")
            for ln in v.splitlines() or [""]:
                lines.append(f"    {ln}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _parse_entry(block_body: str) -> dict[str, Any]:
    """Parse a single frontmatter body (without the --- delimiters) into a dict.

    Handles scalar ``key: value`` and YAML literal-block ``key: |\n    line``.
    Deliberately minimal — we own the writer, so we can rely on stable shape.
    """
    out: dict[str, Any] = {}
    lines = block_body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _FIELD_RE.match(line)
        if not m:
            i += 1
            continue
        key, value = m.group(1), m.group(2)
        if value.strip() == "|":
            # Literal block: gather subsequent indented lines
            block_lines: list[str] = []
            j = i + 1
            while j < len(lines) and (lines[j].startswith("    ") or lines[j] == ""):
                block_lines.append(lines[j][4:] if lines[j].startswith("    ") else "")
                j += 1
            out[key] = "\n".join(block_lines).rstrip()
            i = j
            continue
        # Coerce simple types so callers don't have to
        v: Any = value
        if v.isdigit():
            v = int(v)
        out[key] = v
        i += 1
    return out


def _parse_all_entries(text: str) -> list[dict[str, Any]]:
    """Return a list of entry dicts in file order."""
    return [_parse_entry(m.group(1)) for m in _ENTRY_RE.finditer(text)]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def append_entry(
    *,
    approach_fingerprint: str,
    approach_summary: str,
    taskspec_id: str,
    intent_category: str,
    why_failed: str,
    alternatives: str,
    ttl_days: int = DEFAULT_TTL_DAYS,
    path: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Append a rejected-approach entry, or bump occurrence_count if the
    fingerprint already exists.

    Idempotent at the fingerprint level: calling twice with the same
    ``approach_fingerprint`` results in one entry with ``occurrence_count=2``.
    """
    target = path or DEFAULT_PATH
    now_dt = now or datetime.now(timezone.utc)
    expires_dt = now_dt + timedelta(days=ttl_days)

    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8") if target.exists() else ""
    entries = _parse_all_entries(existing)

    # Dedup: bump counter on fingerprint match.
    for e in entries:
        if e.get("approach_fingerprint") == approach_fingerprint:
            try:
                count = int(e.get("occurrence_count", 1))
            except (TypeError, ValueError):
                count = 1
            e["occurrence_count"] = count + 1
            # Refresh expires_at so the entry "stays warm" with repeated rejection.
            e["expires_at"] = expires_dt.isoformat()
            new_text = "".join(_serialize_entry(x) for x in entries)
            target.write_text(new_text, encoding="utf-8")
            return

    new_entry = {
        "id": f"rej-{uuid.uuid4().hex[:8]}",
        "approach_fingerprint": approach_fingerprint,
        "approach_summary": approach_summary,
        "taskspec_id": taskspec_id,
        "intent_category": intent_category,
        "why_failed": why_failed,
        "alternatives": alternatives,
        "occurrence_count": 1,
        "created_at": now_dt.isoformat(),
        "expires_at": expires_dt.isoformat(),
    }
    entries.append(new_entry)
    new_text = "".join(_serialize_entry(e) for e in entries)
    target.write_text(new_text, encoding="utf-8")


def load_active_entries(
    *,
    intent_category: str,
    path: Path | None = None,
    max_entries: int = DEFAULT_MAX_INJECT,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return unexpired entries matching ``intent_category``, newest-first.

    Caps the returned list at ``max_entries`` to keep injected context bounded.
    """
    target = path or DEFAULT_PATH
    if not target.exists():
        return []
    now_dt = now or datetime.now(timezone.utc)

    entries = _parse_all_entries(target.read_text(encoding="utf-8"))
    out: list[dict[str, Any]] = []
    for e in entries:
        if e.get("intent_category") != intent_category:
            continue
        try:
            exp = datetime.fromisoformat(str(e.get("expires_at", "")))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            # Malformed expires_at → treat as already expired (skip).
            continue
        if exp <= now_dt:
            continue
        out.append(e)

    # Newest first (by created_at where parseable; fall back to file order).
    def _ts(e: dict[str, Any]) -> str:
        return str(e.get("created_at", ""))

    out.sort(key=_ts, reverse=True)
    return out[: max(0, int(max_entries))]


def forget(pattern_or_id: str, *, path: Path | None = None) -> int:
    """Delete matching entries. Returns count removed.

    Matching rules:
    - ``id:<id>`` → exact match on the entry ``id`` field
    - any other string → glob/substring match against ``approach_summary``
      OR ``id``. ``fnmatch`` if the string contains ``*`` or ``?``;
      otherwise plain substring (case-insensitive).
    """
    target = path or DEFAULT_PATH
    if not target.exists():
        return 0

    entries = _parse_all_entries(target.read_text(encoding="utf-8"))

    if pattern_or_id.startswith("id:"):
        wanted_id = pattern_or_id[3:].strip()

        def match(e: dict[str, Any]) -> bool:
            return str(e.get("id", "")) == wanted_id
    else:
        needle = pattern_or_id.strip().lower()
        is_glob = any(ch in needle for ch in "*?[")

        def match(e: dict[str, Any]) -> bool:
            summary = str(e.get("approach_summary", "")).lower()
            entry_id = str(e.get("id", "")).lower()
            if is_glob:
                return fnmatch.fnmatch(summary, needle) or fnmatch.fnmatch(entry_id, needle)
            return needle in summary or needle in entry_id

    keep = [e for e in entries if not match(e)]
    removed = len(entries) - len(keep)
    if removed:
        target.write_text("".join(_serialize_entry(e) for e in keep), encoding="utf-8")
    return removed


def list_active(*, path: Path | None = None, now: datetime | None = None) -> list[str]:
    """Short one-line summaries for the ``/rejections`` slash command.

    Returns up to ``DEFAULT_MAX_INJECT`` items across all intent_categories,
    newest first, in the form ``<id>  [<category>]  <summary>``.
    """
    target = path or DEFAULT_PATH
    if not target.exists():
        return []
    now_dt = now or datetime.now(timezone.utc)

    entries = _parse_all_entries(target.read_text(encoding="utf-8"))
    active: list[dict[str, Any]] = []
    for e in entries:
        try:
            exp = datetime.fromisoformat(str(e.get("expires_at", "")))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if exp > now_dt:
            active.append(e)
    active.sort(key=lambda e: str(e.get("created_at", "")), reverse=True)
    return [
        f"{e.get('id', '?')}  [{e.get('intent_category', '?')}]  {e.get('approach_summary', '')}"
        for e in active[:DEFAULT_MAX_INJECT]
    ]
