"""A2A inbound message scrubber (Day 9).

Reads regex patterns from config/a2a/scrubber-patterns.yaml and applies them
to all string values in a params dict (recursively). Called at the A2A
boundary to redact PHI before attaching params to spans or logs.

Per spike-plan.md §Day 9 and integration-points.md §10:
  - Patterns compiled once at import time (no per-request I/O).
  - Replacement string is [REDACTED].
  - False positives acceptable; false negatives are not.
"""

from __future__ import annotations

import logging
import pathlib
import re
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_PATTERNS_PATH = (
    pathlib.Path(__file__).parent.parent.parent / "config" / "a2a" / "scrubber-patterns.yaml"
)
_REPLACEMENT = "[REDACTED]"


def _load_patterns() -> list[re.Pattern]:
    try:
        with open(_PATTERNS_PATH) as fh:
            config = yaml.safe_load(fh)
        patterns = [re.compile(p) for p in config.get("patterns", [])]
        logger.debug("a2a.scrubber: loaded %d patterns", len(patterns))
        return patterns
    except FileNotFoundError:
        logger.warning("a2a.scrubber: patterns file not found at %s", _PATTERNS_PATH)
        return []
    except Exception as exc:
        logger.error("a2a.scrubber: failed to load patterns: %s", exc)
        return []


_COMPILED_PATTERNS: list[re.Pattern] = _load_patterns()


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        for pattern in _COMPILED_PATTERNS:
            value = pattern.sub(_REPLACEMENT, value)
        return value
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    return value


def scrub_inbound_params(params: dict[str, Any]) -> dict[str, Any]:
    """Scrub PHI patterns from all string values in inbound A2A message params.

    Args:
        params: The `params` dict from an inbound JSON-RPC envelope.

    Returns:
        New dict with all string values scrubbed. Original is not mutated.
    """
    return {k: _scrub_value(v) for k, v in params.items()}
