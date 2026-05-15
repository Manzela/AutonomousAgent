"""Regex-based secret scrubber.

Reads patterns from config/scrubber-patterns.yaml. Scrubs strings before persist or outbound.
Logs every hit (severity, pattern_name, redacted_at, source_context) to scrubber_log_path.

Public API:
  scrubber = Scrubber.from_config(Path("config/scrubber-patterns.yaml"))
  cleaned, hits = scrubber.scrub(text, source="model_response")
  # `hits` is a list of (pattern_name, severity) tuples for caller to log/alert
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


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
