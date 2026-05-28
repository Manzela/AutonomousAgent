"""Regex-based secret scrubber.

Reads patterns from config/scrubber-patterns.yaml. Scrubs strings before persist or outbound.
Logs every hit (severity, pattern_name, redacted_at, source_context) to scrubber_log_path.

Public API:
  scrubber = Scrubber.from_config(Path("config/scrubber-patterns.yaml"))
  cleaned, hits = scrubber.scrub(text, source="model_response")
  # `hits` is a list of (pattern_name, severity) tuples for caller to log/alert

  # Module-level convenience for call sites that just want a one-shot scrub
  # of a single string with no per-callsite YAML bootstrap (alert payload
  # paths in lib/durability/* and lib/kanban/*). Lazy-loads a process-wide
  # singleton from the first available patterns YAML on disk; on bootstrap
  # failure returns the input unchanged so the caller's fail-open path is
  # preserved.
  from lib.scrubber import scrub_string
  cleaned = scrub_string(text, source="telegram_alert")
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional

import yaml

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScrubPattern:
    name: str
    regex: re.Pattern
    replacement: str
    severity: str  # "info" | "warning" | "critical"


@dataclass(frozen=True)
class ScrubHit:
    pattern_name: str
    severity: str
    source: str


class Scrubber:
    def __init__(self, patterns: Iterable[ScrubPattern]):
        self._patterns = list(patterns)

    @classmethod
    def from_config(cls, config_path: Path) -> Scrubber:
        with config_path.open() as f:
            data = yaml.safe_load(f)
        patterns = [
            ScrubPattern(
                name=p["name"],
                regex=re.compile(p["regex"]),
                replacement=p["replacement"],
                severity=p["severity"],
            )
            for p in data["patterns"]
        ]
        return cls(patterns)

    def scrub(self, text: str, *, source: str = "unknown") -> tuple[str, list[ScrubHit]]:
        hits: list[ScrubHit] = []
        scrubbed = text
        for p in self._patterns:
            new, n = p.regex.subn(p.replacement, scrubbed)
            if n > 0:
                hits.extend(ScrubHit(p.name, p.severity, source) for _ in range(n))
            scrubbed = new
        return scrubbed, hits


# ---------------------------------------------------------------------------
# Module-level convenience: scrub_string
# ---------------------------------------------------------------------------
#
# The class-based API requires the caller to bootstrap a Scrubber from a
# patterns YAML, which is fine for the LiteLLM callback (loaded once at
# proxy startup) but awkward for ad-hoc call sites (every outbound alert
# path would have to re-implement path resolution + caching). The helper
# below centralises that bootstrap so all call sites — the existing
# ``scrubber_callback`` and the new alert payload sites in
# ``lib/durability/*`` + ``lib/kanban/telegram_bridge`` — share the same
# patterns + the same single regex compilation.
#
# Path resolution mirrors ``lib/scrubber_callback.PATTERNS_PATH``:
# ``SCRUBBER_PATTERNS_PATH`` env var first, then a small list of known
# locations (container mount paths + the in-repo dev path). On bootstrap
# failure (no patterns file, malformed YAML) the helper is fail-open:
# ``scrub_string`` returns the input unchanged so a missing config never
# crashes a fail-open alert path.

_SINGLETON: Optional[Scrubber] = None
_SINGLETON_FAILED = False
_SINGLETON_LOCK = Lock()


def _default_patterns_path_candidates() -> list[Path]:
    """Ordered list of paths to try when loading the singleton patterns YAML.

    Env override wins. Otherwise we try the two container mount points
    (litellm proxy mounts at ``/app/scrubber-patterns.yaml``; hermes-agent
    mounts at ``/app/runtime/scrubber-patterns.yaml`` — see
    deploy/docker-compose.yml lines 136 and 366) and finally fall back to
    the in-repo dev path so unit tests and local runs work without
    setting an env var.
    """
    env_override = os.environ.get("SCRUBBER_PATTERNS_PATH")
    candidates: list[Path] = []
    if env_override:
        candidates.append(Path(env_override))
    candidates.extend(
        [
            Path("/app/runtime/scrubber-patterns.yaml"),
            Path("/app/scrubber-patterns.yaml"),
            # In-repo dev path: ``lib/scrubber.py`` lives at
            # ``<repo>/lib/scrubber.py`` so the patterns YAML is one
            # directory up + ``config/``.
            Path(__file__).resolve().parent.parent / "config" / "scrubber-patterns.yaml",
        ]
    )
    return candidates


def _load_singleton() -> Optional[Scrubber]:
    """Lazy + thread-safe singleton load. Returns ``None`` on any failure."""
    global _SINGLETON, _SINGLETON_FAILED
    if _SINGLETON is not None:
        return _SINGLETON
    if _SINGLETON_FAILED:
        return None
    with _SINGLETON_LOCK:
        if _SINGLETON is not None:
            return _SINGLETON
        if _SINGLETON_FAILED:
            return None
        for path in _default_patterns_path_candidates():
            if not path.exists():
                continue
            try:
                _SINGLETON = Scrubber.from_config(path)
                return _SINGLETON
            except Exception as exc:  # noqa: BLE001 — fail-open
                _LOG.warning(
                    "scrubber: failed to load patterns from %s: %s — trying next candidate",
                    path,
                    exc,
                )
                continue
        # Exhausted all candidates without success — disable until reset.
        _SINGLETON_FAILED = True
        _LOG.error(
            "scrubber: no usable patterns file found in any candidate path — "
            "scrub_string() will pass text through unchanged"
        )
        return None


def scrub_string(text: str, *, source: str = "unknown") -> str:
    """Scrub a single string using the process-wide patterns singleton.

    Returns the redacted text. Fail-open: on bootstrap failure or
    non-string input returns the input unchanged. Idempotent on
    already-scrubbed text (the replacement tokens don't match any
    pattern's regex).

    The ``source`` label is forwarded to the underlying ``Scrubber.scrub``
    so leak-log entries can attribute the hit to a specific call site
    (e.g. ``telegram_alert``, ``github_fallback_title``).
    """
    if not isinstance(text, str) or not text:
        return text
    scrubber = _load_singleton()
    if scrubber is None:
        return text
    cleaned, _hits = scrubber.scrub(text, source=source)
    return cleaned


def _reset_singleton_for_tests() -> None:
    """Test-only hook: drop the cached singleton + clear the failure flag.

    Tests that monkeypatch ``SCRUBBER_PATTERNS_PATH`` need this to force
    the next ``scrub_string`` call to re-resolve the path. Not part of
    the runtime API surface — production code must never call this.
    """
    global _SINGLETON, _SINGLETON_FAILED
    with _SINGLETON_LOCK:
        _SINGLETON = None
        _SINGLETON_FAILED = False


class ScrubFilter(logging.Filter):
    """logging.Filter that redacts secrets from Python logger messages.

    Install on any logger or root handler so that every ``logger.info/warning/
    error/debug(...)`` call is scrubbed before it reaches Cloud Logging or
    stdout.  Closes O-7: without this, JWTs, session-ids, and chat-ids written
    by any Python logger bypass the scrubber that guards A2A + Telegram.

    Usage::

        import logging
        from lib.scrubber import ScrubFilter
        logging.getLogger().addFilter(ScrubFilter())   # root logger — catches all

    Fail-open: if the scrubber singleton fails to load, or if scrubbing itself
    raises, the original log record passes through unmodified.  The filter
    always returns ``True`` so callers never lose a log line.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = scrub_string(str(record.msg), source="logging.filter")
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: (scrub_string(v, source="logging.filter") if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        scrub_string(a, source="logging.filter") if isinstance(a, str) else a
                        for a in record.args
                    )
        except Exception:  # noqa: BLE001 — fail-open
            pass
        return True


__all__ = [
    "ScrubPattern",
    "ScrubHit",
    "Scrubber",
    "ScrubFilter",
    "scrub_string",
]
