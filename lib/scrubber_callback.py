"""LiteLLM CustomLogger that wires lib.scrubber into the proxy logging pipeline.

Wires the regex-based secret scrubber (lib/scrubber.py) into LiteLLM v1.84.0's
custom-callback interface so every prompt/response that flows through the proxy
is scanned for secrets BEFORE it is logged downstream (OTel/Phoenix/disk).

Wiring (declared in deploy/litellm/config.yaml):
    litellm_settings:
      callbacks: ["otel", "scrubber_callback.proxy_handler_instance"]

LiteLLM's get_instance_fn (litellm.proxy.types_utils.utils) splits on the
last dot, imports `scrubber_callback` from the config-file directory, and
fetches the `proxy_handler_instance` attribute. So this file must be mounted
next to config.yaml inside the litellm container (see docker-compose.yml).

Hook semantics — we use `async_logging_hook` / `logging_hook` (fires BEFORE
the log_success_event chain), which is the right place to redact rather than
post-hoc filter. We don't replace the LLM response that flows back to the
caller; LiteLLM's documented contract for these hooks is "modify what gets
logged", which is precisely the threat model: secrets in prompts/responses
landing in OTel/Phoenix/disk.

Leak log: redaction events are appended to LEAK_LOG_PATH (default
/data/secret-leak-attempts.log inside the container). Each line is a single
JSON object: {ts, call_type, source, pattern_name, severity}. The actual
secret value is NEVER written — only the pattern_name that fired.

Audit reference: phase1-to-phase2-readiness-2026-05-19/security-audit.md C1.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

# Ensure /app is on sys.path so `import lib.scrubber` resolves when LiteLLM
# loads this module via importlib.util.spec_from_file_location (which does
# NOT add the file's dir to sys.path). The compose mount places lib/ at
# /app/lib, mirroring the hermes service's PYTHONPATH=/app convention.
_APP_ROOT = "/app"
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

from lib.scrubber import Scrubber, ScrubHit  # noqa: E402

_LOG = logging.getLogger("litellm.scrubber_callback")

# Patterns file path inside the litellm container — mounted from
# config/scrubber-patterns.yaml by docker-compose.yml. Override via env
# for tests or future relocation.
PATTERNS_PATH = Path(os.environ.get("SCRUBBER_PATTERNS_PATH", "/app/scrubber-patterns.yaml"))

# Where redaction events are appended. Default is /data (shared with hermes
# via the hermes-data named volume). Override via env if mount layout changes.
LEAK_LOG_PATH = Path(os.environ.get("SCRUBBER_LEAK_LOG_PATH", "/data/secret-leak-attempts.log"))


def _load_scrubber() -> Optional[Scrubber]:
    """Best-effort scrubber bootstrap; logs and returns None on failure so the
    proxy keeps serving even if the patterns file is missing/malformed."""
    try:
        if not PATTERNS_PATH.exists():
            _LOG.error(
                "scrubber_callback: patterns file %s missing — scrubber disabled",
                PATTERNS_PATH,
            )
            return None
        return Scrubber.from_config(PATTERNS_PATH)
    except Exception as exc:  # pragma: no cover - defensive
        _LOG.exception("scrubber_callback: failed to load patterns: %s", exc)
        return None


def _record_hits(hits: List[ScrubHit], call_type: str) -> None:
    """Append one JSON line per hit to LEAK_LOG_PATH. Never raises."""
    if not hits:
        return
    try:
        # Lazy mkdir — /data may not exist in non-container test runs.
        LEAK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with LEAK_LOG_PATH.open("a", encoding="utf-8") as fh:
            for hit in hits:
                fh.write(
                    json.dumps(
                        {
                            "ts": ts,
                            "call_type": call_type,
                            "source": hit.source,
                            "pattern_name": hit.pattern_name,
                            "severity": hit.severity,
                        }
                    )
                    + "\n"
                )
    except Exception as exc:  # pragma: no cover - defensive
        _LOG.exception("scrubber_callback: failed to write leak log: %s", exc)


def _scrub_messages(
    messages: Optional[List[Any]], scrubber: Scrubber, source: str
) -> Tuple[Optional[List[Any]], List[ScrubHit]]:
    """Walk an OpenAI-style messages list, scrub `content` fields, collect hits.

    Each message may have `content` as a str or as a list of content parts
    (vision/multimodal). We scrub str content directly; for parts we scrub
    each `text` field. Non-text parts (image_url, tool_use, ...) pass through.
    """
    if not messages:
        return messages, []
    all_hits: List[ScrubHit] = []
    new_messages: List[Any] = []
    for msg in messages:
        if not isinstance(msg, dict):
            new_messages.append(msg)
            continue
        new_msg = dict(msg)
        content = new_msg.get("content")
        if isinstance(content, str):
            cleaned, hits = scrubber.scrub(content, source=source)
            if hits:
                all_hits.extend(hits)
            new_msg["content"] = cleaned
        elif isinstance(content, list):
            new_parts = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    cleaned, hits = scrubber.scrub(part["text"], source=source)
                    if hits:
                        all_hits.extend(hits)
                    new_part = dict(part)
                    new_part["text"] = cleaned
                    new_parts.append(new_part)
                else:
                    new_parts.append(part)
            new_msg["content"] = new_parts
        new_messages.append(new_msg)
    return new_messages, all_hits


def _scrub_response(response_obj: Any, scrubber: Scrubber) -> Tuple[Any, List[ScrubHit]]:
    """Best-effort scrub of an OpenAI ChatCompletion-shaped response.

    Returns the (possibly modified) response and the list of hits. We never
    raise — if the response shape is unfamiliar we pass it through untouched.
    """
    if response_obj is None:
        return response_obj, []
    try:
        # Both dict-like and ModelResponse pydantic objects expose .choices.
        choices = (
            response_obj.get("choices")
            if isinstance(response_obj, dict)
            else getattr(response_obj, "choices", None)
        )
        if not choices:
            return response_obj, []
        all_hits: List[ScrubHit] = []
        for choice in choices:
            message = (
                choice.get("message")
                if isinstance(choice, dict)
                else getattr(choice, "message", None)
            )
            if message is None:
                continue
            content = (
                message.get("content")
                if isinstance(message, dict)
                else getattr(message, "content", None)
            )
            if isinstance(content, str):
                cleaned, hits = scrubber.scrub(content, source="model_response")
                if hits:
                    all_hits.extend(hits)
                if isinstance(message, dict):
                    message["content"] = cleaned
                else:
                    try:
                        message.content = cleaned  # type: ignore[attr-defined]
                    except Exception:
                        pass
        return response_obj, all_hits
    except Exception as exc:  # pragma: no cover - defensive
        _LOG.exception("scrubber_callback: response scrub failed: %s", exc)
        return response_obj, []


# Lazy single instance — load patterns once at module import.
_SCRUBBER: Optional[Scrubber] = _load_scrubber()


# CustomLogger is imported lazily inside the class definition guard so this
# module can be imported in unit tests that don't have litellm installed.
try:
    from litellm.integrations.custom_logger import CustomLogger  # type: ignore
except Exception:  # pragma: no cover - test-time fallback

    class CustomLogger:  # type: ignore[no-redef]
        pass


class ScrubberCallback(CustomLogger):
    """Wraps lib.scrubber.Scrubber as a LiteLLM logging callback.

    LiteLLM v1.84.0 calls `async_logging_hook(kwargs, result, call_type)`
    immediately before `async_log_success_event` (and the sync variant before
    `log_success_event`). Returning a modified `kwargs` redacts the prompt
    in everything that LiteLLM subsequently logs (OTel exporter included).
    """

    def __init__(self) -> None:
        super().__init__()

    # --- Async path (used by /v1/chat/completions which calls acompletion) -------

    async def async_logging_hook(
        self, kwargs: dict, result: Any, call_type: str
    ) -> Tuple[dict, Any]:
        import asyncio

        # _do_scrub does synchronous file I/O (_record_hits). Offload to a
        # thread so the event loop is not blocked on the log-file append.
        return await asyncio.to_thread(self._do_scrub, kwargs, result, call_type)

    def logging_hook(self, kwargs: dict, result: Any, call_type: str) -> Tuple[dict, Any]:
        return self._do_scrub(kwargs, result, call_type)

    # --- Shared impl ---------------------------------------------------------

    def _do_scrub(self, kwargs: dict, result: Any, call_type: str) -> Tuple[dict, Any]:
        if _SCRUBBER is None:
            return kwargs, result
        try:
            messages = kwargs.get("messages")
            new_messages, prompt_hits = _scrub_messages(messages, _SCRUBBER, source="prompt")
            new_result, response_hits = _scrub_response(result, _SCRUBBER)
            if prompt_hits or response_hits:
                _record_hits(prompt_hits + response_hits, call_type=call_type)
                if new_messages is not None:
                    kwargs["messages"] = new_messages
                return kwargs, new_result
            return kwargs, result
        except Exception as exc:  # pragma: no cover - defensive
            _LOG.exception("scrubber_callback: _do_scrub failed: %s", exc)
            return kwargs, result


# Module-level instance referenced from config.yaml as
# `scrubber_callback.proxy_handler_instance`. The name matches the LiteLLM
# proxy docs convention.
proxy_handler_instance = ScrubberCallback()
